"""The Friendly TV (Revlet) HTTP API.

Every endpoint here was read from a capture of the web player; none is
guessed. docs/frndlytv-protocol.md records where each one came from and what
it answers with.

The shape worth knowing before reading the rest: almost everything the
service returns is a *page*. ``page/content?path=X`` answers with a list of
panes, each pane holding one ``section``; a section has a ``sectionInfo``
naming it and a ``sectionData.data`` list of cards. A card carries a
``display`` block (title, subtitles, images) and a ``target`` block saying
what selecting it does -- ``pageType: "player"`` means it plays, anything
else means it opens another page at ``target.path``. That single rule is
what lets one browse route serve Home, Movies, TV and My Stuff alike.
"""

import time

import requests

from . import auth, kodiutils

API_BASE = auth.API_BASE
GUIDE_BASE = "https://frndlytv-tvguideapi.revlet.net"

# The stream endpoint wants to know which provider profile to hand back. The
# web player sends 5 and receives Widevine-protected DASH, which is what
# InputStream Adaptive can play; this is copied from the capture.
STREAM_PROVIDER_DEVICE_ID = "5"

TIMEOUT = 30

# Image references come back as "<profile>,<path>" and the profile names a
# CDN prefix listed in system/config's resourceProfiles. This is that table
# as captured, used when the live config has not been fetched yet or no
# longer carries a profile a card refers to.
IMAGE_PROFILES = {
    "menu": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/menus/mobile/",
    "horizontal": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/1920/1080/content/common/",
    "banner": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/banner/mobile/",
    "poster": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/320/180/content/common/",
    "common": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/320/180/content/common/",
    "epg": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/common/epgs/",
    "network": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/patners/",
    "vertical": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/480/720/content/common/",
    "logo": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/common/logos/",
    "crop": "https://d229kpbsb5jevy.cloudfront.net/frndlytvcomcast/688/387/content/common/",
    "attachments": "https://d644wkylmzakb.cloudfront.net/",
    "amazoncatalogimage": "https://d229kpbsb5jevy.cloudfront.net/frndlytv/960/540/content/common/",
}

# A card's image is served at whatever profile the card names, and no other.
# The table above has a "horizontal" profile at 1920x1080 whose prefix
# differs from "common"'s 320x180 only in the size segment, which looks like
# a free upgrade for artwork Kodi draws full-screen -- but the web player
# never once requested it in any capture, so there is no evidence these asset
# paths exist under it, and a rewritten url that 404s is worse than a soft
# one. Left alone deliberately.

CONFIG_FILE = "config.json"
CONFIG_MAX_AGE = 24 * 60 * 60


class ApiError(Exception):
    """The service answered, and the answer was not usable."""


class Api(object):
    def __init__(self, session=None):
        self.session = session or auth.Session()
        self._http = requests.Session()
        self._config = None

    # -- transport ---------------------------------------------------------

    def _call(self, method, url, retry=True, **kwargs):
        """One request, with a single re-sign-in when the session has lapsed.

        Revlet expires a session id server-side without telling the client in
        advance, and answers a lapsed one with HTTP 401 (or a 200 carrying
        ``status`` false and a message about the session). Both are recovered
        the same way: mint a new session from the stored credentials and send
        the request once more. Only once -- a second failure is a real one.
        """
        headers = dict(self.session.headers())
        headers.update(kwargs.pop("headers", None) or {})
        try:
            reply = self._http.request(method, url, headers=headers,
                                       timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise ApiError("Could not reach Friendly TV: %s" % exc)

        if reply.status_code in (401, 403) and retry:
            kodiutils.log("session was refused (HTTP %s), signing in again"
                          % reply.status_code)
            self.session.refresh()
            return self._call(method, url, retry=False, **kwargs)

        try:
            body = reply.json() or {}
        except ValueError:
            raise ApiError("Friendly TV sent something that was not JSON "
                           "(HTTP %s)" % reply.status_code)

        if body.get("status") is False:
            said = auth._message(body, reply)
            if retry and _looks_like_a_lapsed_session(said):
                kodiutils.log("session looks stale (%s), signing in again"
                              % said)
                self.session.refresh()
                return self._call(method, url, retry=False, **kwargs)
            raise ApiError(said)
        return body

    def get(self, path, params=None, base=None, retry=True):
        return self._call("GET", (base or API_BASE) + path, params=params,
                          retry=retry)

    # -- configuration -----------------------------------------------------

    def config(self):
        """system/config, cached for a day.

        It carries the menu the service wants shown and the CDN prefixes that
        turn an image reference into a url. Neither changes often, and both
        are needed to draw the very first screen, so a stale copy on disk
        beats a network round trip in front of the root menu.
        """
        if self._config is not None:
            return self._config
        cached = kodiutils.read_json(CONFIG_FILE, default=None)
        if cached and time.time() - cached.get("fetched_at", 0) < CONFIG_MAX_AGE:
            self._config = cached.get("body") or {}
            return self._config
        try:
            body = self.get("/service/api/v1/system/config", {"version": "4"})
            self._config = body.get("response") or {}
            kodiutils.write_json(CONFIG_FILE, {"fetched_at": time.time(),
                                               "body": self._config})
        except ApiError as exc:
            # A stale copy is better than no menu at all.
            kodiutils.log("could not refresh the config (%s)" % exc)
            self._config = (cached or {}).get("body") or {}
        return self._config

    def image_profiles(self):
        profiles = dict(IMAGE_PROFILES)
        for entry in (self.config().get("resourceProfiles") or []):
            code, prefix = entry.get("code"), entry.get("urlPrefix")
            if code and prefix:
                profiles[code] = prefix
        return profiles

    def image(self, reference):
        """A full url for a "<profile>,<path>" image reference.

        Values already absolute (the guide's artwork comes straight from
        Rovi) are returned untouched.
        """
        if not reference:
            return ""
        if reference.startswith("http://") or reference.startswith("https://"):
            return reference
        if "," not in reference:
            return ""
        profile, _, tail = reference.partition(",")
        prefix = self.image_profiles().get(profile.strip())
        if not prefix:
            return ""
        return prefix + tail.lstrip("/")

    def menus(self):
        """The service's own top-level menu, minus what this addon cannot do.

        ``settings`` is Kodi's own screen here and ``search`` runs on a
        different API surface this addon has no capture of, so neither is
        offered.
        """
        skip = {"settings", "search", "add-ons"}
        out = []
        for entry in (self.config().get("menus") or []):
            target = (entry.get("targetPath") or "").strip()
            if not target or target in skip:
                continue
            if not entry.get("isClickable", True):
                continue
            out.append({"title": (entry.get("displayText") or target).title(),
                        "path": target})
        return out

    # -- pages -------------------------------------------------------------

    def page(self, path, count=25):
        body = self.get("/service/api/v1/page/content",
                        {"path": path, "count": count})
        return body.get("response") or {}

    def section(self, path, code, count=24, offset=-1):
        """One deferred section's cards.

        A page can describe a section without filling it in -- ``sectionData``
        comes back empty with a ``dataRequestDelay`` -- and this is the call
        the web player makes to populate it.
        """
        body = self.get("/service/api/v1/section/data",
                        {"path": path, "count": count, "code": code,
                         "offset": offset})
        return body.get("response") or {}

    # -- live and guide ----------------------------------------------------

    def live_channels(self):
        """Every live channel, as cards that already know how to play.

        Read from the "Live Now" section rather than from tvguide/channels.
        Both list the lineup, but only this one carries the playable
        ``channel/live/<slug>`` path on each card; tvguide/channels sends
        ``channel//`` and would need a second lookup per channel to resolve.
        """
        response = self.page("section/live_now_home", count=200)
        cards = []
        for pane in (response.get("data") or []):
            section = pane.get("section") or {}
            cards.extend(((section.get("sectionData") or {}).get("data")) or [])
        return cards

    def guide_channels(self):
        """tvguide/channels: the lineup in the order the guide draws it."""
        body = self.get("/service/api/v1/tvguide/channels")
        return (body.get("response") or {}).get("data") or []

    def lineup(self):
        """The channels, joined so each one has both an id and a playable path.

        Two listings describe the same lineup and neither is enough alone.
        tvguide/channels has the ids the schedule is keyed by but an empty
        playable path (every row says ``channel//``); the Live Now cards have
        the playable slug and are keyed by network id, which is a *different*
        number from the channel id. They are joined on the network id, which
        the guide's own rows also carry.

        The guide's channel id wins as the key, because that is what the
        schedule comes back under. ``path`` is "" for a channel Live Now did
        not carry; callers decide whether to spend a request resolving it
        through the guide overlay.
        """
        from . import parse

        by_network = {}
        for raw in self.live_channels():
            attrs = (raw.get("target") or {}).get("pageAttributes") or {}
            network = str(attrs.get("networkid") or "")
            item = parse.card(raw, self)
            if network and item["path"] and network not in by_network:
                by_network[network] = item

        channels = []
        for raw in self.guide_channels():
            if raw.get("id") is None:
                continue
            display = raw.get("display") or {}
            attrs = (raw.get("target") or {}).get("pageAttributes") or {}
            network = str(attrs.get("networkid") or "")
            live = by_network.get(network)
            channels.append({
                "id": str(raw["id"]),
                "network_id": network,
                "name": display.get("title") or display.get("subtitle1") or "",
                "logo": self.image(display.get("imageUrl")),
                "path": live["path"] if live else "",
                "now": live["title"] if live else "",
            })
        return channels

    def guide(self, channel_ids, start_ms, end_ms, page=0):
        """A slice of schedule for a set of channels.

        The web player asks in pages of twelve channels over a day at a time.
        The endpoint lives on its own host and is unauthenticated -- it takes
        the same headers, and answers without them.
        """
        params = {
            "channel_ids": ",".join(str(c) for c in channel_ids),
            "start_time": int(start_ms),
            "end_time": int(end_ms),
            "page": page,
        }
        if page:
            params["skip_tabs"] = 1
        body = self.get("/service/api/v1/static/tvguide", params,
                        base=GUIDE_BASE)
        return (body.get("response") or {}).get("data") or []

    def watch_live_path(self, programme_path):
        """The playable ``channel/live/<slug>`` behind a guide programme.

        The guide overlay -- what the web player opens when a programme is
        selected -- names the channel that programme is on. This is the
        fallback for a channel the Live Now listing did not cover, and it
        costs one request per channel, which is why it is not the primary
        route.
        """
        if not programme_path:
            return ""
        body = self.get("/service/api/v1/template/data",
                        {"template_code": "tvguide_overlay",
                         "path": programme_path})
        data = (body.get("response") or {}).get("data") or {}
        return data.get("target_watchlive") or ""

    def form(self, code, path):
        """A form the service defines, with the choices it offers.

        Recording is done through a generic form mechanism rather than a
        dedicated endpoint: the service describes the options (record this
        episode, record the series) as radio buttons whose ``value`` is an
        opaque instruction string, and the client sends back the one that was
        chosen. Nothing about that string is constructed here -- it is echoed
        exactly as it arrived, which is why this works without knowing what
        its fields mean.
        """
        body = self.get("/service/api/v1/form", {"code": code, "path": path})
        return body.get("response") or {}

    def submit_form(self, code, path, value, field="record_program"):
        """Send one form choice back, and return what the service said.

        Every captured submission -- recording an episode, recording a series,
        stopping either, deleting a series -- posts under the single field
        name ``record_program``, whichever radio button it came from.
        """
        body = self._call("POST", API_BASE + "/service/api/v1/form/submit",
                          json={"code": code, "path": path,
                                "fields": {field: value}})
        message = (body.get("response") or {}).get("message")
        if isinstance(message, dict):
            return message.get("message") or ""
        return message or ""

    def next_programs(self, path, count=5):
        """What is on this channel next, for the info a live card shows."""
        body = self.get("/service/api/v1/get/live/channel/next/programs",
                        {"path": path, "count": count})
        return (body.get("response") or {}).get("data") or []

    # -- playback ----------------------------------------------------------

    def stream(self, path):
        """The manifest and licence url for one playable path.

        Answers with a ``streams`` list, a ``streamStatus`` saying whether the
        account may watch it, and a ``sessionInfo`` holding the poll key that
        identifies this stream to the concurrency counter.
        """
        body = self.get("/service/api/v2/page/stream",
                        {"path": path,
                         "stream_provider_device_id": STREAM_PROVIDER_DEVICE_ID})
        return body.get("response") or {}

    def active_sessions(self):
        body = self.get("/service/api/v1/stream/active/sessions")
        return body.get("response") or []

    def end_session(self, poll_key):
        """Give a stream slot back.

        Friendly TV caps concurrent streams and a slot stays taken until it
        is released or times out, so a Kodi box that never sends this locks
        the account out of its own subscription after a few plays. The web
        player posts it as multipart form data, which is what ``files``
        produces here without a body of its own.
        """
        if not poll_key:
            return
        self._call("POST", API_BASE + "/service/api/v1/stream/session/end",
                   files={"poll_key": (None, poll_key)})
        kodiutils.log("released the stream slot")


def _looks_like_a_lapsed_session(message):
    said = (message or "").lower()
    return any(word in said for word in
               ("session", "login", "log in", "sign in", "unauthor",
                "not authenticated", "token"))
