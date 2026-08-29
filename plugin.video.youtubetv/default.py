"""YouTube TV for Kodi -- plugin entry point."""

import sys
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin

from lib import api, auth, epg, kodiutils, oauth, playback, signin

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
        "Sign in from your phone or laptop",
        "Sign in with a code (experimental)",
        "Choose a cookies.txt file",
        "Paste a Cookie header",
    ])
    if choice == 0:
        return _sign_in_over_the_network()
    if choice == 1:
        return _sign_in_with_code()
    choice -= 2
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


def _sign_in_over_the_network():
    """Serve a sign-in page on the LAN and wait for a jar to be pasted into it.

    Typing a three kilobyte Cookie header on a remote is not something anyone
    does twice, and getting a file onto the box is its own errand. The device
    that already has the session -- the phone or laptop signed in to
    tv.youtube.com -- has a keyboard and a clipboard, so the form goes there.
    """
    server = signin.SignInServer(verify=_verify)
    try:
        server.start()
    except Exception as exc:
        kodiutils.ok_dialog("Could not open the sign-in page: %s" % exc,
                            "Sign-in failed")
        return False

    host = xbmc.getIPAddress() or "127.0.0.1"
    progress = xbmcgui.DialogProgress()
    progress.create(
        "Sign in from another device",
        "On a phone or laptop signed in to tv.youtube.com, open:\n\n"
        "[B]%s[/B]\n\nThis page closes as soon as it has your session."
        % server.url(host))
    try:
        waited = 0
        # Ten minutes is long enough to find a laptop and short enough that a
        # page left open by accident does not stay open all evening.
        while waited < 600 and not server.cookies:
            if progress.iscanceled():
                return False
            xbmc.sleep(500)
            waited += 0.5
        cookies = server.cookies
    finally:
        progress.close()
        server.stop()

    if not cookies:
        kodiutils.notify("Sign-in page closed without a session")
        return False
    auth.save(cookies)
    kodiutils.notify("Signed in")
    return True


def _sign_in_with_code():
    """The device-code flow, and an honest test of whether it is any use here.

    This is what the regular YouTube addon does: Google shows a short code,
    the account authorises it on another device, and InnerTube is then called
    with a bearer token instead of a cookie jar.

    Whether tv.youtube.com honours that is not something the addon can reason
    its way to. Every authenticated request in every capture of the web player
    carries SAPISIDHASH and none carries a bearer token -- which says the web
    player does not use OAuth, not that the surface refuses it. So the token
    is put straight to work fetching the account's own lineup, and kept only
    if that works. A stored credential that silently fails is worse than none.
    """
    client_id, secret = oauth.credentials()
    if not client_id:
        kodiutils.ok_dialog(
            "This needs the client ID and secret of your own Google API "
            "project -- the same pair the YouTube add-on uses. Put them in "
            "this add-on's settings, under Account.", "Nothing to sign in with")
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
            "that token:\n\n%s\n\nThe token has been discarded. Sign in "
            "from your phone or laptop instead." % message,
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
            summary = ("Signed in as %s. %d channels in your lineup."
                       % (name, len(stations)))
            if not _oauth_can_play(client, stations):
                summary += (
                    "\n\nBrowsing and the guide work. Playback does not yet: "
                    "no request this add-on knows how to build gets both a "
                    "session this token is accepted for and a DASH manifest. "
                    "To play anything, also sign in from your phone or "
                    "laptop.")
            return True, summary, name
        tried.append("%s: answered, but with an empty lineup" % name)
        kodiutils.log("oauth probe: %s answered with an empty lineup" % name)
    return False, "\n".join(tried), ""


def _oauth_can_play(client, stations):
    """Whether this bearer session can get a DASH manifest for anything.

    Asked once at sign-in rather than left for the user to discover one
    "cannot play this" at a time. It uses the same search playback does, so
    the answer is the real one and not a second opinion.
    """
    airing = next((s.now for s in stations if s.now and s.now.video_id), None)
    if not airing:
        return True  # nothing to test with; do not claim a problem
    try:
        response = client.player(airing.video_id, api.new_cpn())
        response = playback._with_dash_manifest(
            client, airing.video_id, api.new_cpn(), response)
    except Exception as exc:
        kodiutils.log_error("oauth playability check failed: %s" % exc)
        return True
    return bool((response.get("streamingData") or {}).get("dashManifestUrl"))


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
    # A bearer token counts as signed in too. Checked from the stored file
    # rather than oauth.access_token(), which may go to Google to refresh --
    # not something to do while drawing a menu.
    if not auth.signed_in() and not oauth.load().get("access_token"):
        add_dir("Sign in", plot="From your phone or laptop, or from a cookie "
                                "export.", action="signin")
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
        signed_in_by_code = bool(oauth.load().get("access_token")) \
            and not auth.signed_in()
        kodiutils.ok_dialog(
            ("You are signed in with a code, and that sign-in cannot play. "
             "YouTube TV serves a DASH manifest only to its web client, which "
             "refuses this token whatever the request looks like. Sign in "
             "from your phone or laptop as well and playback will work."
             if signed_in_by_code else
             "YouTube did not offer a DASH manifest for this stream, only its "
             "own SABR endpoint, which Kodi cannot play."),
            "Cannot play this")
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


def route_probe_versions():
    """Log what tv.youtube.com serves each client's user agent.

    Run from the diagnostics settings rather than during playback: it fetches
    the page once per identity and writes the answers to the log, and changes
    nothing.
    """
    cookies = None
    try:
        cookies = auth.load()
    except auth.AuthError:
        pass
    try:
        api.probe_client_versions(cookies=cookies)
    except Exception as exc:
        kodiutils.log_error("client version probe failed: %s" % exc)
        kodiutils.notify("Client version probe failed")
        return
    kodiutils.notify("Client versions written to the log")


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
        _import_cookies()
    elif action == "signout":
        if xbmcgui.Dialog().yesno("YouTube TV", "Forget the stored session?"):
            auth.sign_out()
            oauth.forget()
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
    elif action == "probe_versions":
        route_probe_versions()
        return
    elif action in ("iptv_channels", "iptv_epg"):
        # RunPlugin, not a directory: there is no handle to finish and
        # nothing to draw. The answer goes back over IPTV Manager's socket.
        route_iptv(action.split("_", 1)[1], params.get("port", ""))
        return

    route_root()


if __name__ == "__main__":
    main()
