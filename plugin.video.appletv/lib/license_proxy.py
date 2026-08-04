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


# Widevine keys discovered while serving variant playlists, keyed by key id.
# A title has a separate key per variant/rendition and the master playlist can
# list hundreds of them, so the set collected up front is not complete: record
# every key the proxy actually serves, and the licence handler can then always
# match the exact key a challenge asks for. Both handlers run in the service
# process, so a module-level dict is shared between them.
_DISCOVERED_KEYS = {}


def variant_unwanted(tag, max_h, sdr_only, avc_only):
    """Drop variants the CDM will refuse or the player cannot show.

    Apple keys each quality tier separately and the higher tiers demand output
    protection this system cannot provide: the CDM reports the key as output
    restricted (OnSessionKeysChange status 3) and the DRM session fails. Dolby
    Vision variants are dropped as well because Kodi decodes them as plain
    HEVC, which renders with badly shifted colours.

    Shared with the key collector, which must read its keys from the very
    variants that survive here -- a key belonging to a tier that is never
    played is of no use.
    """
    res = re.search(r'RESOLUTION=\d+x(\d+)', tag)
    if max_h and res and int(res.group(1)) > max_h:
        return True
    codecs = re.search(r'CODECS="([^"]+)"', tag)
    if sdr_only:
        video_range = re.search(r'VIDEO-RANGE=([A-Z]+)', tag)
        if video_range and video_range.group(1) != "SDR":
            return True
        if codecs and re.search(r'\bdv(h[e1]|av)', codecs.group(1)):
            return True
    if avc_only and codecs:
        # Encrypted video is decoded by the CDM, whose decoder handles H.264
        # only: an HEVC variant makes ISA report "ToCdmVideoCodec: Unknown
        # video codec 5". Apple publishes H.264 alongside HEVC at every tier.
        video_codec = codecs.group(1).split(",")[0]
        if not video_codec.startswith("avc"):
            return True
    return False


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


def _license_key_ids(licence):
    """Best-effort list of the key ids a Widevine licence carries.

    Used for diagnostics only: in the License protobuf each key entry starts
    with field 1 (tag 0x0a) holding a 16-byte id, so collect those. Apple's key
    ids end in ASCII padding, which makes the real ones easy to recognise.
    """
    ids = []
    pos = 0
    while len(ids) < 8:
        pos = licence.find(b"\x0a\x10", pos)
        if pos < 0:
            break
        candidate = licence[pos + 2:pos + 18]
        if len(candidate) == 16:
            hexed = candidate.hex()
            if hexed not in ids:
                ids.append(hexed)
        pos += 2
    return ids


def _patch_tenc_kid(data, kid_hex):
    """Replace an all-zero default_KID in every tenc box with the real key id.

    tenc payload: version(1) flags(3) reserved(1) reserved/blocks(1)
    default_isProtected(1) default_Per_Sample_IV_Size(1) default_KID(16),
    so the key id starts 12 bytes after the 'tenc' fourcc.
    """
    try:
        kid = bytes.fromhex(kid_hex)
    except (ValueError, TypeError):
        return data, 0
    if len(kid) != 16:
        return data, 0

    out = bytearray(data)
    patched = 0
    pos = 0
    while True:
        pos = out.find(b"tenc", pos)
        if pos < 0:
            break
        start = pos + 12
        if start + 16 <= len(out) and out[start:start + 16] == b"\x00" * 16:
            out[start:start + 16] = kid
            patched += 1
        pos += 4
    return bytes(out), patched


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
        if parsed.path not in ("/manifest", "/init"):
            self.send_response(404)
            self.end_headers()
            return
        query = parse_qs(parsed.query)
        try:
            target = _decode_url(query.get("u", [""])[0])
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        if parsed.path == "/init":
            self._serve_init(target, query.get("kid", [""])[0])
            return

        ctx = _context()
        is_master = "u=" in parsed.query and parse_qs(parsed.query).get("m", ["0"])[0] == "1"
        is_clear = parse_qs(parsed.query).get("c", ["0"])[0] == "1"
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
            body = self._rewrite(resp.text, target, is_clear).encode("utf-8")
        except Exception as exc:
            kodiutils.log_error("Manifest proxy error: %s" % exc)
            self.send_response(502)
            self.end_headers()
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            # InputStream Adaptive abandons a playlist request when it switches
            # representation; nothing is wrong, so do not log a traceback.
            pass

    def _serve_init(self, target, kid_hex):
        """Serve an init segment with the real key id patched into its tenc box.

        Apple ships default_KID as sixteen zero bytes. InputStream Adaptive
        takes the key id for every sample straight from tenc, and its only
        fallback triggers on an *empty* key id, not an all-zero one, so it asks
        the CDM to decrypt with a key that was never licensed ("kNoKey"). The
        real key id is in the manifest's key PSSH, so write it into tenc here.
        """
        ctx = _context()
        headers = {
            "User-Agent": ctx.get("user_agent") or "Mozilla/5.0",
            "Origin": "https://tv.apple.com",
            "Referer": "https://tv.apple.com/",
        }
        try:
            resp = requests.get(target, headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("Init segment proxy -> HTTP %s" % resp.status_code)
                self.send_response(resp.status_code)
                self.end_headers()
                return
            data, patched = _patch_tenc_kid(resp.content, kid_hex)
            kodiutils.log("Init segment %s: patched %d tenc KID(s) -> %s"
                          % (target.rsplit("/", 1)[-1][:48], patched, kid_hex))
        except Exception as exc:
            kodiutils.log_error("Init segment proxy error: %s" % exc)
            self.send_response(502)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _rewrite(self, text, base_url, clear=False):
        """Route child playlists through the proxy and add KEYID to key lines.

        InputStream Adaptive takes a stream's key id from the KEYID attribute of
        #EXT-X-KEY, falling back to parsing the PSSH. Apple sends no KEYID and
        its v0 PSSH carries the key id inside the Widevine protobuf, which ISA's
        PSSH parser does not read, so the key id ends up empty and the CDM is
        asked to decrypt with an all-zero KID ("kNoKey"). The key id is
        recoverable from the PSSH, so add it here as KEYID.
        """
        is_master = "#EXT-X-STREAM-INF" in text
        if is_master:
            return self._rewrite_master(text, base_url, clear)

        tagged = 0
        mapped = 0
        dropped = 0
        cur_kid = None
        out = []
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            index += 1
            s = line.strip()
            if True:
                if s.startswith("#EXT-X-KEY"):
                    if "urn:uuid:edef8ba9" in s:
                        uri_match = re.search(r'URI="([^"]+)"', s)
                        cur_kid = kid_from_data_uri(uri_match.group(1)) if uri_match else None
                        if cur_kid:
                            # Remember this variant's key so the licence handler
                            # can match challenges for variants that were not
                            # scanned before playback started.
                            _DISCOVERED_KEYS[cur_kid] = uri_match.group(1)
                        fixed = self._add_keyid(s)
                        if fixed != s:
                            tagged += 1
                        out.append(fixed)
                        continue
                    if "METHOD=NONE" in s:
                        cur_kid = None  # clear section (Apple's intro chapters)
                if s.startswith("#EXT-X-MAP"):
                    if cur_kid:
                        # Route the init segment through the proxy so its
                        # all-zero tenc default_KID can be replaced with the
                        # real key id.
                        out.append(re.sub(
                            r'URI="([^"]+)"',
                            lambda m: 'URI="%s"' % self._init_proxied(
                                m.group(1), base_url, cur_kid), s))
                        mapped += 1
                        continue
                    # With no key there is nothing to patch, but the URI still
                    # has to be made absolute: this playlist is served from the
                    # proxy, so a relative one resolves against the proxy and
                    # 404s. Losing the init segment costs the MOOV atom, and
                    # ISA then reports the stream as having no extradata.
                    out.append(re.sub(
                        r'URI="([^"]+)"',
                        lambda m: 'URI="%s"' % urljoin(base_url, m.group(1)), s))
                    continue
                # Media segment URLs stay pointed at Apple's CDN.
                if s and not s.startswith("#"):
                    out.append(urljoin(base_url, s))
                    continue
            out.append(line)
        kodiutils.log("Manifest proxy: variant served, %d KEYID added, "
                      "%d init segment(s) routed" % (tagged, mapped))
        return "\n".join(out) + "\n"

    def _rewrite_master(self, text, base_url, clear=False):
        """Rewrite the master playlist, optionally capping video height.

        Apple uses a different content key per quality tier. The web player
        selects an SD variant and its key decrypts under Kodi's software (L3)
        Widevine CDM; a higher tier's key is licensed but the CDM will not use
        it, which surfaces as kNoKey. Capping the height keeps InputStream
        Adaptive on the tier the web player uses.

        Renditions referenced only by dropped variants are removed too, so ISA
        does not report "Cannot find variant for AUDIO GROUP-ID".
        """
        # Two of the three limits exist to keep the Widevine CDM happy: it
        # refuses the higher tiers' keys and cannot decode HEVC. An unencrypted
        # stream never reaches the CDM, so it plays at whatever quality and
        # codec Apple offers.
        #
        # The dynamic-range limit is not one of those. Kodi decodes Dolby
        # Vision Profile 5 as plain HEVC and renders it with badly shifted
        # colours -- a trailer came out magenta -- and that happens whether or
        # not the stream was encrypted, so it stays in force here.
        if clear:
            max_h, avc_only = 0, False
            sdr_only = kodiutils.get_setting_bool("sdr_only", True)
        else:
            max_h = kodiutils.get_setting_int("max_height", 360)
            sdr_only = kodiutils.get_setting_bool("sdr_only", True)
            avc_only = kodiutils.get_setting_bool("avc_only", True)
        lines = text.splitlines()

        def unwanted(tag):
            return variant_unwanted(tag, max_h, sdr_only, avc_only)

        too_tall = unwanted

        # First pass: decide which variants survive and which groups they use.
        keep_variant = {}
        groups = set()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#EXT-X-STREAM-INF"):
                keep = not too_tall(s)
                keep_variant[i] = keep
                if keep:
                    for attr in ("AUDIO", "SUBTITLES", "CLOSED-CAPTIONS"):
                        m = re.search(r'%s="([^"]+)"' % attr, s)
                        if m:
                            groups.add(m.group(1))

        out = []
        dropped = 0
        index = 0
        while index < len(lines):
            line = lines[index]
            s = line.strip()
            index += 1
            if s.startswith("#EXT-X-STREAM-INF"):
                if not keep_variant.get(index - 1, True):
                    dropped += 1
                    if index < len(lines):
                        index += 1  # skip this variant's URI line
                    continue
            elif s.startswith("#EXT-X-I-FRAME-STREAM-INF"):
                if too_tall(s):
                    dropped += 1
                    continue
            elif s.startswith("#EXT-X-MEDIA"):
                m = re.search(r'GROUP-ID="([^"]+)"', s)
                if max_h and m and groups and m.group(1) not in groups:
                    continue  # rendition only used by variants we removed

            if s.startswith("#") and 'URI="' in s:
                out.append(re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="%s"' % self._proxied(m.group(1), base_url), s))
            elif s and not s.startswith("#"):
                out.append(self._proxied(s, base_url))
            else:
                out.append(line)

        kodiutils.log("Manifest proxy: master served, height cap=%s, %d variant(s) dropped"
                      % (max_h or "none", dropped))
        return "\n".join(out) + "\n"

    def _init_proxied(self, url, base_url, kid):
        return "http://%s:%d/init?kid=%s&u=%s" % (
            BIND_HOST, _port(), kid, _encode_url(urljoin(base_url, url)))

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
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(license_bytes)))
                self.end_headers()
                self.wfile.write(license_bytes)
            except (ConnectionResetError, BrokenPipeError):
                pass
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
        # Keys found up front plus every key seen while serving variants.
        wv_keys = dict(ctx.get("wv_keys") or {})
        wv_keys.update(_DISCOVERED_KEYS)

        candidates = []
        matched_kid = None
        for kid_hex, kuri in wv_keys.items():
            try:
                if bytes.fromhex(kid_hex) in challenge:
                    matched_kid = kid_hex
                    candidates.append(kuri)
                    break
            except ValueError:
                continue
        for kuri in wv_keys.values():
            if kuri not in candidates:
                candidates.append(kuri)
        if not candidates:
            candidates = [None]
        kodiutils.log("License request: challenge=%d bytes, wants KID=%s, known KIDs=[%s]"
                      % (len(challenge), matched_kid or "UNKNOWN",
                         ", ".join(sorted(wv_keys.keys()))))
        # Which credentials went out matters as much as the challenge did, and
        # a refusal reads very differently depending on whether Apple was told
        # who was asking. Neither value is logged, only whether there is one.
        kodiutils.log("License identity: bearer=%s, media-user-token=%s, "
                      "adamId=%s, svcId=%s"
                      % ("yes" if bearer else "NO",
                         "yes" if mut else "NO",
                         ctx.get("adam_id") or "none",
                         ctx.get("svc_id") or "none"))

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
            licence = base64.b64decode(keys[0]["license"])
            kodiutils.log("License OK (attempt %d/%d), contains KIDs=[%s]"
                          % (attempt + 1, len(candidates),
                             ", ".join(_license_key_ids(licence)) or "none found"))
            return licence

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


def manifest_url(real_url, clear=False):
    """Proxy URL for the top-level manifest (m=1 marks it as the master).

    clear=1 says the stream carries no Widevine keys, so the quality filter
    -- which exists only to satisfy the CDM -- does not apply to it.
    """
    return "%s?m=1&c=%d&u=%s" % (manifest_endpoint(), 1 if clear else 0,
                                 _encode_url(real_url))
