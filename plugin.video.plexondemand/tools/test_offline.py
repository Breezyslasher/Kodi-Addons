#!/usr/bin/env python3
"""Offline tests for failure paths the live smoke test can't provoke:
going offline, a revoked sign-in, the pre-22.1.5 secure-decoder flag and a
Live TV channel without an id. Kodi and the network are stubbed.

Usage: python3 tools/test_offline.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
sys.path.insert(0, ADDON)

import requests  # noqa: E402

NOTES, SETTINGS, FILES, DIRS = [], {}, {}, []


class _ListItem(object):
    def __init__(self, label="", path=""):
        self.label, self.path, self.props = label, path, {}

    def setProperty(self, k, v): self.props[k] = v
    def setMimeType(self, m): pass
    def setContentLookup(self, v): pass


class _Addon(object):
    def __init__(self, *a): pass
    def getAddonInfo(self, k): return {"id": "plugin.video.plexondemand", "name": "Plex On Demand",
                                       "profile": "/tmp/pod-offline", "path": ADDON, "version": "21.2.0"}.get(k, "")
    def getSetting(self, k): return SETTINGS.get(k, "")
    def setSetting(self, k, v): SETTINGS[k] = v
    def getLocalizedString(self, i): return "s%d" % i


xbmc = types.ModuleType("xbmc")
xbmc.LOGINFO, xbmc.LOGERROR = 1, 4
xbmc.log = lambda *a, **k: None
xbmc.getCondVisibility = lambda c: c == "System.Platform.Android"
xbmc.getLanguage = lambda *a: "en"
xbmc.ISO_639_1 = 0
xbmc.executebuiltin = lambda *a: None
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.ListItem = _ListItem
xbmcgui.Window = lambda *a: types.SimpleNamespace(setProperty=lambda *a: None, getProperty=lambda *a: "1",
                                                  clearProperty=lambda *a: None)
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = ""
xbmcgui.Dialog = lambda: types.SimpleNamespace(notification=lambda *a, **k: NOTES.append(a),
                                               ok=lambda *a: NOTES.append(a))
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
xbmcplugin = types.ModuleType("xbmcplugin")
xbmcplugin.endOfDirectory = lambda h, succeeded=True, **k: DIRS.append(succeeded)
xbmcplugin.addDirectoryItem = lambda *a, **k: None
xbmcplugin.setContent = lambda *a: None
xbmcplugin.setResolvedUrl = lambda *a: None
for m in (xbmc, xbmcgui, xbmcaddon, xbmcvfs, xbmcplugin):
    sys.modules[m.__name__] = m

from lib import auth, kodiutils  # noqa: E402

# accounts live in memory
auth._account = lambda: dict(FILES.get("acct") or {})
auth._save_account = lambda **c: FILES.update(acct=dict(FILES.get("acct") or {}, **c))

FAIL = []


def check(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (" - %s" % (extra,) if extra != "" else ""))
    if not ok:
        FAIL.append(label)


class Resp(object):
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data if data is not None else {}

    def json(self): return self._data


# ---- 1. offline: transport errors become PlexApiError; the router ends the directory
from lib import api  # noqa: E402
api._PROVIDER_BASES[api.VOD_ID] = api.VOD_BASE
FILES["acct"] = {"anon_token": "ANON"}


def offline(*a, **k):
    raise requests.ConnectionError("Name or service not known")


a = api.PlexVodApi()
a.session.request = offline
try:
    a.hubs()
    check("offline call raises PlexApiError", False, "no exception")
except api.PlexApiError as exc:
    check("offline call raises PlexApiError", "ConnectionError" in str(exc), exc)
except Exception as exc:
    check("offline call raises PlexApiError", False, repr(exc))

sys.argv = ["plugin://plugin.video.plexondemand/", "1", "?action=hubs"]
import default  # noqa: E402
orig = requests.Session.request
requests.Session.request = offline
del DIRS[:]
del NOTES[:]
try:
    default.router("action=hubs")
    check("offline menu ends the directory (no traceback)", DIRS == [False], DIRS)
except Exception as exc:
    check("offline menu ends the directory (no traceback)", False, repr(exc))
check("offline menu shows an error", any("s32025" in str(n) for n in NOTES), NOTES[-1:])
requests.Session.request = orig

# ---- 2. revoked sign-in: confirmed by plex.tv/user 401 -> anonymous + notice
FILES["acct"] = {"plex_token": "USER", "account_name": "me", "anon_token": "ANON"}
calls = []
auth.requests.get = lambda url, **k: calls.append(url) or Resp(401)
del NOTES[:]
pa = auth.PlexAuth()
check("revoked token: invalidate() retries", pa.invalidate() is True)
check("revoked token: signed out, anonymous kept", not pa.signed_in() and pa.token() == "ANON", FILES["acct"])
check("revoked token: user is told", NOTES and "s32087" in str(NOTES[-1]), NOTES[-1:])
FILES["acct"] = {"plex_token": "USER", "account_name": "me", "anon_token": "ANON"}
auth.requests.get = lambda url, **k: Resp(200, {"username": "me"})
check("valid token refused by one endpoint: stays signed in",
      pa.invalidate() is False and pa.signed_in())
FILES["acct"] = {"anon_token": "ANON"}
check("anonymous 401: token re-minted", pa.invalidate() is True and not FILES["acct"].get("anon_token"))

# a full _get round: 401 with a revoked user token retries anonymously and succeeds
FILES["acct"] = {"plex_token": "USER", "account_name": "me", "anon_token": "ANON"}
auth.requests.get = lambda url, **k: Resp(401)
seen = []


def answer(method, url, params=None, headers=None, timeout=None):
    seen.append(headers["X-Plex-Token"])
    return Resp(401) if headers["X-Plex-Token"] == "USER" else Resp(200, {"MediaContainer": {"Hub": [{"key": "/h"}]}})


a = api.PlexVodApi()
a.session.request = answer
check("revoked token: the call succeeds anonymously", a.hubs() == [{"key": "/h"}] and seen == ["USER", "ANON"], seen)

# ---- 3. secure decoder on ISA < 22.1.5 uses license_flags
from lib import playback  # noqa: E402
playback._isa_version = lambda: (21, 2, 0)
sys.modules["inputstreamhelper"] = None
li = _ListItem()
playback._widevine(li, "https://lic", {"X-Plex-Token": "T", "X-Plex-Client-Identifier": "C"})
check("ISA 21 Android: license_flags=force_secure_decoder",
      li.props.get("inputstream.adaptive.license_flags") == "force_secure_decoder"
      and "inputstream.adaptive.secure_decoder" not in li.props, li.props)
playback._isa_version = lambda: (22, 1, 5)
li = _ListItem()
playback._widevine(li, "https://lic", {"X-Plex-Token": "T", "X-Plex-Client-Identifier": "C"})
cfg = json.loads(li.props.get("inputstream.adaptive.drm", "{}")).get("com.widevine.alpha", {})
check("ISA 22.1.5 Android: JSON drm secure_decoder", cfg.get("secure_decoder") is True, cfg)

# ---- 4. Live TV: a channel without an id is left out (IPTV Manager keeps working)
from lib import live, iptv  # noqa: E402
la = live.LiveApi.__new__(live.LiveApi)
la._get = lambda key, params=None: {"Channel": [
    {"id": "a-1", "gridKey": "1", "title": "One", "slug": "one"},
    {"gridKey": "2", "title": "No id"},
    {"id": "", "title": "Empty id"},
    {"id": "c-3", "gridKey": "3", "title": "Hidden", "hidden": True}]}
la.filters = lambda: []
chans = la.channels()
check("channels without an id are dropped", [c["title"] for c in chans] == ["One"], [c["title"] for c in chans])
try:
    streams = iptv.channels(la)["streams"]
    check("IPTV channel list builds", [s["name"] for s in streams] == ["One"], streams)
except Exception as exc:
    check("IPTV channel list builds", False, repr(exc))

print("\n%d failure(s)" % len(FAIL))
sys.exit(1 if FAIL else 0)
