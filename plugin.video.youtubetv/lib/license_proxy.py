"""Local Widevine licence proxy for YouTube's JSON-wrapped exchange.

InputStream Adaptive can only POST a raw Widevine challenge and read a raw
licence back. YouTube instead wraps both in JSON, at
``/youtubei/v1/player/get_drm_license``::

    request : {"context": {...}, "drmSystem": "DRM_SYSTEM_WIDEVINE",
               "videoId": "...", "cpn": "...", "sessionId": "...",
               "licenseRequest": "<b64 challenge>", "drmParams": "...",
               "isKeyRotated": true, "cryptoPeriodIndex": 20693,
               "drmVideoFeature": "DRM_VIDEO_FEATURE_SDR"}
    response: {"status": "LICENSE_STATUS_OK", "license": "<b64 licence>",
               "authorizedFormats": [{"trackType": ..., "keyId": ...}, ...]}

So this small localhost server sits between the two: ISA posts a raw challenge,
we wrap it with the per-playback context the plugin left behind, forward it with
the session's auth headers, and hand the decoded licence bytes back.

The wrinkle is ``isKeyRotated``. Live channels rotate keys, and each period
needs its own licence keyed by ``cryptoPeriodIndex`` -- a number ISA knows
nothing about and cannot supply. See _crypto_period_index() for how it is
derived and why the guess is allowed to be wrong.
"""

import base64
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from . import api, auth, kodiutils, widevine

LICENSE_URL = api.BASE + "player/get_drm_license"
CONTEXT_FILE = "playback_context.json"
PROXY_FILE = "license_proxy.json"
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 57814
TIMEOUT = 30

# A per-session secret every proxy request must carry (query parameter "k").
# Binding to localhost is not access control: every other process on the box
# can reach it, and so can any web page open in a browser there -- a page can
# fire a cross-origin POST at http://127.0.0.1:<port>/license and the proxy
# would forward it to Google with the account's token attached. The secret is
# minted by the service and published in license_proxy.json, which only
# same-user local code can read, so a forged request cannot guess it.
_SECRET = None


def _bearer():
    """The credential for the licence exchange.

    One line where there used to be a preference, a setting and a hazard:
    the SABR path fetched its media on a token and could still licence it on
    cookies, which measured neither cleanly and did not work at all on a box
    that never had a jar.
    """
    return auth.bearer()


def _context():
    return kodiutils.read_json(CONTEXT_FILE, default={}) or {}


def set_context(video_id, cpn, drm_params, is_live, heartbeat=None,
                tracking=None, duration=0):
    """Record what the next licence exchange needs. Called just before play."""
    kodiutils.write_json(CONTEXT_FILE, {
        "video_id": video_id,
        "cpn": cpn,
        "drm_params": drm_params,
        "is_live": bool(is_live),
        # playbackTracking out of the player response: the signed urls the
        # position reports are appended to, and the cadence to send them on.
        # The service reads this file, so it has to travel with the rest.
        "tracking": tracking or {},
        "duration": duration or 0,
        # heartbeatParams straight out of the player response: the token and
        # the first heartbeatServerData the session must quote, and how long
        # it may go quiet. The service reads this file to know what to send.
        "heartbeat": heartbeat or {},
        # The session id is not ours to invent: the player response mints one
        # and embeds it in drmParams, and the licence exchange has to quote
        # that same string. Falling back to a generated one only keeps things
        # moving if the field ever goes missing.
        "session_id": (widevine.session_id_from_drm_params(drm_params)
                       or "ad_" + secrets.token_urlsafe(9)[:13]),
        "created": int(time.time()),
    })


KEY_ID_FILE = "key_ids.json"


def _remember_key_ids(video_id, granted):
    """Keep the key ids the licence server named, per track type.

    ISA reports `ConvertKidStrToBytes: Cannot convert KID ""` on every stream,
    because YouTube's ContentProtection carries no cenc:default_KID and the
    content-id PSSH carries no key ids either -- so ISA opens a session not
    knowing which key belongs to which track. The licence response is the only
    place those ids appear, and it arrives too late for the manifest ISA has
    already parsed.

    Storing them makes the *next* play of the same title able to supply them up
    front. For on-demand the ids are stable, so one failed play teaches the
    next. Live rotates daily, so a stored id is good for the day.
    """
    if not video_id or not granted:
        return
    known = kodiutils.read_json(KEY_ID_FILE, default={}) or {}
    ids = {}
    for entry in granted:
        track, key_id = entry.get("trackType"), entry.get("keyId")
        if track and key_id and track not in ids:
            ids[track] = key_id
    if not ids:
        return
    if known.get(video_id) != ids:
        known[video_id] = ids
        kodiutils.write_json(KEY_ID_FILE, known)
        kodiutils.log("licence: recorded key ids for %s [%s]"
                      % (video_id, ", ".join(sorted(ids))))


def key_ids_for(video_id):
    return (kodiutils.read_json(KEY_ID_FILE, default={}) or {}).get(video_id) or {}


def _crypto_period_index(now=None):
    """The current key period.

    One day per period, which is no longer a guess: the PSSH inside the
    browser's own licence challenge carries crypto_period_seconds = 86400
    beside crypto_period_index = 20693, and 20693 is that day's index. See
    lib/widevine.py.

    The neighbours are still tried on rejection, because a request landing
    either side of a period boundary would otherwise fail for a whole minute.
    """
    return widevine.crypto_period_index(now)


def _encode(value):
    return base64.b64encode(value).decode("ascii")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        """Silence BaseHTTPRequestHandler's stderr logging."""

    def _authorized(self, query):
        supplied = (parse_qs(query).get("k") or [""])[0]
        expected = _secret()
        # Constant-time: the secret is the only thing standing between a local
        # web page and the account's credential.
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def _send(self, status, body=b"", content_type="application/octet-stream"):
        # ISA abandons a manifest request routinely -- it opens one, changes its
        # mind, and closes. Writing into that closed socket raises BrokenPipe,
        # and socketserver prints the whole traceback into the Kodi log. It is
        # not an error worth a stack trace.
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            kodiutils.log("client hung up before the response was sent")

    def do_GET(self):
        parsed = urlparse(self.path)
        if not self._authorized(parsed.query):
            self._send(403)
            return
        if parsed.path.startswith("/sabr/"):
            self._serve_sabr(parsed.path, parse_qs(parsed.query))
            return
        # ISA probes the licence endpoint before posting to it.
        self._send(200, b"ok", "text/plain")

    def _serve_sabr(self, path, query):
        """The three routes a SABR session is served through.

        A segment request blocks while the session pumps: ISA asked for
        segment N, and at the live edge N may not exist for another few
        seconds. Blocking is the honest answer -- an empty body would be
        read as a broken segment and end playback.
        """
        from . import sabr_bridge, sabr_session
        try:
            self._sabr(path, query, sabr_bridge, sabr_session)
        except Exception as exc:
            # Anything that escapes here closes the socket with no response
            # at all, and ISA reports that as "CURLOpen failed" -- which
            # reads like a network fault and was actually a SabrError
            # travelling out of the handler. A status is always sent.
            kodiutils.log_error("sabr bridge: %s: %s"
                                % (type(exc).__name__, exc))
            self._send(500)

    def _sabr(self, path, query, sabr_bridge, sabr_session):
        key = (query.get("id") or [""])[0]
        found = sabr_bridge.lookup(key)
        if not found:
            self._send(404)
            return
        session, _formats = found

        if path == "/sabr/manifest":
            base = {"url": "http://%s:%d" % (BIND_HOST, self.server.server_address[1]),
                    "secret": _secret()}
            body = sabr_bridge.manifest(key, base)
            self._send(200 if body else 503, body.encode("utf-8"),
                       "application/dash+xml")
            return

        itag = int((query.get("itag") or ["0"])[0] or 0)
        try:
            if path == "/sabr/init":
                head = session.initialisation_for(itag)
                # An empty 200 is a valid, empty segment as far as ISA is
                # concerned. 503 says "not yet" and it retries.
                self._send(200 if head else 503, head, "video/mp4")
                return
            if path == "/sabr/segment":
                number = int((query.get("n") or ["0"])[0] or 0)
                body = session.segment(itag, number)
                kodiutils.log("sabr bridge: ISA asked itag %d for %d -> "
                              "%d bytes" % (itag, number, len(body)))
                # 503, not an empty 200 and not 404: ISA retries a 503 and
                # treats the other two as a segment that is broken or gone.
                self._send(200 if body else 503, body, "video/mp4")
                return
        except sabr_session.SabrError as exc:
            kodiutils.log_error("sabr bridge: %s" % exc)
            self._send(404)
            return
        self._send(404)

    def do_POST(self):
        """The licence exchange. ISA posts the raw challenge here.

        Without this method BaseHTTPRequestHandler answers 501 Unsupported
        method, ISA reports "License server returned failure (HTTP error
        501)", the CDM session never opens, and the video stream is dropped
        with "Codec id 27 require extradata" -- three messages, none of them
        naming the missing handler.
        """
        parsed = urlparse(self.path)
        if not self._authorized(parsed.query):
            self._send(403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send(400)
            return

        challenge = self.rfile.read(length)
        try:
            licence = _fetch_license(challenge)
        except auth.AuthError as exc:
            kodiutils.log_error("licence: session rejected: %s" % exc)
            self._send(403)
            return
        except Exception as exc:
            kodiutils.log_error("licence exchange failed: %s" % exc)
            self._send(502)
            return
        self._send(200, licence)


def _post_license(payload, session, bearer):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": api.UA,
        "Origin": api.ORIGIN,
        "Referer": "%s/watch/%s" % (api.ORIGIN, payload.get("videoId", "")),
        "X-Origin": api.ORIGIN,
        "X-YouTube-Client-Name": api.CLIENT_NAME_ID,
        "X-YouTube-Client-Version": kodiutils.get_setting(
            "client_version", api.CLIENT_VERSION) or api.CLIENT_VERSION,
        "X-Goog-AuthUser": "0",
    }
    # The identity has to match the credential, or InnerTube answers 400 for
    # the client rather than for the request.
    headers["Authorization"] = "Bearer " + bearer
    headers["X-YouTube-Client-Name"] = api.client_spec(
        api.OAUTH_CLIENT_NAME)["id"]
    headers["X-YouTube-Client-Version"] = api.effective_version(
        api.OAUTH_CLIENT_NAME)
    visitor = kodiutils.get_setting("visitor_id", "")
    if visitor:
        headers["X-Goog-Visitor-Id"] = visitor

    response = session.post(LICENSE_URL, params={"alt": "json"},
                            data=json.dumps(payload), headers=headers,
                            timeout=TIMEOUT)
    if response.status_code in (401, 403):
        raise auth.AuthError("HTTP %d from the licence server"
                             % response.status_code)
    if response.status_code != 200:
        raise RuntimeError("licence server returned HTTP %d"
                           % response.status_code)
    return response.json()


def _fetch_license(challenge):
    """Wrap ISA's challenge, exchange it, return the raw licence bytes."""
    ctx = _context()
    if not ctx.get("video_id"):
        raise RuntimeError("no playback context -- nothing to license")

    bearer = _bearer()
    session = requests.Session()

    payload = {
        "context": api.context(location=False,
                               client_name=api.OAUTH_CLIENT_NAME),
        "drmSystem": "DRM_SYSTEM_WIDEVINE",
        "videoId": ctx["video_id"],
        "cpn": ctx.get("cpn", ""),
        "sessionId": ctx.get("session_id", ""),
        "licenseRequest": _encode(challenge),
        "drmParams": ctx.get("drm_params", ""),
        "drmVideoFeature": "DRM_VIDEO_FEATURE_SDR",
    }

    # Only live streams rotate keys. For on-demand the two fields are omitted
    # entirely, which is what the web player does.
    candidates = [None]
    if ctx.get("is_live"):
        base = _crypto_period_index()
        candidates = [base, base - 1, base + 1]

    last_status = None
    for index in candidates:
        attempt = dict(payload)
        if index is not None:
            attempt["isKeyRotated"] = True
            attempt["cryptoPeriodIndex"] = index

        body = _post_license(attempt, session, bearer)
        status = body.get("status")
        if status == "LICENSE_STATUS_OK" and body.get("license"):
            if index is not None and index != candidates[0]:
                kodiutils.log("licence: cryptoPeriodIndex %d worked where %d "
                              "did not -- the daily-period guess is off"
                              % (index, candidates[0]))
            licence = widevine.decode_b64(body["license"])
            # Say what was granted. A silent success is indistinguishable from
            # a licence carrying no usable key for the tracks being played,
            # which is exactly the case worth spotting from a log.
            granted = body.get("authorizedFormats") or []
            # The key ids too, not just the track types. The CDM reported
            # exactly one key on the bridge path -- video's -- while audio
            # samples asked for a key it had never been given, and there was
            # no way to tell from this line whether YouTube had withheld the
            # audio key or ISA had never installed it.
            kodiutils.log("licence granted: %d bytes, %d formats [%s]"
                          % (len(licence), len(granted),
                             ", ".join("%s=%s" % (f.get("trackType", "?"),
                                                  (f.get("keyId") or "none")[:16])
                                       for f in granted)))
            _remember_key_ids(ctx.get("video_id", ""), granted)
            return licence
        last_status = status
        kodiutils.log("licence: status %s at cryptoPeriodIndex %s"
                      % (status, index))

    raise RuntimeError("licence refused (%s)" % last_status)


class LicenseProxy(object):
    """The server, owned by the service process."""

    def __init__(self, port=None):
        # `port if port is not None`, not `port or`: 0 is a legitimate request
        # for an ephemeral port and must not fall through to the setting.
        self.port = _port() if port is None else port
        self._server = None
        self._thread = None

    def start(self):
        secret = secrets.token_urlsafe(24)
        # A second Kodi, or one that crashed with the port still held, leaves
        # the configured port taken -- and giving up there disables playback
        # entirely until the machine is rebooted. The port number is an
        # implementation detail: the plugin reads whichever one we bound from
        # license_proxy.json, so any free port serves.
        self._server = None
        for candidate in (self.port, 0):
            try:
                self._server = ThreadingHTTPServer((BIND_HOST, candidate),
                                                   _Handler)
                break
            except OSError as exc:
                kodiutils.log_error("licence proxy could not bind %s:%d: %s"
                                    % (BIND_HOST, candidate, exc))
        if self._server is None:
            return False
        # Bound port, not the requested one: port 0 means "pick one".
        self.port = self._server.server_address[1]
        global _SECRET
        _SECRET = secret
        kodiutils.write_json(PROXY_FILE, {"port": self.port, "secret": secret})

        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="youtubetv-license-proxy")
        self._thread.daemon = True
        self._thread.start()
        kodiutils.log("licence proxy listening on %s:%d" % (BIND_HOST, self.port))
        return True

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        # The published port and the playback context are deliberately left
        # behind. They are how the plugin process finds the service, and a
        # stopping service deleting them is a race it cannot win: Kodi
        # restarts this service when the addon's settings change, and if the
        # old one tears down after the new one has published then the new
        # one's file is the one that goes. The plugin then reports "no SABR
        # session could be opened" for a service that is running perfectly.
        #
        # A left-behind file names a dead port only while the service is
        # down, which is exactly when playback cannot work anyway, and the
        # next start overwrites it.


def _published():
    return kodiutils.read_json(PROXY_FILE, default={}) or {}


def _port():
    return int(kodiutils.get_setting_int("proxy_port", DEFAULT_PORT)
               or DEFAULT_PORT)


def _secret():
    """The session secret, from memory in the service, from disk in the plugin.

    The plugin and the service are separate interpreters, so only one of them
    ever has the in-memory copy.
    """
    if _SECRET:
        return _SECRET
    return _published().get("secret", "")


def sabr_manifest_url(key, wait=6.0):
    """The bridge manifest for a session, or "" if the proxy is not running.

    Waits for it rather than failing outright. The plugin and the service
    are separate processes and the service publishes its port to a file
    when it comes up; installing a new version restarts it, so a play
    started in those first seconds finds nothing published and the whole
    playback is refused for a reason that has nothing to do with the play.
    """
    import time as _time
    deadline = _time.time() + max(wait, 0)
    published = _published()
    said = False
    while not (published.get("port") and published.get("secret")):
        if _time.time() >= deadline:
            where = ""
            try:
                where = " (looked in %s)" % kodiutils.profile_dir()
            except Exception:
                pass
            kodiutils.log_error("the licence proxy has not published a port; "
                                "is the service running?%s" % where)
            return ""
        if not said:
            said = True
            kodiutils.log("waiting for the licence proxy to come up")
        _time.sleep(0.25)
        published = _published()
    return "http://%s:%d/sabr/manifest?id=%s&k=%s" % (
        BIND_HOST, published["port"], key, published["secret"])


def license_url():
    """The endpoint ISA should post challenges to, or "" if not running."""
    published = _published()
    if not published.get("port") or not published.get("secret"):
        return ""
    return "http://%s:%d/license?k=%s" % (BIND_HOST, published["port"],
                                          published["secret"])
