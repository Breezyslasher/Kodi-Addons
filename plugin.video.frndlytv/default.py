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

    if client:
        add_dir("Active streams", url(action="sessions"),
                plot="What this account has playing right now, anywhere. "
                     "Friendly TV limits how many streams run at once.")

    if client and client.session.email:
        add_dir("Sign out (%s)" % client.session.email,
                url(action="signout"))
    else:
        add_dir("Sign in", url(action="signin"))
    finish()


def route_sessions():
    """What this account currently has playing, anywhere.

    Friendly TV caps concurrent streams, and this is the count it caps. Worth
    being able to see, because the symptom of a leaked slot -- "too many
    devices" with nothing actually watching -- is otherwise invisible from
    inside Kodi.

    Every capture of this endpoint caught it empty, so the list is known and
    the shape of an entry is not. Rather than reach for field names never
    observed, an entry is rendered from whatever scalar fields it turns out
    to carry.
    """
    client = _client()
    if client is None:
        return finish()
    try:
        sessions = client.active_sessions()
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Active streams")
        return finish()

    kodiutils.log("active streams: %d" % len(sessions))
    if not sessions:
        add_dir("Nothing is playing on this account", url(action="sessions"),
                plot="Friendly TV reports no active streams. Selecting this "
                     "checks again.")
        return finish()

    for index, session in enumerate(sessions, 1):
        if isinstance(session, dict):
            pairs = [(k, v) for k, v in sorted(session.items())
                     if isinstance(v, (str, int, float, bool)) and v != ""]
            label = str(dict(pairs).get("title")
                        or dict(pairs).get("name")
                        or "Stream %d" % index)
            plot = "\n".join("%s: %s" % (k, v) for k, v in pairs)
        else:
            label, plot = "Stream %d" % index, str(session)
        add_dir(label, url(action="sessions"), plot=plot)
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

    upcoming = [p for p in programmes if p["end_ms"] and p["end_ms"] >= now]

    # Which of these are already recording or scheduled. One request for the
    # whole window, which is what makes it worth asking at all -- the flag is
    # otherwise not on a schedule row anywhere.
    recorded = set()
    try:
        recorded = client.recorded_in_guide(
            [channel_id], now, now + GUIDE_HOURS * 3600 * 1000)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.log("could not read the guide's record markers: %s" % exc)
    if recorded:
        kodiutils.log("guide %s: %d airing(s) marked to record"
                      % (name, sum(1 for p in upcoming
                                   if _programme_id(p) in recorded)))
    on_air = next((p for p in upcoming
                   if p["start_ms"] <= now < p["end_ms"]), None)
    live_path = _live_path_for(client, channel_id, on_air)

    # The schedule endpoint sends a title and two times per airing and
    # nothing else, so the synopsis, cast and artwork are fetched from each
    # airing's overlay. Same setting and same cap as a listing, because it is
    # the same trade: one request per row for something worth reading.
    overlays = {}
    if kodiutils.get_setting_bool("full_info", True):
        try:
            raw = client.overlays(
                [p["path"] for p in upcoming],
                limit=kodiutils.get_setting_int("info_limit", 40))
            overlays = {path: parse.overlay(data, client)
                        for path, data in raw.items()}
        except (api.ApiError, auth.AuthError) as exc:
            kodiutils.log("could not read the guide's overlays: %s" % exc)

    for prog in upcoming:
        over = overlays.get(prog["path"]) or {}
        taping = _programme_id(prog) in recorded
        label = "%s  %s%s" % (_clock(prog["start_ms"]), prog["title"],
                              "  [REC]" if taping else "")
        item = xbmcgui.ListItem(label=label)
        _set_guide_meta(item, prog, over, name)
        item.addContextMenuItems(_guide_menu(prog, over, taping))
        if live_path and prog["start_ms"] <= now < prog["end_ms"]:
            # On the air: two ways to watch it, so the choice is offered
            # rather than assumed. Joining live is the channel's own path;
            # starting over is the programme's, which the stream endpoint
            # answers with a VOD from the beginning.
            item.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(
                HANDLE,
                url(action="guide_play", path=live_path,
                    programme=prog["path"], label=label),
                item, isFolder=False)
        else:
            # Not on the air, so nothing to play -- but an item with an empty
            # url is not "inert", it is one Kodi tries to open, once per row,
            # which is where a guide full of "InputStream: Error opening,"
            # came from. A folder pointing at the info route is inert.
            xbmcplugin.addDirectoryItem(
                HANDLE,
                url(action="programme", name=prog["title"], channel=name,
                    start=prog["start_ms"], end=prog["end_ms"]),
                item, isFolder=True)
    finish("videos")


def _programme_id(prog):
    """The id the record-marker set is keyed by: the tail of epg/play/<id>."""
    return str(prog.get("path", "")).rsplit("/", 1)[-1]


def _guide_menu(prog, over, taping=None):
    """What a guide airing offers besides watching it.

    The show it belongs to is only knowable from the airing's overlay
    (``target_browse_episodes``), so these two entries appear when the
    overlay was fetched and the airing is part of a series -- a film on a
    channel has no show to go to.
    """
    menu = _recording_menu(prog["path"], taping)
    menu.extend(_favourite_menu(prog["path"], prog["title"],
                                prog.get("is_favourite")))
    series = over.get("series")
    if series:
        title = over.get("title") or prog["title"]
        menu.append(("Go to show", "Container.Update(%s)"
                     % url(action="page", path=series, name=title)))
        menu.extend(_similar_menu({"path": series, "title": title}))
    return menu


def _set_guide_meta(item, prog, over, channel):
    """One guide row: when it is on, and whatever its overlay knows."""
    when = "%s - %s on %s" % (_clock(prog["start_ms"]),
                              _clock(prog["end_ms"]), channel)
    bits = [when]
    for extra in (over.get("episode_title"), over.get("repeat")):
        if extra and extra not in bits:
            bits.append(extra)
    if over.get("plot"):
        bits.append(over["plot"])
    plot = "\n".join(bits)

    art = {}
    if over.get("image"):
        art["thumb"] = art["poster"] = art["fanart"] = over["image"]
    if over.get("channel_logo"):
        art.setdefault("icon", over["channel_logo"])
    if art:
        item.setArt(art)

    media = "episode" if over.get("season") or over.get("episode") else "video"
    try:
        tag = item.getVideoInfoTag()
        tag.setMediaType(media)
        tag.setTitle(over.get("episode_title") or prog["title"])
        tag.setPlot(plot)
        if over.get("cast"):
            tag.setCast([xbmc.Actor(person) for person in over["cast"]])
        if over.get("rating"):
            tag.setMpaa(over["rating"])
        if media == "episode":
            tag.setTvShowTitle(prog["title"])
            if over.get("season"):
                tag.setSeason(over["season"])
            if over.get("episode"):
                tag.setEpisode(over["episode"])
        if prog["end_ms"] > prog["start_ms"]:
            tag.setDuration(int((prog["end_ms"] - prog["start_ms"]) / 1000))
    except (AttributeError, TypeError):
        info = {"title": over.get("episode_title") or prog["title"],
                "plot": plot, "mediatype": media}
        if over.get("cast"):
            info["cast"] = over["cast"]
        if over.get("rating"):
            info["mpaa"] = over["rating"]
        item.setInfo("video", info)


def route_programme(name, channel, start, end):
    """Show what a not-yet-airing programme is, and stay where we are.

    Friendly TV offers no catch-up from a guide entry, so there is nothing to
    play here. Ending the directory unsuccessfully leaves the viewer in the
    schedule they were reading rather than navigating them into an empty
    folder.
    """
    when = " - ".join(p for p in (_clock(_ms(start)), _clock(_ms(end))) if p)
    day = _day(_ms(start))
    kodiutils.ok_dialog(
        "%s\n\n%s%s on %s" % (name, (day + " ") if day else "", when, channel),
        name)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def _ms(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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


def _day(milliseconds):
    if not milliseconds:
        return ""
    return time.strftime("%a", time.localtime(milliseconds / 1000.0))


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


def _recording_menu(programme_path, taping=None):
    """Context-menu entries for a guide airing, or none without a path.

    ``taping`` is whether the airing already has a record marker, which the
    guide now knows for a whole window in one request. Where it is known only
    the applicable entry is offered; where it is not (``None``) both are, as
    before, since guessing wrong here means offering to stop a recording that
    was never started.
    """
    if not programme_path:
        return []
    record = ("Record...", "RunPlugin(%s)"
              % url(action="record", path=programme_path, form=RECORD_FORM))
    stop = ("Stop or delete recording...", "RunPlugin(%s)"
            % url(action="record", path=programme_path, form=STOP_FORM))
    if taping is None:
        return [record, stop]
    return [stop] if taping else [record]


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
        # No capture ever exercised "player_recording_form" -- the one a film
        # or show names, as opposed to the guide's "recording_form" -- because
        # clicking Record in the web player reloads the page and drops the
        # request from the log. So if it ever answers with nothing usable, say
        # what it did answer with: one line here settles it without a capture.
        kodiutils.log("recording form %r on %s offered no options; it "
                      "returned element(s): %s"
                      % (form_code, programme_path,
                         [(el.get("elementCode"), el.get("fieldType"))
                          for el in (form.get("elements") or [])] or "none"))
        kodiutils.notify("No recording options for this")
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
        _add_detail_action(act, detail, media, page_path=path)

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


def _add_detail_action(action, detail, media="video", page_path=""):
    """A play button from a details page, with the page's own art and blurb.

    ``page_path`` is the title's own path rather than the button's target, so
    the menu here favourites *the film* and not the airing it happens to be
    playing from. Without it, a title opened from Information had no way to
    be favourited at all.
    """
    label = action["label"] or detail["title"] or "Play"
    if detail["title"] and detail["title"].lower() not in label.lower():
        label = "%s - %s" % (label, detail["title"])
    item = xbmcgui.ListItem(label=label)
    item.setArt(_detail_art(detail))
    _set_detail_meta(item, detail, label, media)
    item.setProperty("IsPlayable", "true")
    if page_path:
        # The page states whether it is already a favourite, so the right
        # verb is shown here rather than both.
        item.addContextMenuItems(
            _favourite_menu(page_path, detail["title"],
                            detail.get("is_favourite")) +
            _similar_menu({"path": page_path, "title": detail["title"]}))
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
        elif parse.is_genre(item):
            # A genre is a word, not a page: there is nothing at "Westerns"
            # to open. Search matches on genre, so the word goes there.
            add_dir(item["title"] or item["path"],
                    url(action="search", query=item["path"]),
                    art=_art(item),
                    plot="%s from Friendly TV's catalogue."
                         % (item["title"] or item["path"]))
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
    menu = _card_menu(item)
    if menu:
        listitem.addContextMenuItems(menu)
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action="page", path=item["path"], name=item["title"]),
        listitem, isFolder=True)


def _favourite_menu(path, name, is_favourite):
    """The favourite entry that applies, given what is known about the item.

    A card carries ``isFavourite``, so only the useful verb is offered --
    showing both would leave the viewer guessing which state they are in.
    Where that is genuinely unknown (``None``: a title's own page does not
    say), both are offered rather than guessing a verb that may be the wrong
    one.
    """
    if not path:
        return []
    add = ("Add to Favourites", "RunPlugin(%s)"
           % url(action="favourite", path=path, name=name, on="1"))
    remove = ("Remove from Favourites", "RunPlugin(%s)"
              % url(action="favourite", path=path, name=name, on="0"))
    if is_favourite is None:
        return [add, remove]
    return [remove] if is_favourite else [add]


def route_favourite(path, name, on):
    """Put this in My Stuff, or take it out."""
    client = _client()
    if client is None:
        return
    try:
        said = client.favourite(path, on)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Favourites")
        return
    kodiutils.log("favourite %s %s -> %s"
                  % ("on" if on else "off", path, said or "no message"))
    # "Added to My Stuff" / "Removed from My Stuff", in the service's words.
    kodiutils.notify(said or (name or path))
    _refresh()


def _card_menu(item):
    """Everything a card offers besides opening or playing it.

    Recording is not a guide-only thing: 2221 of the captured cards name a
    recording form, films and channels among them. The card says which form
    applies, so that is the one asked for -- the guide's airings say
    "recording_form" and a card says "player_recording_form".
    """
    menu = _favourite_menu(item.get("path"), item.get("title", ""),
                           item.get("is_favourite"))
    form = item.get("recording_form")
    if form and item.get("can_record") and item.get("path"):
        menu.append(("Recording...", "RunPlugin(%s)"
                     % url(action="record", path=item["path"], form=form)))
    return menu + _similar_menu(item) + _cast_menu(item)


def _cast_menu(item):
    """A way into "what else is this person in".

    Search matches on people as well as titles -- "Raymond Burr" answers with
    Perry Mason and two of his films -- so the cast is worth being able to
    search. The names are not on the card, only on the title's page, so this
    entry opens a chooser that fetches them rather than putting them in the
    url.
    """
    if not parse.content_id(item.get("path")):
        return []
    return [("Search the cast...", "RunPlugin(%s)"
             % url(action="cast", path=item["path"],
                   name=item.get("title", "")))]


def route_cast(path, name=""):
    """Pick someone from this title's cast, and search for them."""
    client = _client()
    if client is None:
        return
    try:
        detail = parse.detail(client.page(path), client)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Cast")
        return
    people = detail["cast"] + [d for d in detail["directors"]
                               if d not in detail["cast"]]
    if not people:
        kodiutils.notify("No cast listed for %s" % (name or "this"))
        return
    index = xbmcgui.Dialog().select(name or detail["title"] or "Cast", people)
    if index < 0:
        return
    kodiutils.log("searching for cast member %r" % people[index])
    xbmc.executebuiltin("Container.Update(%s)"
                        % url(action="search", query=people[index]))


def _similar_menu(item):
    """A "More like this" entry, for a card the service can key on."""
    if not parse.content_id(item.get("path")):
        return []
    return [("More like this", "Container.Update(%s)"
             % url(action="similar", path=item["path"],
                   name=item.get("title", "")))]


def _add_playable(item, label=None, action="play"):
    label = label or item["title"] or item["path"]
    listitem = xbmcgui.ListItem(label=label)
    listitem.setArt(_art(item))
    listitem.setProperty("IsPlayable", "true")
    _set_meta(listitem, item, label)
    menu = _card_menu(item)
    if menu:
        listitem.addContextMenuItems(menu)
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
        live = item.get("is_live") or (item.get("path") or "").startswith(
            "channel/live/")
        if item.get("duration_ms") and not live:
            tag.setDuration(int(item["duration_ms"] / 1000))
        if live:
            # A live channel's card carries the running time of whatever is on
            # it, and giving Kodi that duration makes a channel look like a
            # finite video: stop watching and it gets marked watched, because
            # Kodi computed a percentage from a length the channel does not
            # have. A channel is never "finished".
            tag.setPlaycount(0)
        _set_resume(listitem, tag, item)
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
        if item.get("duration_ms") and not (
                item.get("is_live")
                or (item.get("path") or "").startswith("channel/live/")):
            info["duration"] = int(item["duration_ms"] / 1000)
        if item.get("is_live"):
            info["playcount"] = 0
        if media == "episode":
            if item.get("season"):
                info["season"] = item["season"]
            if item.get("episode"):
                info["episode"] = item["episode"]
        listitem.setInfo("video", info)


def _set_resume(listitem, tag, item):
    """Where the viewer got to, for a Continue Watching row.

    The service sends progress as a fraction of the running time, so both are
    needed: without the duration there is nothing to multiply. Set through
    setResumePoint where Kodi has it (20+), and through the ResumeTime and
    TotalTime properties on anything older, which is the only way there.
    """
    fraction = item.get("resume") or 0.0
    seconds = item.get("duration_ms", 0) / 1000.0
    if not fraction or seconds <= 0:
        return
    position = fraction * seconds
    try:
        tag.setResumePoint(position, seconds)
    except (AttributeError, TypeError):
        listitem.setProperty("ResumeTime", "%.0f" % position)
        listitem.setProperty("TotalTime", "%.0f" % seconds)


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


def route_guide_play(live_path, programme_path, label=""):
    """Join the channel live, or start the programme from the beginning.

    Both are the same endpoint with a different path. ``channel/live/<slug>``
    gives the live edge; the programme's own ``epg/play/<id>`` is answered
    with a VOD asset whose seek position is zero, which is what starting over
    means.

    The service decides whether that second one exists for a programme still
    airing -- if it does not, its own refusal is what gets shown.
    """
    if not programme_path:
        return route_play(live_path, label)
    choice = xbmcgui.Dialog().select(label or "Watch",
                                     ["Play live", "Start over"])
    if choice < 0:
        return xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
    if choice == 0:
        return route_play(live_path, label)
    kodiutils.log("starting %s over from %s" % (label, programme_path))
    route_play(programme_path, label, from_start=True)


def route_similar(path, name=""):
    """Titles the service considers similar to this one."""
    client = _client()
    if client is None:
        return finish()
    identifier = parse.content_id(path)
    if not identifier:
        kodiutils.notify("Nothing to look up for this")
        return finish()
    try:
        raw = client.more_like_this(identifier)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "More like this")
        return finish()
    cards = [parse.card(c, client) for c in raw]
    kodiutils.log("more like %s (%s): %d card(s)"
                  % (name or path, identifier, len(cards)))
    if not cards:
        kodiutils.notify("Nothing similar to %s" % (name or "this"))
    finish(_add_cards(cards, client))


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


def route_play(path, label="", detail=None, media="video",
               from_start=False):
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
        item = playback.resolve(client, path, label,
                                from_start=from_start)
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
    elif action == "programme":
        route_programme(params.get("name", ""), params.get("channel", ""),
                        params.get("start", 0), params.get("end", 0))
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
    elif action == "guide_play":
        route_guide_play(params.get("path", ""), params.get("programme", ""),
                         params.get("label", ""))
    elif action == "similar":
        route_similar(params.get("path", ""), params.get("name", ""))
    elif action == "cast":
        route_cast(params.get("path", ""), params.get("name", ""))
    elif action == "favourite":
        # RunPlugin: nothing to draw, and the listing stays where it is.
        route_favourite(params.get("path", ""), params.get("name", ""),
                        params.get("on") == "1")
    elif action == "signin":
        route_signin()
    elif action == "sessions":
        route_sessions()
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
