"""Reports playback progress to Plex (/timeline) so Continue Watching and
resume points follow the account.

Capture-confirmed: GET {vod}/timeline?ratingKey&key&state&time&duration
answers 200 {playbackState, viewOffset}; POST/PUT answer 404.
"""

import json
import time

import xbmc
import xbmcgui

from lib import kodiutils
from lib.api import PlexVodApi

PROP = "plexondemand.playing"
INTERVAL = 30


class Player(xbmc.Player):

    def __init__(self):
        super(Player, self).__init__()
        self.current = None
        self.last_report = 0
        self.last_time = 0

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

    def onAVStarted(self):
        raw = xbmcgui.Window(10000).getProperty(PROP)
        xbmcgui.Window(10000).clearProperty(PROP)
        self.current = json.loads(raw) if raw else None
        if self.current:
            self.tick()
            self._report("playing")

    def tick(self):
        try:
            if self.isPlaying():
                self.last_time = self.getTime()
        except RuntimeError:
            pass

    def onPlayBackPaused(self):
        self.tick()
        self._report("paused")

    def onPlayBackResumed(self):
        self._report("playing")

    def onPlayBackStopped(self):
        self._report("stopped")
        self.current = None

    def onPlayBackEnded(self):
        if self.current:
            self.last_time = (self.current.get("duration") or 0) / 1000.0
        self.onPlayBackStopped()


def run():
    monitor = xbmc.Monitor()
    player = Player()
    while not monitor.abortRequested():
        if monitor.waitForAbort(5):
            break
        if player.current:
            player.tick()
            if time.time() - player.last_report >= INTERVAL:
                player._report("playing")


if __name__ == "__main__":
    run()
