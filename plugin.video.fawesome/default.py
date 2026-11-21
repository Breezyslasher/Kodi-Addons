"""Fawesome for Kodi: router and ListItem building."""

import os
import re
import sys
import urllib.parse
import uuid

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils as ku
from lib.api import FawesomeApi, FawesomeError, kind, stream_for, subtitle_tracks

SUBTITLE_DIR = "subtitles"

HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else -1
BASE = sys.argv[0] if sys.argv else "plugin://plugin.video.fawesome/"
STATE_FILE = "state.json"
# Menu sections that are not on-demand catalogue on the website.
SKIP_SECTIONS = ("live",)


def url_for(**kw):
    return BASE + "?" + urllib.parse.urlencode(kw)


def device_id():
    dev = ku.get_setting("device_id")
    if not dev:
        dev = str(uuid.uuid4())
        ku.set_setting("device_id", dev)
    return dev


def get_api():
    state = ku.read_json(STATE_FILE, {}) or {}
    log = ku.log if ku.get_setting_bool("debug_log") else (lambda m: None)
    return FawesomeApi(device_id(), state=state, log=log)


def save_state(api):
    ku.write_json(STATE_FILE, api.state)


# -- ListItem mapping ----------------------------------------------------------

def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _year(item):
    m = re.search(r"\b(19|20)\d\d\b", item.get("release_date") or "")
    return int(m.group(0)) if m else 0


def _list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if x and str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _art(item):
    poster = item.get("main_picture") or item.get("hd_image_portrait") or item.get("hd_image") or ""
    land = (item.get("bg_image_landscape") or item.get("hero_image") or
            item.get("hd_image_landscape") or item.get("bgimage") or "")
    art = {"poster": poster, "thumb": land or poster, "fanart": land,
           "landscape": land, "icon": poster or land}
    logo = item.get("content_logo")
    if logo and str(logo).startswith("http"):
        art["clearlogo"] = logo
    return {k: v for k, v in art.items() if v}


def _clean_title(item):
    title = item.get("title") or item.get("name") or ""
    ep = (item.get("episode_title") or "").strip()
    if _int(item.get("episode")) and ep:
        return ep
    return title


def video_item(item):
    title = _clean_title(item)
    li = xbmcgui.ListItem(title, offscreen=True)
    li.setArt(_art(item))
    tag = li.getVideoInfoTag()
    season, episode = _int(item.get("season")), _int(item.get("episode"))
    tag.setMediaType("episode" if episode else "movie")
    tag.setTitle(title)
    tag.setPlot(item.get("description") or "")
    if item.get("series_name") and episode:
        tag.setTvShowTitle(item["series_name"])
    if season:
        tag.setSeason(season)
    if episode:
        tag.setEpisode(episode)
    if _int(item.get("runtime")):
        tag.setDuration(_int(item.get("runtime")))
    if _year(item):
        tag.setYear(_year(item))
    genres = _list(item.get("content_genre"))
    if genres:
        tag.setGenres(genres)
    directors = _list(item.get("directors"))
    if directors:
        tag.setDirectors(directors)
    actors = _list(item.get("actors"))
    if actors:
        tag.setCast([xbmc.Actor(a) for a in actors])
    if item.get("age_appropriate_rating"):
        tag.setMpaa(str(item["age_appropriate_rating"]))
    li.setProperty("IsPlayable", "true")
    return li


def folder_item(item):
    title = item.get("title") or item.get("name") or ""
    li = xbmcgui.ListItem(title, offscreen=True)
    art = _art(item)
    if item.get("image") and not item["image"].endswith("/public/"):
        art.setdefault("thumb", item["image"])
    li.setArt(art)
    tag = li.getVideoInfoTag()
    tag.setTitle(title)
    tag.setPlot(item.get("description") or "")
    if item.get("series_name"):
        tag.setMediaType("tvshow")
        tag.setTvShowTitle(item["series_name"])
    return li


def add_items(items, in_mylist=False):
    """Add API items (videos and folders) to the directory; return content type."""
    episodes = movies = shows = 0
    for it in items:
        k = kind(it)
        if k == "video":
            if not it.get("node_id"):
                continue
            li = video_item(it)
            if in_mylist:
                li.addContextMenuItems([(ku.localize(32122), "RunPlugin(%s)"
                                         % url_for(action="queue_remove", nid=it["node_id"]))])
            else:
                li.addContextMenuItems([(ku.localize(32121), "RunPlugin(%s)"
                                         % url_for(action="queue_add", nid=it["node_id"]))])
            if _int(it.get("episode")):
                episodes += 1
            else:
                movies += 1
            xbmcplugin.addDirectoryItem(HANDLE, url_for(action="play", nid=it["node_id"]), li, False)
        elif k == "folder":
            li = folder_item(it)
            if it.get("series_name"):
                shows += 1
            xbmcplugin.addDirectoryItem(HANDLE, url_for(action="open", url=it["url"],
                                                        title=it.get("title") or ""), li, True)
    if episodes and episodes >= movies:
        return "episodes"
    if movies:
        return "movies"
    if shows:
        return "tvshows"
    return "files"


def next_page(**kw):
    li = xbmcgui.ListItem(ku.localize(32100), offscreen=True)
    xbmcplugin.addDirectoryItem(HANDLE, url_for(**kw), li, True)


def end(content="videos", cache=True):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache)


# -- routes -------------------------------------------------------------------

def root(api):
    for sec in api.sections():
        if sec["name"].strip().lower() in SKIP_SECTIONS:
            continue
        li = xbmcgui.ListItem(sec["name"], offscreen=True)
        xbmcplugin.addDirectoryItem(HANDLE, url_for(action="section", parent=sec["name"]), li, True)
    li = xbmcgui.ListItem(ku.localize(32120), offscreen=True)
    li.setArt({"icon": "DefaultPlaylist.png"})
    xbmcplugin.addDirectoryItem(HANDLE, url_for(action="mylist"), li, True)
    li = xbmcgui.ListItem(ku.localize(32101), offscreen=True)
    li.setArt({"icon": "DefaultAddonsSearch.png"})
    xbmcplugin.addDirectoryItem(HANDLE, url_for(action="search"), li, True)
    end("files")


def section(api, parent):
    for row in api.rows(parent=parent):
        li = xbmcgui.ListItem(row["name"], offscreen=True)
        xbmcplugin.addDirectoryItem(HANDLE, url_for(action="open", url=row["url"], title=row["name"]), li, True)
    end("files")


def open_feed(api, url, page=0):
    """Any feed URL: a row (recipes.php), a show or list of lists (shows.php),
    or a sub-category menu (sub-categories.php)."""
    if "sub-categories.php" in url:
        for row in api.rows(url=url):
            li = xbmcgui.ListItem(row["name"], offscreen=True)
            xbmcplugin.addDirectoryItem(HANDLE, url_for(action="open", url=row["url"], title=row["name"]), li, True)
        return end("files")
    if "shows.php" in url:
        details, seasons, items = api.series(url)
        if seasons:
            if len(seasons) == 1:
                # A one-season show: skip the pointless single "Season 1" folder
                # and list its episodes directly.
                only = seasons[0]
                return open_feed(api, only.get("feed") or only.get("url"), page)
            for s in seasons:
                it = dict(details)
                it.update((k, v) for k, v in s.items() if v not in ("", None))
                it["url"] = s.get("feed") or s.get("url")
                it["title"] = s.get("title") or s.get("name") or ""
                li = folder_item(it)
                xbmcplugin.addDirectoryItem(HANDLE, url_for(action="open", url=it["url"], title=it["title"]), li, True)
            return end("seasons" if any(s.get("season_no") for s in seasons) else "files")
        if items:
            return end(add_items(items))
    items, more = api.feed(url, page)
    content = add_items(items)
    if more:
        next_page(action="open", url=url, page=page + 1)
    end(content)


def mylist(api):
    items = api.queue_list()
    if not items:
        ku.notify(ku.localize(32125))
    content = add_items(items, in_mylist=True)
    end(content, cache=False)


def queue_change(api, nid, add):
    ok = api.queue_add(nid) if add else api.queue_remove(nid)
    if ok:
        ku.notify(ku.localize(32123 if add else 32124))
        if not add:
            xbmc.executebuiltin("Container.Refresh")  # drop it from the My List view
    else:
        ku.notify(ku.localize(32113), icon=xbmcgui.NOTIFICATION_ERROR)


def search(api, query=None, page=0):
    if query is None:
        # Prompt, then redirect into a results folder whose URL carries the
        # query. Listing the results under this parameter-less folder instead
        # would make Kodi re-open the keyboard every time the folder refreshes
        # -- e.g. when you come back from playing a result.
        text = ku.input_text(ku.localize(32101))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        if text:
            xbmc.executebuiltin("Container.Update(%s,replace)"
                                % url_for(action="search", q=text))
        return
    items, more = api.search(query, page)
    content = add_items(items)
    if more:
        next_page(action="search", q=query, page=page + 1)
    end(content, cache=False)


def play(api, nid):
    item = api.detail(nid)
    url, how = stream_for(item)
    if not url:
        msg = ku.localize(32110 if how == "drm" else 32111)
        ku.notify(msg, icon=xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    li = video_item(item)
    li.setPath(url)
    headers = "User-Agent=" + urllib.parse.quote(api.s.headers["User-Agent"])
    if how == "hls":
        li.setMimeType("application/vnd.apple.mpegurl")
        li.setContentLookup(False)
        if ku.get_setting_bool("use_isa", True):
            li.setProperty("inputstream", "inputstream.adaptive")
            li.setProperty("inputstream.adaptive.manifest_type", "hls")  # ignored on ISA 21+
            li.setProperty("inputstream.adaptive.manifest_headers", headers)
            li.setProperty("inputstream.adaptive.stream_headers", headers)
        else:
            li.setPath(url + "|" + headers)
    else:
        li.setMimeType("video/mp4")
        li.setContentLookup(False)
        li.setPath(url + "|" + headers)
    # Audio: Fawesome's HLS carries one muxed audio track (no alternate audio
    # renditions), so there is nothing to pick -- label that one track with the
    # title's language so Kodi shows it instead of "Unknown".
    audio_lang = item.get("content_language_iso2") or item.get("content_language")
    if audio_lang:
        try:
            li.addStreamInfo("audio", {"language": str(audio_lang)[:3], "channels": 2})
        except Exception:
            pass
    if ku.get_setting_bool("subtitles", True):
        subs = fetch_subtitles(api, item)
        if subs:
            li.setSubtitles(subs)
    ku.log("play %s (%s) %s" % (nid, how, url))
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def fetch_subtitles(api, item):
    """Download the title's WebVTT tracks as <iso>.vtt files, so Kodi labels
    them by language, preferred language (Kodi's, then English) first.

    Fawesome serves no subtitles inside the HLS manifest and no language in its
    caption URLs (hashed filenames), and Kodi's setSubtitles() has no language
    argument -- it reads the language only from the filename. So the only way
    to label the tracks is to control the filename: save each as <iso>.vtt in
    the profile dir. The title's own cc_path is in its native language (often
    es/pt), which is why an unlabelled list showed non-English subtitles.
    """
    tracks = subtitle_tracks(item)
    if not tracks:
        return []
    want = ku.subtitle_languages()  # Kodi's language, then 'en'
    tracks.sort(key=lambda t: want.index(t[0]) if t[0] in want else len(want))
    sub_dir = os.path.join(ku.profile_dir(), SUBTITLE_DIR)
    try:
        os.makedirs(sub_dir, exist_ok=True)
        for old in os.listdir(sub_dir):  # clear last play's tracks so stale langs do not linger
            if old.endswith(".vtt"):
                os.remove(os.path.join(sub_dir, old))
    except Exception:
        pass
    out, seen = [], set()
    for code, url in tracks:
        if code in seen:  # one track per language; Kodi reads the lang from "<iso>.vtt"
            continue
        path = os.path.join(sub_dir, "%s.vtt" % code)
        try:
            r = api.s.get(url, timeout=20)
            r.raise_for_status()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(r.text)
            out.append(path)
            seen.add(code)
        except Exception as exc:
            ku.log_error("subtitle %s failed: %s" % (code, exc))
    return out


def reset_device():
    ku.set_setting("device_id", str(uuid.uuid4()))
    ku.delete_file(STATE_FILE)
    ku.notify(ku.localize(32112))


def main():
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:] if len(sys.argv) > 2 else ""))
    action = params.get("action")
    if action == "reset_device":
        return reset_device()
    # queue_add / queue_remove are invoked via RunPlugin (no directory handle),
    # so they must not end a directory; the rest build one under HANDLE.
    directory = action in (None, "section", "open", "search", "mylist")
    api = get_api()
    try:
        page = int(params.get("page") or 0)
        if not action:
            root(api)
        elif action == "section":
            section(api, params["parent"])
        elif action == "open":
            open_feed(api, params["url"], page)
        elif action == "search":
            search(api, params.get("q"), page)
        elif action == "mylist":
            mylist(api)
        elif action == "play":
            play(api, params["nid"])
        elif action in ("queue_add", "queue_remove"):
            queue_change(api, params["nid"], add=action == "queue_add")
        else:
            ku.log_error("unknown action %r" % action)
    except (FawesomeError, Exception) as exc:  # noqa: BLE001  surface, don't crash Kodi
        ku.log_error("%s failed: %r" % (action or "root", exc))
        ku.notify(ku.localize(32113), icon=xbmcgui.NOTIFICATION_ERROR)
        if action == "play":
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        elif directory:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    finally:
        save_state(api)


if __name__ == "__main__":
    main()
