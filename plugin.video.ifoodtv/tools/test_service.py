#!/usr/bin/env python3
"""Offline test of service.py with the real script.module.skipbuttons and
Kodi stubbed: Skip intro, the credits countdown, Watch credits then the end,
the no-marker episode, a movie, another add-on's video, the autoplay
setting -- and skip_markers() on real values captured from the live API
(docs/recon.md). No network.

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
STREAM = "https://ftmain.cachefly.net/files/a8wn4hw/vi/df/52/10389418/s10389418.m3u8"


class _Player(object):
    def __init__(self, *a):
        self.pos, self.playing, self.seeks, self.file = 0.0, True, [], STREAM

    def isPlaying(self): return self.playing
    def isPlayingVideo(self): return self.playing
    def getTime(self): return self.pos
    def getPlayingFile(self): return self.file
    def seekTime(self, t): self.seeks.append(t)


class _Dialog(object):
    def __init__(self, *a, **k): self.shown = False
    def show(self): self.shown = True
    def close(self): self.shown = False
    def getControl(self, *a): return types.SimpleNamespace(setLabel=lambda *a: None, setVisible=lambda *a: None)
    def setFocusId(self, *a): pass


class _Addon(object):
    def __init__(self, *a): pass
    def getAddonInfo(self, k): return {"path": "/tmp/sb", "profile": "/tmp/ifoodtv-svc",
                                       "id": "plugin.video.ifoodtv", "name": "iFood.tv"}.get(k, "")
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
xbmc.LOGINFO, xbmc.LOGERROR, xbmc.ISO_639_1 = 1, 4, 0
xbmc.log = lambda *a, **k: None
xbmc.Player = _Player
xbmc.Monitor = lambda: types.SimpleNamespace(abortRequested=lambda: True, waitForAbort=lambda t: True)
xbmc.executebuiltin = BUILTINS.append
xbmc.getCondVisibility = lambda c: False
xbmc.getLanguage = lambda *a: "en"
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.WindowXMLDialog, xbmcgui.Window = _Dialog, _Window
xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU = 92, 10
xbmcgui.ListItem = lambda *a, **k: None
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = ""
xbmcgui.Dialog = lambda: types.SimpleNamespace(notification=lambda *a: None)
xbmcgui.INPUT_ALPHANUM = xbmcgui.INPUT_NUMERIC = xbmcgui.ALPHANUM_HIDE_INPUT = 0
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
for m in (xbmc, xbmcgui, xbmcaddon, xbmcvfs):
    sys.modules[m.__name__] = m

import service  # noqa: E402
from lib.api import is_episode, skip_markers  # noqa: E402

FAIL = []


def check(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (" - %s" % (extra,) if extra != "" else ""))
    if not ok:
        FAIL.append(label)


def plays():
    return [b for b in BUILTINS if b.startswith("PlayMedia(")]


def start(p, marks=None, nxt="10389419", url=STREAM):
    PROPS[service.PROP] = json.dumps({"nid": "10389418", "url": url, "marks": marks or {}, "next": nxt})
    del BUILTINS[:]
    p.pos, p.seeks = 0, []
    p.onAVStarted()


NEXT = "PlayMedia(plugin://plugin.video.ifoodtv/?action=play&nid=10389419)"

# --- skip_markers on values captured live (recon, 2026-11-21) ---------------
check("markers: Looney S01E01 intro 0-12, credits unset",
      skip_markers({"runtime": 392, "intro_st": 0, "intro_et": 12, "endcredit_st": 0, "endcredit_et": 0})
      == {"intro": (0.0, 12.0)})
check("markers: Looney S01E02 credits 365 (no intro)",
      skip_markers({"runtime": 430, "intro_st": 0, "intro_et": 0, "endcredit_st": 365, "endcredit_et": 369})
      == {"credits": 365.0})
check("markers: credits within 5 s of the end are dropped (S01E04 402/407)",
      "credits" not in skip_markers({"runtime": 407, "intro_st": 0, "intro_et": 45,
                                     "endcredit_st": 402, "endcredit_et": 407}))
check("markers: Revenge intro 1-57, credits 7228",
      skip_markers({"runtime": 7423, "intro_st": 1, "intro_et": 57, "endcredit_st": 7228, "endcredit_et": 7422})
      == {"intro": (1.0, 57.0), "credits": 7228.0})
check("markers: string values and 'None' parse", skip_markers(
    {"runtime": "600", "intro_st": "None", "intro_et": "30", "endcredit_st": "550", "endcredit_et": "600"})
      == {"intro": (0.0, 30.0), "credits": 550.0})
check("markers: ad_breaks are never markers",
      skip_markers({"runtime": 7423, "ad_breaks": "728, 1478, 2077"}) == {})
check("episode: Looney S01E01 is an episode",
      is_episode({"series_name": "Best of Looney Tunes", "season": "1", "episode": "1"}))
check("episode: a movie (episode None) is not", not is_episode({"series_name": "", "season": "None",
                                                                 "episode": "None"}))

p = service.Player()
check("Skip Buttons module loaded", p.skip is not None)

# A. intro: Skip intro inside the intro, seeks to its end, hides after
start(p, marks={"intro": [0, 45]})
p.skip.tick(5, 0)
check("A Skip intro shown inside the intro", p.skip.segment == "intro")
p.skip.dialog.choice = "primary"
p.skip.tick(6, 1)
check("A Skip intro seeks to the intro end", p.seeks[-1:] == [45], p.seeks)

# B. credits marker + next episode: countdown, plays next once
start(p, marks={"credits": 365})
p.skip.tick(364, 0)
check("B no button before the credits", p.skip.dialog is None)
p.skip.tick(365, 100)
check("B countdown opens at the credits", p.skip.segment == "credits")
p.skip.tick(376, 111)
check("B countdown end plays the next episode", plays() == [NEXT], plays())
p.onPlayBackEnded()
check("B end after the countdown: no second play", len(plays()) == 1, plays())

# C. Watch credits, then the end: next episode once
start(p, marks={"credits": 365})
p.skip.tick(365, 100)
p.skip.dialog.choice = "secondary"
p.skip.tick(366, 101)
check("C Watch credits closes without playing", p.skip.dialog is None and not plays())
p.onPlayBackEnded()
check("C end after Watch credits plays next once", plays() == [NEXT], plays())

# D. episode without a credits marker: no countdown, next at the end
start(p, marks={"intro": [0, 12]})
for t in (300, 380, 391):
    p.skip.tick(t, t)
check("D no marker: no countdown near the end", p.skip.dialog is None)
p.onPlayBackEnded()
check("D no marker: next episode at the end once", plays() == [NEXT], plays())

# E. movie: credits marker but no next -> no countdown, nothing at the end
start(p, marks={"intro": [1, 57], "credits": 7228}, nxt=None)
p.skip.tick(7228, 0)
check("E movie: no countdown", p.skip.dialog is None)
p.onPlayBackEnded()
check("E movie: nothing plays at the end", not plays())

# F. autoplay off: no countdown, no autoplay
SETTINGS["autoplay_next"] = "false"
start(p, marks={"credits": 365})
p.skip.tick(365, 100)
p.onPlayBackEnded()
check("F autoplay off: no countdown, no play", p.skip.dialog is None and not plays())
SETTINGS.pop("autoplay_next")

# G. another add-on's video starts with a stale hand-off: ignored
p.file = "https://example.invalid/other.m3u8"
start(p, marks={"credits": 365})
check("G other add-on's video: not ours, no buttons", p.current is None and p.skip.dialog is None)
p.onPlayBackEnded()
check("G other add-on's video: nothing plays at its end", not plays())
p.file = STREAM

# I. next episode arrives after playback started (looked up after resolve)
PROPS[service.PROP] = json.dumps({"nid": "10389418", "url": STREAM, "marks": {"credits": 365},
                                  "next": None, "next_pending": True})
PROPS.pop(service.NEXT_PROP, None)
del BUILTINS[:]
p.onAVStarted()
check("I pending: no countdown offered yet", p.skip.has_next is False)
PROPS[service.NEXT_PROP] = json.dumps({"nid": "999", "next": "123"})
p.poll_next()
check("I stale answer for another title is ignored", p.skip.has_next is False and service.NEXT_PROP in PROPS)
PROPS[service.NEXT_PROP] = json.dumps({"nid": "10389418", "next": "10389419"})
p.poll_next()
check("I late next picked up", p.skip.has_next is True and p.current["next"] == "10389419")
p.skip.tick(365, 100)
p.skip.tick(376, 111)
check("I late next: countdown plays it", plays() == [NEXT], plays())

# J. lookup finished with no next (last episode / failure): nothing at the end
PROPS[service.PROP] = json.dumps({"nid": "10389418", "url": STREAM, "marks": {},
                                  "next": None, "next_pending": True})
del BUILTINS[:]
p.onAVStarted()
PROPS[service.NEXT_PROP] = json.dumps({"nid": "10389418", "next": None})
p.onPlayBackEnded()
check("J no next: nothing plays at the end", not plays())

# K. playback ends before the lookup answered: nothing plays, no crash
PROPS[service.PROP] = json.dumps({"nid": "10389418", "url": STREAM, "marks": {},
                                  "next": None, "next_pending": True})
PROPS.pop(service.NEXT_PROP, None)
del BUILTINS[:]
p.onAVStarted()
p.onPlayBackEnded()
check("K ended before the lookup: nothing plays", not plays())

# H. no hand-off at all (e.g. a library item): nothing
PROPS.pop(service.PROP, None)
p.onAVStarted()
check("H no hand-off: idle", p.current is None)

print("%d check(s) failed" % len(FAIL) if FAIL else "All checks passed")
sys.exit(1 if FAIL else 0)
