"""Background service: Skip intro and the next-episode countdown
(script.module.skipbuttons), for titles this add-on plays.

What the markers are (confirmed live, docs/recon.md "Skip markers"): every
title record carries intro_st / intro_et / endcredit_st / endcredit_et in
seconds, filled on about half the episodes and most movies; 0 means unset.
default.py hands this service the title, its markers and the next episode's
node_id through a home-window property just before playback starts; nothing
here touches the network.

Behaviour follows the web player (same Future Today player as Fawesome):
  * Skip intro while inside intro_st..intro_et; it seeks to intro_et.
  * At endcredit_st, an episode with a next episode gets the "Next episode"
    countdown with "Watch credits". Movies get no countdown.
  * With no credits marker there is no countdown (no guessed time); the next
    episode simply plays when this one ends, as it also does after
    "Watch credits". The "Autoplay next episode" setting turns both off.
"""

import json
import time

import xbmc
import xbmcgui

from lib import kodiutils as ku

PROP = "ifoodtv.playing"

try:
    from skipbuttons import SkipController
except ImportError:  # the add-on still plays without the module
    SkipController = None


class Player(xbmc.Player):

    def __init__(self):
        xbmc.Player.__init__(self)
        self.current = None
        self.next_started = False
        self.skip = None
        if SkipController:
            self.skip = SkipController(self, play_next=self.play_next,
                                       autoplay=lambda: ku.get_setting_bool("autoplay_next", True))

    def _reset(self):
        if self.skip:
            self.skip.reset()

    def _ours(self, info):
        """True when the file now playing is the stream default.py resolved,
        so a video from another add-on never inherits our buttons."""
        want = (info.get("url") or "").split("|", 1)[0]
        try:
            playing = (self.getPlayingFile() or "").split("|", 1)[0]
        except RuntimeError:
            return True
        return not want or not playing or playing == want

    # ---- player events
    def onAVStarted(self):
        raw = xbmcgui.Window(10000).getProperty(PROP)
        xbmcgui.Window(10000).clearProperty(PROP)
        info = json.loads(raw) if raw else None
        self.next_started = False
        self.current = info if info and self._ours(info) else None
        if not self.current:
            self._reset()
            return
        if self.skip:
            marks = {}
            m = self.current.get("marks") or {}
            if m.get("intro"):
                marks["intro"] = tuple(m["intro"])
            if m.get("credits"):
                marks["credits"] = m["credits"]
            self.skip.reset(marks, has_next=bool(self.current.get("next")))

    def onPlayBackEnded(self):
        # Also after "Watch credits" or a dismissed countdown -- but not when
        # the countdown already started the next one, or it would play twice.
        nxt = (self.current or {}).get("next")
        started = self.next_started
        self.current = None
        self.next_started = False
        self._reset()
        if nxt and not started and ku.get_setting_bool("autoplay_next", True):
            self._play(nxt)

    def onPlayBackStopped(self):
        self.current = None
        self._reset()

    onPlayBackError = onPlayBackStopped

    # ---- next episode
    def play_next(self):
        nxt = (self.current or {}).get("next")
        if nxt:
            self.next_started = True
            self._play(nxt)

    @staticmethod
    def _play(nid):
        xbmc.executebuiltin("PlayMedia(plugin://plugin.video.ifoodtv/?action=play&nid=%s)" % nid)


def run():
    monitor, player = xbmc.Monitor(), Player()
    # tick() must run on this loop: the buttons' callbacks fire while it
    # waits in waitForAbort.
    while not monitor.waitForAbort(1):
        if player.current and player.skip:
            try:
                if player.isPlayingVideo():
                    player.skip.tick(player.getTime(), time.time())
            except RuntimeError:
                pass


if __name__ == "__main__":
    run()
