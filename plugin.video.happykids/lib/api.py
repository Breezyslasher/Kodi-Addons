"""HappyKids web API client (Future Today platform).

HappyKids runs on the same Future Today backend as Fawesome; the service is chosen
by siteId. Read off happykids.tv's own web player (iptv-main.js) and confirmed against
the live service from GitHub's runners; see docs/recon.md. In short:

* API base: https://happykids.tv/home/new/<build>/api/ -- <build> (e.g. v438) is read
  from the player page (https://happykids.tv/home/), because the site moves it on every release.
* Every call carries appId / siteId (40) / auth-token (= platform_id) plus a
  device id. These are the website's public client ids, not an account:
  there is no sign-in.
* Every call except getSecurityToken.php sends that call's securityToken in a
  'token' header, plus the site's Referer/Origin -- without those the API
  answers an empty body. A 5002/5003 status means refresh the token.
* Feed URLs inside responses point at rapi.ifood.tv. The player rewrites them
  onto the API base (same file name, same query).
* start-index is a page number (0, 1, 2 ...), not an item offset.

This module has no Kodi imports so tools/ can drive it from plain Python.
"""

import json
import re
import time
import urllib.parse

import requests

# The service names subtitle languages in English ("Spanish", "French"); map the
# ones it ships to ISO 639-1 so Kodi can label and group the tracks.
LANG_ISO = {
    "english": "en", "spanish": "es", "french": "fr", "portuguese": "pt",
    "german": "de", "italian": "it", "dutch": "nl", "russian": "ru",
    "hindi": "hi", "arabic": "ar", "japanese": "ja", "korean": "ko",
    "chinese": "zh", "mandarin": "zh", "turkish": "tr", "polish": "pl",
    "swedish": "sv", "norwegian": "no", "danish": "da", "finnish": "fi",
    "greek": "el", "hebrew": "he", "thai": "th", "vietnamese": "vi",
    "indonesian": "id", "tagalog": "tl", "filipino": "tl",
}

SITE = "https://happykids.tv/"
# The page that loads the web player (iptv-main.js); its path names the build.
PLAYER = "https://happykids.tv/home/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
APP_ID = "9"
SITE_ID = "40"
PLATFORM_ID = "1217575"
FALLBACK_BUILD = "v438"
BUILD_TTL = 24 * 3600
TOKEN_TTL = 6 * 3600
PAGE_SIZE = 20

# Feed files the add-on can list. Others in the responses are ads, bug ads,
# screensavers and promos.
FEED_FILES = ("recipes.php", "shows.php", "sub-categories.php")
TOKEN_ERRORS = ("5002", "5003")


class ApiError(Exception):
    pass


class FtApi(object):
    """state: a dict persisted by the caller between runs (build, token).

    The add-on keeps it in the profile dir so a folder listing costs one API
    call, not three.
    """

    def __init__(self, device_id, state=None, session=None, log=None):
        self.device_id = device_id
        self.state = state if state is not None else {}
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": SITE,
                               "Origin": SITE.rstrip("/"),
                               "Accept": "application/json, text/plain, */*"})
        self.log = log or (lambda msg: None)

    # -- plumbing ---------------------------------------------------------

    def build(self):
        now = time.time()
        if self.state.get("build") and now - self.state.get("build_at", 0) < BUILD_TTL:
            return self.state["build"]
        build = None
        try:
            r = self.s.get(PLAYER, timeout=20)
            # Absolute (/home/new/vNNN/) or page-relative (new/vNNN/) asset paths.
            m = re.search(r"new/(v\d+)/assets", r.text)
            build = m.group(1) if m else None
        except requests.RequestException as exc:
            self.log("build lookup failed: %s" % exc)
        build = build or self.state.get("build") or FALLBACK_BUILD
        self.state.update(build=build, build_at=now)
        return build

    def api_base(self):
        return SITE + "home/new/%s/api/" % self.build()

    def common_params(self):
        return {"appId": APP_ID, "siteId": SITE_ID, "auth-token": PLATFORM_ID,
                "platform_id": PLATFORM_ID, "deviceId": self.device_id,
                "apiEnv": "production"}

    def token(self, refresh=False):
        now = time.time()
        if not refresh and self.state.get("token") and \
                now - self.state.get("token_at", 0) < TOKEN_TTL:
            return self.state["token"]
        # getSecurityToken.php lives under /home/new/<build>/, so it 404s too
        # when the build has moved -- and it runs before _get's own recovery.
        # Recover the build here as well, or a build change with an expired
        # token would break everything until the build cache lapsed.
        for build_try in (0, 1):
            r = self.s.get(self.api_base() + "getSecurityToken.php",
                           params=self.common_params(), timeout=20)
            if r.status_code == 404 and build_try == 0:
                self.log("getSecurityToken 404 at build %s; refreshing build"
                         % self.state.get("build"))
                self.state.pop("build", None)
                self.state.pop("build_at", None)
                continue
            r.raise_for_status()
            tok = (r.json() or {}).get("securityToken")
            if not tok:
                raise ApiError("no security token in response")
            self.state.update(token=tok, token_at=now)
            return tok
        raise ApiError("getSecurityToken unavailable (build %s)" % self.state.get("build"))

    def local_url(self, url):
        """The web player's rewrite: rapi.ifood.tv/<file>?<q> -> <api base><file>?<q>."""
        sp = urllib.parse.urlsplit(url)
        name = sp.path.rsplit("/", 1)[-1]
        return self.api_base() + name + ("?" + sp.query if sp.query else "")

    def _build_url(self, url, params):
        """Resolve a name/URL to a full API URL with the common query merged."""
        if not url.startswith("http"):
            url = self.api_base() + url
        elif "happykids.tv/home/new/" not in url:
            url = self.local_url(url)
        # Query values already in the URL win over the defaults (a feed URL
        # carries its own keys/searchType); deviceId/apiEnv are always ours.
        sp = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(sp.query, keep_blank_values=True))
        for k, v in self.common_params().items():
            query.setdefault(k, v)
        query["deviceId"] = self.device_id
        query["apiEnv"] = "production"
        query.update((k, str(v)) for k, v in (params or {}).items())
        return urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path,
                                        urllib.parse.urlencode(query), ""))

    def _get(self, url, params=None):
        """GET an API URL (absolute, or a file name under the API base).

        Self-heals two moving parts: an expired token (5002/5003 -> refresh and
        retry) and a moved site build (the API path 404s when the site ships a
        new /home/new/<build>/ -- drop the cached build, re-read it, retry once)."""
        for build_try in (0, 1):
            full = self._build_url(url, params)
            for attempt in (0, 1):
                r = self.s.get(full, headers={"token": self.token(refresh=attempt > 0)},
                               timeout=25)
                if r.status_code == 404 and build_try == 0:
                    # The site build likely moved; drop it, re-read it, retry.
                    self.log("API 404 at build %s; refreshing build" % self.state.get("build"))
                    self.state.pop("build", None)
                    self.state.pop("build_at", None)
                    break  # -> outer loop rebuilds the URL with the fresh build
                r.raise_for_status()
                try:
                    data = r.json()
                except ValueError:
                    raise ApiError("not JSON from %s" % url)
                code = str(((data or {}).get("data") or {}).get("code") or "")
                if data.get("status") == "fail" and code in TOKEN_ERRORS and attempt == 0:
                    self.log("token refused (%s); refreshing" % code)
                    continue
                return data
        raise ApiError("API unavailable (build %s)" % self.state.get("build"))

    # -- catalogue --------------------------------------------------------

    def sections(self):
        """Top menu: [{'name', 'url'}] -- Home, Collections, Shows, Movies ..."""
        data = self._get("index.php", {"parent": "home"})
        out = []
        for ch in data.get("channels") or []:
            if ch.get("feed_type") == "category" and ch.get("name"):
                out.append({"name": ch["name"], "url": ch.get("url") or ""})
        return out

    def rows(self, parent=None, url=None):
        """A section's rows: [{'name', 'url', 'image'}]."""
        data = self._get(url) if url else self._get("sub-categories.php", {"parent": parent})
        out = []
        for ch in data.get("channels") or []:
            u = ch.get("url") or ""
            if not ch.get("name") or not any(f in u for f in FEED_FILES):
                continue
            out.append({"name": ch["name"], "url": u, "image": ch.get("image") or ""})
        return out

    @staticmethod
    def _has_more(data, page):
        """Whether a paged response has a page after this one, using the page
        size the response itself reports (params.limit), not an assumed 20."""
        try:
            count = int(data.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        try:
            limit = int(((data.get("params") or {}).get("limit")) or PAGE_SIZE)
        except (TypeError, ValueError):
            limit = PAGE_SIZE
        return bool(count) and (page + 1) * max(limit, 1) < count

    def feed(self, url, page=0):
        """One page of a row: (items, has_more).

        Items are the API's own dicts; kind() says what each one is.
        """
        data = self._get(url, {"start-index": page})
        if data.get("status") == "fail":
            return [], False
        items = data.get("results") or []
        if isinstance(items, dict):
            items = list(items.values())
        return items, self._has_more(data, page)

    def series(self, url):
        """A show (shows.php listoflist): (showdetails, seasons, items).

        A show with seasons answers with 'channels' (one entry per season, each
        with a feed url); a plain list-of-lists answers with 'results'.
        """
        data = self._get(url)
        seasons = data.get("channels") or []
        if isinstance(seasons, dict):
            seasons = list(seasons.values())
        seasons = [s for s in seasons if isinstance(s, dict) and (s.get("feed") or s.get("url"))]
        items = data.get("results") or []
        return data.get("showdetails") or {}, seasons, items

    def search(self, query, page=0):
        data = self._get("recipes.php", {"searchType": "search", "keys": query.lower(),
                                         "start-index": page})
        if data.get("status") == "fail":
            return [], False
        items = data.get("results") or []
        if isinstance(items, dict):
            items = list(items.values())
        return items, self._has_more(data, page)

    def detail(self, node_id):
        data = self._get("recipes.php", {"searchType": "nodeid", "nid": node_id,
                                         "gridstyle": "flat-movie", "max-results": 1,
                                         "dltype": 1, "start-index": 0})
        res = data.get("results") or []
        if not res:
            raise ApiError("title %s not found" % node_id)
        return res[0]

    # -- My List (queue) --------------------------------------------------
    #
    # There is no account; the queue is keyed by the generated deviceId
    # (persisted in the add-on's settings). The id parameter is videoId (the
    # node_id). An empty list answers status "fail", like an empty search.

    def queue_list(self):
        data = self._get("getQueuelist.php")
        items = data.get("results") or []
        if isinstance(items, dict):
            items = list(items.values())
        return items

    def _queue_change(self, name, node_id):
        data = self._get(name, {"videoId": node_id})
        return str((data or {}).get("status", "")).lower() in ("ok", "true")

    def queue_add(self, node_id):
        return self._queue_change("addQueue.php", node_id)

    def queue_remove(self, node_id):
        return self._queue_change("removeQueue.php", node_id)


def kind(item):
    """'video', 'folder' (series / list of lists / category) or None."""
    if item.get("video_hls_url") or item.get("video_url"):
        return "video"
    u = item.get("url") or ""
    if any(f in u for f in FEED_FILES):
        return "folder"
    return None


def stream_for(item):
    """(url, kind) for a title: 'hls' or 'mp4', or (None, reason).

    Every title seen in the capture with drm=1 (a Widevine/PlayReady DASH
    'video_url') also has an unencrypted HLS rendition in 'video_hls_url'
    (no EXT-X-KEY), which is what the add-on plays.
    """
    hls = item.get("video_hls_url") or ""
    if hls.startswith("http"):
        return hls, "hls"
    vod = item.get("video_url") or ""
    fmt = (item.get("video_format") or "").lower()
    if vod.startswith("http") and "comingsoon" not in vod.lower():
        if ".m3u8" in vod or fmt == "hls":
            return vod, "hls"
        if str(item.get("drm")) != "1" and (".mp4" in vod or fmt == "mp4"):
            return vod, "mp4"
        return None, "drm"
    return None, "missing"


def _iso(name):
    """A language name or code -> ISO 639-1 (lower, letters only)."""
    name = (name or "").strip().lower()
    if name in LANG_ISO:
        return LANG_ISO[name]
    code = re.sub(r"[^a-z]", "", name.split("-")[0])[:3]
    return {"eng": "en", "spa": "es", "fre": "fr", "fra": "fr", "por": "pt"}.get(code, code[:2])


def subtitle_tracks(item):
    """[(iso2, url)] of the title's WebVTT subtitles, native language first.

    `cc_path` is the title's own-language caption (its language is
    `content_language_iso2`, which is en/es/pt -- NOT always English);
    `cc_path_multi_lang` adds the rest (Spanish, French ...). De-duplicated by
    URL, so a title with no extra tracks still yields its one caption.
    """
    out, seen = [], set()

    def add(code, url):
        code = _iso(code)
        if url and str(url).startswith("http") and code and url not in seen:
            seen.add(url)
            out.append((code, url))

    add(item.get("content_language_iso2") or item.get("content_language"),
        item.get("cc_path"))
    extra = item.get("cc_path_multi_lang")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            extra = None
    for entry in extra or []:
        if isinstance(entry, dict):
            add(entry.get("language"), entry.get("file_path") or entry.get("url"))
    return out
