"""Plex On Demand (free, ad-supported) - entry point and router."""

import sys
from urllib.parse import urlencode, parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.api import PlexVodApi, PlexApiError, PAGE_SIZE, TYPE_MOVIE, TYPE_SHOW, is_store_item, image
from lib import playback
from lib.listing import art as _art, info as _info
from lib.auth import account_name

HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1
BASE_URL = sys.argv[0]

S = {
    "featured": 32010,
    "brands": 32077,
    "categories": 32078,
    "live_tv": 32079,
    "live_all": 32080,
    "live_now": 32081,
    "live_next": 32082,
    "live_later": 32085,
    "live_starts": 32086,
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
    "new_search": 32046,
    "watchlist": 32048,
    "add_watchlist": 32049,
    "remove_watchlist": 32050,
    "watchlist_added": 32051,
    "watchlist_removed": 32075,
    "watchlist_empty": 32052,
    "region_notice": 32053,
    "related": 32054,
    "more_cast": 32062,
    "rate": 32063,
    "rentals": 32068,
    "my_rentals": 32069,
    "no_rentals": 32070,
    "not_rented": 32071,
    "rent_price": 32072,
    "buy_price": 32073,
    "rent_where": 32074,
    "stars": 32064,
    "clear_rating": 32065,
    "not_interested": 32066,
    "undo_not_interested": 32067,
    "remove_search": 32047,
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
    keys = [m.get("ratingKey") for m in items if m.get("type") in ("movie", "episode", "show")]
    if not keys:
        return
    states = api.user_states(keys)
    for m in items:
        st = states.get(m.get("ratingKey"))
        if st:
            for field in ("viewOffset", "viewCount", "userRating"):
                if m.get(field) is None:
                    m[field] = st.get(field)


def _menu(m, in_cw=False, in_wl=False):
    rk = m["ratingKey"]
    entries = []
    if m.get("type") in ("movie", "show"):
        if in_wl:
            entries.append((L("remove_watchlist"), "RunPlugin(%s)" % url(
                action="mark", rk=rk, how="removeFromWatchlist")))
        else:
            entries.append((L("add_watchlist"), "RunPlugin(%s)" % url(
                action="mark", rk=rk, how="addToWatchlist")))
    if m.get("type") in ("movie", "show"):
        entries.append((L("related"), "Container.Update(%s)" % url(action="related", rk=rk)))
    if m.get("type") in ("movie", "show", "episode"):
        entries.append((L("rate"), "RunPlugin(%s)" % url(action="rate", rk=rk)))
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


def add_items(items, api=None, in_cw=False, in_wl=False, in_store=False, prices=True):
    content = "videos"
    if not in_store:
        # Safety net: rentals belong in the Rentals menu, never in free lists.
        items = [m for m in items if not is_store_item(m)]
    watchlisted = set()
    if api is not None:
        _apply_user_state(api, items)
        if not in_wl and any(m.get("type") in ("movie", "show") for m in items):
            watchlisted = api.watchlist_keys()
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
        if in_store and prices and m.get("rentalPrice"):
            item.setLabel("%s  [COLOR gray]%s[/COLOR]" % (label, m["rentalPrice"]))
        if in_cw and kind == "episode" and m.get("grandparentTitle"):
            item.setLabel("%s - %s" % (m["grandparentTitle"], label))
        item.addContextMenuItems(_menu(m, in_cw, in_wl or rk in watchlisted))
        if kind in ("movie", "episode") and in_store and prices:
            # Store rows: an action, not a playable item. Kodi reports
            # "Playback failed" whenever a playable item is declined, which is
            # what a not-rented title has to do; an action can just stop.
            content = "movies" if kind == "movie" else "episodes"
            xbmcplugin.addDirectoryItem(HANDLE, url(action="store_play", rk=rk), item, False)
        elif kind in ("movie", "episode"):
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
    _region_notice(api)
    folder(L("continue_watching"), {"action": "continue"})
    folder(L("watchlist"), {"action": "watchlist"})
    # Rentals only when signed in (an anonymous account can't own any) and
    # the user hasn't hidden them (Settings > Browse > Show Rentals).
    if api.auth.signed_in():
        # The account's own rentals/purchases: always reachable when there are
        # any, whatever the store setting says; hidden when there are none.
        if api.owns_any():
            folder(L("my_rentals"), {"action": "list", "key": MY_RENTALS, "store": 1})
        # The store itself (priced rows) can be switched off in Settings.
        if kodiutils.get_setting_bool("show_rentals", True):
            folder(L("rentals"), {"action": "store"})
    if kodiutils.get_setting_bool("show_live", True):
        folder(L("live_tv"), {"action": "live"})
    folder(L("featured"), {"action": "hubs"})
    folder(L("brands"), {"action": "brands"})
    folder(L("categories"), {"action": "categories"})
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


def _region_notice(api):
    """Once per Kodi session: the free catalogue is mainly US-only."""
    win = xbmcgui.Window(10000)
    if win.getProperty("plexondemand.region_checked"):
        return
    win.setProperty("plexondemand.region_checked", "1")
    code = api.region()
    if code and code != "US":
        kodiutils.notify(L("region_notice") % code, time_ms=8000)


MY_RENTALS = "/hubs/sections/store/myRentalsAndPurchases"


def store():
    """Plex's rental store rows. The account's own rentals live on the home
    menu (My Rentals & Purchases). Renting happens in Plex's apps or site."""
    for h in PlexVodApi().store_hubs():
        if h["key"] == MY_RENTALS:
            continue
        folder(h.get("title") or h["key"], {"action": "list", "key": h["key"], "store": 1})
    end("files")


def _directory_folders(rows):
    """Brand folders with their logo (resized)."""
    from lib.api import image
    for d in rows:
        if not d.get("key"):
            continue
        thumb = d.get("thumb") or ""
        art = None
        if thumb and not thumb.lower().split("?")[0].endswith(".svg"):
            t = image(thumb, 400, 400)
            # icon/thumb only: with a poster set, Kodi's poster views show the
            # picture without the name.
            art = {"thumb": t, "icon": t}
        folder(d.get("title") or "?", {"action": "list", "key": d["key"]}, art=art)
    end("files")


def brands():
    _directory_folders(PlexVodApi().brands())


def category_art(title):
    """Bundled category art with the name drawn in (tools/make_category_art.py);
    Plex's own is a bare gradient it prints the name over. None if not bundled."""
    import os
    import re
    import unicodedata
    plain = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    path = os.path.join(kodiutils.addon().getAddonInfo("path"), "resources", "media", "categories", name + ".jpg")
    return path if name and os.path.exists(path) else None


def categories():
    for d in PlexVodApi().categories():
        if d.get("key"):
            t = category_art(d.get("title"))
            folder(d.get("title") or "?", {"action": "list", "key": d["key"]},
                   art={"thumb": t, "icon": t} if t else None)
    end("files")


def hubs():
    for h in PlexVodApi().hubs():
        folder(h.get("title") or h["key"], {"action": "list", "key": h["key"], "title": h.get("title", "")})
    end("files")


SORTS = ["", "titleSort", "titleSort:desc", "audienceRating:desc"]  # setting order


def genres(media_type):
    sort = SORTS[min(kodiutils.get_setting_int("genre_sort", 0), len(SORTS) - 1)]
    for g in PlexVodApi().genres(int(media_type)):
        if g.get("key"):
            query = {"action": "list", "key": g["key"]}
            if sort:
                query["sort"] = sort
            icon = g.get("icon") or ""
            if icon.lower().split("?")[0].endswith(".svg"):
                icon = ""  # Kodi can't draw SVG, and Plex answers Kodi's fetch with 403
            folder(g.get("title") or "?", query, art={"icon": icon} if icon else None)
    end("files")


def listing(key, start=0, sort=None, in_store=False):
    api = PlexVodApi()
    items, total = api.listing(key, start, sort)
    if in_store and key != MY_RENTALS:
        # Plex mixes a few free titles into its store rows; the Rentals store
        # shows only what is rented or bought (free ones are in the free menus).
        items = [m for m in items if is_store_item(m)]
    if key == MY_RENTALS and not items and not start:
        kodiutils.notify(L("no_rentals"))
    # Prices only in the store rows, not in the account's own rentals.
    content = add_items(items, api, in_store=in_store or key == MY_RENTALS, prices=key != MY_RENTALS)
    query = {"action": "list", "key": key}
    if in_store:
        query["store"] = 1
    if sort:
        query["sort"] = sort
    add_next(query, start, len(items), total)
    end(content)


def children(rk, start=0):
    api = PlexVodApi()
    items, total = api.children(rk, start)
    content = add_items(items, api)
    add_next({"action": "children", "rk": rk}, start, len(items), total)
    end(content)


HISTORY_FILE = "search_history.json"
HISTORY_MAX = 20


def search_menu():
    """New search + recent searches. Nothing here opens the keyboard on its
    own, so coming back to this folder never re-prompts."""
    # An action, not a folder: a folder must finish its own listing first, and
    # Kodi drops a Container.Update sent while that listing is still loading
    # (the results then only opened from the recent-search entry).
    new = xbmcgui.ListItem("[B]%s[/B]" % L("new_search"))
    xbmcplugin.addDirectoryItem(HANDLE, url(action="search_new"), new, False)
    for q in kodiutils.read_json(HISTORY_FILE, []) or []:
        item = xbmcgui.ListItem(q)
        item.addContextMenuItems([(L("remove_search"), "RunPlugin(%s)" % url(action="search_forget", query=q))])
        xbmcplugin.addDirectoryItem(HANDLE, url(action="search", query=q), item, True)
    end("files", cache=False)


def search_new():
    """Ask once, then move to a results URL that carries the query: Kodi
    reloads that URL (not this one) when you come back from a video, so the
    keyboard does not reopen."""
    query = kodiutils.input_text(L("search"))
    as_folder = HANDLE >= 0  # opened from a listing an older build made, where this was a folder
    if not query:
        if as_folder:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    query = query.strip()
    history = [q for q in (kodiutils.read_json(HISTORY_FILE, []) or []) if q.lower() != query.lower()]
    kodiutils.write_json(HISTORY_FILE, [query] + history[:HISTORY_MAX - 1])
    if as_folder:
        # Kodi expects a listing here; give it the results rather than fail.
        search(query)
        return
    xbmc.executebuiltin("Container.Update(%s)" % url(action="search", query=query))


def search_forget(query):
    history = [q for q in (kodiutils.read_json(HISTORY_FILE, []) or []) if q != query]
    kodiutils.write_json(HISTORY_FILE, history)
    xbmc.executebuiltin("Container.Refresh")


def search(query=None):
    if not query:
        search_menu()
        return
    api = PlexVodApi()
    items = api.search(query)
    if not items:
        kodiutils.notify(L("no_results"))
    content = add_items(items, api)
    end(content, cache=False)


def watchlist(start=0):
    api = PlexVodApi()
    items, total = api.watchlist(start)
    if not items and not start:
        kodiutils.notify(L("watchlist_empty"))
    content = add_items(items, api, in_wl=True)
    add_next({"action": "watchlist"}, start, len(items), total)
    end(content, cache=False)


def related(rk, kind="similarTitles"):
    """More like this: Plex's similar titles, with a folder for the cast &
    crew's other free titles at the top."""
    api = PlexVodApi()
    if kind == "similarTitles":
        folder("[B]%s[/B]" % L("more_cast"), {"action": "related", "rk": rk, "kind": "more-from-credits"})
    items = api.related(rk, kind)
    content = add_items(items, api)
    end(content)


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


def rate(rk):
    """Rate 1-5 stars (Plex stores 0-10), clear, or mark Not interested.
    Plex keeps dislikes write-only, so only the rating can be shown back."""
    api = PlexVodApi()
    current = api.user_state(rk).get("userRating")
    heading = L("rate")
    if current is not None:
        heading = "%s (%s)" % (heading, L("stars") % (float(current) / 2))
    options = [(L("stars") % n, ("rate", n * 2)) for n in range(5, 0, -1)]
    options += [(L("clear_rating"), ("rate", -1)), (L("not_interested"), ("addToDislikes", None)),
                (L("undo_not_interested"), ("removeFromDislikes", None))]
    pick = xbmcgui.Dialog().select(heading, [o[0] for o in options])
    if pick < 0:
        return
    how, value = options[pick][1]
    try:
        if how == "rate":
            api.rate(rk, value)
        else:
            api.action(how, rk)
    except PlexApiError as exc:
        kodiutils.log_error("%s %s: %s" % (how, rk, exc))
        kodiutils.notify("%s: %s" % (L("error"), exc), icon=xbmcgui.NOTIFICATION_ERROR)
        return
    kodiutils.notify(options[pick][0])
    if how == "rate":
        xbmc.executebuiltin("Container.Refresh")


def mark(rk, how, quiet=False):
    try:
        PlexVodApi().action(how, rk)
    except PlexApiError as exc:
        kodiutils.log_error("%s %s: %s" % (how, rk, exc))
        kodiutils.notify("%s: %s" % (L("error"), exc), icon=xbmcgui.NOTIFICATION_ERROR)
        return
    if how == "addToWatchlist":
        kodiutils.notify(L("watchlist_added"))
    elif how == "removeFromWatchlist":
        kodiutils.notify(L("watchlist_removed"))
    # Refresh so the context menu flips between Add and Remove (and watched
    # ticks / Continue Watching rows update).
    xbmc.executebuiltin("Container.Refresh")


def _not_rented_dialog(m):
    """Short lines, title as the heading: Kodi's OK dialog fits ~4 lines."""
    offers = []
    if m.get("rentalPrice"):
        offers.append(L("rent_price") % (m["rentalPrice"], m.get("viewableDuration") or 48))
    if m.get("purchasePriceLabel"):
        offers.append(L("buy_price") % m["purchasePriceLabel"])
    lines = [L("not_rented")]
    if offers:
        lines.append("   \u00b7   ".join(offers))
    lines.append(L("rent_where") % "watch.plex.tv")
    kodiutils.ok_dialog("[CR]".join(lines), heading=m.get("title") or None)


def store_play(rk):
    """A store-row title: play it if this account has rented/bought it,
    otherwise say where to rent it - without Kodi's "Playback failed"."""
    api = PlexVodApi()
    try:
        item = playback.build(api, rk)
    except playback.NotRented as nr:
        _not_rented_dialog(nr.item)
        return
    except playback.Blocked:
        kodiutils.ok_dialog(L("vpn_blocked"))
        return
    except playback.DrmUnavailable:
        kodiutils.ok_dialog(L("drm_unavailable"))
        return
    except Exception as exc:
        kodiutils.log_error("store play %s failed: %s" % (rk, exc))
        kodiutils.notify(L("playback_failed"), icon=xbmcgui.NOTIFICATION_ERROR)
        return
    xbmc.Player().play(item.getPath(), item)


def play(rk):
    api = PlexVodApi()
    try:
        item = playback.build(api, rk)
    except playback.NotRented as nr:
        _not_rented_dialog(nr.item)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
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


# ---------------------------------------------------------------- live TV
LIVE_PAGE = 100


def live_root():
    from lib.live import LiveApi, ALL
    filters = LiveApi().filters()
    # Featured (Plex's promoted filter) first, then every channel, then genres.
    for title, key, _g in filters[:1]:
        folder(title, {"action": "live_list", "key": key})
    folder(L("live_all"), {"action": "live_list", "key": ALL})
    for title, key, _g in filters[1:]:
        folder(title, {"action": "live_list", "key": key})
    end("files")


def live_list(key, start=0):
    from lib import live
    api = live.LiveApi()
    chans = api.channels(key)
    page = chans[start:start + LIVE_PAGE]
    guide = api.now_next(page)
    for c in page:
        cur, nxt = guide.get(c.get("gridKey"), (None, None))
        label = c.get("title") or "?"
        plot = []
        if cur:
            m = cur["Media"][0]
            label = "%s  [COLOR grey]%s[/COLOR]" % (label, live.programme_title(cur))
            plot.append("[B]%s %s-%s[/B]  %s" % (L("live_now"), live.clock(m["beginsAt"]), live.clock(m["endsAt"]),
                                                  live.programme_title(cur)))
            if live.episode_label(cur):
                plot.append(live.episode_label(cur))
            if cur.get("summary"):
                plot.append(cur["summary"])
        if nxt:
            plot.append("")
            plot.append("[B]%s %s[/B]  %s" % (L("live_next"), live.clock(nxt["Media"][0]["beginsAt"]),
                                              live.programme_title(nxt)))
        if c.get("summary"):
            plot += ["", c["summary"]]
        item = xbmcgui.ListItem(label)
        item.setArt(live.channel_art(c))
        tag = item.getVideoInfoTag()
        tag.setTitle(c.get("title") or "")
        tag.setPlot("\n".join(plot))
        # Leaving a live stream at the live edge looks like "played to the
        # end" to Kodi, which would tick the channel as watched and offer a
        # resume point. A channel is never watched: say so explicitly (the
        # addon's own values win over Kodi's database in the listing).
        tag.setPlaycount(0)
        tag.setResumePoint(0, 0)
        item.addContextMenuItems([(L("live_later"), "Container.Update(%s)" % url(
            action="live_guide", id=c["id"], gk=c.get("gridKey") or "", name=c.get("title") or ""))])
        item.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(HANDLE, url(action="live_play", id=c["id"]), item, False)
    if start + LIVE_PAGE < len(chans):
        nxt_start = start + LIVE_PAGE
        folder("%s (%d/%d)" % (L("next_page"), nxt_start // LIVE_PAGE + 1, (len(chans) + LIVE_PAGE - 1) // LIVE_PAGE),
               {"action": "live_list", "key": key, "start": nxt_start})
    end("videos", cache=False)


def live_guide(channel_id, grid_key, name):
    """What's on a channel from now to 24 hours ahead. The programme on now
    plays the channel; later ones say when they start."""
    import time
    from lib import live
    api = live.LiveApi()
    now = time.time()
    logo = ""
    for m in api.schedule(grid_key, now, now + 24 * 3600):
        media = m["Media"][0]
        on_now = media["beginsAt"] <= now < media["endsAt"]
        day = "" if time.localtime(media["beginsAt"]).tm_yday == time.localtime(now).tm_yday else \
            time.strftime("%a ", time.localtime(media["beginsAt"]))
        title = live.programme_title(m)
        ep = live.episode_label(m)
        label = "%s%s  %s%s" % (day, live.clock(media["beginsAt"]), "[B]%s[/B]" % title if on_now else title,
                                "  [COLOR grey]%s[/COLOR]" % ep if ep else "")
        item = xbmcgui.ListItem(label)
        thumb = m.get("thumb") or m.get("grandparentThumb") or ""
        item.setArt({"thumb": image(thumb, 640, 360), "icon": image(thumb, 640, 360),
                     "fanart": image(m.get("grandparentArt") or m.get("art") or thumb, 1280, 720)})
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        tag.setPlot("[B]%s-%s[/B]  %s\n%s" % (live.clock(media["beginsAt"]), live.clock(media["endsAt"]), name,
                                               m.get("summary") or ""))
        tag.setPlaycount(0)
        tag.setResumePoint(0, 0)
        if on_now:
            item.setProperty("IsPlayable", "true")
            target = url(action="live_play", id=channel_id)
        else:
            target = url(action="live_starts", at=live.clock(media["beginsAt"]), title=title)
        xbmcplugin.addDirectoryItem(HANDLE, target, item, False)
    end("videos", cache=False)


def live_play(channel_id):
    from lib import live
    try:
        item = live.build(live.LiveApi(), channel_id)
    except playback.Blocked:
        kodiutils.ok_dialog(L("vpn_blocked"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    except Exception as exc:
        kodiutils.log_error("live %s failed: %s" % (channel_id, exc))
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
            mark(p["rk"], p.get("how", "scrobble"), bool(p.get("quiet")))
        elif action == "rate":
            rate(p["rk"])
        elif action == "watchlist":
            watchlist(int(p.get("start", 0)))
        elif action == "related":
            related(p["rk"], p.get("kind", "similarTitles"))
        elif action == "store":  # still reachable from old links / favourites
            store()
        elif action == "live":
            live_root()
        elif action == "live_list":
            live_list(p["key"], int(p.get("start", 0)))
        elif action == "live_play":
            live_play(p["id"])
        elif action == "live_guide":
            live_guide(p["id"], p["gk"], p.get("name", ""))
        elif action == "live_starts":
            kodiutils.notify(L("live_starts") % (p.get("title", ""), p.get("at", "")))
        elif action == "brands":
            brands()
        elif action == "categories":
            categories()
        elif action == "hubs":
            hubs()
        elif action == "genres":
            genres(p.get("type", TYPE_MOVIE))
        elif action == "list":
            listing(p["key"], int(p.get("start", 0)), p.get("sort"), bool(p.get("store")))
        elif action == "children":
            children(p["rk"], int(p.get("start", 0)))
        elif action == "search":
            search(p.get("query"))
        elif action == "search_new":
            search_new()
        elif action == "search_forget":
            search_forget(p.get("query", ""))
        elif action == "store_play":
            store_play(p["rk"])
        elif action == "play":
            play(p["rk"])
        elif action == "sign_in":
            sign_in()
        elif action == "sign_out":
            sign_out()
    except PlexApiError as exc:
        kodiutils.log_error(str(exc))
        kodiutils.notify("%s: %s" % (L("error"), exc), icon=xbmcgui.NOTIFICATION_ERROR)
        if HANDLE >= 0 and action not in ("play", "live_play"):
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    # IPTV Manager calls /iptv/channels and /iptv/epg with ?port=N and wants
    # the answer on that socket, not a directory listing.
    from lib import iptv
    _query = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    if not iptv.handle(sys.argv[0], _query.get("port")):
        router(sys.argv[2][1:] if len(sys.argv) > 2 else "")
