"""Localhost relay between InputStream Adaptive and Starz's licence server.

Why it exists: the first Kodi playback got HTTP 500 from license.starz.com
while the same URL works in the browser. The licence URL carries a 766-char
percent-encoded token (+ / ; =). Passed inside ISA's license_key it goes
through Kodi's URL handling, which may decode or re-encode it; a garbled token
is a likely 500. With the relay, ISA only ever sees
http://127.0.0.1:{port}/license/{secret}, and the real URL travels from the
plugin to here through a profile file, untouched.

It also makes licence failures diagnosable: Starz's error body is logged,
which ISA does not do.

Security, as in the Apple TV addon: bound to 127.0.0.1 only, and every request
must carry a per-run random secret in its path.

The body is passed through as-is (the raw Widevine challenge, R{SSM}); the
response is returned as-is (the raw licence).
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from lib import kodiutils

# Written by the service: {"port", "secret"}.
RELAY_FILE = "relay.json"
# Written by the plugin per play: {"url", "headers"}.
LICENSE_FILE = "license.json"

TIMEOUT = 20


def relay_url():
    """The localhost licence URL for ISA, or None if the service's relay is
    not running (then the plugin falls back to the direct URL)."""
    info = kodiutils.read_json(RELAY_FILE, default=None)
    if not info or not info.get("port") or not info.get("secret"):
        return None
    return "http://127.0.0.1:%d/license/%s" % (info["port"], info["secret"])


def _forward(challenge):
    lic = kodiutils.read_json(LICENSE_FILE, default=None) or {}
    url, headers = lic.get("url"), dict(lic.get("headers") or {})
    if not url:
        return 500, b"no licence URL for the current play"
    resp = requests.post(url, data=challenge, headers=headers, timeout=TIMEOUT)
    kodiutils.log("Licence: challenge %d bytes -> HTTP %d, %d bytes (%s)" % (
        len(challenge), resp.status_code, len(resp.content),
        resp.headers.get("Content-Type", "")))
    if resp.status_code >= 500 and "Content-Type" not in headers:
        # The browser sent no Content-Type; if Starz insists on one when the
        # client is not a browser, this is the difference. One retry only.
        kodiutils.log_error("Licence error body: %r" % resp.text[:500])
        headers["Content-Type"] = "application/octet-stream"
        resp = requests.post(url, data=challenge, headers=headers, timeout=TIMEOUT)
        kodiutils.log("Licence retry with Content-Type -> HTTP %d, %d bytes" % (
            resp.status_code, len(resp.content)))
    if resp.status_code != 200:
        kodiutils.log_error("Licence error: HTTP %d, headers=%s, body=%r" % (
            resp.status_code,
            {k: v for k, v in resp.headers.items() if k.lower().startswith("x-")},
            resp.text[:500]))
    return resp.status_code, resp.content


class _Handler(BaseHTTPRequestHandler):
    secret = None

    def do_POST(self):
        if self.path != "/license/%s" % self.secret:
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        challenge = self.rfile.read(length)
        try:
            status, body = _forward(challenge)
        except Exception as exc:
            kodiutils.log_error("Licence relay failed: %s" % exc)
            status, body = 502, str(exc).encode("utf-8", "replace")
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep Kodi's log to the lines above


class LicenseRelay(object):

    def __init__(self):
        self.server = None
        self.thread = None

    def start(self):
        handler = type("Handler", (_Handler,), {"secret": os.urandom(16).hex()})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        kodiutils.write_json(RELAY_FILE, {"port": self.server.server_address[1],
                                          "secret": handler.secret})
        kodiutils.log("Licence relay listening on 127.0.0.1:%d"
                      % self.server.server_address[1])

    def stop(self):
        kodiutils.delete_file(RELAY_FILE)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
