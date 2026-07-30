"""Apple TV catalogue and playback API.

Apple exposes no documented API. The Apple TV web app talks to an internal
"UTS" service at tv.apple.com/api/uts/v3 for catalogue data, and resolves
playback through play.itunes.apple.com. This module mirrors those calls as
closely as they can be reproduced without Apple's cooperation.

The catalogue endpoints (browse/search/detail) are metadata-only and tend to
work with an anonymous bootstrap token. The playback resolution
(``get_playback``) is the part most dependent on Apple's private, changing
services: it must return a DASH/HLS manifest plus a Widevine licence URL that
InputStream Adaptive can use. Where Apple's response shape differs from what is
assumed here, the raw responses are logged so the mapping can be corrected.
"""

import json
import re

from . import kodiutils

UTS_BASE = "https://tv.apple.com/api/uts/v3"
WEB_HOME = "https://tv.apple.com"
PLAY_BASE = "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa"

# Default US storefront; overridable in settings for other regions.
DEFAULT_STOREFRONT = "143441"
DEFAULT_LOCALE = "en-US"

# Apple TV+ Originals "brand" page id used by the web app.
APPLE_TV_PLUS_BRAND = "tv.apple.com/brand/tvs.sbd.4000"


class AppleTVApi(object):
    def __init__(self, auth):
        self.auth = auth
        self.session = auth.session
        self._bootstrap_token = None

    # -- shared request params ------------------------------------------

    def _storefront(self):
        return kodiutils.get_setting("storefront") or DEFAULT_STOREFRONT

    def _locale(self):
        return kodiutils.get_setting("locale") or DEFAULT_LOCALE

    def _common_params(self):
        return {
            "caller": "web",
            "sf": self._storefront(),
            "v": "68",
            "pfm": "web",
            "mfr": "Apple",
            "locale": self._locale(),
            "l": self._locale(),
            "utsk": self._get_bootstrap_token() or "",
        }

    def _get_bootstrap_token(self):
        """Extract the anonymous UTS token the web app embeds in its shell."""
        if self._bootstrap_token:
            return self._bootstrap_token
        stored = self.auth.tokens.get("uts_token")
        if stored:
            self._bootstrap_token = stored
            return stored
        try:
            resp = self.session.get(WEB_HOME, timeout=30)
            # The token is embedded in the page's serialised config JSON.
            match = re.search(r'"utsk"\s*:\s*"([^"]+)"', resp.text)
            if not match:
                match = re.search(r'token%3D([0-9a-f]{16,})', resp.text)
            if match:
                self._bootstrap_token = match.group(1)
                self.auth.tokens["uts_token"] = self._bootstrap_token
                self.auth.save()
                return self._bootstrap_token
            kodiutils.log_error("Could not locate UTS bootstrap token on tv.apple.com")
        except Exception as exc:
            kodiutils.log_error("Bootstrap token fetch failed: %s" % exc)
        return None

    def _get_json(self, url, extra_params=None):
        params = self._common_params()
        if extra_params:
            params.update(extra_params)
        try:
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("UTS GET %s -> %s %s" % (url, resp.status_code, resp.text[:200]))
                return None
            return resp.json()
        except Exception as exc:
            kodiutils.log_error("UTS request error for %s: %s" % (url, exc))
            return None

    # -- catalogue -------------------------------------------------------

    def get_originals_shelves(self):
        """Return the shelves on the Apple TV+ Originals brand page."""
        data = self._get_json(
            "%s/pages/brand" % UTS_BASE,
            {"brandId": APPLE_TV_PLUS_BRAND, "nextToken": ""},
        )
        return self._extract_shelves(data)

    def get_canvas(self, page_type="Watchnow"):
        """Return shelves from a top-level page (Watchnow, MoviesGenre, etc.)."""
        data = self._get_json(
            "%s/pages/frameworks/appletv/pages/%s" % (UTS_BASE, page_type),
            {"nextToken": ""},
        )
        return self._extract_shelves(data)

    def get_shelf_items(self, shelf_id):
        data = self._get_json("%s/shelves/%s" % (UTS_BASE, shelf_id), {"nextToken": ""})
        return self._extract_items(data)

    def search(self, query):
        data = self._get_json(
            "%s/search/incremental" % UTS_BASE,
            {"q": query, "searchSessionId": ""},
        )
        return self._extract_items(data)

    def get_detail(self, item_id, item_type="Movie"):
        endpoint = "movies" if item_type.lower() == "movie" else "shows"
        return self._get_json("%s/%s/%s" % (UTS_BASE, endpoint, item_id), {})

    # -- iTunes purchased/rented library --------------------------------

    def get_itunes_library(self):
        """Purchased and rented movies from the account's library.

        Requires an authenticated session; anonymous callers get nothing.
        """
        if not self.auth.is_authenticated():
            return []
        data = self._get_json(
            "%s/personal/library" % UTS_BASE,
            {"types": "Movie", "nextToken": ""},
        )
        return self._extract_items(data)

    # -- playback --------------------------------------------------------

    def get_playback(self, item_id, item_type="Movie"):
        """Resolve a title to a Widevine-playable stream.

        Returns a dict::

            {"manifest": <url>, "manifest_type": "mpd"|"hls",
             "license_url": <url>, "license_headers": {...}}

        or None if resolution failed. This is the most Apple-private step; the
        assumed response shape is logged when it does not match so the mapping
        can be fixed against a real capture.
        """
        if not self.auth.is_authenticated():
            kodiutils.log_error("Playback requires sign-in")
            return None

        params = self._common_params()
        params.update({"id": item_id, "type": item_type})
        try:
            resp = self.session.get(
                "%s/subscriptionPlayables" % PLAY_BASE,
                params=params,
                timeout=30,
            )
            if resp.status_code != 200:
                kodiutils.log_error("Playables GET -> %s %s" % (resp.status_code, resp.text[:300]))
                return None
            data = resp.json()
        except Exception as exc:
            kodiutils.log_error("Playables request failed: %s" % exc)
            return None

        resolved = self._map_playback(data)
        if not resolved:
            kodiutils.log_error(
                "Could not map playback response; raw shape: %s"
                % json.dumps(data)[:600]
            )
        return resolved

    def _map_playback(self, data):
        """Pull a Widevine manifest + licence URL out of a playables response."""
        try:
            assets = self._deep_find(data, "hls-cbcs") or self._deep_find(data, "streaming")
            manifest = None
            manifest_type = "hls"
            license_url = None

            # Look for a DASH/Widevine asset first (better ISA support),
            # otherwise fall back to HLS.
            dash = self._deep_find(data, "widevine")
            if isinstance(dash, dict):
                manifest = dash.get("url") or dash.get("manifest")
                license_url = dash.get("license-server") or dash.get("licenseUrl")
                manifest_type = "mpd"

            if not manifest and isinstance(assets, dict):
                manifest = assets.get("url") or assets.get("manifest")
                license_url = assets.get("license-server") or assets.get("licenseUrl")

            if manifest and license_url:
                return {
                    "manifest": manifest,
                    "manifest_type": manifest_type,
                    "license_url": license_url,
                    "license_headers": {},
                }
        except Exception as exc:
            kodiutils.log_error("Playback mapping error: %s" % exc)
        return None

    # -- response parsing helpers ---------------------------------------

    def _extract_shelves(self, data):
        if not data:
            return []
        canvas = self._deep_find(data, "canvas") or data.get("data", {})
        shelves = []
        for shelf in self._as_list(self._deep_find(canvas, "shelves")):
            title = shelf.get("title") or shelf.get("displayType") or "Untitled"
            shelf_id = shelf.get("id") or shelf.get("adamId")
            items = self._extract_items(shelf)
            shelves.append({"id": shelf_id, "title": title, "items": items})
        return shelves

    def _extract_items(self, data):
        if not data:
            return []
        raw_items = self._as_list(self._deep_find(data, "items"))
        results = []
        for raw in raw_items:
            item = self._map_item(raw)
            if item:
                results.append(item)
        return results

    def _map_item(self, raw):
        if not isinstance(raw, dict):
            return None
        item_id = raw.get("id") or raw.get("adamId") or raw.get("canonicalId")
        title = raw.get("title") or raw.get("name")
        if not item_id or not title:
            return None
        item_type = raw.get("type") or raw.get("kind") or "Movie"
        images = raw.get("images") or {}
        artwork = images.get("coverArt16X9") or images.get("coverArt") or {}
        art_url = None
        if isinstance(artwork, dict):
            art_url = artwork.get("url")
            if art_url:
                art_url = art_url.replace("{w}", "1920").replace("{h}", "1080").replace("{f}", "jpg")
        return {
            "id": item_id,
            "title": title,
            "type": item_type,
            "plot": raw.get("description") or (raw.get("longDescription") or {}).get("standard", ""),
            "year": raw.get("releaseDate", "")[:4] if raw.get("releaseDate") else None,
            "art": art_url,
            "duration": raw.get("duration"),
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
        """Depth-first search for the first value stored under ``key``."""
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
