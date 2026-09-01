"""Friendly TV for Kodi: menus, listings and playback."""

import sys
import time

from urllib.parse import parse_qsl, urlencode

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
# How far ahead a search looks. Long enough to answer "when is it on" for
# tonight and tomorrow morning, short enough that the guide fetch it costs
# stays a handful of requests.
SEARCH_HOURS = 12


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
            plot="Search channel names and what is on in the next %d hours. "
                 "Friendly TV's own catalogue search runs on an API this "
                 "addon has no capture of, so it is not the same search the "
                 "web player does." % SEARCH_HOURS)

    if client:
        for entry in client.menus():
            # Guide has a richer listing of its own above; the service's own
            # "guide" page is a duplicate of it.
            if entry["path"] == "guide":
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
    import xbmc
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


def route_search(query=""):
    """Search the channel lineup and the guide.

    This is **not** the service's own catalogue search. Friendly TV runs that
    on a separate API surface (``/search/api/v3/``) which no capture has
    exercised -- the web player loads it as a lazy chunk, and the chunk was
    never downloaded in any capture taken so far, so there is no request shape
    to copy and a guessed one would be worse than none.

    What this does instead is search what the captured endpoints already
    return: every channel's name, and every programme title in the next
    ``SEARCH_HOURS``. For a live TV service that answers most of what a viewer
    actually asks -- "is Hallmark on", "when is Perry Mason" -- and every
    result is real rather than hopeful.
    """
    client = _client()
    if client is None:
        return finish()
    if not query:
        query = kodiutils.input_text("Search channels and the guide") or ""
    query = query.strip()
    if not query:
        return finish()
    needle = query.lower()

    try:
        channels = client.lineup()
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not search")
        return finish()

    hits = 0
    for channel in channels:
        if needle in (channel["name"] or "").lower() and channel["path"]:
            item = {"title": channel["name"], "path": channel["path"],
                    "channel_name": channel["name"], "poster": channel["logo"],
                    "channel_logo": channel["logo"], "subtitle": channel["now"],
                    "description": "", "episode_title": "", "genres": [],
                    "duration_ms": 0}
            _add_playable(item, label="%s (channel)" % channel["name"])
            hits += 1

    for match in _search_guide(client, channels, needle):
        _add_guide_hit(match)
        hits += 1

    kodiutils.log("search %r: %d result(s)" % (query, hits))
    if not hits:
        kodiutils.notify("Nothing matching \"%s\"" % query)
    finish("videos")


def _search_guide(client, channels, needle):
    """Programme titles matching ``needle`` in the next SEARCH_HOURS.

    Returns matches in time order, soonest first, so "what is on" reads the
    way a viewer expects rather than in whatever order the batches came back.
    """
    playable = [c for c in channels if c["path"]]
    names = {c["id"]: c for c in playable}
    now = int(time.time() * 1000)
    end = now + SEARCH_HOURS * 3600 * 1000
    ids = [c["id"] for c in playable]
    found = []
    for index in range(0, len(ids), GUIDE_BATCH):
        batch = ids[index:index + GUIDE_BATCH]
        try:
            rows = client.guide(batch, now, end, page=index // GUIDE_BATCH)
        except (api.ApiError, auth.AuthError) as exc:
            kodiutils.log("search: guide batch %d failed: %s"
                          % (index // GUIDE_BATCH, exc))
            continue
        for row in rows:
            channel = names.get(str(row.get("channelId") or ""))
            if not channel:
                continue
            for raw in (row.get("programs") or []):
                prog = parse.programme(raw)
                if not prog["end_ms"] or prog["end_ms"] < now:
                    continue
                if needle not in (prog["title"] or "").lower():
                    continue
                found.append((prog, channel))
    found.sort(key=lambda pair: pair[0]["start_ms"])
    return found


def _add_guide_hit(match):
    """One guide result: playable only while it is actually on the air."""
    prog, channel = match
    now = time.time() * 1000
    on_air = prog["start_ms"] <= now < prog["end_ms"]
    when = "%s %s" % (_day(prog["start_ms"]), _clock(prog["start_ms"]))
    label = "%s - %s (%s)" % (prog["title"], channel["name"],
                              "on now" if on_air else when)
    item = xbmcgui.ListItem(label=label)
    art = {"icon": channel["logo"], "thumb": channel["logo"]}
    item.setArt(art)
    _plot(item, "%s on %s\n%s - %s" % (prog["title"], channel["name"],
                                       _clock(prog["start_ms"]),
                                       _clock(prog["end_ms"])))
    item.addContextMenuItems(_recording_menu(prog["path"]))
    if on_air:
        item.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(
            HANDLE, url(action="play", path=channel["path"], label=label),
            item, isFolder=False)
    else:
        # Nothing to play yet, and Friendly TV offers no catch-up route from
        # a guide entry, so this is an entry that says when rather than a
        # link that would fail.
        xbmcplugin.addDirectoryItem(HANDLE, "", item, isFolder=False)


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


def _day(milliseconds):
    if not milliseconds:
        return ""
    return time.strftime("%a", time.localtime(milliseconds / 1000.0))


def route_page(path, name=""):
    """A page of the service's own hierarchy: Home, Movies, TV, My Stuff."""
    client = _client()
    if client is None:
        return finish()
    try:
        response = client.page(path)
    except (api.ApiError, auth.AuthError) as exc:
        kodiutils.ok_dialog(str(exc), "Could not open %s" % (name or path))
        return finish()

    sections = parse.sections(response, client)
    kodiutils.log("page %s: %d section(s)" % (path, len(sections)))

    # A page that is one section is that section: making the viewer open a
    # single folder to reach the only thing behind it is a wasted click.
    if len(sections) == 1 and sections[0]["cards"]:
        _add_cards(sections[0]["cards"])
        return finish("videos")

    for section in sections:
        if section["cards"]:
            add_dir(section["name"] or path,
                    url(action="section_cached", path=path,
                        code=section["code"], name=section["name"]))
        elif section["code"]:
            add_dir(section["name"] or section["code"],
                    url(action="section", path=path, code=section["code"],
                        name=section["name"]))
    if not sections:
        kodiutils.notify("Nothing here")
    finish()


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
    _add_cards(cards)
    finish("videos")


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
            _add_cards(section["cards"])
            return finish("videos")
    kodiutils.notify("That row is no longer there")
    finish("videos")


def _add_cards(cards):
    for item in cards:
        if not item["path"]:
            continue
        if item["playable"]:
            _add_playable(item)
        else:
            add_dir(item["title"] or item["path"],
                    url(action="page", path=item["path"],
                        name=item["title"]),
                    art=_art(item), plot=_plot_text(item))


def _add_playable(item, label=None):
    label = label or item["title"] or item["path"]
    listitem = xbmcgui.ListItem(label=label)
    listitem.setArt(_art(item))
    listitem.setProperty("IsPlayable", "true")
    _set_meta(listitem, item, label)
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action="play", path=item["path"], label=label),
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
    plot = _plot_text(item)
    try:
        tag = listitem.getVideoInfoTag()
        tag.setTitle(label)
        if plot:
            tag.setPlot(plot)
        if item.get("genres"):
            tag.setGenres(item["genres"])
        if item.get("channel_name"):
            tag.setStudios([item["channel_name"]])
        if item.get("duration_ms"):
            tag.setDuration(int(item["duration_ms"] / 1000))
        if item.get("episode_title"):
            tag.setTagLine(item["episode_title"])
    except (AttributeError, TypeError):
        info = {"title": label}
        if plot:
            info["plot"] = plot
        if item.get("duration_ms"):
            info["duration"] = int(item["duration_ms"] / 1000)
        listitem.setInfo("video", info)


def route_play(path, label=""):
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
    elif action == "page":
        route_page(params.get("path", ""), params.get("name", ""))
    elif action == "section":
        route_section(params.get("path", ""), params.get("code", ""),
                      params.get("name", ""))
    elif action == "section_cached":
        route_section_cached(params.get("path", ""), params.get("code", ""),
                             params.get("name", ""))
    elif action == "search":
        route_search(params.get("query", ""))
    elif action == "record":
        # RunPlugin, not a directory: nothing to draw, and the listing the
        # menu was opened from stays where it is.
        route_record(params.get("path", ""), params.get("form", RECORD_FORM))
    elif action == "play":
        route_play(params.get("path", ""), params.get("label", ""))
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
