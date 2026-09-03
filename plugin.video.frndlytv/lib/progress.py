"""Reporting playback state back to Friendly TV.

The web player POSTs player state and position throughout playback, to a
host that is not the API:

    POST https://ace.api.yuppcdn.net/analytics/partner
    Content-Type: application/x-www-form-urlencoded
    data=<url-encoded JSON>

This is very probably what maintains Continue Watching, though no capture
proves it. Two things point that way: the first position the web player
reported for a title, ``46642``, is exactly the ``seekPositionInMillis`` that
``page/stream`` had just handed back for it, and the ``meta_id`` it sends ends
in the same content id that ``delete/continuewatch/content`` takes to remove
that title from the row. Nothing else in any capture writes progress -- there
is no bookmark or continue-watch *write* endpoint on ``revlet.net`` at all.

Without this a title watched in Kodi never reaches Continue Watching and never
gets a resume position, so that row is fed only by Friendly TV's own apps.

Nothing here is required for playback. Every failure is swallowed and logged.
"""

import json
import time
import uuid

from . import auth, kodiutils

ANALYTICS_URL = "https://ace.api.yuppcdn.net/analytics/partner"

# The POST carries **two** form fields, not one: the JSON under "data", and
# this. Leaving it out is refused outright --
#
#     HTTP 400  Request is missing required form field 'analytics_id'
#
# It is not a secret and not per-user: it is a constant in the web app's own
# configuration block, beside the API base paths and the Facebook and Google
# client ids, and it is the same value on every one of the eight captured
# events.
#
#     analyticsId:"d36bad5f857d14e3d4d4ca4b7055e179"
#
ANALYTICS_ID = "d36bad5f857d14e3d4d4ca4b7055e179"

# The capture's headers, which carry no credentials at all: the account is
# identified inside the payload by ``ui`` and ``bi``, not by a session header.
HEADERS = {
    "User-Agent": auth.USER_AGENT,
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": auth.ORIGIN,
}

# Event types, as observed. The capture's eight events were, in order:
#
#     et=1  ec=1  ps=idle       the session opening, with the long payload
#     et=2  ec=2  ps=idle       unknown; not sent
#     et=11 ec=3  ps=buffering
#     et=12 ec=4  ps=playing
#     et=7  ec=5  ps=playing    the first with a position on it
#     et=10 ec=6  ps=-1         unknown; carries ep as well as pp
#     et=13 ec=7  ps=paused
#     et=14 ec=8  ps=playing    resumed
#
# Only the ones whose meaning the capture actually shows are sent. Guessing a
# code here would be guessing at what the service records.
EV_START = 1
EV_BUFFERING = 11
EV_PLAYING = 12
EV_POSITION = 7
EV_PAUSED = 13
EV_RESUMED = 14

# **Not known:** which event says "stopped". The capture never stopped
# playback, so no stop code was ever seen. Stopping therefore sends a final
# position report rather than an invented code -- the position is the part
# Continue Watching needs, and a wrong event type could be recorded as
# something else entirely.
EV_STOP = EV_POSITION

# How often to report a position while playing. No capture establishes a
# cadence -- the web player sent one position event in the two captured
# minutes -- so this is chosen: often enough that stopping mid-episode leaves
# a useful resume point, rare enough to be nothing on a network.
POSITION_INTERVAL = 30

# Constants copied from the capture. The addon presents itself as the web
# player everywhere else -- the same User-Agent, the same Origin, the same
# device id on the stream request -- and doing it inconsistently here would
# file these events under a client that does not exist.
DEVICE_TYPE = "web"
DEVICE_ID = "5"
DEVICE_CLIENT = "firefox"
PARTNER = "frndlytv"
APP_VERSION = "v13.53"
PLAYER_NAME = "bitmovin"
PLAYER_VERSION = "8.179.0"
SCHEMA = "v2"

# Unset numeric fields go out as -1 in the capture rather than being omitted.
UNSET = -1


def enabled():
    return kodiutils.get_setting_bool("report_progress", True)


class Reporter(object):
    """One play's worth of events.

    Built from the context the plugin wrote when it resolved the stream, so
    the service can report without repeating the stream call.
    """

    def __init__(self, context, session=None):
        info = context.get("analytics") or {}
        self.meta_id = info.get("meta_id") or ""
        self.custom_data = info.get("custom_data") or ""
        self.stream_url = info.get("stream_url") or ""
        self.total_ms = int(info.get("total_ms") or 0)

        session = session or auth.Session()
        self.user_id = _int(session.user_id)
        self.box_id = session.box_id

        # psk is the play-session key: the capture holds it constant for every
        # event of one play, at the epoch ms the play began. sk is a uuid the
        # client makes up; no capture shows it having to match anything, so it
        # is generated per play.
        self.play_key = int(time.time() * 1000)
        self.session_key = str(uuid.uuid4())
        self.count = 0
        self._explained = False

    @property
    def usable(self):
        """Whether there is enough to report anything worth recording."""
        return bool(self.meta_id and self.user_id)

    def _payload(self, event, state, position_ms, total_ms):
        self.count += 1
        now = int(time.time() * 1000)
        body = {
            "a1": self.custom_data,
            "a2": UNSET,
            "at": UNSET,
            "av": SCHEMA,
            "bi": self.box_id,
            "br": UNSET,
            "dc": DEVICE_CLIENT,
            "di": DEVICE_ID,
            "dt": DEVICE_TYPE,
            "ec": self.count,
            "em": UNSET,
            "ep": UNSET,
            "et": event,
            "meta_id": self.meta_id,
            "meta_map": UNSET,
            "pdn": PARTNER,
            "pid": UNSET,
            "pp": position_ms if position_ms is not None else UNSET,
            "ps": state if state is not None else UNSET,
            "psk": self.play_key,
            "sk": self.session_key,
            "sp": UNSET,
            "ts": now,
            "tvl": total_ms or self.total_ms or UNSET,
            "ui": self.user_id,
        }
        if event == EV_START:
            # The opening event carries thirteen more fields describing the
            # device and the stream. "ip" is one of them and is deliberately
            # left out: the addon does not know this box's public address and
            # is not going to ask a third party for it, and the receiving end
            # sees the source address anyway.
            body.update({
                "ap": False,
                "appv": APP_VERSION,
                "cdn": "Widevine",
                "cnt": UNSET,
                "dos": kodiutils.os_name(),
                "dosv": kodiutils.os_version(),
                "is": "1",
                "nf": "home",
                "np": UNSET,
                "pln": PLAYER_NAME,
                "plv": PLAYER_VERSION,
                "su": self.stream_url,
            })
        return body

    def send(self, event, state=None, position_ms=None, total_ms=0):
        """Post one event. Never raises: this is not part of playback."""
        if not self.usable or not enabled():
            return False
        body = self._payload(event, state, position_ms, total_ms)
        try:
            import requests
            response = requests.post(ANALYTICS_URL,
                                     data={"data": json.dumps(body),
                                           "analytics_id": ANALYTICS_ID},
                                     headers=HEADERS, timeout=10)
            ok = response.status_code == 200
        except Exception as exc:
            kodiutils.log("could not report playback (%s): %s"
                          % (_name(event), exc))
            return False
        kodiutils.log("reported %s at %s of %s%s"
                      % (_name(event),
                         _hms(position_ms) if position_ms is not None
                         else "no position",
                         _hms(body["tvl"]) if body["tvl"] != UNSET
                         else "unknown",
                         "" if ok else "  (refused: HTTP %s)"
                         % response.status_code))
        if not ok:
            # A refusal with nothing said about it is not diagnosable, and the
            # three fields this addon does not copy verbatim -- ip omitted,
            # dos/dosv taken from Kodi -- are the obvious suspects. Say what
            # came back and what went out, once per play rather than per event.
            self._explain(response, body)
        return ok

    def _explain(self, response, body):
        if self._explained:
            return
        self._explained = True
        try:
            said = (response.text or "").strip()[:300]
        except Exception:
            said = "<no body>"
        kodiutils.log_error(
            "Friendly TV refused a playback report: HTTP %s %s\n"
            "sent %d fields; the ones this addon does not copy verbatim are "
            "dos=%r dosv=%r sk=%r, and ip is omitted"
            % (response.status_code, said or "<empty body>", len(body),
               body.get("dos"), body.get("dosv"), body.get("sk")))


_NAMES = {EV_START: "start", EV_BUFFERING: "buffering", EV_PLAYING: "playing",
          EV_POSITION: "position", EV_PAUSED: "paused", EV_RESUMED: "resumed"}


def _name(event):
    return _NAMES.get(event, "event %s" % event)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hms(milliseconds):
    seconds = int((milliseconds or 0) / 1000)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)
