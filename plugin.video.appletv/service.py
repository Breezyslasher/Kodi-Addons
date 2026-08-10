"""Background service for the Kodi session.

Two jobs that must outlive the short-lived plugin process:

* the Widevine licence proxy, which InputStream Adaptive talks to over
  localhost throughout playback;
* watch-history reporting, which tells Apple where playback has reached so a
  title shows up in Continue Watching and resumes in the right place on
  Apple's own clients.
"""

import time

import xbmc

from lib import kodiutils
from lib.api import AppleTVApi, PLAYBACK_REPORT_CACHE, SEEK_CONTEXT
from lib.auth import AppleAuth
from lib.license_proxy import LicenseProxy

# How often to tell Apple where playback has reached. The web client posts
# far more often than this, but it is reporting analytics as well; for a
# resume point every half minute is plenty and stays light on the API.
REPORT_INTERVAL_SECS = 30
# Past this fraction the title counts as watched rather than resumable.
FINISHED_FRACTION = 0.95


class WatchHistory(object):
    """Reports the playing position to Apple while an addon stream plays."""

    def __init__(self):
        self._api = None
        self._token = None
        self._active = False
        self._last_report = 0
        self._last_position = 0
        self._duration = 0

    def _lazy_api(self):
        if self._api is None:
            self._api = AppleTVApi(AppleAuth())
        return self._api

    def start(self, player):
        """Mint a now-playing token for whatever the addon just started."""
        self.reset()
        context = kodiutils.read_json(PLAYBACK_REPORT_CACHE, default=None)
        if not context:
            return
        try:
            duration = player.getTotalTime()
        except Exception:
            duration = 0
        if not duration:
            duration = context.get("duration") or 0
        if not context.get("playable_passthrough"):
            return
        try:
            token = self._lazy_api().now_playing_token(context, duration)
        except Exception as exc:
            kodiutils.log_error("Could not mint now-playing token: %s" % exc)
            return
        if not token:
            return
        self._token = token
        self._duration = duration
        self._active = True
        self._last_report = time.monotonic()
        kodiutils.log("Watch history: reporting this stream to Apple")

    def tick(self, player):
        """Report periodically while playing."""
        if not self._active:
            return
        try:
            if not player.isPlayingVideo():
                return
            position = player.getTime()
            if not self._duration:
                self._duration = player.getTotalTime()
        except Exception:
            return
        self._last_position = position
        now = time.monotonic()
        if now - self._last_report < REPORT_INTERVAL_SECS:
            return
        self._last_report = now
        self._send(position, finished=False)

    def stop(self):
        """Report the final position, then forget this stream."""
        if not self._active:
            return
        finished = bool(self._duration) and \
            self._last_position >= self._duration * FINISHED_FRACTION
        self._send(self._last_position, finished=finished)
        self.reset()

    def _send(self, position, finished):
        try:
            self._lazy_api().report_now_playing(
                self._token, position, self._duration, finished)
        except Exception as exc:
            kodiutils.log_error("Watch history report failed: %s" % exc)

    def reset(self):
        self._token = None
        self._active = False
        self._last_report = 0
        self._last_position = 0
        self._duration = 0


class KeyPlaySeeker(object):
    """Seeks the player to a live game's key moments.

    The plugin leaves the offsets (seconds from the stream start) in
    seek_context.json. One moment means "jump here and play on" (a Key Plays
    pick); several means "catch up" -- play each in turn, skipping the lulls
    between them. Seeking live HLS is best-effort, so every step is guarded.
    """

    def __init__(self):
        self.reset()

    def start(self, player):
        self.reset()
        ctx = kodiutils.read_json(SEEK_CONTEXT, default=None)
        # One-shot: clear it so the next stream does not inherit this seek.
        kodiutils.write_json(SEEK_CONTEXT, {})
        segments = (ctx or {}).get("segments") or []
        if not segments:
            return
        self._segments = segments
        self._chain = bool((ctx or {}).get("chain"))
        self._index = 0
        kodiutils.log("Key play: %d moment(s), catch-up=%s"
                      % (len(segments), self._chain))
        self._seek(player, segments[0].get("start"))

    def tick(self, player):
        if not self._chain or self._index >= len(self._segments):
            return
        try:
            if not player.isPlayingVideo():
                return
            position = player.getTime()
        except Exception:
            return
        end = self._segments[self._index].get("end")
        if end is not None and position >= end:
            self._index += 1
            if self._index < len(self._segments):
                self._seek(player, self._segments[self._index].get("start"))
            else:
                # Caught up: let it play on from the last moment.
                self.reset()

    def _seek(self, player, seconds):
        if seconds is None:
            return
        try:
            player.seekTime(float(seconds))
            kodiutils.log("Key play: sought to %.1fs (player now at %.1fs)"
                          % (float(seconds), player.getTime()))
        except Exception as exc:
            kodiutils.log_error("Key play seek failed: %s" % exc)

    def reset(self):
        self._segments = []
        self._chain = False
        self._index = 0


class Player(xbmc.Player):
    def __init__(self, history, seeker):
        super(Player, self).__init__()
        self.history = history
        self.seeker = seeker

    def onAVStarted(self):
        # Only ours: every other player in Kodi is none of our business.
        try:
            if not self.getPlayingFile().startswith("http://127.0.0.1"):
                return
        except Exception:
            return
        self.history.start(self)
        self.seeker.start(self)

    def onPlayBackStopped(self):
        self.history.stop()
        self.seeker.reset()

    def onPlayBackEnded(self):
        self.history.stop()
        self.seeker.reset()


def main():
    proxy = LicenseProxy()
    try:
        proxy.start()
    except Exception as exc:
        kodiutils.log_error("Failed to start licence proxy: %s" % exc)
        return

    history = WatchHistory()
    seeker = KeyPlaySeeker()
    player = Player(history, seeker)

    # One second, not five: catching up hops between short clips, so the
    # position has to be checked often enough to jump at the right moment.
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        history.tick(player)
        seeker.tick(player)
        if monitor.waitForAbort(1):
            break
    history.stop()
    proxy.stop()
    kodiutils.log("License proxy stopped")


if __name__ == "__main__":
    main()
