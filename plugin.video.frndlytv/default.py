"""Friendly TV for Kodi: menus, listings and playback."""

import sys
import time

from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin

from lib import api, auth, kodiutils, parse, playback

HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else -1
BASE_URL = sys.argv[0] if sys.argv else "plugin://plugin.video.frndlytv/"

# How much schedule a channel's own listing shows. The guide endpoint takes a
# window in milliseconds and the web player asks a day at a time.
GUIDE_HOURS = 24
# The guide is asked for in pages of twelve channels, which is the batch size
# the web player uses; a request naming the whole lineup at once is a shape
# nothing has been observed answering.
GUIDE_BATCH = 12
# Results per search request. Sixteen is what the web player asks for, and
# the response's hasMore/totalCount drive the "Next page" entry from there.
SEARCH_PAGE = 16
# The buckets the search endpoint takes. "All" is the unfiltered search; the
# other three are the type filters the web player offers, named as it names
# them in the request rather than as it labels them on screen.
SEARCH_ALL = "All"
SEARCH_BUCKETS = (("Shows", "Series"), ("Movies", "Movie"),
                  ("Channels", "Station"))


def url(**kwargs):
    return BASE_URL + "?" + urlencode({k: v for k, v in kwargs.items()
                                       if v not in (None, "")})


def add_dir(label, target, art=None, plot=""):
    item = xbmcgui.ListItem(label=label)
    item.setArt(art or {})
    if plot:
        _plot(item, plot)
    xbmcplugin.addDirectoryItem(HANDLE, target, item, isFolder=True)


def _plot(item, text):
    """Set the plot, across the two metadata APIs.

    Kodi 20 deprecated ListItem.setInfo in favour of the InfoTagVideo
    getters; the old call still works but logs a warning on every item, which
    on a 200-row listing is 200 lines of log per screen.
    """
    try:
        item.getVideoInfoTag().setPlot(text)
    except (AttributeError, TypeError):
        item.setInfo("video", {"plot": text})


def finish(content=""):
    if content:
        xbmcplugin.setContent(HANDLE, content)
    # The service orders its own rows deliberately -- "On Now" first, a
    # channel's schedule in time order -- so nothing is re-sorted here.
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


def _client(quiet=False):
    """An Api bound to the stored session, or None once the user is told why."""
    session = auth.Session()
    if not session.signed_in:
        email = kodiutils.get_setting("username")
        password = kodiutils.get_setting("password")
        if not email or not password:
            if not quiet:
                kodiutils.ok_dialog(
                    "Add your Friendly TV email address and password in this "
                    "addon's settings, then come back.", "Not signed in")
            return None
        try:
            session.sign_in(email, password)
        except auth.AuthError as exc:
            if not quiet:
                kodiutils.ok_dialog(str(exc), "Could not sign in")
            return None
    return api.Api(session)


# -- routes ---------------------------------------------------------------


def route_root():
    kodiutils.log("root menu; %s" % kodiutils.platform())
    client = _client(quiet=True)

    add_dir("Live TV", url(action="live"),
            plot="Every channel in your lineup, with what is on right now.")
    add_dir("TV Guide", url(action="guide"),
            plot="Channels and their schedule for the next day.")
    add_dir("Search", url(action="search"),
            plot="Search Friendly TV's catalogue of shows and films.")

    if client:
        for entry in client.menus():
            # Guide has a richer listing of its own above; the service's own
            # "guide" page is a duplicate of it.
            if entry["path"] == "guide":
                continue
            # Home is assembled from two endpoints and has its own route.
            if entry["path"] == "home":
                add_dir(entry["title"], url(action="home"))
                continue
            add_dir(entry["title"], url(action="page", path=entry["path"]))

    if client and client.session.email:
        add_dir("Sign out (%s)" % client.session.email,
                url(action="signout"))
    else:
        add_dir("Sign in", url(action="signin"))
    finish()


def route_signin():
    email = kodiutils.get_setting("username") or ""
    email = kodiutils.input_text("Friendly TV email address", default=email)
    if not email:
        return
    password = kodiutils.input_text("Password", hidden=True)
    if not password:
        return
    session = auth.Session()
    try:
        session.sign_in(email, password)
    except auth.AuthError as exc:
        kodiutils.ok_dialog(str(exc), "Could not sign in")
        return
    kodiutils.set_setting("username", email)
    kodiutils.set_setting("password", password)
    kodiutils.notify("Signed in as %s" % session.email)
    _refresh()


def route_signout():
    if not kodiutils.yesno("Sign out of Friendly TV on this device?"):
        return
    auth.Session().clear()
    kodiutils.set_setting("password", "")
    kodiutils.delete_file(api.CONFIG_FILE)
    kodiutils.notify("Signed out")
    _refresh()


def _refresh():
    xbmc.executebuiltin("Container.Refresh")


def route_live():
    """Every live channel, each showing what is on it now."""
    client = _client()
    if client is None:
        return finish()
    try:
        cards = [parse.card(c, client) for c in client.live_channels()]
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not read the channel list")
        return finish()

    kodiutils.log("live: %d channel(s)" % len(cards))
    for item in cards:
        if not item["path"]:
            continue
        _add_playable(item, label=_live_label(item))
    finish("videos")


def _live_label(item):
    """The channel's name in front, because that is what is being chosen.

    These cards are titled with the *programme* on the air, which makes an
    alphabetical channel list read as a random one: "Perry Mason" where the
    viewer is looking for MeTV.
    """
    channel = item["channel_name"]
    if channel and item["title"] and channel != item["title"]:
        return "%s - %s" % (channel, item["title"])
    return channel or item["title"]


def route_guide():
    """Channels as folders, each listing its own schedule."""
    client = _client()
    if client is None:
        return finish()
    try:
        channels = client.guide_channels()
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not read the guide")
        return finish()

    kodiutils.log("guide: %d channel(s)" % len(channels))
    for channel in channels:
        display = channel.get("display") or {}
        name = display.get("title") or display.get("subtitle1") or ""
        if not name or channel.get("id") is None:
            continue
        logo = client.image(display.get("imageUrl"))
        add_dir(name, url(action="guide_channel", channel_id=channel["id"],
                          name=name),
                art={"icon": logo, "thumb": logo})
    finish()


def route_guide_channel(channel_id, name):
    """One channel's schedule for the next day."""
    client = _client()
    if client is None:
        return finish()
    now = int(time.time() * 1000)
    try:
        data = client.guide([channel_id], now, now + GUIDE_HOURS * 3600 * 1000)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not read the schedule")
        return finish()

    programmes = []
    for row in data:
        for raw in (row.get("programs") or []):
            programmes.append(parse.programme(raw))
    programmes.sort(key=lambda p: p["start_ms"])
    kodiutils.log("guide %s: %d airing(s)" % (name, len(programmes)))

    on_air = next((p for p in programmes
                   if p["start_ms"] <= now < p["end_ms"]), None)
    live_path = _live_path_for(client, channel_id, on_air)
    for prog in programmes:
        if not prog["end_ms"] or prog["end_ms"] < now:
            continue
        label = "%s  %s" % (_clock(prog["start_ms"]), prog["title"])
        item = xbmcgui.ListItem(label=label)
        _plot(item, "%s - %s on %s" % (_clock(prog["start_ms"]),
                                       _clock(prog["end_ms"]), name))
        item.addContextMenuItems(_recording_menu(prog["path"]))
        if live_path and prog["start_ms"] <= now < prog["end_ms"]:
            # Only the airing on the air can be played, and playing it means
            # joining the channel live. The rest of the schedule is
            # information: this addon has no catch-up route.
            item.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(
                HANDLE, url(action="play", path=live_path, label=label),
                item, isFolder=False)
        else:
            xbmcplugin.addDirectoryItem(HANDLE, "", item, isFolder=False)
    finish("videos")


def _live_path_for(client, channel_id, on_air=None):
    """The ``channel/live/<slug>`` path for a guide channel id.

    The guide's own channel rows carry ``channel//`` -- an empty path -- so
    the slug has to come from somewhere else. Two routes, cheapest first:

    1. The Live Now listing, which names both the slug and the network id the
       guide keys on. One request covers the whole lineup.
    2. Failing that, the guide overlay for the programme on the air, which
       names the channel it is on. One request per channel, so it is only
       reached for a channel Live Now did not carry.
    """
    try:
        for channel in client.lineup():
            if channel["id"] == str(channel_id) and channel["path"]:
                return channel["path"]
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.log("Live Now did not resolve channel %s: %s"
                      % (channel_id, exc))

    if on_air and on_air.get("path"):
        try:
            found = client.watch_live_path(on_air["path"])
            if found:
                kodiutils.log("resolved channel %s through the guide overlay"
                              % channel_id)
                return found
        except (api.ApiError, auth.AuthError) as exc:
            kodiutils.log("the overlay did not resolve channel %s: %s"
                          % (channel_id, exc))
    return ""


def _clock(milliseconds):
    if not milliseconds:
        return ""
    return time.strftime("%H:%M", time.localtime(milliseconds / 1000.0))


def route_search(query="", bucket=SEARCH_ALL):
    """Friendly TV's own catalogue search.

    This runs on a different API surface from the rest of the addon
    (``/search/api/tivo/v1`` rather than ``/service/api/v1``) but on the same
    host, with the same session headers, and it answers with ordinary cards --
    so results list exactly like any other row and lead to the same pages.
    """
    client = _client()
    if client is None:
        return finish()
    if not query:
        query = kodiutils.input_text("Search Friendly TV") or ""
    query = query.strip()
    if not query:
        return finish()

    try:
        found = client.search_all(query, bucket=bucket, limit=SEARCH_PAGE)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not search")
        return finish()

    cards = [parse.card(c, client) for c in found["cards"]]
    kodiutils.log("search %r [%s]: %d of %s result(s) in %d request(s)%s"
                  % (query, bucket, len(cards), found["total"], found["pages"],
                     "" if found["complete"] else " (stopped at the cap)"))

    # The type filters go at the top, where they narrow what is already on
    # screen rather than being a question asked before anything is shown.
    if bucket == SEARCH_ALL:
        for label, code in SEARCH_BUCKETS:
            add_dir("%s only" % label,
                    url(action="search", query=query, bucket=code),
                    plot='%s matching "%s"' % (label, query))

    content = _add_cards(cards, client)

    if not cards:
        kodiutils.notify('Nothing matching "%s"' % query)
    elif not found["complete"]:
        # Say it rather than let a truncated list look complete.
        kodiutils.notify("Showing the first %d of %s" % (len(cards),
                                                         found["total"]))
    finish(content)


RECORD_FORM = "recording_form"
STOP_FORM = "stop_recording_form"


def _recording_menu(programme_path):
    """Context-menu entries for a guide airing, or none without a path.

    Two entries rather than one, because which of them applies depends on
    whether the programme is already being recorded, and asking the service
    that costs a request the menu would have to make before it could be drawn.
    Each entry fetches its own form when chosen.
    """
    if not programme_path:
        return []
    return [
        ("Record...", "RunPlugin(%s)"
         % url(action="record", path=programme_path, form=RECORD_FORM)),
        ("Stop or delete recording...", "RunPlugin(%s)"
         % url(action="record", path=programme_path, form=STOP_FORM)),
    ]


def route_record(programme_path, form_code):
    """Ask the service what it can do with this airing, then do the chosen one.

    Nothing about the instruction string is built here: the form's options
    arrive with an opaque ``value`` each and the chosen one is echoed back
    verbatim. That is why this works for recording an episode, recording a
    series, stopping either and deleting a series without the addon knowing
    what distinguishes them.
    """
    client = _client()
    if client is None:
        return
    try:
        form = client.form(form_code, programme_path)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Recording")
        return

    options = parse.form_options(form)
    if not options:
        kodiutils.notify("No recording options for this programme")
        return

    if len(options) == 1:
        chosen = options[0]
    else:
        index = xbmcgui.Dialog().select(
            "Recording", [o["label"] for o in options])
        if index < 0:
            return
        chosen = options[index]

    try:
        said = client.submit_form(form_code, programme_path, chosen["value"])
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Recording")
        return
    kodiutils.log("recording: %s -> %s" % (chosen["code"], said or "no message"))
    # The service phrases its own confirmation ("Added to My Stuff"), which is
    # better than anything invented here.
    kodiutils.notify(said or chosen["label"])
    _refresh()


def route_home():
    """Home, which is the one screen assembled from two endpoints.

    ``page/content?path=home`` carries the banners and the Live Now row and
    nothing else -- listing it the way every other page is listed gives a Home
    with a single row of live channels on it, which is exactly what the first
    build did. The rows a viewer expects ("Continue Watching", "Recommended
    for You", "Just Added Movies" ...) come from the TiVo carousel endpoint
    instead, paged behind a cursor.
    """
    client = _client()
    if client is None:
        return finish()
    try:
        rows = client.home_rows()
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open Home")
        return finish()
    if not rows:
        kodiutils.notify("Home came back empty")
        return finish()
    for row in rows:
        add_dir(row["name"] or row["code"],
                url(action="home_row", code=row["code"], name=row["name"]),
                plot="%d item(s)" % len(row["cards"]))
    finish()


def route_home_row(code, name):
    """One row of Home, in full.

    Home is fetched again rather than carried through the url: a row of thirty
    cards does not fit in a plugin path.
    """
    client = _client()
    if client is None:
        return finish()
    try:
        rows = client.home_rows()
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or code))
        return finish()
    for row in rows:
        if row["code"] == code:
            kodiutils.log("home row %r: %d card(s)" % (code, len(row["cards"])))
            return finish(_add_cards(row["cards"], client))
    kodiutils.notify("That row is no longer there")
    finish("videos")


def route_page(path, name=""):
    """A page of the service's own hierarchy: Movies, TV, My Stuff."""
    client = _client()
    if client is None:
        return finish()
    try:
        response = client.page(path)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or path))
        return finish()

    sections = parse.sections(response, client)
    detail = parse.detail(response, client)
    kodiutils.log("page %s: %d section(s), %d action(s)"
                  % (path, len(sections), len(detail["actions"])))

    media = parse.media_of(path)

    # A film or series page is not made of sections: what plays it is a button
    # in the page's content pane. Reading only sections left a film's page
    # completely empty, since a film has no seasons under it either.
    for act in detail["actions"]:
        _add_detail_action(act, detail, media)

    # A page that is one section is that section: making the viewer open a
    # single folder to reach the only thing behind it is a wasted click. Not
    # when there are actions above it, which the flattening would bury.
    if not detail["actions"] and len(sections) == 1 and sections[0]["cards"]:
        return finish(_add_cards(sections[0]["cards"], client))

    # On a title's page the synopsis and cast belong on every row, because
    # Kodi shows the highlighted row's information -- a season folder with
    # nothing on it is what makes a show look like it has no description.
    describe = detail if detail["plot"] or detail["cast"] else None
    for section in sections:
        if section["cards"]:
            _add_section_dir(section["name"] or path, path, section["code"],
                             section["name"], "section_cached", describe)
        elif section["code"]:
            _add_section_dir(section["name"] or section["code"], path,
                             section["code"], section["name"], "section",
                             describe)
    if not sections and not detail["actions"]:
        kodiutils.notify("Nothing here")
    finish("seasons" if describe and media == "tvshow" else "")


def _add_section_dir(label, path, code, name, action, detail):
    """A section folder, carrying the page's own description where there is one."""
    item = xbmcgui.ListItem(label=label)
    if detail:
        item.setArt(_detail_art(detail))
        _set_detail_meta(item, detail, label, "video")
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action=action, path=path, code=code, name=name),
        item, isFolder=True)


def _detail_art(detail):
    art = {}
    if detail["poster"]:
        art["thumb"] = art["poster"] = detail["poster"]
    if detail["fanart"]:
        art["fanart"] = detail["fanart"]
    return art


def _detail_plot(detail):
    """The page's synopsis, with what is on now and when, underneath it."""
    parts = [detail["plot"]]
    for extra in (detail["now"], detail["airing"], detail["expires"]):
        if extra and extra not in parts:
            parts.append(extra)
    return "\n\n".join(p for p in parts if p)


def _set_detail_meta(item, detail, label, media):
    """The synopsis, cast, director, year and certificate onto one row.

    None of this is on the card in a listing -- across every captured
    response, `description` and `Director` are empty on all 8191 cards and
    `cast` on all but 160. It exists only on the title's own page, so it is
    read there and put on every row of that page, which is where Kodi looks
    when a row is highlighted.
    """
    plot = _detail_plot(detail)
    try:
        tag = item.getVideoInfoTag()
        tag.setMediaType(media)
        tag.setTitle(label)
        if plot:
            tag.setPlot(plot)
        if detail["cast"]:
            tag.setCast([xbmc.Actor(name) for name in detail["cast"]])
        if detail["directors"]:
            tag.setDirectors(detail["directors"])
        if detail["year"]:
            tag.setYear(detail["year"])
        if detail["rating"]:
            tag.setMpaa(detail["rating"])
    except (AttributeError, TypeError):
        info = {"title": label, "mediatype": media}
        if plot:
            info["plot"] = plot
        if detail["cast"]:
            info["cast"] = detail["cast"]
        if detail["directors"]:
            info["director"] = ", ".join(detail["directors"])
        if detail["year"]:
            info["year"] = detail["year"]
        if detail["rating"]:
            info["mpaa"] = detail["rating"]
        item.setInfo("video", info)


def _add_detail_action(action, detail, media="video"):
    """A play button from a details page, with the page's own art and blurb."""
    label = action["label"] or detail["title"] or "Play"
    if detail["title"] and detail["title"].lower() not in label.lower():
        label = "%s - %s" % (label, detail["title"])
    item = xbmcgui.ListItem(label=label)
    item.setArt(_detail_art(detail))
    _set_detail_meta(item, detail, label, media)
    item.setProperty("IsPlayable", "true")
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action="play", path=action["path"], label=label),
        item, isFolder=False)


def route_section(path, code, name):
    """A section the page described but did not fill in."""
    client = _client()
    if client is None:
        return finish()
    try:
        response = client.section(path, code)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or code))
        return finish()
    cards = []
    for section in parse.sections(response, client):
        cards.extend(section["cards"])
    if not cards:
        # section/data answers in the page shape when it has rows to group
        # and in a bare {data: [...]} when it does not.
        cards = [parse.card(c, client)
                 for c in (response.get("data") or [])
                 if isinstance(c, dict) and c.get("display")]
    kodiutils.log("section %s/%s: %d card(s)" % (path, code, len(cards)))
    finish(_add_cards(cards, client))


def route_section_cached(path, code, name):
    """A section the page already sent the cards for.

    Fetched again rather than carried through the url: a listing's worth of
    cards does not fit in a plugin path, and the page is cheap.
    """
    client = _client()
    if client is None:
        return finish()
    try:
        response = client.page(path)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or code))
        return finish()
    for section in parse.sections(response, client):
        if section["code"] == code:
            return finish(_add_cards(section["cards"], client))
    kodiutils.notify("That row is no longer there")
    finish("videos")


def _plays_through_its_page(item):
    """True for a card whose page exists only to hold one play button.

    A film's details page has a play button and nothing else -- no seasons, no
    episodes -- so listing it as a folder makes the viewer open a directory to
    find a single item. It is a playable thing wearing a page's clothes.

    A series page is a real folder: it has a season under it per pane, and its
    play button joins the channel currently airing the show, which is not what
    picking the series off a row means.
    """
    return item["path"].startswith("movies/")


def _describe(cards, client):
    """Fill in each card's synopsis and cast from its own page.

    Kodi's Information dialog reads the list item, and a card carries no
    synopsis, cast or director -- those are only on the title's page. So
    without this, Information on a film is an empty box. It costs one request
    per row, run on a pool, and is a setting because that cost is real.
    """
    if not kodiutils.get_setting_bool("full_info", True):
        return
    wanted = [c["path"] for c in cards
              if c["path"] and parse.media_of(c["path"]) in ("movie", "tvshow")]
    if not wanted:
        return
    try:
        pages = client.details(wanted,
                               limit=kodiutils.get_setting_int("info_limit", 40))
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.log("could not read details for this listing: %s" % exc)
        return
    for card in cards:
        response = pages.get(card["path"])
        if response:
            card["detail"] = parse.detail(response, client)


def _add_cards(cards, client=None):
    """List parsed cards, and say what kind of listing they made.

    Returned so the caller can hand it to finish(): a listing of shows has to
    declare itself as shows for a skin to lay it out as shows.
    """
    if client is not None:
        _describe(cards, client)
    for item in cards:
        if not item["path"]:
            continue
        if item["playable"]:
            _add_playable(item)
        elif _plays_through_its_page(item):
            _add_playable(item, action="play_page")
        else:
            _add_card_folder(item)
    return _content_of(cards)


def _add_card_folder(item):
    """A card that opens a page -- a show, mostly -- with its own metadata.

    A show is a folder because it has seasons under it, but it is still a
    show: without the mediatype it lists as an unnamed directory and a skin
    has nothing to draw a poster shelf from.
    """
    label = item["title"] or item["path"]
    listitem = xbmcgui.ListItem(label=label)
    listitem.setArt(_art(item))
    _set_meta(listitem, item, label)
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action="page", path=item["path"], name=item["title"]),
        listitem, isFolder=True)


def _add_playable(item, label=None, action="play"):
    label = label or item["title"] or item["path"]
    listitem = xbmcgui.ListItem(label=label)
    listitem.setArt(_art(item))
    listitem.setProperty("IsPlayable", "true")
    _set_meta(listitem, item, label)
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action=action, path=item["path"], label=label),
        listitem, isFolder=False)


def _art(item):
    art = {}
    if item.get("poster"):
        art["thumb"] = art["poster"] = art["fanart"] = item["poster"]
    if item.get("channel_logo"):
        art["icon"] = item["channel_logo"]
        art.setdefault("thumb", item["channel_logo"])
    return art


def _plot_text(item):
    """What a card says about itself, as one block of text.

    The service spreads this across subtitles that hold different things on
    different pages -- an episode number and airtime on a live card, a
    synopsis on a film -- so they are joined rather than assigned to fields
    that would be wrong half the time.
    """
    parts = []
    for key in ("subtitle", "description"):
        value = item.get(key)
        if value and value not in parts:
            parts.append(value)
    if item.get("episode_title") and item["episode_title"] not in parts:
        parts.insert(0, item["episode_title"])
    return "\n".join(parts)


def _set_meta(listitem, item, label):
    """Everything the card says about itself, onto the info tag.

    ``mediatype`` matters more than it looks: without it Kodi treats every row
    as an anonymous video, so a show gets no poster shelf and an episode no
    season grouping, whatever artwork is attached.
    """
    detail = item.get("detail") or {}
    # The card's own subtitles are a fallback; the page's synopsis is the
    # real one, and it is the only place cast and director exist at all.
    plot = detail.get("plot") or _plot_text(item)
    if detail.get("airing") and detail["airing"] not in plot:
        plot = "%s\n\n%s" % (plot, detail["airing"]) if plot else detail["airing"]
    media = item.get("media") or "video"
    try:
        tag = listitem.getVideoInfoTag()
        tag.setMediaType(media)
        tag.setTitle(item.get("title") or label)
        if plot:
            tag.setPlot(plot)
        if detail.get("cast"):
            tag.setCast([xbmc.Actor(name) for name in detail["cast"]])
        if detail.get("directors"):
            tag.setDirectors(detail["directors"])
        if detail.get("year"):
            tag.setYear(detail["year"])
        if detail.get("rating"):
            tag.setMpaa(detail["rating"])
        if item.get("genres"):
            tag.setGenres(item["genres"])
        if item.get("channel_name"):
            tag.setStudios([item["channel_name"]])
        if item.get("duration_ms"):
            tag.setDuration(int(item["duration_ms"] / 1000))
        if media == "episode":
            if item.get("season"):
                tag.setSeason(item["season"])
            if item.get("episode"):
                tag.setEpisode(item["episode"])
            # The episode's own name, where the card carries one; the row's
            # title is the show on a season listing.
            if item.get("episode_title"):
                tag.setTitle(item["episode_title"])
                tag.setTvShowTitle(item.get("title") or "")
        elif item.get("episode_title"):
            tag.setTagLine(item["episode_title"])
    except (AttributeError, TypeError):
        info = {"title": label, "mediatype": media}
        if plot:
            info["plot"] = plot
        if detail.get("cast"):
            info["cast"] = detail["cast"]
        if detail.get("directors"):
            info["director"] = ", ".join(detail["directors"])
        if detail.get("year"):
            info["year"] = detail["year"]
        if detail.get("rating"):
            info["mpaa"] = detail["rating"]
        if item.get("duration_ms"):
            info["duration"] = int(item["duration_ms"] / 1000)
        if media == "episode":
            if item.get("season"):
                info["season"] = item["season"]
            if item.get("episode"):
                info["episode"] = item["episode"]
        listitem.setInfo("video", info)


def _content_of(cards):
    """The Kodi container type for a listing, from what is actually in it.

    A mixed listing stays "videos": claiming "tvshows" for a row that is half
    films makes a skin lay out the films wrongly.
    """
    kinds = {c.get("media") or "video" for c in cards if c.get("path")}
    if kinds == {"movie"}:
        return "movies"
    if kinds == {"tvshow"}:
        return "tvshows"
    if kinds == {"episode"}:
        return "episodes"
    return "videos"


def route_play_page(path, label=""):
    """Play a details page: fetch it, take its play button, resolve that.

    A film is listed as playable even though its card points at a page,
    because the page holds one play button and nothing else. The button's
    target is the real path, so this is the extra request that turns two
    clicks into one -- it is not guessed at, the page names it.
    """
    client = _client()
    if client is None:
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    try:
        detail = parse.detail(client.page(path), client)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Cannot play this")
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())

    actions = detail["actions"]
    if not actions:
        kodiutils.ok_dialog(
            "Friendly TV's page for this offers nothing to play.",
            "Cannot play this")
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    if len(actions) == 1:
        chosen = actions[0]
    else:
        # More than one way in -- "Start Watching" beside "Start Over" on
        # something airing live. Which one is the viewer's to pick.
        index = xbmcgui.Dialog().select(
            detail["title"] or label or "Play",
            [a["label"] for a in actions])
        if index < 0:
            return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        chosen = actions[index]
    kodiutils.log("%s resolves through its page to %s"
                  % (path, chosen["path"]))
    # The page was fetched anyway, so the synopsis and cast go with it into
    # playback -- that is what the player's own info panel reads.
    route_play(chosen["path"], label or detail["title"], detail=detail,
               media=parse.media_of(path))


def route_play(path, label="", detail=None, media="video"):
    client = _client()
    if client is None:
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    if not playback.ensure_widevine():
        kodiutils.ok_dialog(
            "Kodi could not set up Widevine, which every Friendly TV stream "
            "needs. inputstreamhelper will have said why in the log.",
            "No Widevine")
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    try:
        item = playback.resolve(client, path, label)
    except (playback.PlaybackError, api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Cannot play this")
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    if detail:
        item.setArt(_detail_art(detail))
        _set_detail_meta(item, detail, label or detail["title"], media)
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def route_iptv(kind, port):
    if not port:
        kodiutils.log_error("iptv manager called without a port")
        return
    from lib import iptv
    manager = iptv.IPTVManager(int(port))
    if kind == "channels":
        manager.send_channels()
    else:
        manager.send_epg()


def main():
    params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    action = params.get("action", "")

    if action == "live":
        route_live()
    elif action == "guide":
        route_guide()
    elif action == "guide_channel":
        route_guide_channel(params.get("channel_id", ""),
                            params.get("name", ""))
    elif action == "home":
        route_home()
    elif action == "home_row":
        route_home_row(params.get("code", ""), params.get("name", ""))
    elif action == "page":
        route_page(params.get("path", ""), params.get("name", ""))
    elif action == "section":
        route_section(params.get("path", ""), params.get("code", ""),
                      params.get("name", ""))
    elif action == "section_cached":
        route_section_cached(params.get("path", ""), params.get("code", ""),
                             params.get("name", ""))
    elif action == "search":
        route_search(params.get("query", ""),
                     params.get("bucket", SEARCH_ALL))
    elif action == "record":
        # RunPlugin, not a directory: nothing to draw, and the listing the
        # menu was opened from stays where it is.
        route_record(params.get("path", ""), params.get("form", RECORD_FORM))
    elif action == "play":
        route_play(params.get("path", ""), params.get("label", ""))
    elif action == "play_page":
        route_play_page(params.get("path", ""), params.get("label", ""))
    elif action == "signin":
        route_signin()
    elif action == "signout":
        route_signout()
    elif action in ("iptv_channels", "iptv_epg"):
        # RunPlugin, not a directory: there is no handle to finish and
        # nothing to draw. The answer goes back over IPTV Manager's socket.
        route_iptv(action.split("_", 1)[1], params.get("port", ""))
    else:
        route_root()


if __name__ == "__main__":
    main()
