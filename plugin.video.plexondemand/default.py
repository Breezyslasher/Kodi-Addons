"""Plex On Demand (free, ad-supported) - entry point and router."""

import sys
from urllib.parse import urlencode, parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.api import PlexVodApi, PlexApiError, PAGE_SIZE, TYPE_MOVIE, TYPE_SHOW
from lib import playback
from lib.listing import art as _art, info as _info
from lib.auth import account_name

HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1
BASE_URL = sys.argv[0]

S = {
    "featured": 32010,
    "movies": 32011,
    "shows": 32012,
    "search": 32013,
    "sign_in": 32014,
    "sign_out": 32015,
    "signed_in_as": 32016,
    "link_heading": 32017,
    "link_text": 32018,
    "sign_in_ok": 32019,
    "sign_in_failed": 32020,
    "confirm_sign_out": 32021,
    "no_results": 32022,
    "playback_failed": 32023,
    "next_page": 32024,
    "error": 32025,
    "drm_unavailable": 32026,
    "season_n": 32027,
    "vpn_blocked": 32028,
    "continue_watching": 32040,
    "trailer": 32041,
    "no_trailer": 32042,
    "mark_watched": 32043,
    "mark_unwatched": 32044,
    "remove_cw": 32045,
}


def L(key):
    return kodiutils.localize(S[key])


def url(**kwargs):
    return "%s?%s" % (BASE_URL, urlencode(kwargs))


def folder(label, query, art=None, plot=""):
    item = xbmcgui.ListItem(label)
    if art:
        item.setArt(art)
    if plot:
        item.getVideoInfoTag().setPlot(plot)
    xbmcplugin.addDirectoryItem(HANDLE, url(**query), item, True)


def end(content="videos", cache=True):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache)


def _apply_user_state(api, items):
    """Resume points and watched marks: /library/metadata on the VOD host has
    none, so fetch them for the whole page in one batched userState call."""
    if not kodiutils.get_setting_bool("report_progress", True):
        return
    keys = [m.get("ratingKey") for m in items if m.get("type") in ("movie", "episode")]
    if not keys:
        return
    states = api.user_states(keys)
    for m in items:
        st = states.get(m.get("ratingKey"))
        if st:
            for field in ("viewOffset", "viewCount"):
                if m.get(field) is None:
                    m[field] = st.get(field)


def _menu(m, in_cw=False):
    rk = m["ratingKey"]
    entries = []
    if m.get("type") in ("movie", "show"):
        entries.append((L("trailer"), "RunPlugin(%s)" % url(action="trailer", rk=rk)))
    if m.get("type") in ("movie", "episode"):
        if m.get("viewCount"):
            entries.append((L("mark_unwatched"), "RunPlugin(%s)" % url(action="mark", rk=rk, how="unscrobble")))
        else:
            entries.append((L("mark_watched"), "RunPlugin(%s)" % url(action="mark", rk=rk, how="scrobble")))
    if in_cw:
        entries.append((L("remove_cw"), "RunPlugin(%s)" % url(
            action="mark", rk=rk, how="removeFromContinueWatching")))
    return entries


def add_items(items, api=None, in_cw=False):
    content = "videos"
    if api is not None:
        _apply_user_state(api, items)
    for m in items:
        kind = m.get("type")
        rk = m.get("ratingKey")
        if not rk:
            continue
        label = m.get("title") or rk
        if kind == "episode" and m.get("index") is not None:
            label = "%s. %s" % (m["index"], label)
        item = xbmcgui.ListItem(label)
        item.setArt(_art(m))
        _info(item, m)
        if in_cw and kind == "episode" and m.get("grandparentTitle"):
            item.setLabel("%s - %s" % (m["grandparentTitle"], label))
        item.addContextMenuItems(_menu(m, in_cw))
        if kind in ("movie", "episode"):
            content = "movies" if kind == "movie" else "episodes"
            item.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(HANDLE, url(action="play", rk=rk), item, False)
        elif kind == "show":
            content = "tvshows"
            xbmcplugin.addDirectoryItem(HANDLE, url(action="children", rk=rk), item, True)
        elif kind == "season":
            content = "seasons"
            xbmcplugin.addDirectoryItem(HANDLE, url(action="children", rk=rk), item, True)
    return content


def add_next(query, start, count, total):
    nxt = int(start) + count
    if count and total and nxt < total:
        q = dict(query, start=nxt)
        folder("%s (%d/%d)" % (L("next_page"), nxt // PAGE_SIZE + 1, (total + PAGE_SIZE - 1) // PAGE_SIZE), q)


# ---------------------------------------------------------------- views
def root():
    api = PlexVodApi()
    folder(L("continue_watching"), {"action": "continue"})
    folder(L("featured"), {"action": "hubs"})
    folder(L("movies"), {"action": "genres", "type": TYPE_MOVIE})
    folder(L("shows"), {"action": "genres", "type": TYPE_SHOW})
    folder(L("search"), {"action": "search"})
    if api.auth.signed_in():
        name = account_name() or "Plex"
        item = xbmcgui.ListItem(L("signed_in_as") % name)
        xbmcplugin.addDirectoryItem(HANDLE, url(action="sign_out"), item, False)
    else:
        item = xbmcgui.ListItem(L("sign_in"))
        xbmcplugin.addDirectoryItem(HANDLE, url(action="sign_in"), item, False)
    end("files", cache=False)


def hubs():
    for h in PlexVodApi().hubs():
        folder(h.get("title") or h["key"], {"action": "list", "key": h["key"], "title": h.get("title", "")})
    end("files")


def genres(media_type):
    for g in PlexVodApi().genres(int(media_type)):
        if g.get("key"):
            folder(g.get("title") or "?", {"action": "list", "key": g["key"]},
                   art={"icon": g.get("icon") or ""})
    end("files")


def listing(key, start=0):
    api = PlexVodApi()
    items, total = api.listing(key, start)
    content = add_items(items, api)
    add_next({"action": "list", "key": key}, start, len(items), total)
    end(content)


def children(rk, start=0):
    api = PlexVodApi()
    items, total = api.children(rk, start)
    content = add_items(items, api)
    add_next({"action": "children", "rk": rk}, start, len(items), total)
    end(content)


def search(query=None):
    if not query:
        query = kodiutils.input_text(L("search"))
        if not query:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return
    api = PlexVodApi()
    items = api.search(query)
    if not items:
        kodiutils.notify(L("no_results"))
    content = add_items(items, api)
    end(content, cache=False)


def continue_watching():
    api = PlexVodApi()
    items = api.continue_watching()
    content = add_items(items, api, in_cw=True)
    end(content, cache=False)


def trailer(rk):
    api = PlexVodApi()
    try:
        found = api.trailer(api.metadata(rk))
    except PlexApiError as exc:
        kodiutils.log_error("trailer %s: %s" % (rk, exc))
        found = None
    if not found:
        kodiutils.notify(L("no_trailer"))
        return
    title, stream = found
    li = xbmcgui.ListItem(title, path=stream)
    li.setProperty("inputstream", "inputstream.adaptive")
    li.setMimeType("application/vnd.apple.mpegurl")
    li.setContentLookup(False)
    xbmc.Player().play(stream, li)


def mark(rk, how):
    try:
        PlexVodApi().action(how, rk)
    except PlexApiError as exc:
        kodiutils.log_error("%s %s: %s" % (how, rk, exc))
        kodiutils.notify("%s: %s" % (L("error"), exc), icon=xbmcgui.NOTIFICATION_ERROR)
        return
    xbmc.executebuiltin("Container.Refresh")


def play(rk):
    api = PlexVodApi()
    try:
        item = playback.build(api, rk)
    except playback.Blocked:
        kodiutils.ok_dialog(L("vpn_blocked"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    except playback.DrmUnavailable:
        kodiutils.ok_dialog(L("drm_unavailable"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    except Exception as exc:
        kodiutils.log_error("play %s failed: %s" % (rk, exc))
        kodiutils.notify(L("playback_failed"), icon=xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def _close_settings():
    """Run from the Settings dialog, close it first so it cannot write its
    stale values back over what the sign-in/out just stored."""
    if xbmc.getCondVisibility("Window.IsActive(addonsettings)"):
        xbmc.executebuiltin("Dialog.Close(addonsettings,true)")
        xbmc.sleep(300)


def sign_in():
    _close_settings()
    api = PlexVodApi()
    dlg = xbmcgui.DialogProgress()
    dlg.create(L("link_heading"), "")

    def progress(code, pct):
        if dlg.iscanceled():
            return False
        dlg.update(min(99, pct), L("link_text") % code)
        return True

    try:
        tok = api.auth.link(progress)
    except Exception as exc:
        kodiutils.log_error("sign in failed: %s" % exc)
        tok = None
    finally:
        dlg.close()
    kodiutils.notify(L("sign_in_ok") if tok else L("sign_in_failed"))
    xbmc.executebuiltin("Container.Refresh")


def sign_out():
    _close_settings()
    if xbmcgui.Dialog().yesno(kodiutils.ADDON_NAME, L("confirm_sign_out")):
        PlexVodApi().auth.sign_out()
        xbmc.executebuiltin("Container.Refresh")


def router(paramstring):
    p = dict(parse_qsl(paramstring))
    action = p.get("action")
    try:
        if not action:
            root()
        elif action == "continue":
            continue_watching()
        elif action == "trailer":
            trailer(p["rk"])
        elif action == "mark":
            mark(p["rk"], p.get("how", "scrobble"))
        elif action == "hubs":
            hubs()
        elif action == "genres":
            genres(p.get("type", TYPE_MOVIE))
        elif action == "list":
            listing(p["key"], int(p.get("start", 0)))
        elif action == "children":
            children(p["rk"], int(p.get("start", 0)))
        elif action == "search":
            search(p.get("query"))
        elif action == "play":
            play(p["rk"])
        elif action == "sign_in":
            sign_in()
        elif action == "sign_out":
            sign_out()
    except PlexApiError as exc:
        kodiutils.log_error(str(exc))
        kodiutils.notify("%s: %s" % (L("error"), exc), icon=xbmcgui.NOTIFICATION_ERROR)
        if HANDLE >= 0 and action != "play":
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    router(sys.argv[2][1:] if len(sys.argv) > 2 else "")
