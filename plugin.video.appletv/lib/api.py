"""Apple TV catalogue and playback API (reconstructed from the web client).

Endpoints and parameters here were captured from a real tv.apple.com session:

  Catalogue (anonymous browse works):
    GET tv.apple.com/api/uts/v3/configurations
    GET tv.apple.com/api/uts/v3/canvases/channels/tvs.sbd.4000   (TV+ Originals)
    GET tv.apple.com/api/uts/v3/search?q=...
    GET tv.apple.com/api/uts/v3/contents/{id}/...                (title detail)
  Tokens (scraped from the tv.apple.com HTML shell):
    developerToken  -> Bearer app token used for playback/licence calls
    utsk            -> UTS session token used for catalogue calls
  Playback:
    GET play-edge.itunes.apple.com/.../hls/subscription/playlist.m3u8?...&t=<tok>
    GET play.itunes.apple.com/.../wa/widevineCert
    POST play-edge.itunes.apple.com/.../wa/fpsRequest  (Widevine, JSON-wrapped)

Two playback tokens are minted by Apple between page-load and pressing play and
were not in the capture: the account ``media-user-token`` and the per-title
playback token (the manifest ``t=`` value). They can be supplied via settings
(pasted from a capture) until the mint calls are reproduced; the code logs
clearly when they are missing.
"""

import json
import re

from . import kodiutils
from . import license_proxy

UTS_BASE = "https://tv.apple.com/api/uts/v3"
WEB_HOME = "https://tv.apple.com"
WIDEVINE_CERT_URL = "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/widevineCert"
PLAY_EDGE = "https://play-edge.itunes.apple.com/WebObjects/MZPlayLocal.woa"

DEFAULT_STOREFRONT = "143441"
DEFAULT_LOCALE = "en-US"
UTS_VERSION = "96"
UTS_CLIENT_FLAGS = "OjAAAAEAAAAAAAIAEAAAACMAKwAtAA~~"

APPLE_TV_PLUS_CHANNEL = "tvs.sbd.4000"
SUBSCRIPTION_SVC_ID = "tvs.vds.4105"


class AppleTVApi(object):
    def __init__(self, auth):
        self.auth = auth
        self.session = auth.session
        self._boot = None  # cached {utsk, developer_token, storefront}

    # -- bootstrap (scrape tokens from the web shell) --------------------

    def _bootstrap(self):
        if self._boot is not None:
            return self._boot
        cached = self.auth.tokens.get("boot")
        if cached and cached.get("utsk") and cached.get("developer_token"):
            self._boot = cached
            return cached
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
        self.auth.tokens["boot"] = boot
        self.auth.save()
        return boot

    def developer_token(self):
        return self._bootstrap().get("developer_token")

    def _storefront(self):
        return kodiutils.get_setting("storefront") or self._bootstrap().get("storefront") or DEFAULT_STOREFRONT

    def _locale(self):
        return kodiutils.get_setting("locale") or DEFAULT_LOCALE

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
        url = UTS_BASE + path
        try:
            resp = self.session.get(url, params=self._params(extra_params), timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("UTS %s -> %s %s" % (path, resp.status_code, resp.text[:200]))
                return None
            return resp.json()
        except Exception as exc:
            kodiutils.log_error("UTS request error %s: %s" % (path, exc))
            return None

    # -- catalogue -------------------------------------------------------

    def get_originals_shelves(self):
        """Apple TV+ Originals channel canvas -> list of shelves."""
        data = self._get_json(
            "/canvases/channels/%s" % APPLE_TV_PLUS_CHANNEL,
            {"includePlatter": "true", "platterPassThrough": "true"},
        )
        return self._extract_shelves(data)

    def get_movies_shelves(self):
        data = self._get_json("/canvases/pages/Movies", {"includePlatter": "true"})
        return self._extract_shelves(data)

    def get_shelf_items(self, shelf_id):
        data = self._get_json("/shelves/%s" % shelf_id, {"nextToken": ""})
        return self._extract_items(data)

    def search(self, query):
        data = self._get_json("/search", {"q": query})
        return self._extract_items(data)

    def get_itunes_library(self):
        if not self.auth.is_authenticated():
            return []
        data = self._get_json("/personal/library", {"types": "Movie"})
        return self._extract_items(data)

    def get_detail(self, content_id):
        return self._get_json("/contents/%s/player-tabs" % content_id, {})

    # -- playback --------------------------------------------------------

    def get_playback(self, content_id, item_type="Movie"):
        """Resolve a title to an ISA-playable dict, or None.

        Returns::
            {"manifest": <hls url>, "manifest_type": "hls",
             "license_url": <local proxy url>, "certificate_url": <cert url>}
        """
        if not self.auth.is_authenticated():
            kodiutils.log_error("Playback requires sign-in")
            return None

        boot = self._bootstrap()
        bearer = boot.get("developer_token")
        mut = kodiutils.get_setting("media_user_token") or self.auth.tokens.get("media_user_token")
        if not bearer:
            kodiutils.log_error("No developer token; cannot request playback")
            return None
        if not mut:
            kodiutils.log_error(
                "No media-user-token. Paste one captured from tv.apple.com into the "
                "addon's advanced settings to test playback.")
            return None

        prepared = self._prepare_playback(content_id, bearer, mut)
        if not prepared:
            return None

        # Publish per-playback context for the licence proxy.
        kodiutils.write_json("playback_context.json", {
            "bearer": bearer,
            "media_user_token": mut,
            "skd_uri": prepared.get("skd_uri", ""),
            "adam_id": prepared.get("adam_id", ""),
        })
        return {
            "manifest": prepared["manifest"],
            "manifest_type": "hls",
            "license_url": license_proxy.license_url(),
            "certificate_b64": self.get_widevine_certificate(),
        }

    def get_widevine_certificate(self):
        """Fetch Apple's Widevine service certificate (base64 text)."""
        try:
            resp = self.session.get(WIDEVINE_CERT_URL, timeout=30)
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception as exc:
            kodiutils.log_error("Widevine cert fetch failed: %s" % exc)
        return None

    def _prepare_playback(self, content_id, bearer, mut):
        """Obtain the tokenised HLS manifest URL for a title.

        The web app mints a per-title playback token (the manifest ``t=`` value)
        via a private prepare call that was not in the capture. Until it is
        reproduced, an advanced setting may supply a full manifest URL directly
        so the rest of the pipeline (ISA + Widevine proxy) can be validated.
        """
        override = kodiutils.get_setting("manifest_url_override")
        if override:
            adam = ""
            m = re.search(r"[?&]a=(\d+)", override)
            if m:
                adam = m.group(1)
            return {"manifest": override, "adam_id": adam, "skd_uri": ""}

        adam_id = self._resolve_adam_id(content_id)
        if not adam_id:
            kodiutils.log_error("Could not resolve adamId for %s" % content_id)
            return None
        kodiutils.log_error(
            "Playback prepare call not yet reproduced: need the per-title 't=' "
            "token for adamId %s. Capture a play session and share it, or paste a "
            "manifest URL override in settings." % adam_id)
        return None

    def _resolve_adam_id(self, content_id):
        """Pull the playable adamId from a title's detail (playablePassThrough)."""
        detail = self.get_detail(content_id)
        if not detail:
            return None
        # The adamId travels inside a base64 'playablePassThrough' or a playables map.
        adam = self._deep_find(detail, "adamId")
        if adam:
            return str(adam)
        return None

    # -- response parsing helpers ---------------------------------------

    def _extract_shelves(self, data):
        if not data:
            return []
        shelves = []
        for shelf in self._as_list(self._deep_find(data, "shelves")):
            if not isinstance(shelf, dict):
                continue
            title = shelf.get("title") or shelf.get("displayType") or "More"
            shelf_id = shelf.get("id") or shelf.get("adamId") or ""
            items = self._extract_items(shelf)
            if items:
                shelves.append({"id": shelf_id, "title": title, "items": items})
        return shelves

    def _extract_items(self, data):
        if not data:
            return []
        results = []
        for raw in self._as_list(self._deep_find(data, "items")):
            item = self._map_item(raw)
            if item:
                results.append(item)
        return results

    def _map_item(self, raw):
        if not isinstance(raw, dict):
            return None
        item_id = raw.get("id") or raw.get("canonicalId") or raw.get("adamId")
        title = raw.get("title") or raw.get("name")
        if not item_id or not title:
            return None
        images = raw.get("images") or {}
        art = images.get("coverArt16X9") or images.get("coverArt") or images.get("previewFrame") or {}
        art_url = None
        if isinstance(art, dict) and art.get("url"):
            art_url = art["url"].replace("{w}", "1920").replace("{h}", "1080").replace("{f}", "jpg").replace("{c}", "")
        rel = raw.get("releaseDate")
        year = None
        if isinstance(rel, str) and len(rel) >= 4:
            year = rel[:4]
        return {
            "id": item_id,
            "title": title,
            "type": raw.get("type") or "Movie",
            "plot": raw.get("description") or (raw.get("longDescription") or {}).get("standard", "") if isinstance(raw.get("longDescription"), dict) else raw.get("description", ""),
            "year": year,
            "art": art_url,
        }

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
