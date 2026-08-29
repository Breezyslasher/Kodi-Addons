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
CLIENT_VERSION = "1.20260825.04.00"
# The player's signature timestamp, paired with the client version above.
SIGNATURE_TIMESTAMP = 20689

UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:154.0) "
      "Gecko/20100101 Firefox/154.0")

EPG_BROWSE_ID = "FEunplugged_epg"
TIMEOUT = 30


class ApiError(Exception):
    """The API answered, but not with what we asked for."""


class NotPlayable(ApiError):
    """A title exists but this account cannot play it right now."""


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
    return kodiutils.get_setting("client_version", CLIENT_VERSION) or CLIENT_VERSION


def new_cpn():
    """A client playback nonce.

    Sixteen characters from the web player's alphabet. It ties the player call,
    the licence exchange and the heartbeats of one playback session together,
    so it must be minted once per play and reused, not regenerated per request.
    """
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(random.choice(alphabet) for _ in range(16))


def context(location=True):
    client = {
        "hl": "en",
        "gl": "US",
        "clientName": CLIENT_NAME,
        "clientVersion": _client_version(),
        "platform": "DESKTOP",
        "userAgent": UA,
        "unpluggedAppInfo": {"filterModeType": "UNPLUGGED_FILTER_MODE_TYPE_NONE"},
    }
    if location:
        # The lineup is market-dependent -- locals follow the account's home
        # area -- and the web player always sends this block. Omitting it has
        # not been tested; sending it costs nothing.
        client["unpluggedLocationInfo"] = {
            "clientPermissionState": 2,
            "timezone": time.strftime("%Z") or "UTC",
        }
    return {"client": client}


class Api(object):
    def __init__(self, cookies=None):
        self.cookies = cookies or auth.load()
        self.session = requests.Session()
        self._visitor_id = kodiutils.get_setting("visitor_id", "") or _baked_visitor_id()

    def _headers(self):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Accept": "*/*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "X-Origin": ORIGIN,
            "X-YouTube-Client-Name": CLIENT_NAME_ID,
            "X-YouTube-Client-Version": _client_version(),
            "X-Goog-AuthUser": "0",
            "Authorization": auth.authorization(self.cookies),
            "Cookie": auth.cookie_header(self.cookies),
        }
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        return headers

    def call(self, endpoint, body, params=None):
        url = BASE + endpoint
        payload = dict(body)
        payload.setdefault("context", context())
        try:
            response = self.session.post(url, params=params or {"alt": "json"},
                                         data=json.dumps(payload),
                                         headers=self._headers(), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ApiError("could not reach %s: %s" % (endpoint, exc))

        if response.status_code in (401, 403):
            raise auth.AuthError("YouTube rejected the session (HTTP %d) -- the "
                                 "cookies have probably expired"
                                 % response.status_code)
        if response.status_code != 200:
            raise ApiError("%s returned HTTP %d" % (endpoint, response.status_code))

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

    def player(self, video_id, cpn):
        response = self.call("player", {
            "videoId": video_id,
            "playbackContext": {
                "contentPlaybackContext": {
                    "html5Preference": "HTML5_PREF_WANTS",
                    "signatureTimestamp": SIGNATURE_TIMESTAMP,
                    "referer": "%s/watch/%s" % (ORIGIN, video_id),
                },
                "devicePlaybackCapabilities": {
                    "supportsVp9Encoding": True,
                    "supportXhr": True,
                },
            },
            "cpn": cpn,
            "racyCheckOk": True,
            "captionParams": {},
        }, params={"prettyPrint": "false"})

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
