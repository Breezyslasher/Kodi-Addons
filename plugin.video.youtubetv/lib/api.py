"""The YouTube TV private InnerTube API.

Everything here was derived from browser captures of a signed-in session; see
docs/youtube-tv-protocol.md for the raw shapes. Nothing is documented or
stable, so the client identity constants below are the first thing to check
when calls start failing.

The one identity that matters is client name 41, ``WEB_UNPLUGGED``. The same
InnerTube endpoints under tv.youtube.com return a subscriber's live lineup only
for that client; anything else gets ordinary YouTube.
"""

import json
import random
import re
import string
import time

import requests

from . import auth, kodiutils

ORIGIN = "https://tv.youtube.com"
BASE = ORIGIN + "/youtubei/v1/"

CLIENT_NAME = "WEB_UNPLUGGED"
CLIENT_NAME_ID = "41"
# Bumped by Google whenever they ship a new web player. A stale value is the
# likeliest cause of a sudden LOGIN_REQUIRED against cookies that still work in
# a browser, so it is a setting as well as a default.
CLIENT_VERSION = "1.20260826.04.00"
# The player's signature timestamp, paired with the client version above.
# Both are only the fallback: refresh_bootstrap() reads the live pair off the
# page, because Google ships a player most days and a pinned value is stale
# almost immediately -- these two were already a release behind the browser
# within a day of being captured.
SIGNATURE_TIMESTAMP = 20690

UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:154.0) "
      "Gecko/20100101 Firefox/154.0")

# Sent alongside videoId by the web player on every play.
PLAYER_PARAMS = "2AEA"

EPG_BROWSE_ID = "FEunplugged_epg"
TIMEOUT = 30

# YouTube TV is one surface of InnerTube with several client identities. Only
# WEB_UNPLUGGED has been used in anger, and it is the one answering with SABR
# delivery and signature-ciphered formats; mobile and TV clients are commonly
# served plain URLs on ordinary YouTube, which is why they are worth asking.
#
# Ids and versions from the YouTube-Internal-Clients survey. Each carries its
# own User-Agent, which is not decoration: InnerTube identifies a mobile client
# by its app User-Agent as much as by the context, and sending a browser one
# with an Android context is rejected with HTTP 400. yt-dlp takes the request
# header from context.client.userAgent for exactly this reason, and so do we.
UNPLUGGED_CLIENTS = {
    "WEB_UNPLUGGED": {
        "id": "41",
        "version": CLIENT_VERSION,
        # Filled out to match what the web player actually sends. The response
        # was already identical without these, so they are for fidelity rather
        # than to fix anything -- a request that looks like the real client is
        # one less thing to wonder about later.
        "context": {
            "platform": "DESKTOP",
            "userAgent": UA,
            "clientFormFactor": "UNKNOWN_FORM_FACTOR",
            "clientScreen": "WATCH_FULL_SCREEN",
            "playerType": "UNIPLAYER",
            "applicationState": "ACTIVE",
            "browserName": "Firefox",
            "browserVersion": "154.0",
            "osName": "X11",
            "osVersion": "",
            "deviceMake": "",
            "deviceModel": "",
            "userInterfaceTheme": "USER_INTERFACE_THEME_DARK",
            "tvAppInfo": {
                "livingRoomAppMode": "LIVING_ROOM_APP_MODE_UNSPECIFIED"},
            "acceptHeader": ("text/html,application/xhtml+xml,application/xml;"
                             "q=0.9,*/*;q=0.8"),
        },
    },
    "ANDROID_UNPLUGGED": {
        "id": "29",
        "version": "6.36",
        "context": {
            "platform": "MOBILE",
            "userAgent": ("com.google.android.apps.youtube.unplugged/6.36"
                          " (Linux; U; Android 14) gzip"),
            "androidSdkVersion": 34,
            "osName": "Android",
            "osVersion": "14",
        },
    },
    "IOS_UNPLUGGED": {
        "id": "33",
        "version": "6.36",
        "context": {
            "platform": "MOBILE",
            "userAgent": ("com.google.ios.youtubeunplugged/6.36"
                          " (iPhone16,2; U; CPU iOS 17_5 like Mac OS X)"),
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "osName": "iPhone",
            "osVersion": "17.5.0.21F79",
        },
    },
    "TVHTML5_UNPLUGGED": {
        "id": "65",
        "version": "6.36",
        "context": {
            "platform": "TV",
            "userAgent": ("Mozilla/5.0 (ChromiumStylePlatform) Cobalt/25.master"
                          " (unlike Gecko) Starboard/16"),
        },
    },
    "TV_UNPLUGGED_ANDROID": {
        "id": "63",
        "version": "1.37",
        "context": {
            "platform": "TV",
            "userAgent": ("com.google.android.apps.youtube.unplugged.tv/1.37"
                          " (Linux; U; Android 14) gzip"),
            "androidSdkVersion": 34,
            "osName": "Android",
            "osVersion": "14",
        },
    },
    "TV_UNPLUGGED_CAST": {
        "id": "58",
        "version": "0.1",
        "context": {"platform": "TV", "userAgent": UA},
    },
}


def client_spec(name):
    return UNPLUGGED_CLIENTS.get(name) or UNPLUGGED_CLIENTS[CLIENT_NAME]


class ApiError(Exception):
    """The API answered, but not with what we asked for."""


class NotPlayable(ApiError):
    """A title exists but this account cannot play it right now."""


# The page bootstrap carries the values the running player was built with.
_PAGE_CLIENT_VERSION = re.compile(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([\w.]+)"')
_PAGE_STS = re.compile(r'"STS"\s*:\s*(\d+)')
# Session and rollout state the web player puts in every context block and we
# were sending in none of it. visitorData we had, but only as a header.
_PAGE_VISITOR = re.compile(r'"visitorData"\s*:\s*"([^"]+)"')
_PAGE_ROLLOUT = re.compile(r'"rolloutToken"\s*:\s*"([^"]+)"')
_PAGE_INSTALL = re.compile(r'"appInstallData"\s*:\s*"([^"]+)"')
# The page says outright whether Google considers the jar signed in. This is
# the one fact that separates "the cookies are dead" from "the cookies are
# fine and our InnerTube request is wrong", and every 401 explanation offered
# so far was a guess made without it.
_PAGE_LOGGED_IN = re.compile(r'"(?:LOGGED_IN|loggedIn)"\s*:\s*(true|false)')
BOOTSTRAP_FILE = "client_bootstrap.json"


def refresh_bootstrap(session, cookies):
    """Read clientVersion and signatureTimestamp off a YouTube TV page.

    Both were hardcoded from a capture, and comparing our request against the
    browser's a day later showed both already stale -- clientVersion by a
    release and signatureTimestamp by one. Google ships a player most days, so
    a pinned pair is wrong almost immediately and stays wrong until someone
    edits the source. Reading them from the page keeps the client honest about
    which player it is claiming to be.

    Cached for a day; a failure leaves the previous values in place rather than
    breaking a client that currently works.
    """
    cached = kodiutils.read_json(BOOTSTRAP_FILE, default=None) or {}
    if cached.get("fetched", 0) > time.time() - 86400 and cached.get("version"):
        return cached

    try:
        page = session.get(ORIGIN + "/", timeout=TIMEOUT, headers={
            "User-Agent": UA,
            "Cookie": auth.cookie_header(cookies),
        })
        if page.status_code != 200:
            return cached
        version = _PAGE_CLIENT_VERSION.search(page.text)
        sts = _PAGE_STS.search(page.text)
        visitor = _PAGE_VISITOR.search(page.text)
        rollout = _PAGE_ROLLOUT.search(page.text)
        install = _PAGE_INSTALL.search(page.text)
    except Exception as exc:
        kodiutils.log("bootstrap: could not refresh from the page: %s" % exc)
        return cached

    found = {"fetched": int(time.time())}
    if version:
        found["version"] = version.group(1)
    if sts:
        found["sts"] = int(sts.group(1))
    if visitor:
        found["visitor_data"] = visitor.group(1)
    if rollout:
        found["rollout_token"] = rollout.group(1)
    if install:
        found["app_install_data"] = install.group(1)
    if not found.get("version") and not found.get("sts"):
        return cached

    if found.get("version") and found["version"] != cached.get("version"):
        kodiutils.log("bootstrap: clientVersion %s -> %s"
                      % (cached.get("version") or CLIENT_VERSION,
                         found["version"]))
    if found.get("sts") and found["sts"] != cached.get("sts"):
        kodiutils.log("bootstrap: signatureTimestamp %s -> %s"
                      % (cached.get("sts") or SIGNATURE_TIMESTAMP, found["sts"]))
    merged = dict(cached)
    merged.update(found)
    kodiutils.write_json(BOOTSTRAP_FILE, merged)
    return merged


def session_probe(session, cookies):
    """Ask tv.youtube.com whether it still knows this jar.

    Fetched fresh, not from the bootstrap cache, and only when something has
    already failed -- the point is to answer "were the cookies the problem?"
    at the moment it is asked, with Google's own answer rather than ours.

    Returns True (signed in), False (signed out) or None (could not tell).
    """
    try:
        page = session.get(ORIGIN + "/", timeout=TIMEOUT, headers={
            "User-Agent": UA,
            "Cookie": auth.cookie_header(cookies),
        })
    except Exception as exc:
        kodiutils.log("session probe: could not load the page: %s" % exc)
        return None
    if page.status_code != 200:
        kodiutils.log("session probe: page returned HTTP %d" % page.status_code)
        return None
    # Signed out, tv.youtube.com serves the /welcome/ marketing page, which
    # carries no ytcfg at all -- so the absence of the flag is itself the
    # answer, and reporting it as "could not tell" hid a dead jar behind an
    # inconclusive message. Log where we actually landed either way.
    kodiutils.log("session probe: %s -> %d, %d bytes"
                  % (page.url, page.status_code, len(page.text)))
    if "/welcome" in page.url:
        return False
    match = _PAGE_LOGGED_IN.search(page.text)
    if not match:
        return False
    return match.group(1) == "true"


def _timezone_name():
    """The IANA zone name, e.g. America/New_York."""
    try:
        import datetime
        zone = datetime.datetime.now().astimezone().tzinfo
        name = getattr(zone, "key", None) or str(zone)
        if "/" in name:
            return name
    except Exception:
        pass
    try:
        with open("/etc/timezone", "r") as handle:
            name = handle.read().strip()
            if "/" in name:
                return name
    except Exception:
        pass
    try:
        import os
        link = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in link:
            return link.split("/zoneinfo/", 1)[1]
    except Exception:
        pass
    return "UTC"


def _bootstrap():
    return kodiutils.read_json(BOOTSTRAP_FILE, default={}) or {}


def _signature_timestamp():
    return _bootstrap().get("sts") or SIGNATURE_TIMESTAMP


def _baked_visitor_id():
    """The visitor id from a preloaded build, if it carries one.

    Not required -- Google issues one on the first call and it is remembered
    from the response header -- but starting with the id the session was
    captured under keeps it looking continuous rather than brand new.
    """
    try:
        from . import baked_cookies
    except ImportError:
        return ""
    return getattr(baked_cookies, "VISITOR_ID", "") or ""


def _client_version():
    """The version we claim to be.

    An explicit setting wins, so a user can pin one; otherwise whatever the
    page last told us, falling back to the value this was written against.
    """
    override = kodiutils.get_setting("client_version", "")
    if override and override != CLIENT_VERSION:
        return override
    return _bootstrap().get("version") or CLIENT_VERSION


def new_cpn():
    """A client playback nonce.

    Sixteen characters from the web player's alphabet. It ties the player call,
    the licence exchange and the heartbeats of one playback session together,
    so it must be minted once per play and reused, not regenerated per request.
    """
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(random.choice(alphabet) for _ in range(16))


def context(location=True, client_name=None):
    name = client_name or CLIENT_NAME
    spec = client_spec(name)
    version = _client_version() if name == CLIENT_NAME else spec["version"]

    client = {
        "hl": "en",
        "gl": "US",
        "clientName": name,
        "clientVersion": version,
        "unpluggedAppInfo": {"filterModeType": "UNPLUGGED_FILTER_MODE_TYPE_NONE"},
    }
    client.update(spec["context"])
    if location:
        # The lineup is market-dependent -- locals follow the account's home
        # area -- and the web player always sends this block. The IANA zone,
        # not strftime's abbreviation: the browser sends "America/New_York"
        # where strftime would give "EDT", and one of those is a timezone.
        client["unpluggedLocationInfo"] = {
            "clientPermissionState": 2,
            "timezone": _timezone_name(),
        }

    # Session and rollout state, read off the page. The web player sends all
    # three in every context; we sent visitorData only as a header and the
    # other two not at all.
    boot = _bootstrap()
    visitor = (kodiutils.get_setting("visitor_id", "")
               or boot.get("visitor_data") or _baked_visitor_id())
    if visitor:
        client["visitorData"] = visitor
    if boot.get("rollout_token"):
        client["rolloutToken"] = boot["rollout_token"]
    if boot.get("app_install_data"):
        client["configInfo"] = {"appInstallData": boot["app_install_data"]}
    return {"client": client}


class Api(object):
    def __init__(self, cookies=None):
        self.cookies = cookies or auth.load()
        self.session = requests.Session()
        self._visitor_id = kodiutils.get_setting("visitor_id", "") or _baked_visitor_id()
        try:
            refresh_bootstrap(self.session, self.cookies)
        except Exception as exc:
            kodiutils.log("bootstrap refresh skipped: %s" % exc)

    def _headers(self, client_name=None):
        name = client_name or CLIENT_NAME
        spec = client_spec(name)
        client_id = spec["id"]
        version = _client_version() if name == CLIENT_NAME else spec["version"]
        headers = {
            "Content-Type": "application/json",
            # Must match the client we claim to be, or InnerTube answers 400.
            "User-Agent": spec["context"].get("userAgent", UA),
            "Accept": "*/*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "X-Origin": ORIGIN,
            "X-YouTube-Client-Name": client_id,
            "X-YouTube-Client-Version": version,
            "X-Goog-AuthUser": "0",
            "Authorization": auth.authorization(self.cookies),
            "Cookie": auth.cookie_header(self.cookies),
        }
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        return headers

    def call(self, endpoint, body, params=None, client_name=None):
        url = BASE + endpoint
        payload = dict(body)
        payload.setdefault("context", context(client_name=client_name))
        try:
            response = self.session.post(url, params=params or {"alt": "json"},
                                         data=json.dumps(payload),
                                         headers=self._headers(client_name),
                                         timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ApiError("could not reach %s: %s" % (endpoint, exc))

        if response.status_code != 200:
            # InnerTube explains itself in the body -- which credential it did
            # not like, which client it will not answer for. Reading it costs
            # one parse and replaces a guess with Google's own words.
            detail = ""
            try:
                error = (response.json().get("error") or {})
                detail = error.get("message") or ""
                status = error.get("status") or ""
                if status and status not in detail:
                    detail = ("%s: %s" % (status, detail)).strip(": ")
            except ValueError:
                detail = (response.text or "")[:400]
            kodiutils.log("%s -> HTTP %d as %s v%s%s"
                          % (endpoint, response.status_code,
                             client_name or CLIENT_NAME, _client_version(),
                             ": %s" % detail if detail else ""))

        if response.status_code in (401, 403):
            # Four different explanations have been offered for a 401 here --
            # rotation, staleness, integrity cookies, a bad extraction -- all
            # of them guesses made without asking whether the jar was actually
            # dead. So ask. The page and this call carry the same cookies, so
            # a signed-in page and a 401 cannot both be about the session.
            live = session_probe(self.session, self.cookies)
            names = ",".join(sorted(self.cookies))
            kodiutils.log("session probe: signed in = %s; jar carries %s"
                          % (live, names))
            if live is False:
                raise auth.AuthError(
                    "Google no longer knows this session -- tv.youtube.com "
                    "serves it a signed-out page. Import a fresh cookie "
                    "export.%s" % (" (%s)" % detail if detail else ""))
            if live is True:
                raise auth.AuthError(
                    "The session is still signed in -- tv.youtube.com serves "
                    "these same cookies a signed-in page -- but %s returned "
                    "HTTP %d, so it is the request being refused, not the "
                    "cookies.%s"
                    % (endpoint, response.status_code,
                       " (%s)" % detail if detail else ""))
            raise auth.AuthError(
                "%s returned HTTP %d and the page probe could not say whether "
                "the session is alive.%s"
                % (endpoint, response.status_code,
                   " (%s)" % detail if detail else ""))
        if response.status_code != 200:
            raise ApiError("%s returned HTTP %d%s"
                           % (endpoint, response.status_code,
                              ": %s" % detail if detail else ""))

        # Remember the visitor id Google hands back so later calls look like a
        # continuing session rather than a fresh client each time.
        visitor = response.headers.get("X-Goog-Visitor-Id")
        if visitor and visitor != self._visitor_id:
            self._visitor_id = visitor
            kodiutils.set_setting("visitor_id", visitor)

        try:
            return response.json()
        except ValueError:
            raise ApiError("%s did not return JSON" % endpoint)

    # -- guide ------------------------------------------------------------

    def epg(self, start_ms=None, hours=6, max_airings=11):
        """The channel lineup and schedule.

        One call covers a window; the response carries a continuation token for
        the next. Google caps the reachable range at ``maxDurationMs``, a week.
        """
        start_ms = start_ms or int(time.time() * 1000)
        return self.call("browse", {
            "browseId": EPG_BROWSE_ID,
            "unpluggedBrowseOptions": {"epgOptions": {
                "maxAiringsPerStation": max_airings,
                "initialEpgFetchStartTimeMs": str(int(start_ms)),
                "initialEpgFetchDurationMs": int(hours * 3600 * 1000),
                "paginationDurationMs": int(hours * 3600 * 1000),
                "maxDurationMs": "604800000",
            }},
        })

    def continuation(self, token):
        return self.call("browse", {"continuation": token})

    def browse(self, browse_id, params=None):
        body = {"browseId": browse_id}
        if params:
            body["params"] = params
        return self.call("browse", body)

    # -- search -----------------------------------------------------------

    def search(self, query):
        return self.call("search", {"query": query})

    def suggest(self, query):
        return self.call("suggest", {"input": query})

    # -- playback ---------------------------------------------------------

    def player(self, video_id, cpn, client_name=None):
        response = self.call("player", {
            "videoId": video_id,
            "params": PLAYER_PARAMS,
            "playbackContext": {
                "contentPlaybackContext": {
                    "html5Preference": "HTML5_PREF_WANTS",
                    "signatureTimestamp": _signature_timestamp(),
                    "referer": "%s/watch/%s" % (ORIGIN, video_id),
                    "autonavState": "STATE_OFF",
                    "autoCaptionsDefaultOn": False,
                    "mdxContext": {},
                },
                "devicePlaybackCapabilities": {
                    "supportsVp9Encoding": True,
                    "supportXhr": True,
                },
            },
            "cpn": cpn,
            "racyCheckOk": True,
            "captionParams": {},
        }, params={"prettyPrint": "false"}, client_name=client_name)

        status = response.get("playabilityStatus", {}) or {}
        if status.get("status") != "OK":
            reason = status.get("reason") or status.get("status") or "unknown"
            raise NotPlayable(reason)
        if not response.get("streamingData"):
            raise NotPlayable("no streamingData in the player response")
        return response

    def heartbeat(self, video_id, cpn, sequence, token, server_data):
        """Keep a live stream alive.

        The response asks to be called again after ``pollDelayMs`` (30 s in
        every capture) and carries the ``heartbeatServerData`` to echo next
        time. HEARTBEAT_CHECK_TYPE_YPC is the entitlement check, so a client
        that stops calling should expect the stream to be cut.
        """
        body = {
            "videoId": video_id,
            "cpn": cpn,
            "sequenceNumber": sequence,
            "heartbeatRequestParams": {"heartbeatChecks": [
                "HEARTBEAT_CHECK_TYPE_LIVE_STREAM_STATUS",
                "HEARTBEAT_CHECK_TYPE_YPC",
            ]},
            "playbackState": {"playbackPosition": {
                "utcTimeMillis": str(int(time.time() * 1000))}},
        }
        if token:
            body["heartbeatToken"] = token
        if server_data:
            body["heartbeatServerData"] = server_data
        return self.call("player/heartbeat", body)
