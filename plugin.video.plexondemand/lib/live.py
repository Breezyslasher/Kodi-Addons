"""Plex's free Live TV (tv.plex.provider.epg).

Capture-confirmed (tools/probe.py rounds 18-19, GitHub Actions, US, anonymous
token):

  Provider        plex.tv/media/providers -> tv.plex.provider.epg at
                  https://epg.provider.plex.tv ("Live TV")
  Filters         GET {epg}/ -> MediaProvider.Feature[type=grid]:
                  PromotedGridChannelFilter [Featured -> /lineups/plex/channels/featured]
                  GridChannelFilter [{identifier, title, genreRatingKey,
                  key /lineups/plex/channels?genre=<identifier>}] (~15 genres)
  Channels        GET {epg}/lineups/plex/channels -> Channel[] (~700 in the US):
                  id, gridKey, title, slug, summary, thumb (logo), art,
                  genreRatingKeys, Media[0].Part[0].key =
                  /library/parts/<id>.m3u8
  Stream          that .m3u8 with X-Plex-Token -> HLS master (h264, 1080p,
                  CEA-608 captions); variant URLs carry the token themselves,
                  segments are open. No DRM.
  Guide           GET {epg}/grid?channelGridKey=<gridKey>&date=YYYY-MM-DD ->
                  Metadata[] (one channel and one day per call; a comma list
                  or no date answers 400). Each has title / grandparentTitle,
                  index / parentIndex, summary, thumb, type and
                  Media[0].beginsAt / endsAt (epoch seconds). The day is not
                  a UTC day, so the dates either side of a moment are asked.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import kodiutils
from .api import PlexVodApi, _provider_base, image

EPG_ID = "tv.plex.provider.epg"
EPG_BASE = "https://epg.provider.plex.tv"
ALL = "/lineups/plex/channels"
FEATURED = "/lineups/plex/channels/featured"
WORKERS = 16


class LiveApi(PlexVodApi):

    def __init__(self):
        super(LiveApi, self).__init__()
        self.base = _provider_base(self, EPG_ID, EPG_BASE)
        # Many guide calls run at once; give the pool enough connections.
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=WORKERS)
        self.session.mount("https://", adapter)

    # ---- channels
    def filters(self):
        """[(title, key)]: Featured first, then Plex's genres."""
        out = []
        # The provider root is {"MediaProvider": ...} at the top level, not
        # inside a MediaContainer like everything else (probe round 18).
        resp = self.session.get(self.base + "/", headers=self.headers(), timeout=25)
        data = resp.json() if resp.status_code == 200 else {}
        provider = data.get("MediaProvider") or (data.get("MediaContainer") or {}).get("MediaProvider") or {}
        for f in provider.get("Feature") or []:
            if f.get("type") != "grid":
                continue
            for d in (f.get("PromotedGridChannelFilter") or []) + (f.get("GridChannelFilter") or []):
                if d.get("key") and d.get("title"):
                    out.append((d["title"], d["key"], d.get("genreRatingKey")))
        return out

    def channels(self, key=ALL):
        return [c for c in self._get(key).get("Channel") or [] if not c.get("hidden")]

    def stream_url(self, channel_id):
        return self.base + "/library/parts/%s.m3u8" % channel_id

    # ---- guide
    def grid(self, grid_key, day):
        return self._get("/grid", {"channelGridKey": grid_key, "date": day}).get("Metadata") or []

    def schedule(self, grid_key, since, until):
        """Programmes overlapping [since, until] (epoch seconds), in order.
        The grid's day isn't a UTC day, so ask every UTC date from 12 h
        before to 12 h after the window and keep what overlaps."""
        days = sorted({time.strftime("%Y-%m-%d", time.gmtime(t))
                       for t in range(int(since) - 43200, int(until) + 43201, 43200)})
        seen, out = set(), []
        for day in days:
            try:
                items = self.grid(grid_key, day)
            except Exception as exc:
                kodiutils.log("guide %s %s: %s" % (grid_key, day, exc))
                continue
            for m in items:
                media = (m.get("Media") or [{}])[0]
                b, e = media.get("beginsAt"), media.get("endsAt")
                if not b or not e or e <= since or b >= until or (b, e) in seen:
                    continue
                seen.add((b, e))
                out.append(m)
        return sorted(out, key=lambda m: m["Media"][0]["beginsAt"])

    def now_next(self, channels, now=None):
        """{gridKey: (now, next)} for each channel, fetched in parallel."""
        now = now or time.time()

        def one(c):
            gk = c.get("gridKey")
            # Only today's grid(s): the one or two calls that cover "now".
            progs = self.schedule(gk, now, now + 1) if gk else []
            cur = progs[0] if progs else None
            nxt = None
            if cur:
                end = cur["Media"][0]["endsAt"]
                later = self.schedule(gk, end, end + 1)
                nxt = later[0] if later else None
            return gk, (cur, nxt)

        with ThreadPoolExecutor(WORKERS) as pool:
            return dict(pool.map(one, channels))


def build(api, channel_id):
    """ListItem for a channel's live stream (HLS through InputStream Adaptive;
    the token only matters on the master playlist)."""
    import xbmcgui
    from . import playback
    url = api.stream_url(channel_id)
    headers = api.headers()
    playback._check(api, url, headers)
    # A live channel is not a title the service should report or scrobble.
    xbmcgui.Window(10000).clearProperty("plexondemand.playing")
    li = xbmcgui.ListItem(path=url)
    li.setProperty("inputstream", "inputstream.adaptive")
    li.setMimeType(playback.MIME["hls"])
    li.setContentLookup(False)
    li.setProperty("inputstream.adaptive.manifest_headers", playback._header_str(
        {"X-Plex-Token": headers["X-Plex-Token"],
         "X-Plex-Client-Identifier": headers["X-Plex-Client-Identifier"]}))
    if kodiutils.get_setting_int("max_height", 0):
        li.setProperty("inputstream.adaptive.chooser_resolution_max",
                       "%dp" % kodiutils.get_setting_int("max_height"))
    return li


def programme_title(m):
    """'Show' for episodes (episode title separately), the title otherwise."""
    return m.get("grandparentTitle") or m.get("title") or ""


def episode_label(m):
    if m.get("type") != "episode" or not m.get("grandparentTitle"):
        return ""
    se = ""
    if m.get("parentIndex") and m.get("index"):
        se = "S%02dE%02d " % (int(m["parentIndex"]), int(m["index"]))
    return (se + (m.get("title") or "")).strip()


def clock(epoch):
    return time.strftime("%H:%M", time.localtime(epoch))


def channel_art(c):
    logo = c.get("thumb") or ""
    return {"thumb": logo, "icon": logo, "fanart": image(c.get("art") or "", 1280, 720)}
