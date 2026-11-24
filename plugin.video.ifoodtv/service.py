"""Background service: Skip intro and the next-episode countdown
(script.module.skipbuttons), for titles this add-on plays.

What the markers are (confirmed live, docs/recon.md "Skip markers"): every
title record carries intro_st / intro_et / endcredit_st / endcredit_et in
seconds; 0 means unset. On iFood.tv only the shows and movies have them
(credits on about a third, intro rarely); recipe clips have none.
default.py hands this service the title and its markers through a home-window
property just before playback starts, then the next episode's node_id through
a second one once it has looked it up (after playback started, so a long
show never delays it); nothing here touches the network.

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
NEXT_PROP = "ifoodtv.next"

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
        self.poll_next()

    def poll_next(self):
        """Pick up the next episode once default.py has looked it up (it does
        so after playback started). Matched by nid, so a stale answer for an
        earlier title is never used."""
        cur = self.current
        if not cur or not cur.get("next_pending"):
            return
        raw = xbmcgui.Window(10000).getProperty(NEXT_PROP)
        if not raw:
            return
        try:
            got = json.loads(raw)
        except ValueError:
            got = {}
        if str(got.get("nid")) != str(cur.get("nid")):
            return
        xbmcgui.Window(10000).clearProperty(NEXT_PROP)
        cur["next_pending"] = False
        cur["next"] = got.get("next")
        if self.skip:
            self.skip.has_next = bool(cur["next"])

    def onPlayBackEnded(self):
        # Also after "Watch credits" or a dismissed countdown -- but not when
        # the countdown already started the next one, or it would play twice.
        self.poll_next()
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
        if player.current:
            player.poll_next()
        if player.current and player.skip:
            try:
                if player.isPlayingVideo():
                    player.skip.tick(player.getTime(), time.time())
            except RuntimeError:
                pass


if __name__ == "__main__":
    run()
