"""Background service: the licence relay, and telling Starz how a playback went.

* lib/license_relay.py: localhost relay ISA sends licence requests to.

The plugin process ends as soon as playback starts, so a service is the only
place that can still talk to Starz while a title plays and when it stops:

* Play/update every 30 s, as the website does (keep-alive HAR), with the
  current position.
* Play/stop with the final position when playback ends -- this is what puts
  the resume point into Continue Watching on Starz's own apps, and closes the
  session rather than leaving it to look like it is still playing.

default.py writes the session (content id, playbackSessionId, manifest URL)
to PLAYING_FILE just before resolving the stream; the service only reports
for a playing file that matches it, so other addons' playback is ignored.
"""

import threading

import xbmc

from lib import kodiutils
from lib.api import StarzApi, PLAYING_FILE
from lib.auth import StarzAuth
from lib.license_relay import LicenseRelay
from lib import upnext
from lib.api import to_item

# The website reports every 20-30 s (keep-alive HAR), well inside the 600 s
# keep-alive Starz gives; doing the same keeps the resume point current.
UPDATE_SECS = 30
POLL_SECS = 1


class Session(object):
    def __init__(self, info):
        self.info = info
        self.position = 0.0
        self.last_update = None   # set on the first tick: the start
        self.next_id = None       # the next episode, once looked up


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
        if info.get("live"):
            # Live: no position to save, no next episode, nothing to seek.
            # The live capture shows no Play/update; none are sent.
            kodiutils.log("Playing the STARZ live stream (no progress reports)")
            self.session = None
            return
        self.session = Session(info)
        kodiutils.log("Tracking Starz playback of %s" % info.get("content_id"))
        # Off the callback thread: two metadata requests.
        threading.Thread(target=self._prepare_next, args=(self.session,)).start()
        if info.get("seek_to"):
            # Tuning in to a channel: jump to where its schedule has reached.
            try:
                self.seekTime(float(info["seek_to"]))
                kodiutils.log("Tuned in at %ds" % int(info["seek_to"]))
            except RuntimeError as exc:
                kodiutils.log_error("Seek failed: %s" % exc)

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

    def _prepare_next(self, session):
        """For an episode: find the next one, hand both to Up Next if it is
        installed, and remember it for the built-in autoplay otherwise."""
        try:
            api = StarzApi(StarzAuth())
            current = api.title_detail(session.info["content_id"])
            if current.get("contentType") != "Episode" or not current.get("nextContentId"):
                return
            nxt = api.title_detail(current["nextContentId"])
            cur_item, next_item = to_item(current), to_item(nxt)
            if not cur_item or not next_item:
                return
            session.next_id = next_item["id"]
            if upnext.installed():
                upnext.signal(upnext.build(cur_item, current, next_item, nxt))
                kodiutils.log("Up Next: next episode %s, pop-up at %ss" % (
                    next_item["id"], current.get("creditTimeIn")))
            else:
                kodiutils.log("Next episode %s (autoplay %s)" % (
                    next_item["id"], "on" if kodiutils.get_setting_bool("autoplay_next", True) else "off"))
        except Exception as exc:
            kodiutils.log_error("Next episode lookup failed: %s" % exc)

    def onPlayBackEnded(self):
        next_id = self.session.next_id if self.session else None
        self._finish()
        # Up Next plays the next episode itself; without it, do it here.
        if next_id and not upnext.installed() and kodiutils.get_setting_bool("autoplay_next", True):
            kodiutils.log("Autoplaying next episode %s" % next_id)
            xbmc.executebuiltin("PlayMedia(plugin://plugin.video.starz/?action=play&content_id=%s)"
                                % next_id)

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
