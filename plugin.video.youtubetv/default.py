"""YouTube TV for Kodi -- plugin entry point."""

import sys
import time
from urllib.parse import parse_qsl, urlencode

import xbmcgui
import xbmcplugin

from lib import api, auth, epg, kodiutils, playback

HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else -1
BASE_URL = sys.argv[0] if sys.argv else "plugin://plugin.video.youtubetv/"


def url(**kwargs):
    return "%s?%s" % (BASE_URL, urlencode(kwargs))


def add_dir(label, art=None, plot=None, **params):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt({"thumb": art, "icon": art})
    if plot:
        item.getVideoInfoTag().setPlot(plot)
    xbmcplugin.addDirectoryItem(HANDLE, url(**params), item, isFolder=True)


def finish(content=""):
    if content:
        xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE)


# -- sign-in ---------------------------------------------------------------

def _verify(cookies):
    """Prove the imported jar actually works before storing it.

    Checking that SAPISID and SID are present only proves something was pasted.
    One guide call distinguishes the three ways sign-in goes wrong -- expired
    cookies, the wrong account, no subscription -- while the user is still in
    front of the dialog, instead of at the first play.
    """
    try:
        client = api.Api(cookies=cookies)
        stations = epg.parse_epg(client.epg(hours=1, max_airings=1))
    except auth.AuthError:
        return False, ("Google rejected those cookies. Export them again from "
                       "a browser that is signed in right now -- they expire.")
    except api.ApiError as exc:
        return False, "Could not reach YouTube TV: %s" % exc

    if not stations:
        return False, ("Those cookies work, but the account has no YouTube TV "
                       "lineup. Check it is the account with the subscription.")
    return True, "Signed in -- %d channels" % len(stations)


def _import_cookies():
    """Ask for a cookie jar, either as a file or pasted.

    A browser export is the sane route; the paste exists for the case where the
    file cannot be got onto the Kodi box easily.
    """
    choice = xbmcgui.Dialog().contextmenu([
        "Choose a cookies.txt file",
        "Paste a Cookie header",
    ])
    try:
        if choice == 0:
            path = xbmcgui.Dialog().browseSingle(1, "Select cookies.txt", "files")
            if not path:
                return False
            cookies = auth.parse_cookies_txt(path)
        elif choice == 1:
            pasted = kodiutils.input_text("Paste the Cookie header")
            if not pasted:
                return False
            cookies = auth.parse_cookie_header(pasted)
        else:
            return False
    except Exception as exc:
        kodiutils.ok_dialog("Could not read those cookies: %s" % exc,
                            "Sign-in failed")
        return False

    missing = [name for name in auth.REQUIRED if name not in cookies]
    if missing:
        kodiutils.ok_dialog(
            "That jar has no %s. Export with every domain included, not just "
            "the current site, from a browser signed in to tv.youtube.com."
            % " or ".join(missing), "Sign-in failed")
        return False

    ok, message = _verify(cookies)
    if not ok:
        kodiutils.ok_dialog(message, "Sign-in failed")
        return False

    auth.save(cookies)
    kodiutils.notify(message)
    return True


def _client():
    """An Api bound to the stored session, or None once the user is told why."""
    try:
        return api.Api()
    except auth.AuthError:
        kodiutils.ok_dialog(
            "Sign in first: export the cookies of a browser that is already "
            "signed in to tv.youtube.com, then choose Sign in.",
            "Not signed in")
        return None


# -- routes ----------------------------------------------------------------

def route_root():
    if not auth.signed_in():
        add_dir("Sign in", plot="Import cookies from a signed-in browser.",
                action="signin")
        finish()
        return

    add_dir("Live channels", plot="Every channel in your lineup, playing now.",
            action="channels")
    add_dir("Guide", plot="What is on across the next few hours.",
            action="guide")
    add_dir("Search", action="search")
    add_dir("Sign out", action="signout")
    finish()


def _fetch_stations(client, hours=3):
    response = client.epg(hours=hours)
    return epg.parse_epg(response)


def route_channels():
    client = _client()
    if not client:
        finish()
        return

    try:
        stations = _fetch_stations(client, hours=2)
    except auth.AuthError as exc:
        kodiutils.ok_dialog(str(exc), "Sign-in problem")
        finish()
        return
    except api.ApiError as exc:
        kodiutils.ok_dialog(str(exc), "Could not load the guide")
        finish()
        return

    for station in stations:
        now = station.now
        if not now or not now.video_id:
            continue
        label = station.name
        plot = now.title
        if now.description:
            plot = "%s\n\n%s" % (now.title, now.description)
        if station.next_up:
            plot += "\n\nNext: %s" % station.next_up.label()

        item = xbmcgui.ListItem(label=label)
        item.setArt({"thumb": station.logo, "icon": station.logo,
                     "fanart": now.art or station.logo})
        info = item.getVideoInfoTag()
        info.setTitle(label)
        info.setPlot(plot)
        info.setMediaType("video")
        item.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(
            HANDLE,
            url(action="play", video_id=now.video_id, label=label),
            item, isFolder=False)

    finish("videos")


def route_guide():
    """Channels as folders, each listing its own schedule."""
    client = _client()
    if not client:
        finish()
        return
    try:
        stations = _fetch_stations(client, hours=6)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not load the guide")
        finish()
        return

    for station in stations:
        if not station.airings:
            continue
        add_dir(station.name, art=station.logo,
                plot=station.now.title if station.now else None,
                action="station", station_id=station.station_id,
                name=station.name)
    finish("videos")


def route_station(station_id, name):
    client = _client()
    if not client:
        finish()
        return
    try:
        stations = _fetch_stations(client, hours=12)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not load the guide")
        finish()
        return

    station = next((s for s in stations if s.station_id == station_id), None)
    if not station:
        kodiutils.notify("That channel is no longer in the guide")
        finish()
        return

    now_ms = time.time() * 1000
    for airing in station.airings:
        # Past programmes are listed but not playable: YouTube TV catch-up is
        # a separate entitlement, and offering a dead link reads as a bug.
        playable = airing.is_now or (airing.start_ms or 0) <= now_ms
        item = xbmcgui.ListItem(label=airing.label())
        item.setArt({"thumb": airing.art or station.logo,
                     "fanart": airing.art or station.logo})
        info = item.getVideoInfoTag()
        info.setTitle(airing.title)
        info.setPlot(airing.description)
        info.setMediaType("video")
        if airing.is_now:
            item.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(
                HANDLE,
                url(action="play", video_id=airing.video_id, label=station.name),
                item, isFolder=False)
        else:
            item.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(HANDLE, "", item, isFolder=False)
    finish("videos")


def _add_items(items, content="videos"):
    """List parsed items: playable ones resolve, folders browse deeper."""
    for item in items:
        listitem = xbmcgui.ListItem(label=item.title)
        listitem.setArt({"thumb": item.art, "fanart": item.art})
        info = listitem.getVideoInfoTag()
        info.setTitle(item.title)
        if item.subtitle:
            info.setPlot(item.subtitle)
        info.setMediaType("video")
        if item.playable:
            listitem.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(
                HANDLE,
                url(action="play", video_id=item.video_id, label=item.title),
                listitem, isFolder=False)
        else:
            xbmcplugin.addDirectoryItem(
                HANDLE,
                url(action="browse", browse_id=item.browse_id,
                    name=item.title),
                listitem, isFolder=True)
    finish(content)


def route_search():
    query = kodiutils.input_text("Search YouTube TV")
    if not query:
        finish()
        return
    client = _client()
    if not client:
        finish()
        return
    try:
        response = client.search(query)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Search failed")
        finish()
        return

    items = epg.parse_search(response)
    if not items:
        kodiutils.notify("Nothing found for %s" % query)
    _add_items(items)


def route_browse(browse_id, name):
    """A show, movie or channel page reached from search.

    Search answers with shows rather than episodes, so this is where the
    playable things actually live.
    """
    client = _client()
    if not client:
        finish()
        return
    try:
        response = client.browse(browse_id)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or browse_id))
        finish()
        return

    items = epg.parse_items(response)
    if not items:
        kodiutils.notify("Nothing playable under %s" % (name or browse_id))
    _add_items(items)


def route_play(video_id, label):
    client = _client()
    if not client:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        _response, item = playback.prepare(client, video_id, label=label)
    except api.NotPlayable as exc:
        kodiutils.ok_dialog(str(exc), "Cannot play this")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Playback failed")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    if item is None:
        kodiutils.ok_dialog(
            "YouTube did not offer a DASH manifest for this stream, only its "
            "own SABR endpoint, which Kodi cannot play.", "Cannot play this")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def main():
    params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    action = params.get("action")

    if action == "signin":
        _import_cookies()
    elif action == "signout":
        if xbmcgui.Dialog().yesno("YouTube TV", "Forget the stored session?"):
            auth.sign_out()
            kodiutils.notify("Signed out")
    elif action == "channels":
        route_channels()
        return
    elif action == "guide":
        route_guide()
        return
    elif action == "station":
        route_station(params.get("station_id", ""), params.get("name", ""))
        return
    elif action == "search":
        route_search()
        return
    elif action == "browse":
        route_browse(params.get("browse_id", ""), params.get("name", ""))
        return
    elif action == "play":
        route_play(params.get("video_id", ""), params.get("label", ""))
        return

    route_root()


if __name__ == "__main__":
    main()
