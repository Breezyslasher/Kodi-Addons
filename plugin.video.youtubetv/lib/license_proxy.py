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
import math
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from . import api, auth, kodiutils

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
# would forward it with the account's Google cookies attached. The secret is
# minted by the service and published in license_proxy.json, which only
# same-user local code can read, so a forged request cannot guess it.
_SECRET = None


def _context():
    return kodiutils.read_json(CONTEXT_FILE, default={}) or {}


def set_context(video_id, cpn, drm_params, is_live):
    """Record what the next licence exchange needs. Called just before play."""
    kodiutils.write_json(CONTEXT_FILE, {
        "video_id": video_id,
        "cpn": cpn,
        "drm_params": drm_params,
        "is_live": bool(is_live),
        # One DRM session id per playback, reused across key rotations, the way
        # the web player does it.
        "session_id": "ad_" + secrets.token_urlsafe(9)[:13],
        "created": int(time.time()),
    })


def _crypto_period_index(now=None):
    """The current key period.

    Derived from one observation: a licence request at unix 1787864547 carried
    ``cryptoPeriodIndex: 20693``, and ceil(1787864547 / 86400) is exactly
    20693 -- a daily period, indexed by the day it ends.

    One data point is not a specification. It could as easily be a counter that
    happened to align, and a daily rotation is long for live DRM. So this is a
    starting guess, not an answer: _fetch_license() retries the neighbouring
    indices when the server rejects it, which costs one round trip on the day
    the guess is wrong and nothing at all when it is right.
    """
    return int(math.ceil((now or time.time()) / 86400.0))


def _encode(value):
    return base64.b64encode(value).decode("ascii")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        """Silence BaseHTTPRequestHandler's stderr logging."""

    def _authorized(self, query):
        supplied = (parse_qs(query).get("k") or [""])[0]
        expected = _secret()
        # Constant-time: the secret is the only thing standing between a local
        # web page and the account's cookies.
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def _send(self, status, body=b"", content_type="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        # ISA probes the endpoint before posting to it.
        parsed = urlparse(self.path)
        if not self._authorized(parsed.query):
            self._send(403)
            return
        self._send(200, b"ok", "text/plain")

    def do_POST(self):
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


def _post_license(payload, cookies, session):
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
        "Authorization": auth.authorization(cookies),
        "Cookie": auth.cookie_header(cookies),
    }
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

    cookies = auth.load()
    session = requests.Session()

    payload = {
        "context": api.context(location=False),
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

        body = _post_license(attempt, cookies, session)
        status = body.get("status")
        if status == "LICENSE_STATUS_OK" and body.get("license"):
            if index is not None and index != candidates[0]:
                kodiutils.log("licence: cryptoPeriodIndex %d worked where %d "
                              "did not -- the daily-period guess is off"
                              % (index, candidates[0]))
            return base64.b64decode(body["license"])
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
        try:
            self._server = ThreadingHTTPServer((BIND_HOST, self.port), _Handler)
        except OSError as exc:
            kodiutils.log_error("licence proxy could not bind %s:%d: %s"
                                % (BIND_HOST, self.port, exc))
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
        kodiutils.delete_file(PROXY_FILE)
        kodiutils.delete_file(CONTEXT_FILE)


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


def license_url():
    """The endpoint ISA should post challenges to, or "" if not running."""
    published = _published()
    if not published.get("port") or not published.get("secret"):
        return ""
    return "http://%s:%d/license?k=%s" % (BIND_HOST, published["port"],
                                          published["secret"])
