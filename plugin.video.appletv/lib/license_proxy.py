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
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests

from . import kodiutils

FPS_URL = "https://play-edge.itunes.apple.com/WebObjects/MZPlayLocal.woa/wa/fpsRequest"
CONTEXT_FILE = "playback_context.json"
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 57812


def _context():
    return kodiutils.read_json(CONTEXT_FILE, default={}) or {}


def kid_from_data_uri(uri):
    """Extract the 16-byte Widevine key id (hex) from a key's data: URI PSSH."""
    try:
        if "base64," not in uri:
            return None
        pssh = base64.b64decode(unquote(uri.split("base64,", 1)[1]))
        # WidevinePsshData: key_id is field 2 -> tag 0x12, length 0x10 (16).
        i = pssh.find(b"\x12\x10")
        if i >= 0 and len(pssh) >= i + 18:
            return pssh[i + 2:i + 18].hex()
    except Exception:
        pass
    return None


def _encode_url(url):
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_url(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence default stderr logging

    # -- manifest proxy: add the KEYID that Apple omits -------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/manifest":
            self.send_response(404)
            self.end_headers()
            return
        try:
            target = _decode_url(parse_qs(parsed.query).get("u", [""])[0])
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        ctx = _context()
        is_master = "u=" in parsed.query and parse_qs(parsed.query).get("m", ["0"])[0] == "1"
        headers = {
            "User-Agent": ctx.get("user_agent") or "Mozilla/5.0",
            "Origin": "https://tv.apple.com",
            "Referer": "https://tv.apple.com/",
        }
        # The top-level manifest is token-authenticated by header; the variant
        # playlists are authenticated by the token in their URL and the web
        # player sends no auth headers for them.
        if is_master:
            if ctx.get("bearer"):
                headers["authorization"] = "Bearer " + ctx["bearer"]
            if ctx.get("media_user_token"):
                headers["media-user-token"] = ctx["media_user_token"]

        try:
            resp = requests.get(target, headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("Manifest proxy %s -> HTTP %s"
                                    % ("master" if is_master else "variant", resp.status_code))
                self.send_response(resp.status_code)
                self.end_headers()
                return
            body = self._rewrite(resp.text, target).encode("utf-8")
        except Exception as exc:
            kodiutils.log_error("Manifest proxy error: %s" % exc)
            self.send_response(502)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rewrite(self, text, base_url):
        """Route child playlists through the proxy and add KEYID to key lines.

        InputStream Adaptive takes a stream's key id from the KEYID attribute of
        #EXT-X-KEY, falling back to parsing the PSSH. Apple sends no KEYID and
        its v0 PSSH carries the key id inside the Widevine protobuf, which ISA's
        PSSH parser does not read, so the key id ends up empty and the CDM is
        asked to decrypt with an all-zero KID ("kNoKey"). The key id is
        recoverable from the PSSH, so add it here as KEYID.
        """
        is_master = "#EXT-X-STREAM-INF" in text
        tagged = 0
        out = []
        for line in text.splitlines():
            s = line.strip()
            if is_master:
                if s.startswith("#") and 'URI="' in s:
                    out.append(re.sub(
                        r'URI="([^"]+)"',
                        lambda m: 'URI="%s"' % self._proxied(m.group(1), base_url), s))
                    continue
                if s and not s.startswith("#"):
                    out.append(self._proxied(s, base_url))
                    continue
            else:
                if s.startswith("#EXT-X-KEY") and "urn:uuid:edef8ba9" in s:
                    fixed = self._add_keyid(s)
                    if fixed != s:
                        tagged += 1
                    out.append(fixed)
                    continue
                # Segment and init-segment URLs stay pointed at Apple's CDN.
                if s and not s.startswith("#"):
                    out.append(urljoin(base_url, s))
                    continue
            out.append(line)
        if not is_master:
            kodiutils.log("Manifest proxy: variant served, %d KEYID added" % tagged)
        return "\n".join(out) + "\n"

    def _add_keyid(self, line):
        if "KEYID=" in line:
            return line
        m = re.search(r'URI="([^"]+)"', line)
        if not m:
            return line
        kid = kid_from_data_uri(m.group(1))
        if not kid:
            kodiutils.log_error("Could not read key id from EXT-X-KEY URI")
            return line
        return line + ',KEYID="0x%s"' % kid

    def _proxied(self, url, base_url):
        return "%s?u=%s" % (manifest_endpoint(), _encode_url(urljoin(base_url, url)))

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


def _port():
    info = kodiutils.read_json("license_proxy.json", default={}) or {}
    return info.get("port", DEFAULT_PORT)


def license_url():
    """URL the plugin points ISA at, using the port the service published."""
    return "http://%s:%d/widevine" % (BIND_HOST, _port())


def manifest_endpoint():
    return "http://%s:%d/manifest" % (BIND_HOST, _port())


def manifest_url(real_url):
    """Proxy URL for the top-level manifest (m=1 marks it as the master)."""
    return "%s?m=1&u=%s" % (manifest_endpoint(), _encode_url(real_url))
