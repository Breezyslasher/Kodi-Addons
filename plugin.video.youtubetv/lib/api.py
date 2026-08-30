"""The YouTube TV private InnerTube API.

Everything here was derived from browser captures of a signed-in session; see
docs/youtube-tv-protocol.md for the raw shapes. Nothing is documented or
stable, so the client identity constants below are the first thing to check
when calls start failing.

Two identities have been used in anger. ``WEB_UNPLUGGED`` (client name 41) is
the web player's, authenticated with a cookie jar, and it is the one the
captures were taken from; the addon no longer signs in that way. What it uses
is ``TVHTML5_UNPLUGGED`` (65), which is what a device-code bearer token is
accepted as -- asked as WEB_UNPLUGGED the same token answers HTTP 400
INVALID_ARGUMENT. WEB_UNPLUGGED stays in the table below because the protocol
notes are written against it and because it names what a capture was.
"""

import base64
import json
import random
import re
import string
import threading
import time

import requests

from urllib.parse import quote, urlparse, urlunparse

from . import auth, kodiutils

ORIGIN = "https://tv.youtube.com"
BASE = ORIGIN + "/youtubei/v1/"

CLIENT_NAME = "WEB_UNPLUGGED"
# The identity a device-code bearer token is accepted as. Measured, not
# assumed: of the six the addon knows, this is the one that answered.
OAUTH_CLIENT_NAME = "TVHTML5_UNPLUGGED"
CLIENT_NAME_ID = "41"
# Bumped by Google whenever they ship a new web player. Only a fallback:
# refresh_bootstrap reads the live value off the page, and it is a setting
# too, because a pinned one is stale within a day.
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

# The order the guide comes back in. YouTube TV's own live tab offers five,
# and the choice rides along as a continuation *beside* the browseId rather
# than replacing it. Whatever is chosen here is what the addon lists and what
# IPTV Manager numbers its channels by.
#
# The numbers are the varint the token carries, and they are also the values
# the setting stores, so a setting reads straight through with no mapping
# table to get out of step. 0 means "send no token" -- whatever the account
# last chose on the web.
EPG_ORDERS = {
    "default": 1,        # the lineup order: locals first
    "custom": 2,         # the order set on tv.youtube.com
    "watched": 3,        # most watched
    "az": 4,
    "za": 5,
}
EPG_ORDER_VALUES = frozenset(EPG_ORDERS.values())

# The Library is not asked for by browseId. The web client sends it as a
# continuation token, and a token is what the capture shows going out, so a
# token is what is sent here. It is a two-field protobuf -- field 80226972
# wrapping field 2, the browse id -- which is why "FEunplugged_library" is
# legible inside the base64.
LIBRARY_CONTINUATION = "4qmFsgIVEhNGRXVucGx1Z2dlZF9saWJyYXJ5"

# Home the same way. The trailing "%3D" is percent-encoded base64 padding and
# is sent exactly as the capture shows it: the token is opaque to us, and
# decoding it to re-encode it is a way to break it.
HOME_CONTINUATION = "4qmFsgIUEhBGRXVucGx1Z2dlZF9ob21lGgA%3D"
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
        # Not 6.36. That value was one version copied across three clients in
        # this table, and it cost more than tidiness: asked at 6.36 the TV
        # client is served a serverAbrStreamingUrl and NO ustreamer config,
        # so SABR answers sabr.malformed_config and the whole token path
        # reads as closed. Asked at this version, the same token on the same
        # account is handed a 2332-character config and useServerDrivenAbr.
        # Read off the TV shell page, and confirmed by sweeping the two
        # against each other on one run.
        "version": "7.20260826.15.00",
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
# Where the player JavaScript lives. Needed to compute n -- see nsig.py.
_PAGE_JS_URL = re.compile(r'"jsUrl"\s*:\s*"([^"]+base\.js)"')
BOOTSTRAP_FILE = "client_bootstrap.json"
# Bumped whenever a new field is read off the page. Without this the day-long
# cache serves a copy written before the field existed, and the caller sees a
# page that "does not name a player js" when the page names one perfectly well
# -- a stale cache wearing the costume of a parsing failure.
BOOTSTRAP_SCHEMA = 2

# Where to look for the running player's identity, in order.
#
# This mattered less when the addon held a cookie jar: tv.youtube.com/ served
# the signed-in app page, whose ytcfg names the player js, the client version
# and the signature timestamp. Signed out it serves the /welcome/ marketing
# page instead, which carries no ytcfg at all -- so removing the jar took the
# player js with it, and without a player there is no n, and without n every
# media url is a 403. That is what the 2026-08-29 16:30 run hit.
#
# None of these needs a credential. Which of them actually names a player is
# logged per candidate rather than assumed, because the answer is a property
# of Google's pages today and not something to reason out from here.
BOOTSTRAP_PAGES = (
    ORIGIN + "/",
    ORIGIN + "/tv",
    "https://www.youtube.com/tv",
    "https://www.youtube.com/",
)


def refresh_bootstrap(session):
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
    if (cached.get("schema") == BOOTSTRAP_SCHEMA
            and cached.get("fetched", 0) > time.time() - 86400
            and (cached.get("version") or cached.get("js_url"))):
        return cached

    page = None
    for candidate in BOOTSTRAP_PAGES:
        try:
            reply = session.get(candidate, timeout=TIMEOUT,
                                headers={"User-Agent": UA})
        except Exception as exc:
            kodiutils.log("bootstrap: %s -> %s" % (candidate, exc))
            continue
        names_js = bool(_PAGE_JS_URL.search(reply.text or ""))
        kodiutils.log("bootstrap: %s -> HTTP %d, %d bytes, names a player js: "
                      "%s" % (candidate, reply.status_code, len(reply.text or ""),
                              "yes" if names_js else "no"))
        if reply.status_code == 200 and names_js:
            page = reply
            break
        if reply.status_code == 200 and page is None:
            page = reply
    if page is None:
        return cached

    # Only a tv.youtube.com page describes the Unplugged client. www.youtube.com
    # names a player js -- the same /s/player/<id>/ tree, fetched from our own
    # origin below -- but its clientVersion, visitorData and rollout token
    # belong to ordinary YouTube, and adopting those would describe a client
    # this addon is not.
    unplugged = urlparse(page.url).netloc == urlparse(ORIGIN).netloc
    js_url = _PAGE_JS_URL.search(page.text)
    sts = _PAGE_STS.search(page.text) if unplugged else None
    version = _PAGE_CLIENT_VERSION.search(page.text) if unplugged else None
    visitor = _PAGE_VISITOR.search(page.text) if unplugged else None
    rollout = _PAGE_ROLLOUT.search(page.text) if unplugged else None
    install = _PAGE_INSTALL.search(page.text) if unplugged else None

    found = {"fetched": int(time.time()), "schema": BOOTSTRAP_SCHEMA}
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
    if js_url:
        found["js_url"] = js_url.group(1).replace("\\/", "/")
    if not (found.get("version") or found.get("sts") or found.get("js_url")):
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


_PLAYER_JS = {}


def _kept_player():
    """The newest player nsig saved in the profile, as (id, source).

    A fallback for when no page will name one. The file is what a previous
    run actually fetched and solved n with, so it is a player this addon has
    already been served media against -- not a guess. It goes stale when
    Google ships a new one, which is what the page lookup is for; this only
    keeps a box that has played before from stopping dead when the lookup
    fails.
    """
    try:
        import os
        where = kodiutils.profile_dir()
        kept = [name for name in os.listdir(where)
                if name.startswith("player-") and name.endswith(".js")]
        if not kept:
            return None
        newest = max(kept, key=lambda name: os.path.getmtime(
            os.path.join(where, name)))
        with open(os.path.join(where, newest), "r", encoding="utf-8") as handle:
            return newest[len("player-"):-len(".js")], handle.read()
    except Exception as exc:
        kodiutils.log("player js: nothing usable kept on disk: %s" % exc)
        return None


def player_js(session):
    """The player JavaScript, fetched once and kept for the session.

    A megabyte of it, so it is held in memory rather than re-fetched per
    segment. Returns (player_id, source); the id is the release hash out of the
    url, which is what the solved values are cached against -- a new player
    means new answers.
    """
    boot = refresh_bootstrap(session)
    path = boot.get("js_url")
    if not path:
        kept = _kept_player()
        if kept:
            player_id, source = kept
            kodiutils.log("player js: no page named one, so reusing the "
                          "player kept on disk (%s, %d bytes)"
                          % (player_id, len(source)))
            _PLAYER_JS[player_id] = source
            return player_id, source
        raise ApiError("no page names a player js and none is kept on disk")
    if path.startswith("//"):
        url = "https:" + path
    elif path.startswith("/"):
        url = ORIGIN + path
    else:
        url = path
    player_id = ""
    match = re.search(r"/player/([0-9a-fA-F]+)/", url)
    if match:
        player_id = match.group(1)
    if player_id in _PLAYER_JS:
        return player_id, _PLAYER_JS[player_id]

    # The page points at player_es6, and that build defeated every extraction
    # pattern: its only .get("n") is a url-rewriting helper, and the markers the
    # patterns look for appear nowhere in it. Google publishes the same player
    # id as player_ias as well -- the ES5 build, and the one every published
    # pattern was written against -- so ask for that first and keep the page's
    # own url as the fallback.
    #
    # The variant survey found the one that matters. Of the ten builds Google
    # publishes for a player id, only the two "tce" ones carry .set("n", --
    # the landmark every other build, including the two the page points at,
    # lacks entirely. So ask for those first: same player, same release,
    # compiled without the opcode dispatcher hiding the transform.
    candidates = []
    for variant in ("player_ias_tce.vflset", "player_es6_tce.vflset",
                    "player_ias.vflset"):
        for original in ("player_es6.vflset", "player_ias.vflset"):
            if original in url and variant != original:
                candidate = url.replace(original, variant)
                if candidate not in candidates:
                    candidates.append(candidate)
                break
    candidates.append(url)

    last = None
    for candidate in candidates:
        try:
            response = session.get(candidate, timeout=TIMEOUT,
                                   headers={"User-Agent": UA})
        except Exception as exc:
            kodiutils.log("player js: %s -> %s" % (candidate, exc))
            continue
        kodiutils.log("player js: %s -> HTTP %d, %d bytes"
                      % (candidate, response.status_code, len(response.content)))
        if response.status_code == 200:
            _PLAYER_JS[player_id] = response.text
            return player_id, response.text
        last = response.status_code
    raise ApiError("no player js could be fetched (last HTTP %s)" % last)


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


# player id -> the signatureTimestamp that player declares.
_PLAYER_STS = {}
_STS_IN_PLAYER = re.compile(r"signatureTimestamp\s*:\s*(\d+)")


def _signature_timestamp():
    """The timestamp of the player we will actually unscramble with.

    Not the page's. The page named 20690 while the player the addon fetches
    and extracts n from, e937390a, declares signatureTimestamp:20684 in its
    own source -- so we were promising one build and unscrambling with
    another, and every url minted for us expected a transform we never
    applied. They were refused for everyone, the browser included, which is
    exactly what that mismatch looks like from outside.

    So read it from the player itself, which cannot disagree with the
    transform by construction, and keep the page's value as the fallback
    for when the player cannot be fetched.
    """
    for sts in _PLAYER_STS.values():
        return sts
    try:
        import requests
        session = requests.Session()
        player_id, js = player_js(session)
        found = _STS_IN_PLAYER.search(js)
        if found:
            _PLAYER_STS[player_id] = int(found.group(1))
            kodiutils.log("player %s declares signatureTimestamp %s"
                          % (player_id, found.group(1)))
            return _PLAYER_STS[player_id]
    except Exception as exc:
        kodiutils.log("could not read signatureTimestamp from the player: %s"
                      % exc)
    return _bootstrap().get("sts") or SIGNATURE_TIMESTAMP


def _baked_visitor_id():
    """The visitor id from a preloaded build, if it carries one.

    Not required -- Google issues one on the first call and it is remembered
    from the response header -- but starting with the id the session was
    captured under keeps it looking continuous rather than brand new.
    """
    # A personal build may carry the browser session's own visitorData, which
    # matters because a proof-of-origin token is bound to the visitorData
    # that minted it: paste a token from one session while presenting
    # another's identity and the media is refused. They belong together, so
    # they are baked together.
    try:
        from . import baked_session
    except ImportError:
        return ""
    return getattr(baked_session, "VISITOR_ID", "") or ""


def visitor_data():
    """The visitorData this session presents, for anything bound to it.

    A proof-of-origin token is bound to it, so the two have to be asked for
    together or the token is minted against an identity we do not use.
    """
    return (kodiutils.get_setting("visitor_id", "")
            or _bootstrap().get("visitor_data")
            or _baked_visitor_id())


def _client_version():
    """The version we claim to be.

    An explicit setting wins, so a user can pin one; otherwise whatever the
    page last told us, falling back to the value this was written against.

    The fallback is 1.20260826.04.00 because that is what the browser sends
    today, read out of its own SABR request: streamerContext subfield 1
    carries locale en_US, client 41 and version "1.20260826.04.00". We were
    a day behind it.
    """
    override = kodiutils.get_setting("client_version", "")
    if override and override != CLIENT_VERSION:
        return override
    return _bootstrap().get("version") or CLIENT_VERSION


def effective_version(name=None):
    """The version we would actually send for a client.

    Three call sites used to work this out for themselves, and one of them
    once printed the table's version while the headers carried the page's --
    a log line that lied about the request beside it. One reader keeps them
    honest, and gives probes something public to ask.
    """
    name = name or CLIENT_NAME
    if name == CLIENT_NAME:
        return _client_version()
    return client_spec(name)["version"]


def _log_sts():
    """The signature timestamp we are about to declare, said out loud."""
    sts = _signature_timestamp()
    kodiutils.log("player request: signatureTimestamp %s, clientVersion %s"
                  % (sts, _client_version()))
    return sts


def player_body(video_id, cpn):
    """The player request body, as the working play path sends it.

    Hoisted out of Api.player because a probe that hand-rolls its own body
    measures the body, not the server: an abbreviated one -- no
    playbackContext, no signatureTimestamp -- came back UNPLAYABLE with zero
    formats for the same client and credential that this one gets 25 formats
    from. Any comparison between clients has to hold the request still.
    """
    return {
        "videoId": video_id,
        "params": PLAYER_PARAMS,
        "playbackContext": {
            "contentPlaybackContext": {
                "html5Preference": "HTML5_PREF_WANTS",
                # Logged every play, not only when it changes. It says which
                # player build we are claiming we will unscramble with, and
                # if it disagrees with the build n is actually solved from,
                # the urls we are minted expect a transform we never apply.
                # The browser sends 20684 today.
                "signatureTimestamp": _log_sts(),
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
    }


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
    version = effective_version(name)

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
    #
    # Only for the web client, though. These describe the running web player --
    # its rollout, its install data, its visitor session -- and handing them to
    # an Android or iOS identity describes a client that is not the one making
    # the request. Sent to all six, the mobile and TV clients answered HTTP 400
    # INVALID_ARGUMENT, which reads like a refusal and is actually a complaint
    # about the request.
    if name != CLIENT_NAME:
        return {"client": client}

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


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _pb_num(field, value):
    return _varint(field << 3) + _varint(value)


def _pb_bytes(field, payload):
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _pb_b64(raw):
    """base64url, percent-encoded, as these tokens travel."""
    return quote(base64.urlsafe_b64encode(raw).decode(), safe="")


def epg_order_token(order, max_airings, max_duration_ms, start_ms,
                    initial_ms, pagination_ms):
    """The continuation that asks for the guide in a particular order.

    Read out of the live tab's own order dropdown and rebuilt rather than
    copied, because the token repeats the epgOptions the request is making
    and a stale copy would ask for somebody else's window. Its shape:

        80226972 {
          2: "FEunplugged_epg"
          3: "8gMEIgIwAQ%3D%3D"        # itself 62 { 4 { 6: <order> } }
          22 { 1 { 1: maxAiringsPerStation, 3: maxDurationMs,
                   4: initialEpgFetchStartTimeMs,
                   5: initialEpgFetchDurationMs, 6: paginationDurationMs } }
        }

    Only field 6 of the inner selector distinguishes the five orders, and
    this builder reproduces all five of the tokens in the 2026-08-29 capture
    byte for byte -- which is what makes it a reconstruction rather than a
    guess. tools/checks/test_pages.py keeps it that way.
    """
    selector = _pb_b64(_pb_bytes(62, _pb_bytes(4, _pb_num(6, order))))
    options = _pb_bytes(1, (_pb_num(1, max_airings)
                            + _pb_num(3, max_duration_ms)
                            + _pb_num(4, start_ms)
                            + _pb_num(5, initial_ms)
                            + _pb_num(6, pagination_ms)))
    body = (_pb_bytes(2, EPG_BROWSE_ID.encode())
            + _pb_bytes(3, selector.encode())
            + _pb_bytes(22, options))
    return _pb_b64(_pb_bytes(80226972, body))


class Api(object):
    def __init__(self, bearer=None):
        """A session on the stored device-code token.

        There used to be a choice here -- a cookie jar, a token, and a
        setting to say which won -- and with it the standing hazard that a
        caller verifying one credential would silently verify the other and
        record the identity the wrong one answered as. There is one
        credential now, so there is nothing to pick.
        """
        self.bearer = bearer if isinstance(bearer, str) and bearer else auth.bearer()
        # A device-code token is minted for a limited-input client and
        # YouTube TV treats it that way: asked as WEB_UNPLUGGED it answers
        # HTTP 400 INVALID_ARGUMENT, and asked as TVHTML5_UNPLUGGED the same
        # token returned a 150 channel lineup. So the identity travels with
        # the credential rather than every call having to remember one.
        self.client_name = auth.client_name()
        self.session = requests.Session()
        # A show page's seasons are fetched together rather than one after
        # another, so more than one thread can be inside call() at once. The
        # request itself is fine -- requests pools per host -- but the
        # visitor id a reply can carry is not, and it ends in a write to the
        # profile.
        self._lock = threading.Lock()
        self._visitor_id = kodiutils.get_setting("visitor_id", "") or _baked_visitor_id()
        try:
            refresh_bootstrap(self.session)
        except Exception as exc:
            kodiutils.log("bootstrap refresh skipped: %s" % exc)

    def _headers(self, client_name=None):
        name = client_name or self.client_name
        spec = client_spec(name)
        client_id = spec["id"]
        version = effective_version(name)
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
            "Authorization": "Bearer " + self.bearer,
        }
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        return headers

    def report(self, url):
        """POST one playback-stats ping and return the HTTP status.

        Not an InnerTube call and deliberately not routed through ``call``:
        the body is empty, everything is in the query string the player
        response already signed, and a 204 is success. The capture of
        2026-08-28 03:10 shows no Authorization header on these at all --
        the browser's cookies, the visitor id and the origin were the whole
        of the credential. A token session has no cookies, so it sends the
        bearer; the endpoint answers 204 either way.

        The url comes back from playbackTracking pointing at s.youtube.com
        and the player sends it to the origin instead, so the host is
        rewritten rather than taken as given.
        """
        parts = urlparse(url)
        if parts.netloc != urlparse(ORIGIN).netloc:
            url = urlunparse(parts._replace(scheme="https",
                                            netloc=urlparse(ORIGIN).netloc))
        headers = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "X-Goog-AuthUser": "0",
            "Content-Length": "0",
        }
        headers["Authorization"] = "Bearer " + self.bearer
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        try:
            response = self.session.post(url, data=b"", headers=headers,
                                         timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ApiError("could not reach %s: %s" % (parts.path, exc))
        return response.status_code

    def call(self, endpoint, body, params=None, client_name=None):
        url = BASE + endpoint
        payload = dict(body)
        payload.setdefault(
            "context", context(client_name=client_name or self.client_name))
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
                # "Request contains an invalid argument" does not say which
                # argument, and guessing at it is how an evening gets spent.
                # InnerTube names the field in error.details when it knows it,
                # and taking message alone threw that away.
                details = error.get("details")
                if details:
                    detail = "%s | details=%s" % (detail, json.dumps(details)[:600])
            except ValueError:
                detail = (response.text or "")[:400]
            asked = client_name or self.client_name
            shown = effective_version(asked)
            kodiutils.log("%s -> HTTP %d as %s v%s%s"
                          % (endpoint, response.status_code, asked, shown,
                             ": %s" % detail[:300] if detail else ""))

        if response.status_code in (401, 403):
            # A refused token is a refused token: it has either expired
            # beyond refreshing or was never authorised for this account.
            # There is nothing to probe -- the old jar-or-request question
            # existed because a cookie could be alive in a browser and
            # refused here at the same time, which a bearer cannot be.
            raise auth.AuthError(
                "YouTube TV refused the stored sign-in (%s returned HTTP %d). "
                "Sign in again.%s"
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
            with self._lock:
                fresh_id = visitor != self._visitor_id
                self._visitor_id = visitor
            if fresh_id:
                kodiutils.set_setting("visitor_id", visitor)

        try:
            return response.json()
        except ValueError:
            raise ApiError("%s did not return JSON" % endpoint)

    # -- guide ------------------------------------------------------------

    def epg(self, start_ms=None, hours=6, max_airings=11, client_name=None,
            order=None):
        """The channel lineup and schedule.

        One call covers a window; the response carries a continuation token for
        the next. Google caps the reachable range at ``maxDurationMs``, a week.
        """
        start_ms = int(start_ms or time.time() * 1000)
        window = int(hours * 3600 * 1000)
        body = {
            "browseId": EPG_BROWSE_ID,
            "unpluggedBrowseOptions": {"epgOptions": {
                "maxAiringsPerStation": max_airings,
                "initialEpgFetchStartTimeMs": str(start_ms),
                "initialEpgFetchDurationMs": window,
                "paginationDurationMs": window,
                "maxDurationMs": "604800000",
            }},
        }
        if order in EPG_ORDER_VALUES:
            # Sent alongside the browseId, not instead of it: that is what
            # the web client does, and the token repeats the same
            # epgOptions that the body carries, so the two are built from
            # one set of values here.
            body["continuation"] = epg_order_token(
                order, max_airings, 604800000, start_ms, window, window)
        return self.call("browse", body, client_name=client_name)

    def continuation(self, token, client_name=None):
        return self.call("browse", {"continuation": token},
                         client_name=client_name)

    def library(self, client_name=None):
        """Recordings, purchases and what is scheduled to record."""
        return self.continuation(LIBRARY_CONTINUATION, client_name=client_name)

    def home(self, client_name=None):
        """The front page: resume watching, top picks, and the genre rows."""
        return self.continuation(HOME_CONTINUATION, client_name=client_name)

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
        response = self.call("player", player_body(video_id, cpn),
                             params={"prettyPrint": "false"},
                             client_name=client_name)

        status = response.get("playabilityStatus", {}) or {}
        if status.get("status") != "OK":
            reason = status.get("reason") or status.get("status") or "unknown"
            raise NotPlayable(reason)
        if not response.get("streamingData"):
            raise NotPlayable("no streamingData in the player response")
        return response

    def heartbeat(self, video_id, cpn, sequence, token, server_data):
        """Keep a playback session alive.

        The response asks to be called again after ``pollDelayMs`` and carries
        the ``heartbeatServerData`` to echo next time. This is the shape the
        web player sends, field for field, from the 2026-08-28 03:10 capture of
        two minutes of on-demand playback -- two checks and an empty
        ``unpluggedParams``, no playbackState:

            {"heartbeatChecks": ["HEARTBEAT_CHECK_TYPE_YPC",
                                 "HEARTBEAT_CHECK_TYPE_UNPLUGGED"],
             "unpluggedParams": {}}

        HEARTBEAT_CHECK_TYPE_YPC is the entitlement check. The player response
        that starts a session says how long a client may go quiet --
        ``intervalMilliseconds`` 30000 and ``maxRetries`` 3 -- and ninety
        seconds is where playback stops for a client that never calls.
        """
        body = {
            "videoId": video_id,
            "cpn": cpn,
            "sequenceNumber": sequence,
            "heartbeatRequestParams": {
                "heartbeatChecks": [
                    "HEARTBEAT_CHECK_TYPE_YPC",
                    "HEARTBEAT_CHECK_TYPE_UNPLUGGED",
                ],
                "unpluggedParams": {},
            },
        }
        if token:
            body["heartbeatToken"] = token
        if server_data:
            body["heartbeatServerData"] = server_data
        return self.call("player/heartbeat", body)
