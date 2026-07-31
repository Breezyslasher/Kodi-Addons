"""Local Widevine license proxy for Apple's JSON-wrapped licence exchange.

InputStream Adaptive can only POST a raw Widevine challenge and read a raw
licence back. Apple's licence server (``.../wa/fpsRequest``) instead expects the
challenge wrapped in a JSON envelope and returns the licence wrapped too::

    request : {"streaming-request":{"version":1,"streaming-keys":[
                 {"lease-action":"start","id":1,"challenge":"<b64 challenge>",
                  "key-system":"com.widevine.alpha","uri":"skd://...",
                  "adamId":"...","isExternal":true}]}}
    response: {"streaming-response":{"streaming-keys":[
                 {"license":"<b64 licence>","id":1, ...}]}}

This tiny localhost HTTP server sits between ISA and Apple: ISA posts the raw
challenge here, we wrap it, add the auth headers, forward it to Apple, unwrap the
licence and hand the raw bytes back to ISA. The per-playback context (tokens,
skd uri, adamId) is read from a file written by the plugin just before playback.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from . import kodiutils

FPS_URL = "https://play-edge.itunes.apple.com/WebObjects/MZPlayLocal.woa/wa/fpsRequest"
CONTEXT_FILE = "playback_context.json"
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 57812


def _context():
    return kodiutils.read_json(CONTEXT_FILE, default={}) or {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence default stderr logging

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            challenge = self.rfile.read(length)
            license_bytes = self._fetch_license(challenge)
            if license_bytes is None:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(license_bytes)))
            self.end_headers()
            self.wfile.write(license_bytes)
        except Exception as exc:
            kodiutils.log_error("License proxy error: %s" % exc)
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _fetch_license(self, challenge):
        ctx = _context()
        bearer = ctx.get("bearer")
        mut = ctx.get("media_user_token")
        if not bearer or not mut:
            kodiutils.log_error("License proxy missing bearer/media-user-token")
            return None

        # Apple needs the data: URI of the exact key this challenge is for
        # (video and audio have different keys); a wrong or missing uri gives a
        # 500. Match by the key id embedded in the challenge, then fall back to
        # the other known keys rather than failing outright.
        wv_keys = ctx.get("wv_keys") or {}
        candidates = []
        for kid_hex, kuri in wv_keys.items():
            try:
                if bytes.fromhex(kid_hex) in challenge:
                    candidates.append(kuri)
                    break
            except ValueError:
                continue
        matched = bool(candidates)
        for kuri in wv_keys.values():
            if kuri not in candidates:
                candidates.append(kuri)
        if not candidates:
            candidates = [None]
        kodiutils.log("License request: %d bytes, exact key match=%s, %d candidate(s)"
                      % (len(challenge), "yes" if matched else "no", len(candidates)))

        url = ctx.get("license_server") or FPS_URL
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://tv.apple.com",
            "Referer": "https://tv.apple.com/",
            "authorization": "Bearer " + bearer,
            "x-apple-music-user-token": mut,
            "x-apple-renewal": "true",
        }
        last_error = None
        for attempt, candidate in enumerate(candidates):
            key = {
                "lease-action": "start",
                "id": 1,
                "challenge": base64.b64encode(challenge).decode("ascii"),
                "key-system": "com.widevine.alpha",
                "adamId": ctx.get("adam_id", ""),
                "isExternal": bool(ctx.get("is_external", True)),
                "svcId": ctx.get("svc_id", ""),
            }
            if candidate:
                key["uri"] = candidate
            envelope = {"streaming-request": {"version": 1, "streaming-keys": [key]}}
            resp = requests.post(url, data=json.dumps(envelope), headers=headers, timeout=30)
            if resp.status_code != 200:
                last_error = "HTTP %s %s" % (resp.status_code, resp.text[:150])
                continue
            try:
                keys = (resp.json().get("streaming-response", {}) or {}).get("streaming-keys", [])
            except ValueError:
                last_error = "non-JSON response %s" % resp.text[:150]
                continue
            if not keys or "license" not in keys[0]:
                last_error = "no licence in response %s" % resp.text[:150]
                continue
            kodiutils.log("License OK (attempt %d/%d)" % (attempt + 1, len(candidates)))
            return base64.b64decode(keys[0]["license"])

        kodiutils.log_error("fpsRequest failed after %d attempt(s): %s"
                            % (len(candidates), last_error))
        return None


class LicenseProxy(object):
    """Threaded localhost proxy; start() once, stays up for the Kodi session."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        try:
            self._server = ThreadingHTTPServer((BIND_HOST, self.port), _Handler)
        except OSError:
            # Port busy: let the OS choose a free one.
            self._server = ThreadingHTTPServer((BIND_HOST, 0), _Handler)
            self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Publish the chosen port so the plugin can build the licence URL.
        kodiutils.write_json("license_proxy.json", {"port": self.port})
        kodiutils.log("License proxy listening on %s:%d" % (BIND_HOST, self.port))
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None


def license_url():
    """URL the plugin points ISA at, using the port the service published."""
    info = kodiutils.read_json("license_proxy.json", default={}) or {}
    return "http://%s:%d/widevine" % (BIND_HOST, info.get("port", DEFAULT_PORT))
