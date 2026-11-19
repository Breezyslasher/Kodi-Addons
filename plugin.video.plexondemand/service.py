"""Background service: reports playback progress to Plex and offers the next
episode at the credits.

Capture-confirmed:
  GET {vod}/timeline?ratingKey&key&state&time&duration -> 200 (POST/PUT 404);
      the reported time is what userState / Continue Watching return later.
  PUT {vod}/actions/scrobble?ratingKey -> 200, userState viewState complete.
  Metadata?includeMarkers=1 -> Marker {type: credits, startTimeOffset} on some
      titles; the next episode comes from a continuous playQueue (playback.py).
"""

import json
import time

import xbmc
import xbmcgui

from lib import kodiutils
from lib.api import PlexVodApi

PROP = "plexondemand.playing"
INTERVAL = 30
WATCHED_AT = 0.90       # past this share of the runtime, a stop counts as watched
CREDITS_FALLBACK = 25   # seconds before the end, when an episode has no marker

try:
    from skipbuttons import SkipController
except ImportError:  # optional dependency
    SkipController = None


class Player(xbmc.Player):

    def __init__(self):
        super(Player, self).__init__()
        self.current = None
        self.last_report = 0
        self.last_time = 0
        self.skip = None
        if SkipController:
            self.skip = SkipController(self, play_next=self.play_next,
                                       autoplay=lambda: kodiutils.get_setting_bool("autoplay_next", True))

    # ---- reporting
    def _report(self, state):
        if not self.current or not kodiutils.get_setting_bool("report_progress", True):
            return
        try:
            PlexVodApi()._get("/timeline", {
                "ratingKey": self.current["ratingKey"], "key": self.current["key"],
                "state": state, "time": int(self.last_time * 1000),
                "duration": int(self.current.get("duration") or 0)})
        except Exception as exc:
            kodiutils.log("timeline %s failed: %s" % (state, exc))
        self.last_report = time.time()

    def _finish(self, ended=False):
        """Final report; mark watched when the end (or nearly) was reached."""
        cur = self.current
        if not cur:
            return
        duration = (cur.get("duration") or 0) / 1000.0
        if ended and duration:
            self.last_time = duration
        self._report("stopped")
        if duration and self.last_time >= duration * WATCHED_AT \
                and kodiutils.get_setting_bool("report_progress", True):
            try:
                PlexVodApi().action("scrobble", cur["ratingKey"])
            except Exception as exc:
                kodiutils.log("scrobble failed: %s" % exc)
        self.current = None
        if self.skip:
            self.skip.reset()

    def tick(self):
        try:
            if self.isPlaying():
                self.last_time = self.getTime()
        except RuntimeError:
            pass

    # ---- player events
    def onAVStarted(self):
        if self.current:  # a new item replaced ours without a stop event
            self._finish()
        raw = xbmcgui.Window(10000).getProperty(PROP)
        xbmcgui.Window(10000).clearProperty(PROP)
        self.current = json.loads(raw) if raw else None
        if not self.current:
            return
        self.tick()
        self._report("playing")
        if self.skip:
            marks = {}
            credits = self.current.get("credits")
            duration = (self.current.get("duration") or 0) / 1000.0
            if not credits and self.current.get("next") and duration > CREDITS_FALLBACK * 4:
                credits = duration - CREDITS_FALLBACK
            if credits:
                marks["credits"] = credits
            self.skip.reset(marks, has_next=bool(self.current.get("next")))

    def onPlayBackPaused(self):
        self.tick()
        self._report("paused")

    def onPlayBackResumed(self):
        self._report("playing")

    def onPlayBackStopped(self):
        self._finish()

    def onPlayBackEnded(self):
        nxt = (self.current or {}).get("next")
        self._finish(ended=True)
        if nxt and not self.skip and kodiutils.get_setting_bool("autoplay_next", True):
            self._play(nxt)

    def onPlayBackError(self):
        self.current = None
        if self.skip:
            self.skip.reset()

    # ---- next episode
    def play_next(self):
        nxt = (self.current or {}).get("next")
        if nxt:
            self._play(nxt)

    @staticmethod
    def _play(rating_key):
        xbmc.executebuiltin("PlayMedia(plugin://plugin.video.plexondemand/?action=play&rk=%s)" % rating_key)


def run():
    monitor = xbmc.Monitor()
    player = Player()
    while not monitor.abortRequested():
        if monitor.waitForAbort(1):
            break
        if not player.current:
            continue
        player.tick()
        if player.skip:
            try:
                if player.isPlayingVideo():
                    player.skip.tick(player.getTime(), time.time())
            except RuntimeError:
                pass
        if time.time() - player.last_report >= INTERVAL:
            player._report("playing")


if __name__ == "__main__":
    run()
