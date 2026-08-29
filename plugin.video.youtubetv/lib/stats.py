"""Reporting playback position back to YouTube.

Watching a title through the addon used to leave no trace on the account: the
web UI still showed it unstarted, "Continue watching" never moved, and every
play began at zero. The only thing the addon sent during playback was
``player/heartbeat``, which is an entitlement check and carries no position.

The web player does it with POSTs carrying an empty body -- everything is in
the query string, and success is a 204. Captured over two minutes of on-demand
playback on 2026-08-28 03:10::

    03:09:38 /api/stats/playback  cmt=797.003                   len=1442.842
    03:09:40 /api/stats/watchtime cmt=799.006 st=797.003        et=799.006  state=playing
    03:09:50 /api/stats/watchtime cmt=808.996 st=799.006,805.233,808.915 et=805.233,808.915,808.996
    03:10:00 /api/stats/watchtime cmt=818.98  st=808.996        et=818.98   state=playing
    03:10:15 /api/stats/watchtime cmt=833.989 st=818.98         et=833.989  state=playing
    03:10:43 /api/stats/watchtime cmt=862.091 st=833.989        et=862.091  state=paused

``cmt`` is where the playhead is. ``st``/``et`` are the spans watched since
the last report -- a comma separated list when playback was not contiguous,
which is how a seek is expressed. ``state`` is playing or paused.

None of the signed parts are ours to invent. The player response hands over
``playbackTracking`` with a ``videostatsPlaybackUrl`` and a
``videostatsWatchtimeUrl`` whose query already carries ``docid``, ``ei``,
``of``, ``osid``, ``plid``, ``upt``, ``vm``, ``cl`` and ``len``; the client
appends the playback state to that base rather than building a url, and sends
it to tv.youtube.com even though the base names s.youtube.com. The same
object states the cadence: ``videostatsDefaultFlushIntervalSeconds`` 40 and
``videostatsScheduledFlushWalltimeSeconds`` [10, 20, 30], which is the 4/14/24
/39 seen above once the first report at start is counted.

Only the fields the capture shows are sent, and only ones we can answer
honestly. ``fmt``/``afmt`` are the itags in use, which InputStream Adaptive
chooses and never tells the addon, so they are left out rather than made up.
"""

import time

try:
    from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
except ImportError:  # pragma: no cover - Python 2 never runs this addon
    from urllib import urlencode
    from urlparse import urlparse, parse_qsl, urlunparse

from . import api, kodiutils

# The web player sends ver=2 on both endpoints in every capture.
PROTOCOL_VERSION = "2"

DEFAULT_FLUSH_SECONDS = 40
DEFAULT_FLUSH_WALLTIMES = (10, 20, 30)


def _merge(base_url, params):
    """Add params to a url that already has a signed query."""
    parts = urlparse(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    fresh = set(params)
    query = [(k, v) for k, v in query if k not in fresh]
    query.extend((k, v) for k, v in params.items() if v is not None)
    return urlunparse(parts._replace(query=urlencode(query)))


def _client_params():
    """Who is reporting. The same identity the rest of the addon claims."""
    return {
        "c": api.CLIENT_NAME,
        "cver": api.CLIENT_VERSION,
        "cplayer": "UNIPLAYER",
        "cos": "X11",
        "cplatform": "DESKTOP",
        "hl": "en_US",
        "cr": "US",
    }


def _time(value):
    """Match the player's own formatting: three decimals, no exponent."""
    return "%.3f" % float(value)


class Reporter(object):
    """One playing title's report stream.

    Keeps the spans watched since the last flush so ``st``/``et`` describe
    what was actually seen rather than a straight line from first to last
    position -- that is the difference between YouTube recording a seek and
    recording ten minutes nobody watched.
    """

    def __init__(self, tracking, video_id, cpn, duration=0.0, client=None):
        self._client = client
        self.video_id = video_id
        self.cpn = cpn
        self.duration = float(duration or 0.0)
        self._tracking = tracking or {}
        self._started = time.time()
        self._spans = []
        self._span_start = None
        self._last_position = None
        self._last_flush = 0.0
        self._flush_index = 0
        self._sent_playback = False
        self._finished = False

        try:
            self._interval = float(self._tracking.get(
                "videostatsDefaultFlushIntervalSeconds")
                or DEFAULT_FLUSH_SECONDS)
        except (TypeError, ValueError):
            self._interval = DEFAULT_FLUSH_SECONDS
        walltimes = self._tracking.get(
            "videostatsScheduledFlushWalltimeSeconds")
        try:
            self._walltimes = sorted(float(x) for x in walltimes)
        except (TypeError, ValueError):
            self._walltimes = list(DEFAULT_FLUSH_WALLTIMES)

    # -- what we can report ------------------------------------------------

    def _url(self, key):
        entry = self._tracking.get(key) or {}
        return entry.get("baseUrl") or ""

    @property
    def usable(self):
        return bool(self._url("videostatsWatchtimeUrl") and self.cpn)

    # -- the position stream -----------------------------------------------

    def observe(self, position, playing):
        """Take a reading of the playhead. Called on every service tick."""
        position = float(position)
        if self._span_start is None:
            self._span_start = position
        elif self._last_position is not None and playing:
            # A jump means a seek: close the span here and start a new one, so
            # the pair of lists says what was watched and what was skipped.
            if abs(position - self._last_position) > SEEK_TOLERANCE_SECONDS:
                self._spans.append((self._span_start, self._last_position))
                self._span_start = position
        self._last_position = position

    def due(self):
        """Whether it is time to flush, on the player's own cadence."""
        if not self._sent_playback:
            return True
        elapsed = time.time() - self._started
        if self._flush_index < len(self._walltimes):
            return elapsed >= self._walltimes[self._flush_index]
        return (time.time() - self._last_flush) >= self._interval

    def flush(self, state="playing", final=False):
        """Send one report covering everything since the last one.

        The cadence only advances when a report actually goes out, so a flush
        with nothing watched yet -- the one at the moment playback starts --
        does not spend the first scheduled walltime on an empty report.
        """
        if self._last_position is None or self._finished:
            return
        if not self._sent_playback:
            self._send_playback()
        if self._send_watchtime(state, final):
            self._last_flush = time.time()
            if self._flush_index < len(self._walltimes):
                self._flush_index += 1
        if final:
            self._finished = True

    # -- the two requests --------------------------------------------------

    def _send_playback(self):
        """The one-off ping that opens the report stream.

        The web player sends it once, before any watchtime, carrying the
        position it is starting from -- which for a resumed title is the
        resume point rather than zero.
        """
        url = self._url("videostatsPlaybackUrl")
        self._sent_playback = True
        if not url:
            return
        params = _client_params()
        params.update({
            "cpn": self.cpn,
            "ver": PROTOCOL_VERSION,
            "cmt": _time(self._last_position),
            "rt": _time(time.time() - self._started),
            "fs": "0",
            "volume": "100",
            "muted": "0",
        })
        self._post(_merge(url, params), "playback")

    def _send_watchtime(self, state, final):
        """Report the spans watched since the last one. True if one went out.

        A span of no length is not a span. They turn up whenever two readings
        in a row jump -- scrubbing through a title does exactly that -- and
        reporting them would tell YouTube an instant was watched.
        """
        url = self._url("videostatsWatchtimeUrl")
        if not url:
            return False
        spans = list(self._spans)
        if self._span_start is not None:
            spans.append((self._span_start, self._last_position))
        spans = [(s, e) for s, e in spans if e - s > MIN_SPAN_SECONDS]
        if not spans:
            if not final:
                # Nothing watched since the last report: say nothing, and keep
                # the accumulator so the next flush covers this stretch too.
                return False
            spans = [(self._last_position, self._last_position)]
        self._spans = []
        self._span_start = self._last_position

        params = _client_params()
        params.update({
            "cpn": self.cpn,
            "ver": PROTOCOL_VERSION,
            "cmt": _time(self._last_position),
            "st": ",".join(_time(s) for s, _ in spans),
            "et": ",".join(_time(e) for _, e in spans),
            "state": state,
            "rt": _time(time.time() - self._started),
            "fs": "0",
            "volume": "100",
            "muted": "0",
        })
        if final:
            # The player marks the last report of a session so the server
            # stops expecting more rather than timing the session out.
            params["final"] = "1"
        self._post(_merge(url, params), "watchtime")
        return True

    def _post(self, url, what):
        if self._client is None:
            return
        try:
            status = self._client.report(url)
        except Exception as exc:
            kodiutils.log_error("%s report failed: %s" % (what, exc))
            return
        # 204 is what the capture shows for a report YouTube accepted.
        log = kodiutils.log if status in (200, 204) else kodiutils.log_error
        log("%s reported %s at %.1fs -> HTTP %d"
            % (what, self.video_id, self._last_position or 0.0, status))


# A tick is two seconds, so anything beyond a few seconds of movement between
# readings is a seek rather than ordinary playback.
SEEK_TOLERANCE_SECONDS = 5.0

# Below this a span is a rounding artifact rather than something watched.
MIN_SPAN_SECONDS = 0.05
