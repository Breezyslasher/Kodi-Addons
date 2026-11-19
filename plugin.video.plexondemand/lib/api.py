"""Plex free on-demand catalogue (tv.plex.provider.vod) API.

Every call here is confirmed by capture (tools/probe.py run from GitHub
Actions, US region, anonymous token):

  Provider list   GET plex.tv/media/providers  -> tv.plex.provider.vod at
                  baseURL https://vod.provider.plex.tv (read at runtime, the
                  constant below is only the fallback)
  Home rows       GET {vod}/hubs                -> ~73 Hub placeholders, each
                  key /hubs/sections/movies/<slug>
  Row contents    GET {vod}<hub key>            -> Metadata[] (paged with
                  X-Plex-Container-Start / -Size)
  Genres          GET {vod}/library/sections/movies/genre?type=1|2
                  -> Directory[] whose key is
                  /library/sections/movies/all?genre=<id>&type=1|2
                  (.../all without a genre answers 404)
  Title           GET {vod}/library/metadata/<ratingKey>
  Seasons/eps     GET {vod}/library/metadata/<ratingKey>/children
  Search          GET discover.provider.plex.tv/library/search
                  ?searchProviders=discover,plexAVOD -> SearchResults[] where
                  id == "plex" is "Free On Demand"
  Stream          Media[].Part[].key = /library/parts/<id>-hls.m3u8 and
                  -dash.mpd on the VOD host; the manifest needs X-Plex-Token
                  (401 without), segments are on vod-content.plexvideos.com
  Ads             Metadata.Ad[] = {type: vmap, url: {vod}/vmap?...}; client
                  side, nothing is stitched into the stream.
  Continue        GET {vod}/hubs/continueWatching -> Hub[].Metadata[] with
  Watching        viewOffset (filled by /timeline reports)
  Resume point    GET discover.provider.plex.tv/library/metadata/<rk>/userState
                  -> UserState[{viewOffset, viewCount, viewState}]
                  (/library/metadata on the VOD host carries no viewOffset)
  Trailers        Metadata.primaryExtraKey (/library/metadata/<rk>/extras/<id>)
                  -> Media[].Part[].key .../parts/hls.m3u8, which redirects to
                  an open HLS on vod-content.plexvideos.com
  Markers         GET /library/metadata/<rk>?includeMarkers=1 -> Marker[]
                  {type: intro|credits, startTimeOffset, endTimeOffset} (ms)
  Up next         POST {vod}/playQueues?uri=provider://tv.plex.provider.vod
                  /library/metadata/<rk>&type=video&continuous=1 -> the rest of
                  the show in order (Metadata[])
  Actions         PUT {vod}/actions/scrobble|removeFromContinueWatching
                  ?ratingKey=<rk> (from the provider's Feature "actions")

The VOD base is read from plex.tv/media/providers once per run (falls back to
the constant; the Advanced setting overrides both).
"""

import requests

from . import kodiutils
from .auth import PlexAuth, base_headers

VOD_BASE = "https://vod.provider.plex.tv"
DISCOVER_BASE = "https://discover.provider.plex.tv"
VOD_ID = "tv.plex.provider.vod"
PAGE_SIZE = 50
TIMEOUT = 25

TYPE_MOVIE = 1
TYPE_SHOW = 2


class PlexApiError(Exception):
    pass


class PlexVodApi(object):

    def __init__(self):
        self.auth = PlexAuth()
        self.session = requests.Session()
        self.base = kodiutils.get_setting("base_url_override").rstrip("/") or _provider_base(self)

    # ---- transport
    def headers(self):
        h = base_headers()
        h["X-Plex-Token"] = self.auth.token()
        return h

    def _get(self, url, params=None, method="GET", retry=True):
        if not url.startswith("http"):
            url = self.base + url
        resp = self.session.request(method, url, params=params, headers=self.headers(), timeout=TIMEOUT)
        if resp.status_code == 401 and retry and self.auth.invalidate():
            return self._get(url, params, method, retry=False)
        if resp.status_code >= 400:
            raise PlexApiError("%s %s -> HTTP %s" % (method, url.split("?")[0], resp.status_code))
        try:
            data = resp.json()
        except ValueError:
            raise PlexApiError("non-JSON answer from %s" % url.split("?")[0])
        return data.get("MediaContainer") or {}

    @staticmethod
    def page_params(start):
        return {"X-Plex-Container-Start": int(start or 0), "X-Plex-Container-Size": PAGE_SIZE}

    # ---- browse
    def hubs(self):
        return [h for h in self._get("/hubs").get("Hub") or [] if h.get("key")]

    def hub_items(self, key, start=0):
        c = self._get(key, self.page_params(start))
        return c.get("Metadata") or [], _total(c)

    def genres(self, media_type):
        return self._get("/library/sections/movies/genre", {"type": media_type}).get("Directory") or []

    def listing(self, key, start=0):
        c = self._get(key, self.page_params(start))
        return c.get("Metadata") or [], _total(c)

    def metadata(self, rating_key, markers=False):
        params = {"includeMarkers": 1} if markers else None
        items = self._get("/library/metadata/%s" % rating_key, params).get("Metadata") or []
        if not items:
            raise PlexApiError("no metadata for %s" % rating_key)
        return items[0]

    def children(self, rating_key, start=0):
        c = self._get("/library/metadata/%s/children" % rating_key, self.page_params(start))
        return c.get("Metadata") or [], _total(c)

    def search(self, query):
        c = self._get(DISCOVER_BASE + "/library/search", {
            "query": query, "searchTypes": "movies,tv", "searchProviders": "discover,plexAVOD",
            "includeMetadata": 1, "limit": 30})
        out = []
        for section in c.get("SearchResults") or []:
            if section.get("id") != "plex":
                continue
            for result in section.get("SearchResult") or []:
                meta = result.get("Metadata")
                if meta and meta.get("ratingKey"):
                    out.append(meta)
        return out


    # ---- watch state
    def continue_watching(self):
        c = self._get("/hubs/continueWatching")
        items = list(c.get("Metadata") or [])
        for hub in c.get("Hub") or []:
            items.extend(hub.get("Metadata") or [])
        return items

    def user_state(self, rating_key):
        try:
            c = self._get(DISCOVER_BASE + "/library/metadata/%s/userState" % rating_key)
        except PlexApiError as exc:
            kodiutils.log("userState %s: %s" % (rating_key, exc))
            return {}
        return (c.get("UserState") or [{}])[0]

    def user_states(self, rating_keys):
        """{ratingKey: UserState} for many titles in one call (comma-joined)."""
        out = {}
        keys = [k for k in rating_keys if k]
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            try:
                c = self._get(DISCOVER_BASE + "/library/metadata/%s/userState" % ",".join(chunk))
            except PlexApiError as exc:
                kodiutils.log("userState batch: %s" % exc)
                continue
            for st in c.get("UserState") or []:
                out[st.get("ratingKey")] = st
        return out

    def subtitle_url(self, stream_key, fmt="srt"):
        """Open CDN URL of a subtitle stream (/library/streams/<id>-<lang>);
        the provider redirects ?format=srt|vtt to vod-static.plex.tv."""
        url = stream_key if stream_key.startswith("http") else self.base + stream_key
        resp = self.session.get(url, params={"format": fmt}, headers=self.headers(), timeout=TIMEOUT,
                                stream=True)
        resp.close()
        return resp.url if resp.status_code == 200 else None

    def action(self, name, rating_key):
        return self._get("/actions/%s" % name, {"ratingKey": rating_key}, method="PUT")

    # ---- extras / up next
    def trailer(self, item):
        """(title, playable url) of the title's trailer, or None."""
        key = item.get("primaryExtraKey")
        extras = []
        if key:
            extras = self._get(key).get("Metadata") or []
        if not extras:
            extras = self._get("/library/metadata/%s/extras" % item["ratingKey"]).get("Metadata") or []
        extras.sort(key=lambda e: 0 if e.get("subtype") == "trailer" else 1)
        for extra in extras:
            for media in extra.get("Media") or []:
                for part in media.get("Part") or []:
                    if part.get("key"):
                        url = part["key"] if part["key"].startswith("http") else self.base + part["key"]
                        # The part answers with a redirect to an open HLS URL on
                        # the CDN; hand Kodi that, so no token rides along.
                        resp = self.session.get(url, headers=self.headers(), timeout=TIMEOUT)
                        if resp.status_code == 200:
                            return extra.get("title") or "Trailer", resp.url
        return None

    def next_item(self, rating_key):
        """The item after rating_key in a continuous play queue (next episode)."""
        uri = "provider://%s/library/metadata/%s" % (VOD_ID, rating_key)
        c = self._get("/playQueues", {"uri": uri, "type": "video", "continuous": 1}, method="POST")
        items = c.get("Metadata") or []
        for i, m in enumerate(items):
            if m.get("ratingKey") == rating_key:
                return items[i + 1] if i + 1 < len(items) else None
        return None


_PROVIDER_BASE = None


def _provider_base(api):
    """The VOD provider's baseURL from plex.tv/media/providers (cached per run)."""
    global _PROVIDER_BASE
    if _PROVIDER_BASE:
        return _PROVIDER_BASE
    _PROVIDER_BASE = VOD_BASE
    try:
        h = base_headers()
        h["X-Plex-Token"] = api.auth.token()
        resp = api.session.get("https://plex.tv/media/providers", headers=h, timeout=TIMEOUT)
        data = resp.json() if resp.status_code == 200 else []
        providers = data if isinstance(data, list) else (data.get("MediaContainer") or {}).get("MediaProvider") or []
        for p in providers:
            if p.get("identifier") == VOD_ID and str(p.get("baseURL", "")).startswith("https://"):
                _PROVIDER_BASE = p["baseURL"].rstrip("/")
    except Exception as exc:
        kodiutils.log("provider list unavailable, using %s: %s" % (VOD_BASE, exc))
    return _PROVIDER_BASE


def _total(container):
    try:
        return int(container.get("totalSize") or container.get("size") or 0)
    except (TypeError, ValueError):
        return 0
