"""Apple TV catalogue and playback API (reconstructed from the web client).

Captured from a real tv.apple.com session:

  Catalogue (anonymous browse works):
    GET tv.apple.com/api/uts/v3/configurations
    GET tv.apple.com/api/uts/v3/canvases/channels/tvs.sbd.4000   (TV+ Originals)
    GET tv.apple.com/api/uts/v3/search?q=...
  Tokens scraped from the tv.apple.com HTML shell:
    developerToken -> Bearer app token
    utsk           -> UTS session token
  Playback: the server-rendered title page embeds everything needed:
    "hlsUrl"          -> the HLS manifest URL (already carries the ?t= token)
    "userToken"       -> the account media-user-token (present when signed in)
    "fpsKeyServerUrl" -> the Widevine licence endpoint (JSON-wrapped, proxied)
    "assetAdamId"/"svcId"/"isExternal" -> licence-request parameters

Apple serves Widevine (not FairPlay) to non-Apple clients: the HLS manifest
carries a Widevine PSSH, so InputStream Adaptive can decrypt it.
"""

import json
import re

from . import kodiutils
from . import license_proxy

UTS_BASE = "https://tv.apple.com/api/uts/v3"
WEB_HOME = "https://tv.apple.com"
WIDEVINE_CERT_URL = "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/widevineCert"

DEFAULT_STOREFRONT = "143441"
DEFAULT_LOCALE = "en-US"
UTS_VERSION = "96"
UTS_CLIENT_FLAGS = "OjAAAAEAAAAAAAIAEAAAACMAKwAtAA~~"
APPLE_TV_PLUS_CHANNEL = "tvs.sbd.4000"

CANVAS_CACHE = "canvas_cache.json"

# Poster/thumbnail image keys seen in canvas items, best first.
IMAGE_KEYS = (
    "posterArt", "coverArt16X9", "coverArt", "shelfItemImage",
    "shelfImageBackground", "previewFrame", "singleColorContentLogo",
    "contentLogo", "fullColorContentLogo",
)


class AppleTVApi(object):
    def __init__(self, auth):
        self.auth = auth
        self.session = auth.session
        self._boot = None

    # -- bootstrap (scrape tokens from the web shell) --------------------

    def _bootstrap(self, force=False):
        if self._boot is not None and not force:
            return self._boot
        boot = {"utsk": None, "developer_token": None, "storefront": DEFAULT_STOREFRONT,
                "user_token": None}
        try:
            # Fetch the page as a browser would. The shared session carries
            # idmsa API headers (Accept: application/json, X-Requested-With,
            # Origin) which, once signed in, make tv.apple.com return a non-HTML
            # response with no embedded tokens. Override them per request.
            html = self.session.get(WEB_HOME, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": None,
                "X-Requested-With": None,
                "Origin": None,
                "Referer": None,
            }, timeout=30).text
            m = re.search(r'"utsk"\s*:\s*"([^"]+)"', html)
            if m:
                boot["utsk"] = m.group(1)
            m = re.search(r'"developerToken"\s*:\s*"([A-Za-z0-9_.\-]{20,})"', html)
            if m:
                boot["developer_token"] = m.group(1)
            m = re.search(r'"storefrontId"\s*:\s*"?(\d+)"?', html)
            if m:
                boot["storefront"] = m.group(1)
            # Present only when the session is signed in to tv.apple.com; this
            # is the account media-user-token needed for playback.
            m = re.search(r'"userToken"\s*:\s*"([^"]+)"', html)
            if m:
                boot["user_token"] = m.group(1)
                self.auth.tokens["media_user_token"] = m.group(1)
                self.auth.save()
        except Exception as exc:
            kodiutils.log_error("Bootstrap scrape failed: %s" % exc)
        # Fall back to the last good tokens if this scrape came up empty.
        cached = self.auth.tokens.get("boot") or {}
        if not boot["utsk"]:
            boot["utsk"] = cached.get("utsk")
        if not boot["developer_token"]:
            boot["developer_token"] = cached.get("developer_token")
        if not boot["utsk"]:
            kodiutils.log_error("Could not obtain utsk token from tv.apple.com")
        else:
            self.auth.tokens["boot"] = {"utsk": boot["utsk"],
                                        "developer_token": boot["developer_token"],
                                        "storefront": boot["storefront"]}
            self.auth.save()
        self._boot = boot
        return boot

    def _media_user_token(self):
        tok = (kodiutils.get_setting("media_user_token")
               or self._bootstrap().get("user_token")
               or self.auth.tokens.get("media_user_token"))
        if tok:
            return tok
        # Signed in but no token yet: mint one via the store-login exchange.
        if self.auth.tokens.get("authenticated"):
            dev = self._bootstrap().get("developer_token")
            if dev:
                return self.auth.authorize_media(dev)
        return None

    def _storefront(self):
        return kodiutils.get_setting("storefront") or self._bootstrap().get("storefront") or DEFAULT_STOREFRONT

    def _locale(self):
        return kodiutils.get_setting("locale") or DEFAULT_LOCALE

    def _country(self):
        loc = self._locale()
        return (loc.split("-")[-1] if "-" in loc else "us").lower()

    def _params(self, extra=None):
        params = {
            "caller": "web",
            "pfm": "web",
            "v": UTS_VERSION,
            "locale": self._locale(),
            "sf": self._storefront(),
            "utscf": UTS_CLIENT_FLAGS,
            "utsk": self._bootstrap().get("utsk") or "",
        }
        if extra:
            params.update(extra)
        return params

    def _get_json(self, path, extra_params=None):
        try:
            resp = self.session.get(UTS_BASE + path, params=self._params(extra_params), timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("UTS %s -> %s %s" % (path, resp.status_code, resp.text[:200]))
                return None
            return resp.json()
        except Exception as exc:
            kodiutils.log_error("UTS request error %s: %s" % (path, exc))
            return None

    # -- catalogue -------------------------------------------------------

    def get_originals_shelves(self):
        data = self._get_json(
            "/canvases/channels/%s" % APPLE_TV_PLUS_CHANNEL,
            {"includePlatter": "true", "platterPassThrough": "true"},
        )
        shelves = self._extract_shelves(data)
        # Cache items so opening a shelf shows the full list (they're already here).
        cache = {s["id"]: s["items"] for s in shelves if s.get("id")}
        kodiutils.write_json(CANVAS_CACHE, cache)
        return shelves

    def get_shelf_items(self, shelf_id):
        cache = kodiutils.read_json(CANVAS_CACHE, default={}) or {}
        return cache.get(shelf_id, [])

    def search(self, query):
        data = self._get_json("/search", {"searchTerm": query, "topResultsOnly": "true"})
        items = []
        for shelf in self._extract_shelves(data):
            items.extend(shelf["items"])
        return items

    def get_show_episodes(self, show_id, page=30, max_pages=25):
        """Return a show's episodes (paginated via nextToken 'offset:size')."""
        episodes = []
        offset = 0
        for _ in range(max_pages):
            data = self._get_json(
                "/shows/%s/episodes" % show_id,
                {"nextToken": "%d:%d" % (offset, page),
                 "includeSeasonSummary": "false",
                 "selectedSeasonEpisodesOnly": "false"},
            )
            raw_eps = self._deep_find(data, "episodes") if data else None
            if not raw_eps:
                break
            for raw in raw_eps:
                item = self._map_item(raw, force_type="Episode")
                if item:
                    episodes.append(item)
            if len(raw_eps) < page:
                break
            offset += page
        return episodes

    # -- playback --------------------------------------------------------

    def get_playback(self, content_id, item_type="Movie"):
        """Resolve a title to an ISA-playable dict, or None."""
        boot = self._bootstrap()
        bearer = boot.get("developer_token")
        if not bearer:
            kodiutils.log_error("No developer token; cannot request playback")
            return None

        assets = self._prepare_playback(content_id, item_type)
        if not assets:
            return None

        mut = assets.get("user_token") or self._media_user_token()
        if not mut:
            kodiutils.log_error(
                "No media-user-token. Sign in (it is read from tv.apple.com when "
                "signed in), or paste one into the addon's advanced settings.")
            return None

        # Headers the manifest/segment requests need (token-authenticated).
        stream_headers = {
            "authorization": "Bearer " + bearer,
            "media-user-token": mut,
            "Origin": WEB_HOME,
            "User-Agent": self.session.headers.get("User-Agent", ""),
        }
        wv_keys = self._collect_widevine_keys(assets["manifest"], stream_headers)
        kodiutils.log("Collected %d Widevine key(s)" % len(wv_keys))

        # First key collected is the video variant's; used to pre-initialise DRM
        # so a decrypter exists before the first encrypted chapter.
        pre_init = None
        for kid_hex, uri in wv_keys.items():
            pssh_b64 = self._pssh_from_data_uri(uri)
            if pssh_b64:
                import base64 as _b64
                pre_init = "%s|%s" % (
                    pssh_b64,
                    _b64.b64encode(bytes.fromhex(kid_hex)).decode("ascii"))
            break

        kodiutils.write_json("playback_context.json", {
            "bearer": bearer,
            "media_user_token": mut,
            "adam_id": assets.get("adam_id", ""),
            "svc_id": assets.get("svc_id", ""),
            "is_external": assets.get("is_external", True),
            "wv_keys": wv_keys,
            "license_server": assets.get("license_server", ""),
            "user_agent": self.session.headers.get("User-Agent", ""),
        })
        return {
            # Served through the local proxy so the KEYID Apple omits can be
            # added; without it ISA decrypts with an all-zero key id.
            "manifest": license_proxy.manifest_url(assets["manifest"]),
            "manifest_type": "hls",
            "license_url": license_proxy.license_url(),
            "certificate_b64": self.get_widevine_certificate(),
            "stream_headers": stream_headers,
            "pre_init_data": pre_init,
        }

    def _prepare_playback(self, content_id, item_type):
        """Resolve playback assets via the UTS JSON endpoint.

        GET /api/uts/v3/{movies|episodes}/{id} with a Bearer developer token and
        the account media-user-token returns the playables, including hlsUrl (the
        manifest with its ?t= token), fpsKeyServerUrl and the asset ids. This
        avoids scraping the HTML page.
        """
        override = kodiutils.get_setting("manifest_url_override")
        if override:
            override = override.replace("&amp;", "&").strip()
            return {"manifest": override, "adam_id": self._q(override, "a"),
                    "svc_id": self._q(override, "svcId"), "is_external": True}

        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        headers = {}
        if bearer:
            headers["authorization"] = "Bearer " + bearer
        if mut:
            headers["media-user-token"] = mut
            headers["Origin"] = WEB_HOME

        # Sporting events (umc.cse.*) are not movies or episodes and are not
        # supported; the movies endpoint returns 404 for them.
        if str(content_id).startswith("umc.cse."):
            kodiutils.log_error("Live sports events are not supported (%s)" % content_id)
            return None

        endpoint = "episodes" if str(item_type) == "Episode" else "movies"
        text = self._get_text("/%s/%s" % (endpoint, content_id),
                              {"ctx_brand": APPLE_TV_PLUS_CHANNEL}, headers)
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            kodiutils.log_error("Playback response was not JSON")
            return None

        # A title can have several playables (feature you are entitled to, an
        # unentitled purchase option, trailers). Pick the entitled one with a
        # stream -- grabbing the first hlsUrl in the JSON returns the trailer.
        assets = self._select_playable_assets(data)
        if not assets or not assets.get("hlsUrl"):
            kodiutils.log_error(
                "No playable stream for %s. Likely not in your subscription/"
                "region, or no media-user-token." % content_id)
            return None

        hls = assets["hlsUrl"].encode("utf-8").decode("unicode_escape").replace("&amp;", "&")
        qp = assets.get("fpsKeyServerQueryParameters") or {}
        return {
            "manifest": hls,
            "user_token": mut,
            "license_server": assets.get("fpsKeyServerUrl"),
            "adam_id": str(assets.get("assetAdamId") or qp.get("adamId") or self._q(hls, "a")),
            "svc_id": qp.get("svcId") or self._q(hls, "svcId"),
            "is_external": qp.get("isExternal", True),
        }

    def _select_playable_assets(self, data):
        """Return the assets of the entitled, streamable playable."""
        playables = self._deep_find(data, "playables")
        if isinstance(playables, dict):
            candidates = list(playables.values())
        elif isinstance(playables, list):
            candidates = playables
        else:
            candidates = []

        def has_stream(p):
            return isinstance(p, dict) and isinstance(p.get("assets"), dict) \
                and p["assets"].get("hlsUrl")

        entitled = [p for p in candidates if has_stream(p) and p.get("isEntitledToPlay")]
        # Prefer the Apple TV+ channel when more than one is entitled.
        for p in entitled:
            if p.get("channelId") == APPLE_TV_PLUS_CHANNEL:
                return p["assets"]
        if entitled:
            return entitled[0]["assets"]
        # Fallback: any playable that carries a stream.
        for p in candidates:
            if has_stream(p):
                return p["assets"]
        return None

    def _get_text(self, path, extra_params, headers):
        try:
            resp = self.session.get(UTS_BASE + path, params=self._params(extra_params),
                                    headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("UTS %s -> %s %s" % (path, resp.status_code, resp.text[:200]))
                return None
            return resp.text
        except Exception as exc:
            kodiutils.log_error("UTS request error %s: %s" % (path, exc))
            return None

    def _collect_widevine_keys(self, manifest_url, headers=None):
        """Map each Widevine key id (hex) to its data: URI from the manifest.

        Apple's licence server needs the URI of the exact key a challenge is
        for; a title has separate video and audio keys, and sending the wrong
        one (or none) makes fpsRequest return 500. Index the keys by the key id
        inside each PSSH so the proxy can pick the right one per challenge.
        """
        keys = {}
        # Variant playlists are authenticated by the token in their URL; the web
        # player requests them with no authorization/media-user-token headers,
        # and sending those makes the request fail.
        plain = {k: v for k, v in (headers or {}).items()
                 if k.lower() in ("user-agent", "origin")}
        try:
            master = self.session.get(manifest_url, headers=headers, timeout=30).text
            base = manifest_url.rsplit("/", 1)[0] + "/"
            targets = []
            for line in master.splitlines():
                s = line.strip()
                if s and not s.startswith("#") and "playlist.m3u8" in s:
                    targets.append(s if s.startswith("http") else base + s)
                    break
            for line in master.splitlines():
                if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line:
                    m = re.search(r'URI="([^"]+)"', line)
                    if m:
                        u = m.group(1)
                        targets.append(u if u.startswith("http") else base + u)
                        break
            for url in targets:
                try:
                    text = self.session.get(url, headers=plain, timeout=30).text
                except Exception as exc:
                    kodiutils.log_error("Variant fetch failed: %s" % exc)
                    continue
                for line in text.splitlines():
                    if "urn:uuid:edef8ba9" in line:
                        m = re.search(r'URI="([^"]+)"', line)
                        if m:
                            kid = self._kid_from_data_uri(m.group(1))
                            if kid:
                                keys[kid] = m.group(1)
        except Exception as exc:
            kodiutils.log_error("Widevine key collection failed: %s" % exc)
        return keys

    @staticmethod
    def _pssh_from_data_uri(uri):
        """Return the base64 PSSH carried in a key's data: URI."""
        try:
            from urllib.parse import unquote
            if "base64," not in uri:
                return None
            return unquote(uri.split("base64,", 1)[1]).strip()
        except Exception:
            return None

    @staticmethod
    def _kid_from_data_uri(uri):
        """Extract the 16-byte key id (hex) from a Widevine data: URI PSSH."""
        try:
            import base64
            from urllib.parse import unquote
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

    def get_widevine_certificate(self):
        try:
            resp = self.session.get(WIDEVINE_CERT_URL, timeout=30)
            if resp.status_code == 200:
                # The endpoint returns the raw DER certificate; ISA wants it as
                # correctly-padded base64.
                import base64
                return base64.b64encode(resp.content).decode("ascii")
        except Exception as exc:
            kodiutils.log_error("Widevine cert fetch failed: %s" % exc)
        return None

    @staticmethod
    def _q(url, key):
        m = re.search(r"[?&]%s=([^&]+)" % re.escape(key), url)
        return m.group(1) if m else ""

    # -- response parsing helpers ---------------------------------------

    def _extract_shelves(self, data):
        if not data:
            return []
        shelves = []
        for shelf in self._as_list(self._deep_find(data, "shelves")):
            if not isinstance(shelf, dict):
                continue
            title = self._shelf_title(shelf)
            shelf_id = shelf.get("id") or shelf.get("channelId") or ""
            items = self._extract_items(shelf.get("items"))
            if items and shelf_id:
                shelves.append({"id": str(shelf_id), "title": title, "items": items})
        return shelves

    def _shelf_title(self, shelf):
        header = shelf.get("header")
        if isinstance(header, dict):
            t = self._deep_find(header, "title")
            if isinstance(t, str) and t:
                return t
        return shelf.get("title") or shelf.get("displayType") or "More"

    def _extract_items(self, items):
        results = []
        for raw in self._as_list(items):
            item = self._map_item(raw)
            if item:
                results.append(item)
        return results

    def _map_item(self, raw, force_type=None):
        if not isinstance(raw, dict):
            return None
        item_id = raw.get("id") or raw.get("canonicalId") or raw.get("adamId")
        title = raw.get("title") or raw.get("name")
        item_type = force_type or raw.get("type") or "Movie"
        # Skip non-playable promo/brand/trailer tiles.
        if not item_id or not title or item_type in ("Brand", "Upsell", "Preview"):
            return None
        rel = raw.get("releaseDate")
        year = rel[:4] if isinstance(rel, str) and len(rel) >= 4 else None
        long_desc = raw.get("longDescription")
        plot = raw.get("description") or (long_desc.get("standard") if isinstance(long_desc, dict) else "") or ""
        season = raw.get("seasonNumber")
        episode = raw.get("episodeNumber")
        label = title
        if item_type == "Episode" and season and episode:
            label = "S%dE%d · %s" % (season, episode, title)
        return {
            "id": item_id,
            "title": label,
            "sort_title": title,
            "type": item_type,
            "plot": plot,
            "year": year,
            "season": season,
            "episode": episode,
            "duration": raw.get("duration"),
            "art": self._item_art(raw.get("images") or {}),
        }

    def _item_art(self, images):
        for key in IMAGE_KEYS:
            val = images.get(key)
            if isinstance(val, dict) and val.get("url"):
                return (val["url"].replace("{w}", "1920").replace("{h}", "1080")
                        .replace("{f}", "jpg").replace("{c}", "").replace("{cropcode}", ""))
        return None

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
        return []

    def _deep_find(self, data, key):
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for value in data.values():
                found = self._deep_find(value, key)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = self._deep_find(value, key)
                if found is not None:
                    return found
        return None
