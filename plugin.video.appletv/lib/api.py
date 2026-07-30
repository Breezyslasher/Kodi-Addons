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
        boot = {"utsk": None, "developer_token": None, "storefront": DEFAULT_STOREFRONT}
        try:
            html = self.session.get(WEB_HOME, timeout=30).text
            m = re.search(r'"utsk"\s*:\s*"([^"]+)"', html)
            if m:
                boot["utsk"] = m.group(1)
            m = re.search(r'"developerToken"\s*:\s*"([A-Za-z0-9_.\-]{20,})"', html)
            if m:
                boot["developer_token"] = m.group(1)
            m = re.search(r'"storefrontId"\s*:\s*"?(\d+)"?', html)
            if m:
                boot["storefront"] = m.group(1)
        except Exception as exc:
            kodiutils.log_error("Bootstrap scrape failed: %s" % exc)
        if not boot["utsk"]:
            kodiutils.log_error("Could not scrape utsk token from tv.apple.com")
        self._boot = boot
        return boot

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

        mut = (assets.get("user_token")
               or kodiutils.get_setting("media_user_token")
               or self.auth.tokens.get("media_user_token"))
        if not mut:
            kodiutils.log_error(
                "No media-user-token found. Sign in, or paste one from a "
                "tv.apple.com capture into the addon's advanced settings.")
            return None

        # Headers the manifest/segment requests need (token-authenticated).
        stream_headers = {
            "authorization": "Bearer " + bearer,
            "media-user-token": mut,
            "Origin": WEB_HOME,
            "User-Agent": self.session.headers.get("User-Agent", ""),
        }
        wv_uri = self._extract_widevine_uri(assets["manifest"], stream_headers)

        kodiutils.write_json("playback_context.json", {
            "bearer": bearer,
            "media_user_token": mut,
            "adam_id": assets.get("adam_id", ""),
            "svc_id": assets.get("svc_id", ""),
            "is_external": assets.get("is_external", True),
            "wv_uri": wv_uri,
            "license_server": assets.get("license_server", ""),
        })
        return {
            "manifest": assets["manifest"],
            "manifest_type": "hls",
            "license_url": license_proxy.license_url(),
            "certificate_b64": self.get_widevine_certificate(),
            "stream_headers": stream_headers,
        }

    def _prepare_playback(self, content_id, item_type):
        """Scrape the server-rendered title page for playback assets.

        The page embeds hlsUrl (manifest incl. the per-title ?t= token),
        userToken (media-user-token), the licence server URL and the asset
        parameters. Requires the session to be signed in to tv.apple.com.
        """
        override = kodiutils.get_setting("manifest_url_override")
        if override:
            override = override.replace("&amp;", "&").strip()
            return {"manifest": override, "adam_id": self._q(override, "a"),
                    "svc_id": self._q(override, "svcId"), "is_external": True}

        html = self._fetch_title_page(content_id, item_type)
        if not html:
            return None

        def find(pattern):
            m = re.search(pattern, html)
            return m.group(1) if m else None

        hls = find(r'"hlsUrl"\s*:\s*"([^"]+)"')
        if not hls:
            kodiutils.log_error(
                "No hlsUrl on the title page for %s. This usually means the "
                "session is not signed in to tv.apple.com, or the title is not "
                "playable with your subscription/region." % content_id)
            return None
        hls = hls.encode("utf-8").decode("unicode_escape").replace("&amp;", "&")

        return {
            "manifest": hls,
            "user_token": find(r'"userToken"\s*:\s*"([^"]+)"'),
            "license_server": find(r'"fpsKeyServerUrl"\s*:\s*"([^"]+)"'),
            "adam_id": find(r'"assetAdamId"\s*:\s*"?(\d+)"?') or self._q(hls, "a"),
            "svc_id": find(r'"svcId"\s*:\s*"([^"]+)"') or self._q(hls, "svcId"),
            "is_external": True,
        }

    def _fetch_title_page(self, content_id, item_type):
        """Fetch the tv.apple.com title page, trying each content-type path."""
        cc = self._country()
        types = ["movie", "episode", "show"]
        t = (item_type or "").lower()
        if t in types:
            types.remove(t)
            types.insert(0, t)
        for kind in types:
            url = "%s/%s/%s/x/%s" % (WEB_HOME, cc, kind, content_id)
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200 and '"hlsUrl"' in resp.text:
                    return resp.text
            except Exception as exc:
                kodiutils.log_error("Title page fetch failed (%s): %s" % (kind, exc))
        # Last resort: the type-less canonical URL.
        try:
            resp = self.session.get("%s/%s/%s" % (WEB_HOME, cc, content_id), timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return None

    def _extract_widevine_uri(self, manifest_url, headers=None):
        """Pull the Widevine key data: URI from the manifest, for fpsRequest."""
        try:
            master = self.session.get(manifest_url, headers=headers, timeout=30).text
            variant = None
            base = manifest_url.rsplit("/", 1)[0] + "/"
            for line in master.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "playlist.m3u8" in line:
                    variant = line if line.startswith("http") else base + line
                    break
            text = self.session.get(variant, headers=headers, timeout=30).text if variant else master
            m = re.search(r'KEYFORMAT="urn:uuid:edef8ba9[^"]*"[^\n]*?URI="([^"]+)"', text)
            if not m:
                m = re.search(r'URI="(data:[^"]*edef8ba9[^"]*)"', text)
            return m.group(1) if m else ""
        except Exception as exc:
            kodiutils.log_error("Widevine URI extraction failed: %s" % exc)
            return ""

    def get_widevine_certificate(self):
        try:
            resp = self.session.get(WIDEVINE_CERT_URL, timeout=30)
            if resp.status_code == 200:
                return resp.text.strip()
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
