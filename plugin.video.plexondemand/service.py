"""Background service: reports playback progress to Plex and offers the next
episode at the credits.

Capture-confirmed:
  GET {vod}/timeline?ratingKey&key&state&time&duration -> 200 (POST/PUT 404);
      the reported time is what userState / Continue Watching return later.
  PUT {vod}/actions/scrobble?ratingKey -> 200, userState viewState complete.
  Metadata?includeMarkers=1 -> Marker {type: credits, startTimeOffset, final}
      on ~22% of movies and no episodes (survey); no intro markers seen. The
      next episode comes from a continuous playQueue (playback.py).
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
HANDOFF_MAX_AGE = 300   # seconds between playback.build() and the video starting

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
        self.next_started = False
        self.paused = False
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
        # Reaching the credits counts as watched; titles without a credits
        # marker (most of them, see README) fall back to 90 % of the runtime.
        credits = cur.get("credits")
        done = (credits and self.last_time >= credits) or (duration and self.last_time >= duration * WATCHED_AT)
        if done and kodiutils.get_setting_bool("report_progress", True):
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
        self.current = self._claim(json.loads(raw)) if raw else None
        self.next_started = False
        self.paused = False
        if not self.current:
            if self.skip:  # another add-on's video: no button left from an earlier title
                self.skip.reset()
            return
        self.tick()
        self._report("playing")
        if self.skip:
            marks = {}
            # Only Plex's own credits marker starts the countdown (user's choice:
            # no guessed fallback). Without one, the next episode autoplays at
            # the end instead (onPlayBackEnded).
            credits = self.current.get("credits")
            if credits:
                marks["credits"] = credits
            if self.current.get("intro"):
                marks["intro"] = tuple(self.current["intro"])
            self.skip.reset(marks, has_next=bool(self.current.get("next")))

    def periodic(self):
        """The regular timeline report, with the state Plex should see."""
        if time.time() - self.last_report >= INTERVAL:
            self._report("paused" if self.paused else "playing")

    def _claim(self, handoff):
        """The handed-over title, only if it is what has just started: set by
        playback.build() moments ago, and this stream or this title's plugin
        URL is playing. A stale one (its playback never started) is dropped,
        so an unrelated video is never reported, scrobbled or counted down."""
        if time.time() - float(handoff.get("at") or 0) > HANDOFF_MAX_AGE:
            kodiutils.log("dropping a stale playing-title handoff")
            return None
        try:
            playing = self.getPlayingFile()
        except RuntimeError:
            return handoff
        path = (handoff.get("path") or "").split("?")[0]
        rk = handoff.get("ratingKey") or ""
        if (path and path in playing) or (rk and "rk=%s" % rk in playing):
            return handoff
        kodiutils.log("playing file is not the handed-over title; ignoring it")
        return None

    def onPlayBackPaused(self):
        self.tick()
        self.paused = True
        self._report("paused")

    def onPlayBackResumed(self):
        self.paused = False
        self._report("playing")

    def onPlayBackStopped(self):
        self._finish()

    def onPlayBackEnded(self):
        # Autoplay at the end whenever there is a next episode, including after
        # "Watch credits" or a dismissed countdown; but not when the countdown
        # already started it, or it would play twice.
        nxt = (self.current or {}).get("next")
        started = self.next_started
        self._finish(ended=True)
        self.next_started = False
        if nxt and not started and kodiutils.get_setting_bool("autoplay_next", True):
            self._play(nxt)

    def onPlayBackError(self):
        self.current = None
        # A title that failed to start must not be picked up by the next video.
        xbmcgui.Window(10000).clearProperty(PROP)
        if self.skip:
            self.skip.reset()

    # ---- next episode
    def play_next(self):
        nxt = (self.current or {}).get("next")
        if nxt:
            self.next_started = True
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
        player.periodic()
    # Let go of the Player (and the Skip Buttons dialog it holds) before the
    # interpreter ends, or Kodi logs "left several classes in memory".
    if player.skip:
        player.skip.reset()
    player.skip = None
    del player
    del monitor


if __name__ == "__main__":
    run()
