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
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from . import (api, auth, kodiutils, manifest as manifest_mod, nsig,
               widevine)

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
        # web page and the account's cookies.
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
        if parsed.path == "/manifest":
            self._serve_manifest(parse_qs(parsed.query))
            return
        # ISA probes the licence endpoint before posting to it.
        self._send(200, b"ok", "text/plain")

    def _serve_manifest(self, query):
        """Fetch the manifest from Google and hand ISA a repaired copy.

        Live manifests are re-fetched every few seconds (minimumUpdatePeriod is
        5s here), so this runs continuously during playback, not just once.
        """
        target = (query.get("u") or [""])[0]
        if not target:
            self._send(400)
            return
        target = unquote(target)
        if not target.startswith("https://"):
            # The secret already gates this, but never let the proxy be aimed
            # somewhere arbitrary with the account's cookies attached.
            self._send(403)
            return
        try:
            cookies = auth.load()
            response = requests.get(target, timeout=TIMEOUT, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Cookie": auth.cookie_header(cookies),
            })
        except Exception as exc:
            kodiutils.log_error("manifest fetch failed: %s" % exc)
            self._send(502)
            return
        if response.status_code != 200:
            kodiutils.log_error("manifest fetch returned HTTP %d"
                                % response.status_code)
            self._send(response.status_code)
            return
        try:
            body = manifest_mod.patch(response.content)
            body = manifest_mod.add_po_token(
                body, kodiutils.get_setting("po_token", ""))
            body = _resolve_n(body, cookies)
        except Exception as exc:
            # A manifest we failed to repair still beats no manifest.
            kodiutils.log_error("manifest patch failed, passing it through: %s"
                                % exc)
            body = response.content
        self._send(200, body, "application/dash+xml")

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
            licence = widevine.decode_b64(body["license"])
            # Say what was granted. A silent success is indistinguishable from
            # a licence carrying no usable key for the tracks being played,
            # which is exactly the case worth spotting from a log.
            granted = body.get("authorizedFormats") or []
            kodiutils.log("licence granted: %d bytes, %d formats [%s]"
                          % (len(licence), len(granted),
                             ", ".join(sorted({f.get("trackType", "?")
                                               for f in granted}))))
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


def _resolve_n(body, cookies):
    """Compute n for this manifest and write it back into every BaseURL.

    Measured rather than assumed: on a url the edge does serve, rotating one
    character of n turns 15,010,219 bytes into an empty-bodied 403, and
    removing n does the same. So it has to be right, and the only thing that
    knows how to make it right is the player's own JavaScript.

    A failure is logged and the manifest passed through untouched. That plays
    no better than before, but it leaves the reason in the log rather than
    substituting one silent 403 for another.
    """
    urls = manifest_mod.base_urls(body)
    if not any("n=" in url for url in urls):
        return body
    session = requests.Session()
    try:
        player_id, js = api.player_js(session, cookies)
    except Exception as exc:
        kodiutils.log_error("nsig: no player js, leaving n as minted: %s" % exc)
        return body
    try:
        return manifest_mod.rewrite_n(
            body, lambda value: nsig.solve(js, value, player_id))
    except Exception as exc:
        kodiutils.log_error("nsig: could not solve n, leaving it as minted: %s"
                            % exc)
        return body


def manifest_url(real_url):
    """The proxied manifest URL to hand ISA, or the real one if not running."""
    published = _published()
    if not published.get("port") or not published.get("secret"):
        return real_url
    return "http://%s:%d/manifest?k=%s&u=%s" % (
        BIND_HOST, published["port"], published["secret"], quote(real_url, safe=""))


def license_url():
    """The endpoint ISA should post challenges to, or "" if not running."""
    published = _published()
    if not published.get("port") or not published.get("secret"):
        return ""
    return "http://%s:%d/license?k=%s" % (BIND_HOST, published["port"],
                                          published["secret"])
