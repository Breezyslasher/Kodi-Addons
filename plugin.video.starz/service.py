"""Background service: the licence relay, and telling Starz how a playback went.

* lib/license_relay.py: localhost relay ISA sends licence requests to.

The plugin process ends as soon as playback starts, so a service is the only
place that can still talk to Starz while a title plays and when it stops:

* Play/update every few minutes, inside Starz's keepAliveSeconds (600 in the
  captures), with the current position. [VERIFY] the update body.
* Play/stop with the final position when playback ends -- this is what puts
  the resume point into Continue Watching on Starz's own apps, and closes the
  session rather than leaving it to look like it is still playing.

default.py writes the session (content id, playbackSessionId, manifest URL)
to PLAYING_FILE just before resolving the stream; the service only reports
for a playing file that matches it, so other addons' playback is ignored.
"""

import xbmc

from lib import kodiutils
from lib.api import StarzApi, PLAYING_FILE
from lib.auth import StarzAuth
from lib.license_relay import LicenseRelay

# Report well inside the 600 s keep-alive Starz gives.
UPDATE_SECS = 300
POLL_SECS = 1


class Session(object):
    def __init__(self, info):
        self.info = info
        self.position = 0.0
        self.last_update = None   # set on the first tick: the start


class Player(xbmc.Player):

    def __init__(self):
        xbmc.Player.__init__(self)
        self.session = None

    def onAVStarted(self):
        info = kodiutils.read_json(PLAYING_FILE, default=None)
        if not info:
            return
        try:
            playing = self.getPlayingFile()
        except RuntimeError:
            return
        if info.get("manifest_url") and info["manifest_url"] not in playing:
            return
        kodiutils.delete_file(PLAYING_FILE)
        self._finish()   # a previous title that never reported stopping
        self.session = Session(info)
        kodiutils.log("Tracking Starz playback of %s" % info.get("content_id"))

    def tick(self, now):
        """Called every POLL_SECS by the service loop while a session runs."""
        if not self.session:
            return
        try:
            if self.isPlayingVideo():
                self.session.position = self.getTime()
        except RuntimeError:
            return
        if self.session.last_update is None:
            self.session.last_update = now
        elif now - self.session.last_update >= UPDATE_SECS:
            self._report("update")
            self.session.last_update = now

    def _report(self, kind):
        s = self.session
        if not s or not s.info.get("session_id"):
            return
        try:
            StarzApi(StarzAuth()).report_play(kind, s.info["content_id"],
                                              s.info["session_id"], s.position)
            kodiutils.log("Play/%s sent at %ds" % (kind, int(s.position)))
        except Exception as exc:
            kodiutils.log_error("Play/%s failed: %s" % (kind, exc))

    def _finish(self):
        if self.session:
            self._report("stop")
            self.session = None

    def onPlayBackStopped(self):
        self._finish()

    def onPlayBackEnded(self):
        self._finish()

    def onPlayBackError(self):
        self._finish()


def run():
    monitor = xbmc.Monitor()
    relay = LicenseRelay()
    relay.start()
    player = Player()
    clock = 0.0
    try:
        while not monitor.abortRequested():
            if monitor.waitForAbort(POLL_SECS):
                break
            clock += POLL_SECS
            player.tick(clock)
        player._finish()
    finally:
        relay.stop()


if __name__ == "__main__":
    run()
