#!/usr/bin/env python3
"""Run the addon's own lib/ code against the live Plex VOD provider with the
Kodi modules stubbed, so browse + stream resolution are exercised outside
Kodi (from GitHub Actions; plex hosts are blocked in the dev container).

Usage: python3 tools/smoke_test.py   (needs `requests`)
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SETTINGS = {}
PROPS = {}


class _ListItem(object):
    def __init__(self, label="", path=""):
        self.label, self.path, self.props, self.art, self.subs, self.mime = label, path, {}, {}, [], None
        self.tag = types.SimpleNamespace(**{n: (lambda *a, **k: None) for n in (
            "setMediaType", "setTitle", "setPlot", "setTagLine", "setYear", "setPremiered", "setDuration",
            "setMpaa", "setStudios", "setGenres", "setRating", "setTvShowTitle", "setSeason", "setEpisode",
            "setResumePoint", "setPlaycount")})

    def setProperty(self, k, v): self.props[k] = v
    def setArt(self, a): self.art.update(a)
    def setSubtitles(self, s): self.subs = s
    def setMimeType(self, m): self.mime = m
    def setContentLookup(self, v): pass
    def getVideoInfoTag(self): return self.tag


class _Addon(object):
    def __init__(self, *a): pass
    def getAddonInfo(self, k): return {"id": "plugin.video.plexondemand", "name": "Plex On Demand",
                                       "profile": "/tmp/pod", "version": "21.5.0"}.get(k, "")
    def getSetting(self, k): return SETTINGS.get(k, "")
    def setSetting(self, k, v): SETTINGS[k] = v
    def getLocalizedString(self, i): return str(i)


class _Window(object):
    def __init__(self, *a): pass
    def setProperty(self, k, v): PROPS[k] = v
    def getProperty(self, k): return PROPS.get(k, "")
    def clearProperty(self, k): PROPS.pop(k, None)


xbmc = types.ModuleType("xbmc")
xbmc.LOGINFO, xbmc.LOGERROR = 1, 4
xbmc.log = lambda m, l=1: print("  [log]", m)
xbmc.getCondVisibility = lambda c: False
xbmc.getLanguage = lambda *a: "en"
xbmc.ISO_639_1 = 0
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.ListItem, xbmcgui.Window = _ListItem, _Window
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = ""
xbmcgui.Dialog = lambda: types.SimpleNamespace(notification=lambda *a: None, ok=lambda *a: None)
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
for m in (xbmc, xbmcgui, xbmcaddon, xbmcvfs):
    sys.modules[m.__name__] = m

from lib.api import PlexVodApi, TYPE_MOVIE, TYPE_SHOW  # noqa: E402
from lib import playback  # noqa: E402

FAIL = []


def check(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (" - " + str(extra) if extra else ""))
    if not ok:
        FAIL.append(label)


api = PlexVodApi()
tok = api.auth.token()
print("::add-mask::" + tok)
check("anonymous token", bool(tok))
from lib.auth import base_headers as _bh
_h = _bh()
pin = api.auth.create_pin(_h)
check("PIN create", bool(pin.get("code")) and pin.get("clientIdentifier") == _h["X-Plex-Client-Identifier"],
      len(pin.get("code") or ""))
try:
    check("PIN poll (same client id)", api.auth.check_pin(pin["id"], _h) is None)
except Exception as exc:
    check("PIN poll (same client id)", False, exc)
SETTINGS.pop("client_id", None)  # a lost setting must not change the id
check("client id stable across lost setting", _bh()["X-Plex-Client-Identifier"] == _h["X-Plex-Client-Identifier"])
hubs = api.hubs()
check("hubs", len(hubs) > 10, len(hubs))
items, total = api.hub_items(hubs[0]["key"])
check("hub items", bool(items), "%d of %d" % (len(items), total))
mg, sg = api.genres(TYPE_MOVIE), api.genres(TYPE_SHOW)
check("genres", bool(mg) and bool(sg), "%d movie / %d show" % (len(mg), len(sg)))
movies, total = api.listing(mg[0]["key"])
check("genre listing page 1", len(movies) > 0, "%d of %d" % (len(movies), total))
page2, _ = api.listing(mg[0]["key"], start=len(movies))
check("genre listing page 2 differs", page2 and page2[0].get("ratingKey") != movies[0].get("ratingKey"))
shows, _ = api.listing(sg[0]["key"])
show = next((s for s in shows if s.get("type") == "show"), None)
check("show in show genre", bool(show))
seasons, _ = api.children(show["ratingKey"])
eps, _ = api.children(seasons[0]["ratingKey"])
check("seasons/episodes", bool(seasons) and bool(eps), "%d seasons, %d eps" % (len(seasons), len(eps)))
res = api.search("star")
check("search free section", bool(res), [r.get("title") for r in res[:3]])

clear = drm = None
for h in hubs[:20]:
    for m in api.hub_items(h["key"])[0]:
        ms = m.get("Media") or []
        if any(x.get("drm") for x in ms):
            drm = drm or m
        elif ms:
            clear = clear or m
    if clear and drm:
        break

sys.modules["inputstreamhelper"] = None  # force the ImportError path
for label, m in (("clear", clear), ("drm", drm), ("episode", eps[0])):
    try:
        li = playback.build(api, m["ratingKey"])
        p = li.props
        check("play %s: %s" % (label, m.get("title")), li.path.startswith("https://"),
              "%s mime=%s drm=%s legacy_key=%s subs=%d" % (
                  li.path.split("?")[0].rsplit("/", 1)[-1], li.mime, "inputstream.adaptive.drm" in p,
                  "inputstream.adaptive.license_key" in p, len(li.subs)))
        check("  manifest headers carry token", "X-Plex-Token=" in p.get("inputstream.adaptive.manifest_headers", ""))
        if label == "drm":
            key = p.get("inputstream.adaptive.license_key", "")
            check("  licence has X-Plex-DRM", "X-Plex-DRM=widevine" in key, key.split("|")[0].rsplit("/", 2)[-2:])
        check("  timeline handoff", "plexondemand.playing" in PROPS)
    except playback.Blocked:
        print("SKIP play %s: blocked from this IP (451 VPN/proxy)" % label)
if drm:
    import requests
    from lib.auth import base_headers
    part = [x for x in drm["Media"] if x.get("protocol") == "dash"][0]["Part"][0]
    h = base_headers(); h.update({"X-Plex-Token": tok, "X-Plex-DRM": "widevine",
                                  "Content-Type": "application/octet-stream"})
    r = requests.post(api.base + part["license"], data=b"\x08\x04", headers=h, timeout=20)
    check("licence endpoint answers cert request", r.status_code == 200 and r.content[:1] == b"\x08", r.status_code)
r = api._get("/timeline", {"ratingKey": clear["ratingKey"], "key": "/library/metadata/%s" % clear["ratingKey"],
                           "state": "stopped", "time": 1000, "duration": clear.get("duration", 0)})
check("timeline report", "playbackState" in r, r.get("playbackState"))
print("\n%d failure(s)" % len(FAIL))
sys.exit(1 if FAIL else 0)
