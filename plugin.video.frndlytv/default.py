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
        for raw in client.live_channels():
            attrs = (raw.get("target") or {}).get("pageAttributes") or {}
            if str(attrs.get("networkid") or "") == str(channel_id):
                return parse.card(raw, client)["path"]
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
