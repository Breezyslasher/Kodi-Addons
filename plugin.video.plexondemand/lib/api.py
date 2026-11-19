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
        self.base = kodiutils.get_setting("base_url_override").rstrip("/") or VOD_BASE

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

    def metadata(self, rating_key):
        items = self._get("/library/metadata/%s" % rating_key).get("Metadata") or []
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


def _total(container):
    try:
        return int(container.get("totalSize") or container.get("size") or 0)
    except (TypeError, ValueError):
        return 0
