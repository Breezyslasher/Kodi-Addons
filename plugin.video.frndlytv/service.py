"""Background service: give the stream slot back when playback stops.

Friendly TV counts concurrent streams per account and a slot stays taken
until something posts its poll key to ``stream/session/end``. The plugin
process exits as soon as it hands a url to Kodi, so it cannot be the thing
that does that -- it is already gone by the time the viewer stops watching.
This is.

Without it an account locks itself out of its own subscription after a few
plays, with "you are watching on too many devices" and no device actually
watching.
"""

import time

import xbmc

from lib import api, auth, kodiutils, playback, progress

# How long to let the player settle before believing its clock,
# and how many times to look. Kept short: this is a log line, and
# nothing else waits on it.
WHERE_TRIES = 5
WHERE_WAIT = 1


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        self._playing_key = ""
        self._reporter = None
        self._paused = False
        self._last_position = None
        self._next_report = 0.0

    def run(self):
        kodiutils.log("service starting; %s" % kodiutils.platform())
        player = xbmc.Player()
        while not self.abortRequested():
            try:
                self._tick(player)
            except Exception as exc:
                # A raise here would end the service for the rest of the
                # Kodi session, and with it every future slot release.
                kodiutils.log_error("service tick failed: %s" % exc)
            if self.waitForAbort(2):
                break
        # Kodi closing mid-stream still leaves a slot held.
        self._release()
        kodiutils.log("service stopping")

    def _tick(self, player):
        if player.isPlayingVideo():
            if not self._playing_key:
                context = kodiutils.read_json(playback.CONTEXT_FILE,
                                              default={}) or {}
                key = context.get("poll_key") or ""
                if key:
                    self._playing_key = key
                    kodiutils.log("holding a stream slot for %s"
                                  % (context.get("path") or "?"))
                    self._begin_reporting(context)
                    self._report_position(player, context)
            else:
                self._follow(player)
        elif self._playing_key:
            self._release()

    # -- reporting progress ------------------------------------------------

    def _begin_reporting(self, context):
        """Open a reporting session for the play that has just started."""
        self._reporter = None
        self._paused = False
        self._last_position = None
        self._next_report = 0.0
        if not progress.enabled():
            return
        try:
            reporter = progress.Reporter(context)
        except Exception as exc:
            kodiutils.log("could not start progress reporting: %s" % exc)
            return
        if not reporter.usable:
            kodiutils.log("nothing to report progress against for %s "
                          "(no analytics id or no user id)"
                          % (context.get("path") or "?"))
            return
        self._reporter = reporter
        reporter.send(progress.EV_START, state="idle")
        reporter.send(progress.EV_BUFFERING, state="buffering")
        reporter.send(progress.EV_PLAYING, state="playing")

    def _follow(self, player):
        """Report pauses, resumes, and where playback has got to.

        Position is only reported once the running time can contain it, for
        the same reason `_report_position` waits: asked too early, a
        multi-period manifest answers with a number that is not a position.
        """
        if not self._reporter:
            return
        where, total = self._clock(player)
        if where is None:
            return
        self._last_position = where

        paused = _is_paused()
        if paused != self._paused:
            self._paused = paused
            self._reporter.send(
                progress.EV_PAUSED if paused else progress.EV_RESUMED,
                state="paused" if paused else "playing",
                position_ms=where, total_ms=total)
            self._next_report = time.time() + progress.POSITION_INTERVAL
            return

        if paused:
            return
        now = time.time()
        if now >= self._next_report:
            self._next_report = now + progress.POSITION_INTERVAL
            self._reporter.send(progress.EV_POSITION, state="playing",
                                position_ms=where, total_ms=total)

    def _end_reporting(self):
        """Playback stopped, and where it got to.

        The service's own stop event, captured by leaving a video with the
        back button: the player goes back to "idle" and reports the position
        it left off at.
        """
        reporter, self._reporter = self._reporter, None
        if not reporter or self._last_position is None:
            return
        reporter.send(progress.EV_STOPPED, state="idle",
                      position_ms=self._last_position)

    @staticmethod
    def _clock(player):
        """(position_ms, total_ms), or (None, 0) while the answer is nonsense."""
        try:
            where, total = player.getTime(), player.getTotalTime()
        except Exception:
            return None, 0
        if total <= 0 or not 0 <= where <= total:
            return None, 0
        return int(where * 1000), int(total * 1000)

    def _report_position(self, player, context):
        """Say where playback actually began, once it is under way.

        The addon can log what it *asked* for, but not what ISA did with it,
        and for a start-over those are exactly the thing in question: a live
        manifest opened at its window start looks identical, from the plugin
        side, to one opened at the live edge. The player knows. On a live
        stream the position is measured within the timeshift window, so a
        start-over that worked reads near zero and one that did not reads
        near the window's length.
        """
        # The slot is claimed the moment Kodi says it is playing video, which
        # is before the demuxer has settled: asked then, a multi-period DASH
        # answered getTime() with 384109 seconds, and the log read "playback
        # began at 106:41:49 of 0:50:03". So the answer is used only once it
        # is one the running time can contain.
        for attempt in range(WHERE_TRIES):
            if attempt and self.waitForAbort(WHERE_WAIT):
                return
            try:
                where, total = player.getTime(), player.getTotalTime()
            except Exception as exc:
                kodiutils.log("could not read the play position: %s" % exc)
                return
            if total > 0 and 0 <= where <= total:
                kodiutils.log("playback began at %s of %s%s"
                              % (_hms(where), _hms(total),
                                 "  (start over)" if context.get("from_start")
                                 else ""))
                return
        kodiutils.log("the player did not report a usable position "
                      "(%s of %s); not reporting one" % (where, total))

    def _release(self):
        self._end_reporting()
        key, self._playing_key = self._playing_key, ""
        if not key:
            return
        try:
            api.Api().end_session(key)
        except (api.ApiError, auth.AuthError) as exc:
            # Nothing to retry against: the session may already have been
            # reaped server-side, and a slot left behind times out on its own.
            kodiutils.log("could not release the stream slot: %s" % exc)
        kodiutils.delete_file(playback.CONTEXT_FILE)


def _is_paused():
    try:
        return bool(xbmc.getCondVisibility("Player.Paused"))
    except Exception:
        return False


def _hms(seconds):
    seconds = int(seconds or 0)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


if __name__ == "__main__":
    Service().run()
