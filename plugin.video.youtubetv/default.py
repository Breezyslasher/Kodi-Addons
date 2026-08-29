"""YouTube TV for Kodi -- plugin entry point."""

import re
import sys
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin

from lib import api, auth, epg, kodiutils, oauth, playback

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

def _sign_in():
    """The device-code flow: the only way in.

    Google shows a short code, the account authorises it on another device,
    and InnerTube is called with a bearer token. This is what the regular
    YouTube addon does, and tv.youtube.com honours it -- as
    TVHTML5_UNPLUGGED, which _verify_bearer establishes rather than assumes.

    The addon used to take a cookie jar exported from a signed-in browser
    instead, which needed no Google API project but went stale every few
    days. This needs a project once and then refreshes itself.

    The token is put straight to work fetching the account's own lineup and
    kept only if that works: a stored credential that silently fails is
    worse than none.
    """
    client_id, secret = oauth.credentials()
    if not client_id:
        kodiutils.ok_dialog(
            "Signing in needs a Google API project. This add-on looks in "
            "three places and found none: its own settings, the YouTube "
            "add-on's (if you have that set up, its project is reused as "
            "is), and one built into this build.\n\n"
            "Create one at console.cloud.google.com -- enable the YouTube "
            "Data API v3, make an OAuth client ID of type \"TVs and Limited "
            "Input devices\" -- and paste the ID and secret into this "
            "add-on's settings, under Account.",
            "Nothing to sign in with")
        return False

    try:
        started = oauth.request_code(client_id)
    except oauth.OAuthError as exc:
        kodiutils.ok_dialog(str(exc), "Sign-in failed")
        return False

    url = started.get("verification_url") or "https://www.google.com/device"
    progress = xbmcgui.DialogProgress()
    progress.create("Sign in with a code",
                    "On another device, open:\n\n[B]%s[/B]\n\n"
                    "and enter the code:  [B]%s[/B]"
                    % (url, started.get("user_code")))
    deadline = time.time() + min(int(started.get("expires_in") or 300), 900)
    try:
        token = oauth.poll_for_token(
            client_id, secret, started["device_code"],
            started.get("interval"), deadline,
            cancelled=progress.iscanceled)
    except oauth.OAuthError as exc:
        progress.close()
        kodiutils.ok_dialog(str(exc), "Sign-in failed")
        return False
    finally:
        progress.close()

    if not token:
        return False

    oauth.save(token)
    ok, message, client_name = _verify_bearer()
    if ok:
        # Remember which identity answered, so every later call makes the
        # request that works rather than the one that does not.
        oauth.save(token, client_name=client_name)
    if not ok:
        oauth.forget()
        kodiutils.ok_dialog(
            "Google signed us in, but no client identity got a lineup from "
            "that token:\n\n%s\n\nThe token has been discarded. Check the "
            "account authorised is the one with the YouTube TV "
            "subscription." % message,
            "YouTube TV does not accept this")
        return False
    kodiutils.ok_dialog(message, "Signed in")
    return True


def _verify_bearer():
    """Ask YouTube TV for the lineup with the bearer token, as each client.

    One refusal is not an answer. A device-code token is minted for a
    limited-input client, so being turned away while claiming to be the *web*
    player says only that -- and the first run of this came back
    "INVALID_ARGUMENT: Request contains an invalid argument", which is what a
    malformed request looks like rather than what a rejected credential looks
    like. So every identity the addon knows is tried and the outcomes are
    reported together; the TV ones are the ones OAuth would plausibly suit.
    """
    try:
        client = api.Api()
    except auth.AuthError as exc:
        return False, str(exc), ""

    order = ["TVHTML5_UNPLUGGED", "TV_UNPLUGGED_ANDROID", "TV_UNPLUGGED_CAST",
             "ANDROID_UNPLUGGED", "IOS_UNPLUGGED", "WEB_UNPLUGGED"]
    tried = []
    for name in order:
        if name not in api.UNPLUGGED_CLIENTS:
            continue
        try:
            stations = epg.parse_epg(client.epg(hours=2, client_name=name))
        except Exception as exc:
            tried.append("%s: %s" % (name, str(exc)[:160]))
            kodiutils.log("oauth probe: %s refused -- %s" % (name, exc))
            continue
        if stations:
            kodiutils.log("oauth probe: %s answered with %d station(s)"
                          % (name, len(stations)))
            return True, ("Signed in as %s. %d channels in your lineup."
                          % (name, len(stations))), name
        tried.append("%s: answered, but with an empty lineup" % name)
        kodiutils.log("oauth probe: %s answered with an empty lineup" % name)
    return False, "\n".join(tried), ""


def _client():
    """An Api bound to the stored session, or None once the user is told why."""
    try:
        return api.Api()
    except auth.AuthError:
        kodiutils.ok_dialog(
            "Sign in first: choose Sign in, and authorise the code on your "
            "phone or laptop.", "Not signed in")
        return None


# -- routes ----------------------------------------------------------------

def route_root():
    # Read from the stored file rather than through oauth.access_token(),
    # which may go to Google to refresh -- not something to do while drawing
    # a menu.
    if not auth.signed_in():
        add_dir("Sign in", plot="Authorise a code on your phone or laptop.",
                action="signin")
        finish()
        return

    add_dir("Live channels", plot="Every channel in your lineup, playing now.",
            action="channels")
    add_dir("Guide", plot="What is on across the next few hours.",
            action="guide")
    add_dir("Search", action="search")
    # Offered while already signed in, not only before, so a session that
    # has gone wrong can be replaced without signing out first.
    add_dir("Sign in again", plot="Authorise a new code, replacing the "
                                  "stored sign-in.", action="signin")
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


def _expand_sections(client, response, items):
    """Fetch the shelves the page deferred, and add what they hold.

    A show page carries its newest episode or two and defers the rest: Rick
    and Morty answers with two, and hides Seasons 1-9 and Extras behind ten
    continuation tokens. Listing the page alone showed two episodes on an
    account entitled to nine.

    The shelves are fetched together rather than one after another. Ten
    round trips in series is a folder that takes several seconds to open,
    and they do not depend on each other.
    """
    sections = epg.section_continuations(response)
    if not sections:
        return items

    kodiutils.log("the page itself carried %d item(s) and defers %d shelf/ves"
                  % (len(items), len(sections)))
    seen = {item.video_id or item.browse_id for item in items}

    def fetch(pair):
        label, token = pair
        try:
            shelf = client.continuation(token)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log("could not open %s: %s" % (label or "a shelf", exc))
            return label, [], 0
        return label, epg.parse_items(shelf), epg.unplayable_count(shelf)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, len(sections))) as pool:
        fetched = list(pool.map(fetch, sections))

    barred = 0
    for label, found, unplayable in fetched:
        added = 0
        for item in found:
            key = item.video_id or item.browse_id
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            added += 1
        barred += unplayable
        # An empty shelf and a shelf of episodes this account has no rights
        # to both used to log "0 of 0", and only one of those is a show you
        # cannot watch.
        kodiutils.log("%s: %d of %d were new%s"
                      % (label or "shelf", added, len(found),
                         ", and %d listed that this account cannot play"
                         % unplayable if unplayable else ""))
    kodiutils.log("listing %d item(s) in all%s"
                  % (len(items),
                     " -- %d more are listed but not playable on this "
                     "account" % barred if barred else ""))
    return _in_episode_order(items)


_EPISODE = re.compile(r"^S(\d+)\s*E(\d+)\b")


def _in_episode_order(items):
    """Sort a merged list into episode order, when it is one.

    Merging leaves the page's own two newest episodes in front of the
    seasons that follow, so Rick and Morty listed S9E10, S9E9, then S7E10,
    S8E1, S9E8 and down -- every episode present and none of them in order.

    Only when every title names a season and an episode, which is what a
    show page's shelves give and what a channel or a film page does not.
    Anything else keeps the order the server chose.
    """
    numbered = [_EPISODE.match(item.title or "") for item in items]
    if not items or not all(numbered):
        return items
    order = {id(item): (int(m.group(1)), int(m.group(2)))
             for item, m in zip(items, numbered)}
    return sorted(items, key=lambda item: order[id(item)])


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

    items = _expand_sections(client, response, epg.parse_items(response))
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


def route_play_channel(station_id):
    """Play whatever is on this channel now.

    IPTV Manager writes channel urls into a playlist that outlives any one
    programme, so the url it stores names the station and the airing is
    looked up here, at the moment of playing. The same route is what makes a
    channel bookmarkable from inside the addon.
    """
    client = _client()
    if not client:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        stations = _fetch_stations(client, hours=2)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not load the guide")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    station = next((s for s in stations if s.station_id == station_id), None)
    if not station or not station.now or not station.now.video_id:
        kodiutils.ok_dialog(
            "Nothing is listed as playing on this channel right now.",
            "Cannot play this")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    route_play(station.now.video_id, station.name)


def route_iptv(what, port):
    """Answer service.iptv.manager on the socket it just opened."""
    if not port:
        kodiutils.log_error("iptv manager: called with no port")
        return
    from lib import iptv
    manager = iptv.IPTVManager(port)
    if what == "channels":
        manager.send_channels()
    else:
        manager.send_epg()


def main():
    params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    action = params.get("action")

    if action == "signin":
        _sign_in()
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
    elif action == "play_channel":
        route_play_channel(params.get("station_id", ""))
        return
        return
    elif action in ("iptv_channels", "iptv_epg"):
        # RunPlugin, not a directory: there is no handle to finish and
        # nothing to draw. The answer goes back over IPTV Manager's socket.
        route_iptv(action.split("_", 1)[1], params.get("port", ""))
        return

    route_root()


if __name__ == "__main__":
    main()
