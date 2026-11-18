"""Starz catalogue and playback-info calls.

Read from the captures (docs/recon.md row C):

* playdata.starz.com/metadata-service/play/partner/Web/v8/blocks is public (no
  auth headers). With playContents=map it returns every page's rows (HOME,
  SERIES, MOVIES, BROWSE, ...) plus a PLAY_CONTENTS block whose
  playContentsById holds the metadata for every title the rows reference.
* .../v8/content?contentIds=... fetches titles the map lacks.
* auth.starz.com/api/v4/User/continue is the signed-in resume list.

* Series: v8/content?contentIds={id} (no includes) is the whole tree.
* Search: v8/content?searchString=&contentType=... -> ids.
* My List: GET/PUT/DELETE auth.starz.com/api/v4/User/Queue[/{id}].
* Playback: see playback().

Shapes returned to default.py:

    item     {"id", "type": "movie"|"series"|"episode", "title", "plot",
              "year", "duration", "season", "episode", "mpaa", "genres",
              "art", "resume"}
    row      {"id", "title", "kind": "items"|"folder"}
    playback {"manifest_url", "manifest_type", "license_url",
              "license_headers", "stream_headers", "subtitles"}
"""

import os
import re
import time
from urllib.parse import quote

import requests

from lib import kodiutils
from lib.auth import AUTH_API, USER_AGENT, NotReadyError

METADATA = "https://playdata.starz.com/metadata-service/play/partner/Web/v8/"
LINEAR = "https://playdata.starz.com/metadata-service/play/partner/Web/v6/content/linear"
RECOMMENDER = "https://recommender.starz.com/api/v3/recommendations/partner/Web/profile/"
IMAGES = "https://stz1.imgix.net/Web/contentId/%s/type/%s/dimension/%s"
LANG = "en-US"
PAGES = "BROWSE,HOME,MOVIES,PLAYLIST,SEARCH,SEARCH RESULTS,SERIES"
# The web client's own include list for blocks (sign-in HAR #285).
INCLUDES = ("badgeKeys,categoryKeys,comingSoon,contentId,contentType,detail,"
            "episodeCount,firstEpisodeRuntime,free,icon,images,logLine,"
            "maxReleaseYear,message,minReleaseYear,newContent,original,"
            "popularity,previewContentId,product,properCaseTitle,releaseYear,"
            "runtime,seasonNumber,seriesName,templates,title,topContentId,"
            # Cast for movies/series in every list; accepted by v8/content
            # (endpoint check probe_movie_cast_includes).
            "actors,directors,studio")

BLOCKS_CACHE = "blocks.json"
# The current play session, written by default.py and read by service.py.
PLAYING_FILE = "playing.json"
BLOCKS_TTL = 30 * 60

# Rows whose titles are listed by id. Others (TRAILER_SWIM_LANE,
# RECOMMENDED_SWIM_LANE, ...) are personalised through recommender.starz.com
# and are left out for now.
ID_ROWS = {"SWIM_LANE", "POPULAR_SWIM_LANE", "TOP_CONTENT_SWIM_LANE",
           "SWIM_LANE_CATEGORY"}
TYPES = {"Movie": "movie", "Series with Season": "series", "Season": "season",
         "Episode": "episode"}

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


def _episode_title(content):
    """"Not Exactly How We Planned" rather than "S1 E1 - Not Exactly...":
    Kodi shows season/episode numbers itself."""
    # Not titleBrief: in the series HAR that is a production code ("Power 101").
    title = content.get("properCaseTitle") or content.get("title") or ""
    return re.sub(r"^S\d+\s*E\d+\s*-\s*", "", title)


def to_item(content, series=None):
    """A Kodi-facing item. ``series`` is the parent series content, for the
    art seasons and episodes borrow (series HAR: episodes have their own
    STUDIO still, seasons their own KEY art; poster and logo are the
    series')."""
    kind = TYPES.get(content.get("contentType"))
    if not kind:
        return None
    cid = content.get("contentId")
    title = content.get("properCaseTitle") or content.get("title") or ""
    if kind == "episode":
        title = _episode_title(content)
    item = {"id": str(cid), "type": kind,
            "title": title,
            "plot": content.get("logLine", ""),
            "year": content.get("releaseYear") or content.get("minReleaseYear"),
            "duration": content.get("runtime") or content.get("runTime"),
            "mpaa": content.get("ratingName", ""),
            "genres": content.get("simplifiedGenres") or [],
            "art": _art(cid)}
    sid = (series or {}).get("contentId") or content.get("topContentId")
    if kind in ("season", "episode") and sid:
        parent = _art(sid)
        # Parent art goes under tvshow.* as in Kodi's own library. A plain
        # "poster" on an episode makes poster/wall views show the series
        # poster instead of the episode still.
        art = {"tvshow.%s" % k: v for k, v in parent.items()}
        art["fanart"] = parent["fanart"]
        if kind == "episode":
            art["thumb"] = art["landscape"] = _image(cid, "STUDIO", "2560x1440", 1280)
        else:
            art["thumb"] = art["landscape"] = _image(cid, "KEY", "2560x1440", 1280)
            art["poster"] = parent["poster"]   # seasons do have posters
        item["art"] = art
        item["series_id"] = str(sid)
        item["series_title"] = (series or {}).get("title") or content.get("seriesName", "")
    if kind == "season":
        item["season"] = content.get("order")
        item["episode_count"] = content.get("episodeCount")
    if kind == "episode":
        item["season"] = content.get("seasonNumber") or content.get("season")
        item["episode"] = content.get("order") or content.get("episodeNumber")
    if content.get("resumeSeconds"):
        item["resume"] = content["resumeSeconds"]
    if content.get("comingSoon"):
        item["coming_soon"] = True
    _add_people(item, content)
    return item


def _add_people(item, content):
    """Cast, directors, writers and studio when the content carries them
    (the series tree does for series, seasons and episodes: actors and
    directors as [{id, fullName}], credits with keyedRoles, W = writer)."""
    actors = content.get("actors") or []
    if actors:
        # No photos: Configuration's starzHeadshotImageUrlTemplate answers
        # 403 to anyone but the website (endpoint check, 2026-11-18).
        item["cast"] = [{"name": a.get("fullName", ""), "order": n}
                        for n, a in enumerate(actors) if a.get("fullName")]
    directors = [d.get("fullName") for d in content.get("directors") or []
                 if d.get("fullName")]
    if directors:
        item["directors"] = directors
    writers = [c.get("name") for c in content.get("credits") or []
               if any(r.get("key") == "W" for r in c.get("keyedRoles") or [])]
    if writers:
        item["writers"] = writers
    studio = re.sub(r"^©\s*\d{4}\s*", "", content.get("studio") or "").strip()
    if studio:
        item["studio"] = studio


def is_watched(entry):
    """Starz says so, or playback reached the credits (creditTimeIn), which
    is where Starz's own apps move on to the next episode."""
    if entry.get("isCompleted"):
        return True
    credits, resume = entry.get("creditTimeIn"), entry.get("resumeSeconds")
    return bool(credits and resume and resume >= credits)


def apply_history(items, history):
    """Watched mark or resume point on each movie/episode from User/history
    entries keyed by contentId. Items with no entry are left alone."""
    for item in items:
        entry = history.get(item.get("id"))
        if not entry or item.get("type") not in ("movie", "episode"):
            continue
        if is_watched(entry):
            item["playcount"] = 1
            item.pop("resume", None)
        elif entry.get("resumeSeconds"):
            item["resume"] = entry["resumeSeconds"]
            item.setdefault("duration", entry.get("runTime"))
    return items


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

    # -- full catalogue: All / Genres / filter & sort ---------------------
    # The blocks map holds every title (sign-in HAR: all 449 movies, i.e.
    # every ALL_MOVIES key, and 54 series), each with simplifiedGenres,
    # popularity (higher = more popular: the site's Popular rows run in
    # descending order) and release years. The website's filter dialog works
    # on the same data client-side; there is no separate endpoint.

    SORTS = ("az", "popular", "year")
    # The website's Filters panel (screenshot of its view-all page): type
    # (All / Movies / Series), Release Year by decade and Run Time buckets,
    # each a set of checkboxes; Sort (A to Z / Popular / Release Year) is a
    # separate bar. No request is made when either changes.
    DECADES = ("2020", "2010", "2000", "1990", "1980", "1970", "1960", "pre1960")
    RUNTIMES = ("lt20", "20-40", "40-60", "gt60")

    def _catalogue(self, kind=None):
        wanted = {"movie": ("Movie",), "series": ("Series with Season",)}.get(
            kind, ("Movie", "Series with Season"))
        return [c for c in self._content_map().values()
                if c.get("contentType") in wanted]

    def genres(self, kind=None):
        """[(genre, count)] A-Z."""
        counts = {}
        for c in self._catalogue(kind):
            for g in c.get("simplifiedGenres") or []:
                counts[g] = counts.get(g, 0) + 1
        return sorted(counts.items(), key=lambda gc: gc[0].lower())

    @staticmethod
    def _sort_title(c):
        title = (c.get("properCaseTitle") or c.get("title") or "").lower()
        return re.sub(r"^(the|a|an)\s+", "", title)

    @staticmethod
    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _years(self, c):
        """(first, last) release year; a series spans its seasons."""
        if c.get("releaseYear"):
            y = self._int(c["releaseYear"])
            return y, y
        lo = self._int(c.get("minReleaseYear"))
        hi = self._int(c.get("maxReleaseYear")) or lo
        return (lo or hi), hi

    def _in_decades(self, c, decades):
        lo, hi = self._years(c)
        if not hi:
            return False
        for d in decades:
            start, end = (0, 1959) if d == "pre1960" else (int(d), int(d) + 9)
            if lo <= end and hi >= start:   # a series counts in every decade it ran
                return True
        return False

    def _in_runtimes(self, c, buckets):
        """Minutes: a movie's runtime, a series' first-episode runtime."""
        mins = self._int(c.get("runtime") or c.get("firstEpisodeRuntime")) / 60.0
        if not mins:
            return False
        return any((b == "lt20" and mins < 20) or (b == "20-40" and 20 <= mins < 40)
                   or (b == "40-60" and 40 <= mins <= 60) or (b == "gt60" and mins > 60)
                   for b in buckets)

    def _source(self, source):
        if source == "recommended":
            return self._recommended_contents()
        return self._catalogue()

    def browse(self, source="all", kind=None, decades=(), runtimes=(), genres=(),
               sort=""):
        """A full list with the website's filters and sort applied. Empty
        filters mean everything; no sort keeps the source's own order
        (A-Z for the whole catalogue)."""
        titles = self._source(source)
        if kind:
            want = "Movie" if kind == "movie" else "Series with Season"
            titles = [c for c in titles if c.get("contentType") == want]
        if decades:
            titles = [c for c in titles if self._in_decades(c, decades)]
        if runtimes:
            titles = [c for c in titles if self._in_runtimes(c, runtimes)]
        if genres:
            titles = [c for c in titles
                      if set(genres) & set(c.get("simplifiedGenres") or [])]
        if not sort and source == "all":
            sort = "az"
        if sort == "popular":
            titles.sort(key=lambda c: (-(c.get("popularity") or 0), self._sort_title(c)))
        elif sort == "year":
            titles.sort(key=lambda c: (-self._years(c)[1], self._sort_title(c)))
        elif sort == "az":
            titles.sort(key=self._sort_title)
        items = [i for i in (to_item(c) for c in titles) if i]
        return self._finish(items)

    def _recommended_contents(self):
        """Personalised picks for the selected profile (recommender HARs:
        GET recommender.starz.com/api/v3/recommendations/partner/Web/
        profile/{profileId} -> {"ranks": [{contentId, ...}]}, signed with
        the app token, not the session), in Starz's order."""
        profile = self.auth.profile_id
        if not profile:
            return []
        resp = self.http.get(RECOMMENDER + profile, headers=self.auth.app_headers(),
                             timeout=TIMEOUT)
        resp.raise_for_status()
        ids = [r.get("contentId") for r in (resp.json() or {}).get("ranks") or []]
        return [c for c in self.contents(ids)
                if c.get("contentType") in ("Movie", "Series with Season")]

    # -- people and "If you like" (actor HAR) --------------------------------

    def title_detail(self, content_id):
        """Full detail for one title (no includes): actors, directors and
        credits for a movie, series or episode."""
        data = self._get("content", {"lang": LANG, "contentIds": str(content_id)})
        contents = (data.get("playContentArray") or {}).get("playContents") or []
        return contents[0] if contents else {}

    def people(self, content_id):
        """[(artist_id, name, roles)] - cast in billing order, then the rest
        of the credits (directors, writers, producers...)."""
        c = self.title_detail(content_id)
        out, seen = [], set()
        for a in c.get("actors") or []:
            if a.get("id") and a["id"] not in seen:
                seen.add(a["id"])
                out.append((a["id"], a.get("fullName", ""), ["Actor"]))
        for cr in c.get("credits") or []:
            if cr.get("id") and cr["id"] not in seen:
                seen.add(cr["id"])
                out.append((cr["id"], cr.get("name", ""),
                            [r.get("name") for r in cr.get("keyedRoles") or [] if r.get("name")]))
        return out

    def artist(self, artist_id):
        """(name, items): everything on Starz the person is in.
        GET v8/artists?artistId={id}&lang -> {"artists": {"artist": [{id,
        name, keyedRoles, contents: [{id, type}]}]}} (actor HAR #14; public)."""
        data = self._get("artists", {"artistId": str(artist_id), "lang": LANG})
        found = ((data.get("artists") or {}).get("artist") or [{}])[0]
        roles = {str(c.get("id")): [r.get("name") for r in c.get("keyedRoles") or []
                                    if r.get("name")]
                 for c in found.get("contents") or [] if c.get("id")}
        items = [i for i in (to_item(c) for c in self.contents(list(roles))) if i]
        for item in items:
            # Their role on each title (director/writer pages in the second
            # actor HAR); shown when it is not simply acting.
            r = [x for x in roles.get(item["id"], []) if x != "Actor"]
            if r:
                item["label_extra"] = ", ".join(r)
        return found.get("name", ""), self._finish(items)

    def similar(self, content_id):
        """"If you like X": GET recommender.../recommendations/partner/Web?
        contentIdsCSV={id} with the app token -> {"ranks": [...]} (actor HAR
        #35; the website passes the series id on an episode page)."""
        resp = self.http.get(RECOMMENDER.rsplit("/profile/", 1)[0],
                             params={"contentIdsCSV": str(content_id)},
                             headers=self.auth.app_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        ids = [r.get("contentId") for r in (resp.json() or {}).get("ranks") or []
               if str(r.get("contentId")) != str(content_id)]
        items = [i for i in (to_item(c) for c in self.contents(ids)) if i]
        return self._finish(items)

    def categories_row_id(self):
        """The Browse page's "Browse By" grid (CATEGORY_GRID)."""
        for block in self.blocks():
            if block.get("blockType") == "PAGE" and block.get("pageKey") == "BROWSE":
                for sub in block.get("blocks") or []:
                    if sub.get("blockType") == "CATEGORY_GRID":
                        return str(sub["id"])
        return None

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
            elif block.get("blockType") == "COMING_SOON" and self._coming_soon_ids():
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
        ids = (self._coming_soon_ids() if block.get("blockType") == "COMING_SOON"
               else self._row_ids(block))
        items = [to_item(c) for c in self.contents(ids)]
        return [], self._finish([i for i in items if i])

    def _coming_soon_ids(self):
        """The Home page's COMING_SOON block carries no contentIds; the
        website lists the titles flagged comingSoon in the blocks map."""
        return [k for k, c in self._content_map().items()
                if c.get("comingSoon") and c.get("contentType") in TYPES]

    def _finish(self, items, series_id=None):
        """Every listing ends here: hide what the profile's parental controls
        block, then add watched marks and resume points."""
        return apply_history(self.allowed(items), self.history(series_id))

    def blocked_ratings(self):
        """Ratings switched off for the selected profile.
        GET ParentalControls/{profileId} -> {"parentalControls": [{rating,
        type, enabled}]} (device HAR); edited on the website behind the
        account PIN (parental HAR). The catalogue is not per profile, so the
        website hides restricted titles itself, as this does. Empty when
        signed out or if Starz fails."""
        if "_blocked" not in self.__dict__:
            blocked = set()
            if self.auth.signed_in and self.auth.profile_id:
                try:
                    data = self.auth.request(
                        "GET", AUTH_API + "ParentalControls/" + self.auth.profile_id).json()
                    blocked = {c.get("rating") for c in (data or {}).get("parentalControls") or []
                               if c.get("rating") and c.get("enabled") is False}
                except Exception as exc:
                    kodiutils.log_error("ParentalControls: %s" % exc)
            self._blocked = blocked
        return self._blocked

    def allowed(self, items):
        blocked = self.blocked_ratings()
        if not blocked:
            return items
        kept = [i for i in items if i.get("mpaa") not in blocked]
        if len(kept) != len(items):
            kodiutils.log("Parental controls hid %d title(s)" % (len(items) - len(kept)))
        return kept

    def history(self, series_id=None):
        """User/history as {contentId: entry} (sign-in HAR #304; series HAR
        ?topContentId=). Empty when signed out or if Starz fails: watched
        marks are a nicety and must never break a listing."""
        if not self.auth.signed_in:
            return {}
        key = str(series_id or "")
        cache = self.__dict__.setdefault("_history", {})
        if key not in cache:
            params = {"topContentId": series_id} if series_id else None
            try:
                entries = self.auth.request("GET", AUTH_API + "User/history",
                                            params=params).json() or []
            except Exception as exc:
                kodiutils.log_error("User/history: %s" % exc)
                entries = []
            cache[key] = {str(e.get("contentId")): e for e in entries
                          if isinstance(e, dict)}
        return cache[key]

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
        return self.allowed(items)

    def series_tree(self, series_id):
        """The whole series: v8/content?contentIds={id} with no includes
        returns it with childContent seasons, each with childContent
        episodes (series HAR #513)."""
        data = self._get("content", {"lang": LANG, "contentIds": str(series_id)})
        contents = (data.get("playContentArray") or {}).get("playContents") or []
        if not contents:
            raise NotReadyError("Starz returned nothing for series %s" % series_id)
        return contents[0]

    def seasons(self, series_id):
        series = self.series_tree(series_id)
        seasons = sorted(series.get("childContent") or [],
                         key=lambda s: s.get("order") or 0)
        return series, [i for i in (to_item(s, series) for s in seasons) if i]

    def episodes(self, series_id, season_id):
        series = self.series_tree(series_id)
        for season in series.get("childContent") or []:
            if str(season.get("contentId")) == str(season_id):
                eps = sorted(season.get("childContent") or [],
                             key=lambda e: e.get("order") or 0)
                items = [i for i in (to_item(e, series) for e in eps) if i]
                return self._finish(items, series_id)
        return []

    def search(self, query):
        """Matching ids, then their metadata (series HAR #324, #326)."""
        # The web client's exact query (no lang).
        data = self._get("content", {"include": "contentId",
                                     "contentType": ["Movie", "Series with Season"],
                                     "searchString": query, "limit": 1000})
        ids = [c.get("contentId") for c in
               (data.get("playContentArray") or {}).get("playContents") or []]
        items = [i for i in (to_item(c) for c in self.contents(ids)) if i]
        return self._finish(items)

    # -- linear schedule (schedule HAR) ---------------------------------------

    @staticmethod
    def _timezone():
        """IANA zone for the schedule's day boundaries (the website sends the
        browser's, e.g. America/New_York). Times in the reply are absolute
        (startMs), so this only decides where "today" starts and ends."""
        tz = os.environ.get("TZ", "")
        if "/" in tz:
            return tz.lstrip(":")
        try:
            with open("/etc/timezone") as fh:
                tz = fh.read().strip()
                if "/" in tz:
                    return tz
        except OSError:
            pass
        real = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in real:
            return real.split("zoneinfo/", 1)[1]
        return "America/New_York"

    def schedule(self, code, day=0):
        """One channel's line-up for a day (0 = today, 1 = tomorrow).
        GET v6/content/linear?fromDay&toDay&tz&services={code}&templates=true
        (public) -> {"linearSchedule": {"elements": [{contentType, title,
        contentId, startMs, endMs, availableOnOtt, logLine, ratingName,
        releaseYear, simplifiedGenres, ...}]}}. The reply runs from the
        evening before into the night after; kept as Starz sends it, the
        caller picks the window it wants."""
        data = self._get_url(LINEAR, {"fromDay": str(day), "toDay": str(day),
                                      "tz": self._timezone(),
                                      "services": code.lower(), "templates": "true"})
        return ((data.get("linearSchedule") or {}).get("elements")) or []

    def on_now(self, code):
        now = time.time() * 1000
        for el in self.schedule(code, 0):
            if (el.get("startMs") or 0) <= now < (el.get("endMs") or 0):
                return el
        return None

    def _get_url(self, url, params):
        resp = self.http.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # -- devices (device HARs) ---------------------------------------------

    def devices(self):
        """GET User/devices -> [{deviceId, name, type, lastAccess, country,
        countryRegion, city}], newest first as Starz returns them."""
        return (self.auth.request("GET", AUTH_API + "User/devices").json()
                or {}).get("devices") or []

    def logout_devices(self, device_ids):
        """POST User/devices/logout {"Devices": [deviceId, ...]} -> 204
        (device HAR: the list went from 6 to 5, minus exactly the id sent)."""
        self.auth.request("POST", AUTH_API + "User/devices/logout",
                          json={"Devices": list(device_ids)})

    def hide_from_continue(self, content_id):
        """PUT User/resumepoints/hide {"ids": [id]} -> 204. A movie is hidden
        by its own contentId (hide HAR #20, #33); an episode by its series'
        (episode hide HAR: {"ids": [21220]} for a Power episode)."""
        self.auth.request("PUT", AUTH_API + "User/resumepoints/hide",
                          json={"ids": [int(content_id)]})

    # -- play session reporting (service.py) ---------------------------------

    def report_play(self, kind, content_id, session_id, position):
        """Play/update or Play/stop. Stop's body is captured (episode HAR
        #398: contentId as a string, playbackSessionId, an int position;
        204). [VERIFY] update: the web bundle sends the same three fields."""
        self.auth.request("POST", AUTH_API + "Play/%s" % kind,
                          json={"contentId": str(content_id),
                                "playbackSessionId": session_id,
                                "mediaResumePointSeconds": int(position)})

    # -- My List (User/Queue) ------------------------------------------------

    def my_list(self):
        entries = self.auth.request("GET", AUTH_API + "User/Queue").json() or []
        entries.sort(key=lambda e: e.get("order") or 0)
        ids = [e.get("contentId") for e in entries]
        items = [i for i in (to_item(c) for c in self.contents(ids)) if i]
        return self._finish(items)

    def my_list_ids(self):
        """{contentId: remindMe} for everything on My List."""
        try:
            return {str(e.get("contentId")): bool(e.get("remindMe")) for e in
                    self.auth.request("GET", AUTH_API + "User/Queue").json() or []}
        except Exception:
            return {}

    def set_reminder(self, content_id, on):
        """Remind me on a Coming Soon title: the same PUT User/Queue/{id} as
        Add to My List (series HAR #252, {"remindMe": false}) with the flag
        set. [VERIFY] remindMe true, which no capture has shown yet."""
        self.auth.request("PUT", AUTH_API + "User/Queue/%s" % content_id,
                          json={"remindMe": bool(on)})

    def add_to_list(self, content_id):
        self.auth.request("PUT", AUTH_API + "User/Queue/%s" % content_id,
                          json={"remindMe": False})

    def remove_from_list(self, content_id):
        self.auth.request("DELETE", AUTH_API + "User/Queue/%s" % content_id)

    # -- playback ------------------------------------------------------------

    def playback(self, content_id, subtitle_langs=("en",)):
        """Resolve a title to a playback dict (play HAR, recon rows D-K).

        GET Play?contentId= is the entitlement/device check (204 when fine;
        403 errorCode 1305 at the device limit). POST Play/start returns the
        DASH manifest (mediaUrl), the licence endpoint and a per-play licence
        token, and WebVTT captions. The licence URL is
        mediaDrmEndpoint/{default_KID}?token={token percent-encoded}; the
        server takes the raw Widevine challenge with no auth headers, so ISA
        posts to it directly. Segments need no cookies or signed URLs.
        The clear preroll the web player shows first is skipped.
        """
        cid = int(content_id)
        self.auth.request("GET", AUTH_API + "Play", params={"contentId": cid})
        info = self.auth.request("POST", AUTH_API + "Play/start",
                                 json={"contentId": cid,
                                       "mediaResumePointSeconds": None}).json()
        manifest_url = info["mediaUrl"]
        kid = self._default_kid(manifest_url)
        license_url = "%s/%s?token=%s" % (info["mediaDrmEndpoint"].rstrip("/"),
                                          kid, quote(info["mediaLicenseToken"], safe=""))
        kodiutils.log("Play/start ok: content=%s kid=%s keepAlive=%ss" % (
            cid, kid, info.get("keepAliveSeconds")))
        web = {"User-Agent": USER_AGENT, "Origin": "https://www.starz.com",
               "Referer": "https://www.starz.com/"}
        return {"manifest_url": manifest_url, "manifest_type": "mpd",
                "license_url": license_url,
                # Exactly what the browser sent: no Content-Type, no auth.
                "license_headers": web,
                "stream_headers": web,
                "subtitles": self._captions(info.get("captions") or [], subtitle_langs),
                "session_id": info.get("playbackSessionId"),
                "keep_alive": info.get("keepAliveSeconds")}

    def _default_kid(self, manifest_url):
        """The manifest's cenc:default_KID, which names the licence URL.
        The captured manifest carries exactly one, shared by every track."""
        resp = self.http.get(manifest_url, timeout=TIMEOUT)
        resp.raise_for_status()
        kids = re.findall(r'default_KID="([0-9a-fA-F-]{32,36})"', resp.text)
        if not kids:
            raise NotReadyError("No default_KID in the manifest; this title is "
                                "packaged differently from the captured one.")
        if len(set(k.lower() for k in kids)) > 1:
            kodiutils.log("Manifest has %d key ids; licensing with the first"
                          % len(set(kids)))
        return kids[0].lower()

    @staticmethod
    def _captions(captions, langs):
        """WebVTT caption URLs, preferred languages first (en-US -> en)."""
        def rank(c):
            lang = (c.get("language") or "").split("-")[0].lower()
            return langs.index(lang) if lang in langs else len(langs)
        return [c["url"] for c in sorted(captions, key=rank)
                if c.get("url") and (c.get("type") or "VTT").upper() == "VTT"]
