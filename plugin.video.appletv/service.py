"""Background service for the Kodi session.

Two jobs that must outlive the short-lived plugin process:

* the Widevine licence proxy, which InputStream Adaptive talks to over
  localhost throughout playback;
* watch-history reporting, which tells Apple where playback has reached so a
  title shows up in Continue Watching and resumes in the right place on
  Apple's own clients.
"""

import threading
import time

import xbmc

from lib import kodiutils
from lib import license_proxy
from lib.api import AppleTVApi, PLAYBACK_REPORT_CACHE, SEEK_CONTEXT
from lib.auth import AppleAuth
from lib.license_proxy import LicenseProxy, release_leases

try:
    from skipbuttons import SkipController
except ImportError:
    # The shared module is a separate add-on; without it the skip buttons are
    # simply absent and everything else plays as before.
    SkipController = None

# How often to tell Apple where playback has reached. The web client posts
# far more often than this, but it is reporting analytics as well; for a
# resume point every half minute is plenty and stays light on the API.
REPORT_INTERVAL_SECS = 30
# Past this fraction the title counts as watched rather than resumable.
FINISHED_FRACTION = 0.95
# Below this many seconds a stop is not reported: a stop in the first moments
# often lands before the first tick recorded a real position, and reporting ~0
# would move Apple's resume point to the start and wipe it.
RESUME_MIN_SECS = 15


class WatchHistory(object):
    """Reports the playing position to Apple while an addon stream plays."""

    def __init__(self):
        self._api = None
        self._token = None
        self._adam_id = None
        self._item_type = None
        self._active = False
        self._last_report = 0
        self._last_position = 0
        self._duration = 0

    def _lazy_api(self):
        if self._api is None:
            self._api = AppleTVApi(AppleAuth())
        return self._api

    def start(self, player):
        """Set up position reporting for whatever the addon just started."""
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
        # An iTunes purchase reports its resume point to the MediaAPI
        # playback-positions endpoint (bearer + media-user-token), keyed by its
        # store id -- not the now-playing service, which it is not part of.
        if context.get("adam_id"):
            self._adam_id = context["adam_id"]
            self._item_type = context.get("item_type") or "Movie"
            self._duration = duration
            self._live = bool(context.get("live"))
            self._active = True
            self._last_report = time.monotonic()
            kodiutils.log("Watch history: reporting this purchase's position")
            return
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
        # A live game is never "finished" just because you are at the live edge
        # (position ~= the DVR length): leaving it must not mark the game
        # watched while it is still being played. Only its position is reported.
        self._live = bool(context.get("live"))
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
        finished = (not self._live) and bool(self._duration) and \
            self._last_position >= self._duration * FINISHED_FRACTION
        # A near-start stop (often before the first tick set a real position,
        # so _last_position is still 0) must not report ~0 and wipe an existing
        # resume point. Below the threshold, and not finished, just forget it.
        if self._last_position < RESUME_MIN_SECS and not finished:
            self.reset()
            return
        self._send(self._last_position, finished=finished)
        self.reset()

    def _send(self, position, finished):
        # A purchase saves its position to the MediaAPI, keyed by its store id.
        if self._adam_id:
            try:
                self._lazy_api().report_playback_position(
                    self._adam_id, self._item_type, position, finished)
            except Exception as exc:
                kodiutils.log_error("iTunes position report failed: %s" % exc)
            return
        try:
            self._lazy_api().report_now_playing(
                self._token, position, self._duration, finished)
        except Exception as exc:
            kodiutils.log_error("Watch history report failed: %s" % exc)

    def reset(self):
        # Drop the cached API/auth too: it is rebuilt per play from the current
        # session.json, so a sign-out or account switch between plays is picked
        # up instead of the old account being reused (and written back).
        self._api = None
        self._token = None
        self._adam_id = None
        self._item_type = None
        self._active = False
        self._live = False
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


def start_resume(player):
    """One-shot at playback start: skip Apple's intro ident bumper.

    The marker comes from the master playlist (license_proxy.start_markers()).
    If Kodi (or ISA) has already seeked past the pre-roll -- e.g. a Continue
    Watching item whose Kodi resume point put it mid-stream -- nothing is done,
    so this never fights Kodi's own resume.

    Resuming at Apple's watched.time is deliberately NOT done here. Kodi already
    resumes Continue Watching items from the resume point the plugin sets, and
    seeking to watched.time on top of that overrode an explicit "Play from
    beginning" -- which also starts at position 0, indistinguishable from a
    fresh play at this layer -- sending the viewer back to the middle.
    """
    m = license_proxy.start_markers()
    if not m:
        return
    pre = m.get("pre_roll_end") or 0.0
    if pre < 2:
        return
    try:
        pos = player.getTime()
    except Exception:
        return
    # Already resumed/seeked past the ident: leave it exactly as Kodi set it.
    if pos > pre + 2:
        return
    if pre > pos + 1:
        try:
            player.seekTime(float(pre))
            kodiutils.log("Start seek: skip pre-roll ident to %.1fs" % pre)
        except Exception as exc:
            kodiutils.log_error("Start seek failed: %s" % exc)


class SkipButtons(object):
    """Drives the Skip recap / Skip intro / Next episode buttons.

    The markers come from the master playlist Apple serves (parsed by the
    licence proxy into license_proxy.skip_markers()); the next episode is
    looked up from the show's episode list. Both reads are off the player's
    callback thread. A no-op when script.module.skipbuttons is not installed.
    """

    def __init__(self, player):
        self._ctl = None
        self._api = None
        self._next_url = None
        if SkipController is not None:
            try:
                self._ctl = SkipController(
                    player, play_next=self._play_next,
                    autoplay=lambda: kodiutils.get_setting_bool(
                        "autoplay_next", True))
            except Exception as exc:
                kodiutils.log_error("Skip buttons unavailable: %s" % exc)

    def start(self, player):
        if not self._ctl:
            return
        self._next_url = None
        marks = license_proxy.skip_markers()
        if not marks:
            self._ctl.reset()
            return
        ctx = kodiutils.read_json(PLAYBACK_REPORT_CACHE, default={}) or {}
        content_id = ctx.get("content_id")
        threading.Thread(target=self._prepare, args=(marks, content_id),
                         daemon=True).start()

    def _prepare(self, marks, content_id):
        has_next = False
        if content_id and kodiutils.get_setting_bool("autoplay_next", True):
            try:
                nxt = self._lazy_api().next_episode_id(content_id)
                if nxt:
                    self._next_url = (
                        "plugin://plugin.video.appletv/"
                        "?action=play&item_id=%s&item_type=Episode" % nxt)
                    has_next = True
            except Exception as exc:
                kodiutils.log("Skip buttons: next-episode lookup failed: %s" % exc)
        try:
            self._ctl.reset(marks, has_next=has_next,
                            credits=bool(marks.get("credits")))
            kodiutils.log("Skip buttons: marks=%s next=%s" % (marks, has_next))
        except Exception as exc:
            kodiutils.log_error("Skip buttons reset failed: %s" % exc)

    def tick(self, player):
        if not self._ctl:
            return
        try:
            if player.isPlayingVideo():
                self._ctl.tick(player.getTime(), time.time())
        except Exception:
            pass

    def reset(self):
        # Rebuild the API per play from the current session (see WatchHistory).
        self._api = None
        if self._ctl:
            try:
                self._ctl.reset()
            except Exception:
                pass

    def _play_next(self):
        if self._next_url:
            xbmc.executebuiltin("PlayMedia(%s)" % self._next_url)

    def _lazy_api(self):
        if self._api is None:
            self._api = AppleTVApi(AppleAuth())
        return self._api


class Player(xbmc.Player):
    def __init__(self, history, seeker):
        super(Player, self).__init__()
        self.history = history
        self.seeker = seeker
        self.skip = SkipButtons(self)

    def onAVStarted(self):
        # Only ours: every other player in Kodi is none of our business.
        try:
            if not self.getPlayingFile().startswith("http://127.0.0.1"):
                return
        except Exception:
            return
        self.history.start(self)
        self.seeker.start(self)
        start_resume(self)
        self.skip.start(self)

    def onPlayBackStopped(self):
        self.history.stop()
        self.seeker.reset()
        self.skip.reset()
        self._release_leases()

    def onPlayBackEnded(self):
        self.history.stop()
        self.seeker.reset()
        self.skip.reset()
        self._release_leases()

    @staticmethod
    def _release_leases():
        # Off the player's callback thread: releasing posts to Apple, and a
        # slow network must not hang Kodi when a video closes.
        threading.Thread(target=release_leases, daemon=True).start()


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
        player.skip.tick(player)
        if monitor.waitForAbort(1):
            break
    history.stop()
    # Kodi is quitting: a stream still playing gets no onPlayBackStopped whose
    # background release can finish, so release synchronously here with a short
    # timeout, which frees a live session left open when the app is closed.
    release_leases(timeout=4)
    proxy.stop()
    kodiutils.log("License proxy stopped")


if __name__ == "__main__":
    main()
