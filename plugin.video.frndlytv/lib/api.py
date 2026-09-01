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

import threading
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
    """The service answered, and the answer was not usable.

    ``code`` is the service's own error code where it sent one, so a caller
    can tell apart a refusal it should report from one it should treat as an
    ordinary empty result.
    """

    def __init__(self, message, code=0):
        Exception.__init__(self, message)
        self.code = code


# What the search API answers with when a query simply matches nothing. It is
# a refusal in the protocol's terms -- status false, HTTP 200 -- but it is an
# ordinary empty result in the user's terms.
NO_MATCHES = 404


class Api(object):
    def __init__(self, session=None):
        self.session = session or auth.Session()
        self._local = threading.local()
        # Details for a listing are fetched on a pool, so a lapsed session
        # would have every one of those threads sign in at once -- a dozen
        # concurrent sign-ins for one expiry, each invalidating the last. One
        # thread refreshes; the rest wait and then find a fresh session.
        self._signing_in = threading.Lock()
        self._config = None

    @property
    def _http(self):
        """One requests.Session per thread.

        Details for a listing are fetched on a small pool of threads, and a
        requests.Session is not documented as thread-safe -- sharing one is
        the kind of thing that works until a connection is reused mid-flight.
        """
        http = getattr(self._local, "http", None)
        if http is None:
            http = self._local.http = requests.Session()
        return http

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
            self._refresh_once(headers.get("session-id"))
            return self._call(method, url, retry=False, **kwargs)

        try:
            body = reply.json() or {}
        except ValueError:
            raise ApiError("Friendly TV sent something that was not JSON "
                           "(HTTP %s)" % reply.status_code)

        if body.get("status") is False:
            said = auth._message(body, reply)
            code = auth._code(body)
            if retry and not code and _looks_like_a_lapsed_session(said):
                kodiutils.log("session looks stale (%s), signing in again"
                              % said)
                self._refresh_once(headers.get("session-id"))
                return self._call(method, url, retry=False, **kwargs)
            raise ApiError(said, code)
        return body

    def _refresh_once(self, stale_id):
        """Sign in again, once, however many threads noticed at the same time.

        The caller passes the session id its own request went out with. If it
        has already changed by the time the lock is taken, another thread has
        rebuilt the session and this one has nothing to do.
        """
        with self._signing_in:
            if stale_id and self.session.session_id != stale_id:
                return
            self.session.refresh()

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

    def tivo_content(self, path="homeScreen", carousels=10, assets=30,
                     cursor=""):
        """A page of the TiVo-backed carousel screen.

        Home is the one screen that does *not* live behind page/content.
        ``page/content?path=home`` carries the banners and the Live Now row
        and nothing else -- every other row on Home ("Continue Watching",
        "Recommended for You", "Just Added Movies" ...) comes from here.

        The rows are paged: the first request asks for ten and every later one
        for four, each carrying the ``pageCursor`` the previous response
        returned. The panes themselves are the ordinary section/card shape, so
        parse.sections reads them unchanged.
        """
        params = {"path": path, "carouselCount": carousels,
                  "assetsCount": assets}
        if cursor:
            params["pageCursor"] = cursor
        body = self.get("/service/api/v1/tivo/content", params)
        return body.get("response") or {}

    def home_rows(self, max_pages=6):
        """Every row on Home, in the order Friendly TV puts them in.

        The Live Now row comes from page/content and the rest from
        tivo/content, which is an implementation detail of the service rather
        than anything a viewer should see, so they are merged here and the
        callers just get rows.

        ``max_pages`` bounds the cursor walk. The captured session settled
        after five requests; the cap only decides how far down Home this
        reaches, never whether it works.
        """
        from . import parse

        rows = []
        try:
            live = self.page("home", count=200)
            for section in parse.sections(live, self):
                if section["cards"]:
                    rows.append(section)
        except ApiError as exc:
            # Home is still worth drawing without its live row.
            kodiutils.log("home: the live row did not load: %s" % exc)

        cursor, seen = "", set()
        for page in range(max_pages):
            try:
                body = self.tivo_content(carousels=10 if not cursor else 4,
                                         cursor=cursor)
            except ApiError as exc:
                kodiutils.log("home: carousel page %d failed: %s"
                              % (page + 1, exc))
                break
            for section in parse.sections(body, self):
                if section["cards"] and section["code"] not in seen:
                    seen.add(section["code"])
                    rows.append(section)
            cursor = body.get("pageCursor") or ""
            if not cursor:
                break
        kodiutils.log("home: %d row(s)" % len(rows))
        return rows

    def search(self, query, limit=16, offset=0, bucket="All"):
        """The service's own catalogue search.

        A different API surface from everything else -- ``/search/api/tivo/v1``
        rather than ``/service/api/v1`` -- but the same host and the same
        session headers, and the results are ordinary cards.
        """
        try:
            body = self.get("/search/api/tivo/v1/get/search/query",
                            {"query": query, "limit": limit, "offset": offset,
                             "bucket": bucket})
        except ApiError as exc:
            # "We didn't find any matches" is how this API says zero results:
            # status false with a 404 in an error object, which is a refusal
            # in the protocol's terms and an empty list in the viewer's. It
            # happens routinely -- the Channels filter matches nothing for
            # most queries -- and must not surface as a failure.
            if exc.code == NO_MATCHES:
                kodiutils.log("search %r [%s]: no matches" % (query, bucket))
                return {"cards": [], "has_more": False, "total": 0}
            raise
        response = body.get("response") or {}
        results = response.get("searchResults") or {}
        # The landing screen returns a list of buckets here; a query returns
        # one bucket as an object. Only the query form is used.
        if isinstance(results, list):
            cards = []
            for bucket_ in results:
                cards.extend((bucket_ or {}).get("data") or [])
        else:
            cards = results.get("data") or []
        return {
            "cards": cards,
            "has_more": bool(response.get("hasMore")),
            "total": response.get("totalCount") or len(cards),
        }

    def search_all(self, query, bucket="All", limit=16, max_pages=20):
        """Every result for a query, not just the first page.

        The endpoint pages sixteen at a time and that is the only page size
        any capture uses, so the pages are walked rather than asked for in one
        larger request. It costs a handful of requests -- the observed totals
        are 37 and 77, so three and five -- and it means a search lands as one
        scrollable list instead of a chain of "Next page" folders.

        ``max_pages`` is a stop, not a page size: it bounds a query that
        matches thousands. Reaching it is reported so the caller can say the
        list was cut rather than quietly showing part of it.
        """
        cards, offset, pages, total = [], 0, 0, 0
        while pages < max_pages:
            found = self.search(query, limit=limit, offset=offset,
                                bucket=bucket)
            cards.extend(found["cards"])
            total = found["total"] or len(cards)
            pages += 1
            # An empty page with has_more still set would loop forever;
            # nothing observed does that, and nothing needs to.
            if not found["has_more"] or not found["cards"]:
                return {"cards": cards, "total": total, "complete": True,
                        "pages": pages}
            offset += limit
        return {"cards": cards, "total": total, "complete": False,
                "pages": pages}

    def details(self, paths, workers=8, limit=40):
        """Several titles' pages at once, as {path: response}.

        A listing card carries no synopsis, cast or director -- across every
        captured response those fields are empty on all 8191 cards but 160.
        They exist only on the title's own page, so Kodi's own Information
        dialog has nothing to show unless the pages are fetched.

        That is one request per row, which is why it runs on a small pool and
        is capped: a row of thirty costs thirty requests however it is done,
        and the pool is what keeps that a few round trips rather than thirty.
        Failures are dropped silently -- a missing synopsis is not worth
        failing a listing over.
        """
        return self._fan_out(self.page, paths, workers, limit, "details")

    def overlays(self, paths, workers=8, limit=40):
        """Guide overlays for several airings at once, as {path: data}.

        The schedule endpoint sends a title, an id and two times per airing
        and nothing else -- no synopsis, no cast, no artwork. All of that is
        in the overlay the web player opens when an airing is selected, one
        request per airing.
        """
        def one(path):
            body = self.get("/service/api/v1/template/data",
                            {"template_code": "tvguide_overlay", "path": path})
            return (body.get("response") or {}).get("data") or {}

        return self._fan_out(one, paths, workers, limit, "guide overlays")

    def _fan_out(self, fetch, paths, workers, limit, what):
        """Run ``fetch`` over ``paths`` on a small pool, dropping failures."""
        wanted = [p for p in dict.fromkeys(paths) if p][:limit]
        if not wanted:
            return {}
        out = {}

        def one(path):
            try:
                return path, fetch(path)
            except Exception:
                return path, None

        started = time.time()
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for path, response in pool.map(one, wanted):
                    if response:
                        out[path] = response
        except Exception as exc:
            kodiutils.log("could not fetch %s concurrently (%s)" % (what, exc))
            for path in wanted:
                path, response = one(path)
                if response:
                    out[path] = response
        kodiutils.log("%s: %d of %d in %.1fs"
                      % (what, len(out), len(wanted), time.time() - started))
        return out

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
