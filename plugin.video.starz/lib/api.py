"""Starz catalogue and playback-info calls.

Read from the captures (docs/recon.md row C):

* playdata.starz.com/metadata-service/play/partner/Web/v8/blocks is public (no
  auth headers). With playContents=map it returns every page's rows (HOME,
  SERIES, MOVIES, BROWSE, ...) plus a PLAY_CONTENTS block whose
  playContentsById holds the metadata for every title the rows reference.
* .../v8/content?contentIds=... fetches titles the map lacks.
* auth.starz.com/api/v4/User/continue is the signed-in resume list.

Not captured yet: series -> seasons -> episodes, search, and playback (rows
C, D-K). Those raise NotReadyError.

Shapes returned to default.py:

    item     {"id", "type": "movie"|"series"|"episode", "title", "plot",
              "year", "duration", "season", "episode", "mpaa", "genres",
              "art", "resume"}
    row      {"id", "title", "kind": "items"|"folder"}
    playback {"manifest_url", "manifest_type", "license_url",
              "license_headers", "stream_headers", "subtitles"}
"""

import time

import requests

from lib import kodiutils
from lib.auth import AUTH_API, USER_AGENT, NotReadyError

METADATA = "https://playdata.starz.com/metadata-service/play/partner/Web/v8/"
IMAGES = "https://stz1.imgix.net/Web/contentId/%s/type/%s/dimension/%s"
LANG = "en-US"
PAGES = "BROWSE,HOME,MOVIES,PLAYLIST,SEARCH,SEARCH RESULTS,SERIES"
# The web client's own include list for blocks (sign-in HAR #285).
INCLUDES = ("badgeKeys,categoryKeys,comingSoon,contentId,contentType,detail,"
            "episodeCount,firstEpisodeRuntime,free,icon,images,logLine,"
            "maxReleaseYear,message,minReleaseYear,newContent,original,"
            "popularity,previewContentId,product,properCaseTitle,releaseYear,"
            "runtime,seasonNumber,seriesName,templates,title,topContentId")

BLOCKS_CACHE = "blocks.json"
BLOCKS_TTL = 30 * 60

# Rows whose titles are listed by id. Others (TRAILER_SWIM_LANE,
# RECOMMENDED_SWIM_LANE, ...) are personalised through recommender.starz.com
# and are left out for now.
ID_ROWS = {"SWIM_LANE", "POPULAR_SWIM_LANE", "TOP_CONTENT_SWIM_LANE",
           "SWIM_LANE_CATEGORY"}
TYPES = {"Movie": "movie", "Series with Season": "series", "Episode": "episode"}

TIMEOUT = 20


def _image(content_id, kind, dimension, width):
    return (IMAGES % (content_id, kind, dimension) +
            "/lang/%s?fit=max&auto=format,compress&w=%d" % (LANG, width))


def _art(content_id):
    landscape = _image(content_id, "KEY", "2560x1440", 1280)
    return {"thumb": landscape, "fanart": _image(content_id, "KEY", "2560x1440", 1920),
            "poster": _image(content_id, "KEY", "1536x2048", 600),
            "landscape": landscape,
            "clearlogo": "https://stz1.imgix.net/Web/contentId/%s/type/titleart"
                         "?fit=max&auto=format,compress&w=400&trim=color" % content_id}


def to_item(content):
    kind = TYPES.get(content.get("contentType"))
    if not kind:
        return None
    cid = content.get("contentId")
    item = {"id": str(cid), "type": kind,
            "title": content.get("properCaseTitle") or content.get("title") or "",
            "plot": content.get("logLine", ""),
            "year": content.get("releaseYear") or content.get("minReleaseYear"),
            "duration": content.get("runtime") or content.get("runTime"),
            "mpaa": content.get("ratingName", ""),
            "genres": content.get("simplifiedGenres") or [],
            "art": _art(cid)}
    if kind == "episode":
        item["season"] = content.get("seasonNumber") or content.get("season")
        item["episode"] = content.get("order") or content.get("episodeNumber")
        item["series_id"] = str(content.get("topContentId") or "")
    if content.get("resumeSeconds"):
        item["resume"] = content["resumeSeconds"]
    return item


class StarzApi(object):

    def __init__(self, auth):
        self.auth = auth
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT,
                                  "Accept": "application/json, text/plain, */*",
                                  "Origin": "https://www.starz.com",
                                  "Referer": "https://www.starz.com/"})
        self._blocks = None

    # -- metadata ------------------------------------------------------------

    def _get(self, path, params):
        resp = self.http.get(METADATA + path, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def blocks(self):
        """All pages' rows plus the id -> title map, cached for half an hour
        (each Kodi navigation is a new process)."""
        if self._blocks is None:
            cached = kodiutils.read_json(BLOCKS_CACHE, default=None)
            if cached and time.time() - cached.get("at", 0) < BLOCKS_TTL:
                self._blocks = cached["data"]
            else:
                data = self._get("blocks", {"playContents": "map", "lang": LANG,
                                            "pages": PAGES, "includes": INCLUDES})
                self._blocks = data.get("blocks") or []
                kodiutils.write_json(BLOCKS_CACHE, {"at": time.time(),
                                                    "data": self._blocks})
        return self._blocks

    def _content_map(self):
        for block in self.blocks():
            if block.get("blockType") == "PLAY_CONTENTS":
                return block.get("playContentsById") or {}
        return {}

    def contents(self, ids):
        """Titles by id: from the blocks map, fetching any it lacks."""
        cmap = self._content_map()
        ids = [str(i) for i in ids]
        missing = [i for i in ids if i not in cmap]
        if missing:
            data = self._get("content", {"lang": LANG, "contentIds": ",".join(missing),
                                         "includes": INCLUDES})
            for c in (data.get("playContentArray") or {}).get("playContents") or []:
                cmap[str(c.get("contentId"))] = c
        return [cmap[i] for i in ids if i in cmap]

    def _find(self, block_id, blocks=None):
        for block in self.blocks() if blocks is None else blocks:
            if str(block.get("id")) == str(block_id):
                return block
            found = self._find(block_id, block.get("blocks") or [])
            if found:
                return found
        return None

    @staticmethod
    def _row_ids(block):
        ids = list(block.get("contentIds") or [])
        for sub in block.get("blocks") or []:
            if sub.get("blockType") == "CONTENT" and sub.get("contentId"):
                ids.append(sub["contentId"])
        return ids

    # -- catalogue -----------------------------------------------------------

    def page_rows(self, page="HOME"):
        """The rows of one page (HOME, SERIES, MOVIES, BROWSE)."""
        for block in self.blocks():
            if block.get("blockType") == "PAGE" and block.get("pageKey") == page:
                return self._rows(block.get("blocks") or [])
        return []

    def _rows(self, blocks):
        rows = []
        for block in blocks:
            title = block.get("title")
            if not title:
                continue
            subs = block.get("blocks") or []
            if any(s.get("blockType") in ID_ROWS for s in subs):
                rows.append({"id": str(block["id"]), "title": title, "kind": "folder"})
            elif self._row_ids(block) and (block.get("blockType") in ID_ROWS or subs):
                rows.append({"id": str(block["id"]), "title": title, "kind": "items"})
        return rows

    def row(self, row_id):
        """(sub_rows, items) for a row id: a folder has rows, a row has items."""
        block = self._find(row_id)
        if not block:
            return [], []
        subs = block.get("blocks") or []
        if any(s.get("blockType") in ID_ROWS for s in subs):
            return self._rows(subs), []
        items = [to_item(c) for c in self.contents(self._row_ids(block))]
        return [], [i for i in items if i]

    def continue_watching(self):
        entries = self.auth.request("GET", AUTH_API + "User/continue").json() or []
        meta = {str(c.get("contentId")): c
                for c in self.contents([e.get("contentId") for e in entries])}
        items = []
        for entry in entries:
            merged = dict(meta.get(str(entry.get("contentId")), {}))
            merged.update({k: v for k, v in entry.items() if v is not None})
            item = to_item(merged)
            if item:
                if entry.get("seriesTitle") and item["type"] == "episode":
                    item["title"] = "%s - %s" % (entry["seriesTitle"],
                                                 entry.get("displayTitle") or item["title"])
                items.append(item)
        return items

    def seasons(self, series_id):
        raise NotReadyError("Series drill-down not captured yet: open a series, "
                            "pick a season, and save that HAR (recon row C).")

    def episodes(self, series_id, season_id):
        raise NotReadyError("Season drill-down not captured yet (recon row C).")

    def search(self, query):
        raise NotReadyError("Search not captured yet: run a search on the web "
                            "player and save that HAR (recon row C).")

    # -- playback ------------------------------------------------------------

    def playback(self, content_id):
        raise NotReadyError("Playback not captured yet: press play on a title, "
                            "let it run 30 seconds, and save that HAR (rows D-K).")
