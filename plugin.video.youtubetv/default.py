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
            "Signing in needs a Google API project, and none was found.\n\n"
            "The easiest fix is the YouTube add-on (plugin.video.youtube): "
            "set that up as its own instructions describe, and this add-on "
            "reuses the same project automatically -- nothing to paste here, "
            "and none of its quota is spent, because this add-on never calls "
            "googleapis.com.\n\n"
            "Otherwise, make one at console.cloud.google.com -- enable the "
            "YouTube Data API v3, then an OAuth client ID of type \"TVs and "
            "Limited Input devices\" -- and paste the ID and secret into "
            "this add-on's settings, under Account.",
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

    add_dir("Home", plot="What YouTube TV puts on its own front page: "
                         "resume watching, top picks, and the genre rows.",
            action="home")
    add_dir("Live channels", plot="Every channel in your lineup, playing now.",
            action="channels")
    add_dir("Guide", plot="What is on across the next few hours.",
            action="guide")
    add_dir("Library", plot="Your recordings, purchases and what is "
                            "scheduled to record.", action="library")
    add_dir("Networks", plot="Every network you can watch, and YouTube TV's "
                             "own Sports, Shows, Movies, News and Family "
                             "categories.", action="networks")
    add_dir("Search", action="search")
    # Offered while already signed in, not only before, so a session that
    # has gone wrong can be replaced without signing out first.
    add_dir("Sign in again", plot="Authorise a new code, replacing the "
                                  "stored sign-in.", action="signin")
    add_dir("Sign out", action="signout")
    finish()


def _fetch_stations(client, hours=3, pages=1):
    """The lineup, and as much schedule as ``pages`` requests will reach.

    One request covers the window it asks for and hands back a token for the
    next. The second page of the 2026-08-29 guide carried 748 more airings
    across the same 148 channels -- describing each channel only on the
    first page and sending only a stationId and more airings thereafter --
    so following it takes the same lineup from 953 airings to 1555, a
    median of 10 per channel rather than 6.

    Asking for a longer window instead is not the same thing and is not
    what the web client does: it asks for about six hours and paginates.
    """
    response = client.epg(hours=hours,
                          order=kodiutils.get_setting_int("epg.order", 0))
    stations = epg.parse_epg(response)

    token = epg.continuation_token(response) if pages > 1 else None
    fetched = 1
    while token and fetched < pages:
        try:
            page = client.continuation(token)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log("guide: page %d did not open: %s" % (fetched + 1, exc))
            break
        fetched += 1
        parsed = epg.parse_epg(page)
        added = epg.merge_airings(stations, parsed)
        kodiutils.log("guide: page %d parsed %d row(s) and added %d airing(s)"
                      % (fetched, len(parsed), added))
        if not added:
            # A page that answered and gave nothing is the interesting case:
            # either it holds no airings, or it holds them in a shape this
            # addon cannot read. Only the response says which.
            kodiutils.log("guide page %d shape: %s" % (fetched, epg.describe(page)))
            kodiutils.dump_response("guide-page-shape.json", page)
        following = epg.continuation_token(page)
        if not added or not following or following == token:
            break
        token = following

    _guide_census(stations, response)
    return stations


def _guide_census(stations, response):
    """Say what the guide gave and, precisely, what got dropped.

    route_channels skips any station whose current airing has no video id,
    and route_guide skips any station with no airings, both without a word.
    A lineup that lists eighty channels where the web app shows a hundred
    and fifty looks the same in the log as one that works. These are three
    different faults and they are counted apart:

      * no station parsed at all -- the guide's own shape has changed;
      * a station with no airings -- it parsed, but its row carried no
        programme, so Guide drops it;
      * a station whose airing has no video id -- it has a programme with
        nowhere to play from, so Live channels drops it.

    The response is kept only when nothing at all is playable, or when more
    than half the lineup is being dropped. It is a couple of megabytes, and
    writing it on every guide open would be its own problem.
    """
    airings = sum(len(station.airings) for station in stations)
    empty = [s.name for s in stations if not s.airings]
    silent = [s.name for s in stations
              if s.airings and not (s.now and s.now.video_id)]
    playable = len(stations) - len(empty) - len(silent)
    kodiutils.log("guide: %d station(s), %d airing(s), %d playable now"
                  % (len(stations), airings, playable))

    def some(names):
        shown = ", ".join(names[:20])
        return shown + (" ... and %d more" % (len(names) - 20)
                        if len(names) > 20 else "")

    if empty:
        kodiutils.log("guide: %d station(s) came back with no airings at all, "
                      "so Guide drops them: %s" % (len(empty), some(empty)))
    if silent:
        kodiutils.log("guide: %d station(s) have a programme with no video "
                      "id, so Live channels drops them: %s"
                      % (len(silent), some(silent)))
    # A station with no name and no logo is not dropped -- it lists as its
    # own id -- so the counts above would call that lineup healthy. Counted
    # separately, and when many are nameless the field names the stations
    # actually carry are logged: only 7 of the 148 in the web capture have a
    # "name" at all, the rest being named by the accessibility label on
    # their logo, so a client that files that logo under another key loses
    # the name and the picture together.
    nameless = [s for s in stations if s.name == s.station_id]
    logoless = [s for s in stations if not s.logo]
    if nameless or logoless:
        kodiutils.log("guide: %d station(s) with no name and %d with no logo"
                      % (len(nameless), len(logoless)))
        fields = {}
        for count, renderer in enumerate(epg.walk(response,
                                                  "epgStationRenderer")):
            if count >= 40 or not isinstance(renderer, dict):
                break
            for key in renderer:
                fields[key] = fields.get(key, 0) + 1
        kodiutils.log("guide: station fields (first %d) -- %s"
                      % (min(40, len(stations)),
                         ", ".join("%s x%d" % pair for pair in
                                   sorted(fields.items(), key=lambda p: -p[1]))
                         or "none"))

    # A guide carrying about one airing per station is a guide with no
    # schedule in it: the 2026-08-29 web capture returned 953 airings for
    # these 148 stations over six hours, and this account's own requests
    # return 143. Whatever the difference is, the response is the only thing
    # that can say, so it is kept.
    thin = stations and airings < len(stations) * 2
    if thin:
        kodiutils.log("guide: %d airing(s) for %d station(s) -- about one "
                      "each, so the schedule did not come back"
                      % (airings, len(stations)))
    if (not playable or thin
            or len(empty) + len(silent) > len(stations) / 2
            or len(nameless) > len(stations) / 2):
        kodiutils.log("guide shape: %s" % epg.describe(response))
        kodiutils.dump_response("guide-shape.json", response)


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
        stations = _fetch_stations(client, hours=6, pages=4)
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
        # A programme names its show, so its series can be recorded from
        # here -- which is the useful thing to do with a listing of what is
        # coming up.
        item.addContextMenuItems(_dvr_menu(airing.show_id, airing.title))
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


def _dvr_menu(browse_id, name):
    """Context-menu entries for recording a show, or none without an id.

    Both are offered rather than the one that applies, because nothing in
    the responses says whether a show is already being recorded. The guide's
    airings carry an ``entitiesDvrStatus``, but it holds only the entity id
    -- no state -- so choosing between "record" and "stop" would be a guess,
    and a wrong guess here silently cancels a recording.
    """
    if not browse_id:
        return []
    return [
        ("YouTube TV: record this show",
         "RunPlugin(%s)" % url(action="dvr", on="1",
                               browse_id=browse_id, name=name)),
        ("YouTube TV: stop recording this show",
         "RunPlugin(%s)" % url(action="dvr", on="0",
                               browse_id=browse_id, name=name)),
    ]


def route_dvr(browse_id, name, on):
    """Add a show to the library, or take it out again.

    YouTube TV records a series rather than an airing, so the id here is
    always a show's, never a programme's -- for a guide airing it comes out
    of the side-sheet command, which names both.
    """
    client = _client()
    if not client:
        return
    what = name or browse_id
    try:
        if on:
            said = client.start_dvr(browse_id)
        else:
            said = client.stop_dvr(browse_id)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not change the recording")
        return
    # YouTube TV's own words when it says any, because a refusal comes back
    # as a 200 with a different sentence in it, and "Recording X" printed
    # over the top of that would be a lie the log could not correct.
    kodiutils.notify(said or ("Recording %s" % what if on
                              else "No longer recording %s" % what))


# Renderers that name something other than a show. The DVR records a series
# and takes a show's id, so offering "record this show" on a network or on a
# genre chip offers to record something that is not a programme.
_NOT_A_SHOW = ("unpluggedGridChannelRenderer", "unpluggedChipRenderer",
               "unpluggedIntentChipRenderer")


def _label_of(item):
    """The title, with the airing time in front when the row has one.

    Two scheduled recordings of one show list as the same word twice --
    "Phineas and Ferb", "Phineas and Ferb", an hour apart -- and the start
    time is the only thing that tells them apart in a menu that shows
    labels and not plots. Rows with no start time (episodes, films,
    channels) are left exactly as they are.
    """
    if not item.start_ms:
        return item.title
    when = time.localtime(item.start_ms / 1000.0)
    fmt = "%H:%M" if when[:3] == time.localtime()[:3] else "%a %H:%M"
    return "%s  %s" % (time.strftime(fmt, when), item.title)


def _add_items(items, content="videos"):
    """List parsed items: playable ones resolve, folders browse deeper."""
    for item in items:
        _add_item(item)
    finish(content)


def _dvr_one(browse_id, name, recording):
    """The one DVR entry that applies, where the page says which.

    A tile cannot say whether a title is already in the library, so both
    entries are offered there. A title's own page can -- its header carries
    dvrOn -- and offering both when the answer is known is how a recording
    gets cancelled by somebody aiming for the other one.
    """
    if not browse_id:
        return []
    if recording:
        return [("YouTube TV: remove %s from your library" % name,
                 "RunPlugin(%s)" % url(action="dvr", on="0",
                                       browse_id=browse_id, name=name))]
    return [("YouTube TV: add %s to your library" % name,
             "RunPlugin(%s)" % url(action="dvr", on="1",
                                   browse_id=browse_id, name=name))]


def _add_item(item, plot="", dvr=None):
    """One row, without ending the directory.

    ``plot`` overrides the tile's own line, for a page that carries a real
    synopsis its tiles do not. ``dvr`` is (browse_id, name, recording) where
    the page knows the library state, and adds the one entry that applies.
    """
    listitem = xbmcgui.ListItem(label=_label_of(item))
    listitem.setArt({"thumb": item.art, "fanart": item.art})
    info = listitem.getVideoInfoTag()
    info.setTitle(item.title)
    if plot or item.subtitle:
        info.setPlot(plot or item.subtitle)
    if item.duration:
        info.setDuration(item.duration)
    info.setMediaType("video")
    if item.playable:
        listitem.setProperty("IsPlayable", "true")
        if dvr:
            listitem.addContextMenuItems(_dvr_one(*dvr))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            url(action="play", video_id=item.video_id, label=item.title),
            listitem, isFolder=False)
    elif item.content_type == "MOVIE":
        # A film has one thing to play, so selecting it plays it. Its
        # tile carries no video id -- only a browseEndpoint -- so the
        # id is fetched off its page when it is picked, and the page
        # itself moves to the context menu, which is where YouTube TV's
        # own tile keeps it ("Go to The Accountant").
        listitem.setProperty("IsPlayable", "true")
        info.setMediaType("movie")
        # Not "Go to <title>" any more. A film's page is four tabs --
        # Watch now, About, Lead cast, Suggested -- and this addon reads
        # two of them: the one that plays, which selecting the film now
        # does, and the one that recommends. So the menu offers the half
        # that is left rather than a page whose other half is the button
        # that was just pressed.
        listitem.addContextMenuItems(
            [("Extras and suggested titles",
              "Container.Update(%s)"
              % url(action="browse_suggested", browse_id=item.browse_id,
                    name=item.title, params=item.params))]
            + _dvr_menu(item.browse_id, item.title))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            url(action="play_movie", browse_id=item.browse_id,
                label=item.title),
            listitem, isFolder=False)
    else:
        if item.source not in _NOT_A_SHOW:
            listitem.addContextMenuItems(
                _dvr_menu(item.browse_id, item.title))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            url(action="browse", browse_id=item.browse_id,
                name=item.title, params=item.params),
            listitem, isFolder=True)


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

    _list_search(client, query, response)


def _search_pages(client, response, limit=4):
    """A search and the pages it defers, which is where the films are.

    Not one search result in fourteen read as a film, and the kind was not
    the reason: "blues" answers with Shows, Sports and "On now & upcoming",
    and hands back a continuation carrying "From your library", "On demand"
    and Movies -- where The Blues Brothers is, saying MOVIE plainly. A
    search read one page deep finds no films at all.

    The token goes to search rather than to browse, unlike every other
    continuation here.
    """
    pages = [response]
    token = epg.page_continuation(response)
    while token and len(pages) < limit:
        try:
            page = client.search_continuation(token)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log("search: page %d did not open: %s" % (len(pages) + 1, exc))
            break
        pages.append(page)
        following = epg.page_continuation(page)
        if not following or following == token:
            break
        token = following
    return pages


def _list_search(client, query, response):
    """Search results as the rows YouTube TV grouped them into."""
    pages = _search_pages(client, response)
    rows = _sections_of(pages)
    kodiutils.log("search %r: %d page(s), %d row(s) -- %s"
                  % (query, len(pages), len(rows),
                     ", ".join("%s (%d)" % (r.title, len(r.items))
                               for r in rows) or "nothing"))
    if rows:
        _list_sections(rows, "search_row", extra={"query": query})
        finish()
        return
    # No rows this addon knows: list whatever the pages named, flat.
    items = []
    seen = set()
    for page in pages:
        for item in epg.parse_search(page):
            key = (item.video_id or item.browse_id, item.params)
            if key not in seen:
                seen.add(key)
                items.append(item)
    if not items:
        kodiutils.notify("Nothing found for %s" % query)
    _add_items(items)


def route_search_row(query, name, token=""):
    """One row of a search, in full."""
    client = _client()
    if not client or not query:
        finish()
        return
    try:
        response = client.search(query)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Search failed")
        finish()
        return
    rows = _sections_of(_search_pages(client, response))
    section = next((r for r in rows if r.title == name), None)
    if section is None and token:
        section = epg.Section(name, [], token)
    if section is None:
        kodiutils.notify("%s is no longer in those results"
                         % (name or "That row"))
        finish()
        return
    _add_items(_follow_pages(client, section,
                             fetch=client.search_continuation))


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


def route_browse(browse_id, name, params=""):
    """A show, movie or channel page reached from search or the Browse tab.

    Search answers with shows rather than episodes, so this is where the
    playable things actually live.

    ``params`` is carried because one browseId is not always one page: the
    Browse tab's category chips are all FEunplugged_chips and name which
    category they mean only there.
    """
    client = _client()
    if not client:
        finish()
        return
    # A category is a page of rows, and is the one page here known to defer
    # rows to a second request. Routed by its id rather than by shape,
    # because the shape that would catch it -- "a page with named rows" --
    # also catches a show page, whose seasons are read a different way and
    # whose capture pins that reading.
    if browse_id == api.CHIPS_ID:
        route_category(browse_id, name, params)
        return
    try:
        response = client.browse(browse_id, params or None)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or browse_id))
        finish()
        return

    # A network page is tabs -- LIVE, SERIES, MOVIES, ORIGINALS -- and only
    # the one on screen ships with anything in it. Listing the page flat
    # showed what was on now and nothing else, because the rest of it was
    # seven tokens nobody spent.
    tabs = epg.browse_tabs(response)
    if tabs:
        kodiutils.log("%s: %d tab(s) -- %s"
                      % (name or browse_id, len(tabs),
                         ", ".join("%s (%d)" % (t.title, len(t.items))
                                   for t in tabs)))
        # Every tab the page carried, read or not, and logged even when all
        # of them read. Adult Swim answered the web client with four tabs
        # and this one with two, and the count alone cannot say whether the
        # other two were dropped here or never sent -- which is the
        # difference between a reader to fix and a request to change.
        shapes = epg.tab_shapes(response)
        kodiutils.log("%s: tabs on the page -- %s"
                      % (name or browse_id, "; ".join(shapes)))
        # The page itself is no longer kept when a tab goes unread. That
        # answered the question it was written for -- these tabs defer
        # themselves in a reloadContinuationData, which the line above now
        # prints the path to -- and a title's page drops two tabs every
        # time by design: About and Lead cast name a description and 22
        # people, and neither is somewhere to go.
        # A tab stays a tab while the page has more than one. Live is a
        # kind of its own -- what is on that network now -- and folding it
        # into the page put eleven programmes above four folders on AMC,
        # which reads as a listing with some folders lost in it rather than
        # as a network with a Live tab. A page carrying a single tab has no
        # menu to be: that one is listed in place.
        #
        # A page about one *title* is the other exception, and the header
        # names it: a film or show carries an
        # unpluggedContentDetailsHeaderRenderer where a network carries an
        # unpluggedNetworkPromoHeaderRenderer. Its tabs are not a menu --
        # they are the thing, and then some notes about it -- so Rogue One
        # opens on Rogue One, with Suggested behind it, rather than on two
        # folders. That is what a film reached from search needs: search
        # calls it a SHOW, so it is a folder, and this is the page it opens.
        header = epg.title_header(response)
        if header and tabs[0].items:
            recording = epg.dvr_state(response)
            dvr = (browse_id, name or epg.text(header.get("title")),
                   recording) if recording is not None else None
            plot = epg.page_description(response)
            # The tab can defer shelves of its own. That is where a show
            # keeps its seasons, and where a film's extras would be: the
            # web client's show page hangs "Season 1"..."Season 9" and
            # "Extras" off ten tokens rather than listing them.
            shown = _expand_sections(client, response, list(tabs[0].items))
            for item in shown:
                _add_item(item, plot=plot, dvr=dvr)
            kodiutils.log("%s: %d to play, %s alongside"
                          % (name or browse_id, len(shown),
                             ", ".join("%s (%d)" % (t.title, len(t.items))
                                       for t in tabs[1:]) or "nothing"))
            _list_sections(tabs[1:], "browse_section",
                           extra={"browse_id": browse_id, "params": params})
            finish("movies" if header.get("contentType") == "MOVIE"
                   else "videos")
            return
        if len(tabs) == 1 and tabs[0].items:
            _add_items(tabs[0].items)
            return
        _list_sections(tabs, "browse_section",
                       extra={"browse_id": browse_id, "params": params})
        finish()
        return

    items = _expand_sections(client, response, epg.parse_items(response))
    if not items:
        kodiutils.notify("Nothing playable under %s" % (name or browse_id))
    _add_items(items)


def route_category(browse_id, name, params):
    """One of the Browse tab's categories: Sports, Shows, Movies, News, Family.

    Rows, not a list -- "Picked for you", "On now & upcoming", "Thriller
    movies" -- and it defers more of them to a second request, the way Home
    does. Under them go the page's own genre chips, which are how it
    narrows itself and which name no row of their own.
    """
    client = _client()
    if not client:
        finish()
        return
    first_page, pages, error = _whole_page(
        client, lambda: client.browse(browse_id, params or None))
    if error:
        kodiutils.ok_dialog(error, "Could not open %s" % (name or "that category"))
        finish()
        return

    rows = _sections_of(pages)
    if not rows:
        for page in pages:
            rows.extend(epg.any_rows(page))
    chips = epg.page_chips(first_page)
    kodiutils.log("%s: %d page(s), %d row(s), %d chip(s) -- %s"
                  % (name or browse_id, len(pages), len(rows), len(chips),
                     ", ".join("%s (%d)" % (r.title, len(r.items))
                               for r in rows) or "nothing"))
    if not rows and not chips:
        kodiutils.log("%s shape: %s" % (name or browse_id,
                                        epg.describe(first_page)))
        _add_items(epg.parse_items(first_page))
        return

    _list_sections(rows, "browse_section",
                   extra={"browse_id": browse_id, "params": params})
    # Listed with add_dir rather than through _add_items: a genre is not a
    # show, and the context menu there offers to record one.
    for chip in chips:
        add_dir(chip.title, art=chip.art,
                plot="Narrow %s to %s." % (name or "this category", chip.title),
                action="browse", browse_id=chip.browse_id,
                name="%s: %s" % (name, chip.title) if name else chip.title,
                params=chip.params)
    finish("videos")


def route_browse_section(browse_id, name, params="", token=""):
    """One tab of a network page, or one row of a category.

    The page is asked for again rather than the token being carried in the
    url. That is how Home's and the Library's rows already work, and the
    token is the part most likely to go stale: a tab reopened from a
    bookmarked url an hour later should get this hour's schedule.
    """
    client = _client()
    if not client:
        finish()
        return
    if browse_id == api.CHIPS_ID:
        _first, pages, error = _whole_page(
            client, lambda: client.browse(browse_id, params or None))
        sections = _sections_of(pages)
        if not sections:
            for page in pages:
                sections.extend(epg.any_rows(page))
    else:
        try:
            response = client.browse(browse_id, params or None)
        except (auth.AuthError, api.ApiError) as exc:
            error, sections = str(exc), []
        else:
            error, sections = "", epg.browse_tabs(response)
    if error:
        kodiutils.ok_dialog(error, "Could not open %s" % (name or browse_id))
        finish()
        return

    section = next((s for s in sections if s.title == name), None)
    if section is None and token:
        # The page came back reshuffled and this row is not in it. The token
        # picked up when the row was drawn still names the row, so spend it
        # rather than telling the user a row they can see has gone.
        kodiutils.log("%s: not on the page this time; following its own token"
                      % (name or "that row"))
        section = epg.Section(name, [], token)
    if section is None:
        kodiutils.notify("%s is no longer on this page" % (name or "That row"))
        finish()
        return
    # A tab may name a page of its own rather than a continuation. Opening
    # it is opening that page, so hand it back to route_browse, which knows
    # what to do with rows, tabs and a flat list alike.
    if section.browse_id and not section.items and not section.token:
        route_browse(section.browse_id, name, section.params)
        return
    items = _follow_pages(client, section)
    if not items:
        kodiutils.notify("Nothing playable under %s" % (name or browse_id))
    _add_items(items)


def _follow_pages(client, section, limit=10, fetch=None):
    """Everything behind a row's continuation token, page after page.

    One token covers two cases and they are handled the same way: a filter
    whose selected sort came back empty because YouTube TV deferred it, and a
    row whose first page arrived with more behind it. Stops when a page adds
    nothing new, repeats its own token, or the limit is reached, so a server
    that keeps handing back the same token cannot spin here.
    """
    # A search row's token is spent at search, not at browse, like the
    # search page's own. Every other row here is a browse continuation.
    fetch = fetch or client.continuation
    items = list(section.items)
    seen = {(item.video_id or item.browse_id, item.params, item.start_ms)
            for item in items}
    token = section.token
    page = 0
    while token and page < limit:
        page += 1
        try:
            response = fetch(token)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log("%s: page %d did not open: %s"
                          % (section.title, page, exc))
            break
        added = 0
        # This page can defer shelves the way a show page does -- and show
        # pages are now reached through a tab, not directly: Brewster's
        # Millions answered with "Watch now" and "Suggested" rather than a
        # flat list. Expanding here is what keeps a season list from being
        # ten empty shelves once it sits behind one.
        for item in _expand_sections(client, response,
                                     epg.parse_items(response)):
            key = (item.video_id or item.browse_id, item.params, item.start_ms)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            added += 1
        kodiutils.log("%s: page %d added %d item(s)"
                      % (section.title, page, added))
        if not added:
            # A page that answered and held nothing readable is the
            # interesting case: say what its renderers carry, so the key
            # their endpoint sits under can be read off the log.
            for line in (epg.unreadable_sample(response)
                         or epg.renderer_sample(response, limit=4)):
                kodiutils.log("%s: %s" % (section.title, line))
        following = epg.continuation_token(response)
        if not added or not following or following == token:
            break
        token = following
    return items


def _whole_page(client, fetch, limit=4):
    """A page and the pages of rows it defers, merged into one response list.

    Home arrives four rows at a time: the first response carries "Top picks
    for you", "Resume watching", "Shows" and "Add to membership", and hangs
    the other twenty behind one token on the section list itself. Following
    it is what turns a four-row front page into the twenty-four rows the web
    client shows.
    """
    try:
        response = fetch()
    except (auth.AuthError, api.ApiError) as exc:
        return None, [], str(exc)

    pages = [response]
    token = epg.page_continuation(response)
    while token and len(pages) < limit:
        try:
            page = client.continuation(token)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log("page %d did not open: %s" % (len(pages) + 1, exc))
            break
        pages.append(page)
        following = epg.page_continuation(page)
        if not following or following == token:
            break
        token = following
    return response, pages, ""


def _sections_of(pages):
    """Every named row across the pages, first occurrence winning."""
    sections = []
    seen = set()
    for page in pages:
        for section in epg.page_shelves(page):
            if section.title in seen:
                continue
            seen.add(section.title)
            sections.append(section)
    return sections


def _list_sections(sections, action, extra=None):
    """Rows as folders, each carrying its own token as well as its name.

    The name alone is not enough to find a row again. A category is not the
    same page twice: two requests for Movies a moment apart came back with
    "Drama movies" and then "Drama thriller movies", and with 18 and then 17
    fantasy comedies (2026-08-29 23:49). Reopening a row by name means
    reopening a page that has since been reshuffled, and the row that was
    selected may simply not be in it.
    """
    for section in sections:
        # A network's tabs arrive empty with a token each, so "0 item(s)"
        # would be wrong about all but the one that is on the air.
        plot = ("%d item(s)" % len(section.items) if section.items
                else "Fetched when you open it.")
        add_dir(section.title,
                art=section.items[0].art if section.items else "",
                plot=plot, action=action, name=section.title,
                token=section.token, **(extra or {}))
    return bool(sections)


def route_home():
    """The front page: resume watching, top picks, and the genre rows.

    Every row becomes a folder rather than being flattened: Home holds five
    hundred titles across two dozen rows, and one list of five hundred is not
    a menu.
    """
    client = _client()
    if not client:
        finish()
        return
    first_page, pages, error = _whole_page(client, client.home)
    if error:
        kodiutils.ok_dialog(error, "Could not open the front page")
        finish()
        return

    sections = _sections_of(pages)
    kodiutils.log("home: %d page(s), %d row(s) -- %s"
                  % (len(pages), len(sections),
                     ", ".join("%s (%d)" % (s.title, len(s.items))
                               for s in sections) or "nothing"))
    if sections:
        _list_sections(sections, "home_row")
        finish()
        return

    # No known container. Look for named rows by shape before giving up,
    # and keep the response either way: the shape having changed is the
    # thing worth knowing.
    kodiutils.log("home shape: %s" % epg.describe(first_page))
    kodiutils.dump_response("home-shape.json", first_page)
    rows = []
    for page in pages:
        rows.extend(epg.any_rows(page))
    if rows:
        kodiutils.log("home: %d row(s) by shape" % len(rows))
        _list_sections(rows, "home_row")
        finish()
        return
    kodiutils.log("home: nothing recognised; listing the page flat")
    _add_items(epg.parse_items(first_page))


def route_home_row(name, token=""):
    """One row of the front page, in full."""
    client = _client()
    if not client:
        finish()
        return
    _first, pages, error = _whole_page(client, client.home)
    if error:
        kodiutils.ok_dialog(error, "Could not open the front page")
        finish()
        return
    sections = _sections_of(pages)
    if not sections:
        for page in pages:
            sections.extend(epg.any_rows(page))
    section = next((s for s in sections if s.title == name), None)
    if section is None and token:
        section = epg.Section(name, [], token)
    if section is None:
        kodiutils.notify("%s is no longer on the front page"
                         % (name or "That row"))
        finish()
        return
    _add_items(_follow_pages(client, section))


def route_networks():
    """YouTube TV's Browse tab: the networks, under its category chips.

    Listed as rows rather than flattened, for the reason Home is: the tab
    holds 256 networks and five categories, and one list of 261 things is
    not a menu.
    """
    client = _client()
    if not client:
        finish()
        return
    first_page, pages, error = _whole_page(client, client.networks)
    if error:
        kodiutils.ok_dialog(error, "Could not open the networks")
        finish()
        return

    sections = _networks_sections(pages)
    kodiutils.log("networks: %d page(s), %d row(s) -- %s"
                  % (len(pages), len(sections),
                     ", ".join("%s (%d)" % (s.title, len(s.items))
                               for s in sections) or "nothing"))
    if sections:
        _list_sections(sections, "networks_row")
        finish()
        return

    # The capture is the web client's. Should the TV client answer this tab
    # in a container of its own -- as it does the Library -- keep the
    # response: the shape is the only thing that says what to write next.
    kodiutils.log("networks shape: %s" % epg.describe(first_page))
    kodiutils.dump_response("networks-shape.json", first_page)
    kodiutils.log("networks: nothing recognised; listing the tab flat")
    _add_items(epg.parse_items(first_page))


def _networks_sections(pages):
    """The tab's named rows, by container first and by shape second."""
    sections = _sections_of(pages)
    if sections:
        return sections
    rows = []
    for page in pages:
        rows.extend(epg.any_rows(page))
    return rows


def route_networks_row(name, token=""):
    """One row of the Browse tab, in full."""
    client = _client()
    if not client:
        finish()
        return
    _first, pages, error = _whole_page(client, client.networks)
    if error:
        kodiutils.ok_dialog(error, "Could not open the networks")
        finish()
        return
    section = next((s for s in _networks_sections(pages) if s.title == name),
                   None)
    if section is None and token:
        section = epg.Section(name, [], token)
    if section is None:
        kodiutils.notify("%s is no longer on the Browse tab"
                         % (name or "That row"))
        finish()
        return
    _add_items(_follow_pages(client, section))


def _library_sections(response):
    """(rows, filters, recognised) for a library response.

    The grid readers were written from web-client captures. The TV client
    answers with something else -- 0 rows and 0 filters on a real account --
    so when neither matches, fall back to finding named rows by shape. That
    reader needs no container name, and on the web captures it recovers the
    same rows plus the grid's own "Recordings & purchases".
    """
    rows, filters = epg.parse_library(response)
    if rows or filters:
        return rows, filters, True
    return epg.any_rows(response), [], False


def route_library():
    """Recordings, purchases and scheduled recordings.

    The page answers with its curated rows -- "New in your library", "Most
    watched", "Scheduled recordings" -- and a grid filtered All / Shows /
    Movies / Sports / Events / Purchased. "All" is the library itself, so its
    contents are listed here rather than hidden one click down; everything
    else becomes a folder. Filters the account has nothing under never
    appear: library_filters drops the empty-state cards.
    """
    client = _client()
    if not client:
        finish()
        return
    try:
        response = client.library()
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open your library")
        finish()
        return

    shelves, filters, known = _library_sections(response)
    kodiutils.log("library: %d row(s) and %d filter(s)%s -- %s"
                  % (len(shelves), len(filters),
                     "" if known else " (by shape: no known container)",
                     ", ".join("%s (%d)" % (s.title, len(s.items))
                               for s in shelves + filters) or "nothing"))
    if not known:
        kodiutils.log("library shape: %s" % epg.describe(response))
        kodiutils.dump_response("library-shape.json", response)

    # Any empty tab is worth explaining, not only an entirely empty page:
    # the first time this ran, Scheduled's single item was enough to keep
    # the whole diagnostic quiet while eight tabs listed nothing.
    if any(not section.items for section in filters):
        for line in epg.unreadable_sample(response):
            kodiutils.log("library: %s" % line)
        for line in epg.renderer_sample(response):
            kodiutils.log("library: %s" % line)
        kodiutils.dump_response("library-shape.json", response)

    _list_sections(shelves, "library_section")
    _list_sections(filters[1:], "library_section")

    if filters:
        _add_items(_follow_pages(client, filters[0]))
        return
    if shelves:
        finish("videos")
        return
    # Nothing named a row. List what the page names anyway; the shape log and
    # the dump above say what arrived instead.
    kodiutils.log("library: nothing recognised; listing the page flat")
    items = epg.parse_items(response)
    if not items:
        kodiutils.notify("Nothing in your library yet")
    _add_items(items)


def route_library_section(name, token=""):
    """One row or one filter of the library, in full."""
    client = _client()
    if not client:
        finish()
        return
    try:
        response = client.library()
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open your library")
        finish()
        return

    shelves, filters, _known = _library_sections(response)
    section = next((s for s in shelves + filters if s.title == name), None)
    if section is None and token:
        section = epg.Section(name, [], token)
    if section is None:
        kodiutils.notify("%s is no longer in your library"
                         % (name or "That row"))
        finish()
        return
    _add_items(_follow_pages(client, section))


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


def route_play_movie(browse_id, label):
    """Play a film, whose tile names its page rather than its stream.

    Nothing in a movie tile carries a video id: the navigationEndpoint is a
    browseEndpoint and the menu offers "Go to <title>". The id lives on that
    page, so it is fetched here, on selection, rather than a page being put
    in front of somebody who picked a film to watch it.

    Which of the page's videos is the film is read from the page's order:
    the first playable thing on it. That order is the right one, and the
    second entry is not a trailer -- it is your own recording. John Wick 3
    answered with the film and with "AMC • R • 4w ago", an airing recorded
    a month earlier, in that order. The film is the copy without the ad
    breaks, so first is also best. Every candidate is logged anyway, since
    one page is one page.
    """
    client = _client()
    if not client:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        response = client.browse(browse_id)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (label or browse_id))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    # A tabbed page keeps what it plays in its own first tab; an untabbed
    # one carries it directly. Either way the film is the first playable
    # thing the page names.
    tabs = epg.browse_tabs(response)
    found = []
    for tab in tabs:
        found = [item for item in tab.items if item.playable]
        if found:
            break
    if not found:
        found = [item for item in epg.parse_items(response) if item.playable]
    kodiutils.log("%s: %d playable on the page -- %s"
                  % (label or browse_id, len(found),
                     ", ".join("%s (%s)" % (item.title, item.video_id)
                               for item in found[:6]) or "nothing"))
    if not found:
        kodiutils.ok_dialog(
            "YouTube TV lists %s but names nothing to play on its page. It "
            "may need to be bought or added to your library first."
            % (label or "this film"), "Nothing to play")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    route_play(found[0].video_id, label or found[0].title)


def route_browse_suggested(browse_id, name, params=""):
    """Everything on a title's page except the thing that plays.

    Reached from the context menu on a film, where it replaced "Go to
    <title>". Rogue One's page is Watch now, About, Lead cast and Suggested,
    of which this addon reads the first and the last, and selecting the film
    already does what the first is for.

    Named for extras as well as suggestions because a title that has them
    should keep them here. Nothing in any capture names a trailer or a bonus
    feature -- neither the search of 2026-08-30 01:00 nor Rogue One's own
    page holds the word -- so what such a title answers with is not
    established. Every tab is logged, so the first title that has any says
    where they live.
    """
    client = _client()
    if not client:
        finish()
        return
    try:
        response = client.browse(browse_id, params or None)
    except (auth.AuthError, api.ApiError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or browse_id))
        finish()
        return

    tabs = epg.browse_tabs(response)
    rest = tabs[1:]
    kodiutils.log("%s: beside the one that plays -- %s"
                  % (name or browse_id,
                     ", ".join("%s (%d)" % (t.title, len(t.items))
                               for t in rest) or "nothing"))
    if not rest:
        kodiutils.notify("%s has nothing else on its page"
                         % (name or "That title"))
        finish()
        return
    if len(rest) == 1:
        _add_items(_follow_pages(client, rest[0]))
        return
    _list_sections(rest, "browse_section",
                   extra={"browse_id": browse_id, "params": params})
    finish()


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
    elif action == "home":
        route_home()
        return
    elif action == "home_row":
        route_home_row(params.get("name", ""), params.get("token", ""))
        return
    elif action == "library":
        route_library()
        return
    elif action == "library_section":
        route_library_section(params.get("name", ""),
                              params.get("token", ""))
        return
    elif action == "search":
        route_search()
        return
    elif action == "search_row":
        route_search_row(params.get("query", ""), params.get("name", ""),
                         params.get("token", ""))
        return
    elif action == "networks":
        route_networks()
        return
    elif action == "networks_row":
        route_networks_row(params.get("name", ""), params.get("token", ""))
        return
    elif action == "browse":
        route_browse(params.get("browse_id", ""), params.get("name", ""),
                     params.get("params", ""))
        return
    elif action == "browse_suggested":
        route_browse_suggested(params.get("browse_id", ""),
                               params.get("name", ""),
                               params.get("params", ""))
        return
    elif action == "browse_section":
        route_browse_section(params.get("browse_id", ""),
                             params.get("name", ""), params.get("params", ""),
                             params.get("token", ""))
        return
    elif action == "dvr":
        # RunPlugin, not a directory: nothing to draw, and the listing the
        # menu was opened from stays where it is.
        route_dvr(params.get("browse_id", ""), params.get("name", ""),
                  params.get("on") == "1")
        return
    elif action == "play":
        route_play(params.get("video_id", ""), params.get("label", ""))
        return
    elif action == "play_movie":
        route_play_movie(params.get("browse_id", ""), params.get("label", ""))
        return
    elif action == "play_channel":
        route_play_channel(params.get("station_id", ""))
        return
    elif action in ("iptv_channels", "iptv_epg"):
        # RunPlugin, not a directory: there is no handle to finish and
        # nothing to draw. The answer goes back over IPTV Manager's socket.
        route_iptv(action.split("_", 1)[1], params.get("port", ""))
        return

    route_root()


if __name__ == "__main__":
    main()
