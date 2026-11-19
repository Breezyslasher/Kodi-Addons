#!/usr/bin/env python3
"""Offline test of service.py with the real script.module.skipbuttons and
Kodi stubbed: credits countdown, Watch credits, autoplay at the end without
double plays, the no-marker fallback, intro markers, watched marking, and
the credits-marker choice in playback._remember. No network.

Usage: python3 tools/test_service.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
REPO = os.path.dirname(ADDON)
sys.path.insert(0, ADDON)
sys.path.insert(0, os.path.join(REPO, "script.module.skipbuttons", "lib"))

PROPS, BUILTINS, SETTINGS = {}, [], {}


class _Player(object):
    def __init__(self, *a):
        self.pos, self.playing, self.seeks = 0.0, True, []

    def isPlaying(self): return self.playing
    def isPlayingVideo(self): return self.playing
    def getTime(self): return self.pos
    def seekTime(self, t): self.seeks.append(t)


class _Dialog(object):
    def __init__(self, *a, **k): self.shown = False
    def show(self): self.shown = True
    def close(self): self.shown = False
    def getControl(self, *a): return types.SimpleNamespace(setLabel=lambda *a: None, setVisible=lambda *a: None)
    def setFocusId(self, *a): pass


class _Addon(object):
    def __init__(self, *a): pass
    def getAddonInfo(self, k): return {"path": "/tmp/sb", "profile": "/tmp/pod-svc", "id": "plugin.video.plexondemand",
                                       "name": "Plex On Demand"}.get(k, "")
    def getLocalizedString(self, i): return {30000: "Skip recap", 30001: "Skip intro", 30002: "Next episode (%d)",
                                             30003: "Watch credits"}.get(i, "s%d" % i)
    def getSetting(self, k): return SETTINGS.get(k, "")
    def setSetting(self, k, v): SETTINGS[k] = v


class _Window(object):
    def __init__(self, *a): pass
    def setProperty(self, k, v): PROPS[k] = v
    def getProperty(self, k): return PROPS.get(k, "")
    def clearProperty(self, k): PROPS.pop(k, None)


xbmc = types.ModuleType("xbmc")
xbmc.LOGINFO, xbmc.LOGERROR = 1, 4
xbmc.log = lambda *a, **k: None
xbmc.Player = _Player
xbmc.Monitor = lambda: types.SimpleNamespace(abortRequested=lambda: True, waitForAbort=lambda t: True)
xbmc.executebuiltin = BUILTINS.append
xbmc.getCondVisibility = lambda c: False
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.WindowXMLDialog, xbmcgui.Window = _Dialog, _Window
xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU = 92, 10
xbmcgui.ListItem = lambda *a, **k: None
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = ""
xbmcgui.Dialog = lambda: types.SimpleNamespace(notification=lambda *a: None)
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
for m in (xbmc, xbmcgui, xbmcaddon, xbmcvfs):
    sys.modules[m.__name__] = m

import service  # noqa: E402
from lib import playback  # noqa: E402

CALLS = []


class FakeApi(object):
    def _get(self, path, params=None, method="GET"):
        CALLS.append((path, dict(params or {})))
        return {}

    def action(self, name, rk):
        CALLS.append(("/actions/" + name, {"ratingKey": rk}))


service.PlexVodApi = FakeApi
FAIL = []


def check(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (" - %s" % (extra,) if extra != "" else ""))
    if not ok:
        FAIL.append(label)


def plays():
    return [b for b in BUILTINS if b.startswith("PlayMedia(")]


def start(p, **cur):
    cur.setdefault("ratingKey", "EP1")
    cur.setdefault("key", "/library/metadata/" + cur["ratingKey"])
    PROPS[service.PROP] = json.dumps(cur)
    del BUILTINS[:]
    del CALLS[:]
    p.pos = 0
    p.onAVStarted()


p = service.Player()
check("Skip Buttons module loaded", p.skip is not None)

# A. credits marker + next episode: countdown at the marker, plays next once
start(p, duration=2400000, credits=2300.0, next="EP2")
p.skip.tick(2299, 0)
check("A no button before the credits", p.skip.dialog is None)
p.skip.tick(2300, 100)
check("A countdown opens at the credits marker", p.skip.segment == "credits")
p.skip.tick(2311, 111)
check("A countdown end plays next episode", plays() == ["PlayMedia(plugin://plugin.video.plexondemand/?action=play&rk=EP2)"],
      plays())
p.onPlayBackEnded()
check("A ended after countdown: no second play", len(plays()) == 1, plays())

# B. Watch credits: nothing during credits, next episode at the end (bug 2.1)
start(p, duration=2400000, credits=2300.0, next="EP2")
p.skip.tick(2300, 100)
p.skip.dialog.choice = "secondary"
p.skip.tick(2301, 101)
check("B Watch credits closes the button without playing", p.skip.dialog is None and not plays())
p.pos = 2400
p.onPlayBackEnded()
check("B end after Watch credits autoplays next once", len(plays()) == 1, plays())

# B2. Back on the countdown behaves like Watch credits
start(p, duration=2400000, credits=2300.0, next="EP2")
p.skip.tick(2300, 100)
p.skip.dialog.choice = "back"
p.skip.tick(2301, 101)
p.onPlayBackEnded()
check("B2 Back then end autoplays next once", len(plays()) == 1, plays())

# B3. autoplay setting off: no countdown, no autoplay
SETTINGS["autoplay_next"] = "false"
start(p, duration=2400000, credits=2300.0, next="EP2")
p.skip.tick(2300, 100)
p.onPlayBackEnded()
check("B3 autoplay off: no countdown, no play", p.skip.dialog is None and not plays())
SETTINGS.pop("autoplay_next")

# C. no marker: countdown CREDITS_FALLBACK s before the end
start(p, duration=2000000, credits=None, next="EP2")
p.skip.tick(2000 - service.CREDITS_FALLBACK - 1, 0)
check("C no button before the fallback", p.skip.dialog is None)
p.skip.tick(2000 - service.CREDITS_FALLBACK, 1)
check("C fallback countdown at duration - %ds" % service.CREDITS_FALLBACK, p.skip.segment == "credits")

# D. intro marker: Skip intro, seeks to its end
start(p, duration=2000000, intro=[100.0, 190.0], next=None)
p.skip.tick(120, 0)
check("D Skip intro shown inside the intro", p.skip.segment == "intro")
p.skip.dialog.choice = "primary"
p.skip.tick(121, 1)
check("D Skip intro seeks to the intro end", p.seeks[-1:] == [190.0], p.seeks)

# E. a non-Plex video: no current, buttons reset
start(p, duration=2400000, credits=2300.0, next="EP2")
p.skip.tick(2300, 100)
PROPS.pop(service.PROP, None)
p.current = None
p.onAVStarted()
check("E other add-on's video: no title, no button", p.current is None and p.skip.dialog is None)

# F. movie: reaching its credits marks it watched; no next-episode button
start(p, ratingKey="MOV", duration=6000000, credits=5700.0, next=None)
p.skip.tick(5700, 0)
check("F movie: no countdown without a next episode", p.skip.dialog is None)
p.pos = 5710
p.tick()  # the service loop samples the position every second
p.onPlayBackStopped()
check("F stop past the credits marker scrobbles", ("/actions/scrobble", {"ratingKey": "MOV"}) in CALLS)
start(p, ratingKey="MOV", duration=6000000, credits=5700.0, next=None)
p.pos = 3000
p.tick()
p.onPlayBackStopped()
check("F stop mid-movie does not scrobble", not any(c[0] == "/actions/scrobble" for c in CALLS))

# G. _remember: prefer the final credits marker, carry intro
playback._remember({"ratingKey": "X", "duration": 3000000, "Marker": [
    {"type": "credits", "startTimeOffset": 2900000, "endTimeOffset": 2950000},
    {"type": "credits", "startTimeOffset": 2800000, "endTimeOffset": 3000000, "final": True},
    {"type": "intro", "startTimeOffset": 30000, "endTimeOffset": 90000}]}, {"ratingKey": "Y"})
h = json.loads(PROPS[service.PROP])
check("G final credits marker preferred", h["credits"] == 2800.0, h["credits"])
check("G intro handed over", h["intro"] == [30.0, 90.0], h["intro"])
playback._remember({"ratingKey": "X", "Marker": [
    {"type": "credits", "startTimeOffset": 100000}, {"type": "credits", "startTimeOffset": 200000}]})
check("G without final: latest credits start", json.loads(PROPS[service.PROP])["credits"] == 200.0)

print("\n%d failure(s)" % len(FAIL))
sys.exit(1 if FAIL else 0)
