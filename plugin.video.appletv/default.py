"""Apple TV addon entry point and router."""

import json
import sys
from urllib.parse import urlencode, parse_qsl, quote

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import AppleAuth, STATUS_OK, STATUS_NEEDS_2FA, STATUS_ERROR
from lib.api import (AppleTVApi, CHANNELS, APPLE_TV_PLUS_CHANNEL, F1_CHANNEL,
                     PLAYBACK_REPORT_CACHE)
from lib.itunes import ItunesStore

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

# String ids (see resources/language/.../strings.po).
S = {
    "originals": 32010,
    "movies": 32011,
    "itunes_library": 32012,
    "search": 32013,
    "sign_in": 32014,
    "sign_out": 32015,
    "signed_in_as": 32016,
    "enter_apple_id": 32020,
    "enter_password": 32021,
    "enter_2fa": 32022,
    "sign_in_ok": 32023,
    "sign_in_failed": 32024,
    "sign_in_required": 32025,
    "search_heading": 32026,
    "no_results": 32027,
    "playback_failed": 32028,
    "sd_notice": 32029,
    "confirm_sign_out": 32030,
    "play_trailer": 32031,
    "choose_trailer": 32032,
    "no_trailer": 32033,
    "bonus_content": 32034,
    "choose_bonus": 32035,
    "no_bonus": 32036,
    "follow_team": 32037,
    "unfollow_team": 32038,
    "followed": 32039,
    "unfollowed": 32040,
    "follow_failed": 32042,
    "add_watchlist": 32045,
    "watchlist_added": 32046,
    "watchlist_failed": 32047,
    "remove_watchlist": 32048,
    "watchlist_removed": 32049,
    "watchlist": 32051,
    "sub_active": 32052,
    "sub_none": 32053,
    "sub_renews": 32054,
    "sub_family": 32055,
    "sub_unknown": 32056,
    "sub_shared_with_you": 32058,
    "following": 32059,
    "search_suggestions": 32061,
    "choose_feed": 32062,
    "play_feed": 32063,
    "related": 32064,
    "clubs": 32065,
    "highlights": 32066,
    "spotlight": 32067,
    "race_weekend": 32068,
    "cast": 32069,
    "itunes_sign_in": 32070,
    "itunes_sign_out": 32071,
    "itunes_sign_in_ok": 32072,
    "itunes_sign_in_failed": 32073,
    "films": 32077,
    "tv_shows": 32078,
}


def L(key):
    return kodiutils.localize(S[key])


def url(**kwargs):
    return "%s?%s" % (BASE_URL, urlencode(kwargs))


def extras_context_menu(item, item_id, item_type):
    """Context-menu entries that play a title's trailers and bonus features."""
    item.addContextMenuItems([
        (L("play_trailer"), "RunPlugin(%s)" % url(
            action="extras", kind="trailers", item_id=item_id, item_type=item_type)),
        (L("bonus_content"), "RunPlugin(%s)" % url(
            action="extras", kind="bonus", item_id=item_id, item_type=item_type)),
        (L("related"), "Container.Update(%s)" % url(
            action="related", item_id=item_id)),
        (L("cast"), "Container.Update(%s)" % url(
            action="cast", item_id=item_id, item_type=item_type)),
    ] + watchlist_menu_items(item_id))


def watchlist_menu_items(item_id):
    """Add and remove are both offered rather than one toggle.

    Apple does report membership, as inUpNext, but only on a title's own page
    and on an episode list -- never on the shelf items most of the addon
    lists, so the state is unknown where the menu is usually opened.
    """
    return [
        (L("add_watchlist"), "RunPlugin(%s)" % url(
            action="watchlist", item_id=item_id, on="1")),
        (L("remove_watchlist"), "RunPlugin(%s)" % url(
            action="watchlist", item_id=item_id, on="0")),
    ]


def apply_entry_info(tag, entry):
    """Set what Apple sends about a title on any item, folder or not."""
    if entry.get("show_title"):
        # An episode listed away from its show -- Continue Watching does this
        # -- has no other way of saying which show it belongs to.
        try:
            tag.setTvShowTitle(entry["show_title"])
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("title") or entry.get("sort_title"):
        try:
            tag.setTitle(entry.get("sort_title") or entry["title"])
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
    if entry.get("plot"):
        tag.setPlot(entry["plot"])
    if entry.get("year"):
        try:
            tag.setYear(int(entry["year"]))
        except (TypeError, ValueError):
            pass
    if entry.get("season"):
        try:
            tag.setSeason(int(entry["season"]))
        except (TypeError, ValueError):
            pass
    if entry.get("episode"):
        try:
            tag.setEpisode(int(entry["episode"]))
        except (TypeError, ValueError):
            pass
    if entry.get("genres"):
        try:
            tag.setGenres(list(entry["genres"]))
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("mpaa"):
        try:
            tag.setMpaa(entry["mpaa"])
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("tagline"):
        try:
            tag.setTagLine(entry["tagline"])
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("studio"):
        try:
            tag.setStudios([entry["studio"]])
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("premiered"):
        try:
            tag.setPremiered(entry["premiered"])
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("watched"):
        # Apple says whether the account has finished a title; Kodi shows that
        # as a watched tick beside it.
        try:
            tag.setPlaycount(1)
        except (TypeError, ValueError, AttributeError):
            pass
    if entry.get("duration"):
        try:
            tag.setDuration(int(entry["duration"]))
        except (TypeError, ValueError):
            pass


def add_dir(label, action, art=None, extras_for=None, context=None,
            info=None, media_type=None, **params):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    # A folder with no info tag gets no Info dialog at all, which is why shows
    # and seasons had none. Apple describes them the same as anything else.
    if info or media_type:
        tag = item.getVideoInfoTag()
        if media_type:
            try:
                tag.setMediaType(media_type)
            except (TypeError, ValueError, AttributeError):
                pass
        if info:
            apply_entry_info(tag, info)
    if extras_for:
        extras_context_menu(item, extras_for[0], extras_for[1])
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action=action, **params), item, isFolder=True
    )


def add_playable(entry, cast=None):
    item = xbmcgui.ListItem(label=entry["title"])
    if entry.get("art"):
        item.setArt(entry["art"])
    tag = item.getVideoInfoTag()
    tag.setTitle(entry.get("sort_title") or entry["title"])
    kind = str(entry.get("type"))
    tag.setMediaType("episode" if kind == "Episode" else "movie")
    if entry.get("start_time"):
        try:
            import time
            tag.setFirstAired(time.strftime("%Y-%m-%d", time.localtime(entry["start_time"])))
        except (TypeError, ValueError, OverflowError):
            pass
    apply_entry_info(tag, entry)
    # Apple reports how far the account already is into a title, so it can be
    # resumed here at the point another Apple client left it.
    resume = entry.get("resume") or {}
    if resume.get("position") and resume.get("total"):
        try:
            tag.setResumePoint(resume["position"], resume["total"])
        except (TypeError, ValueError, AttributeError):
            pass
    # Kodi has a cast area of its own, so the people credited on a title fill
    # it in rather than only being listed. Apple keeps them on the title's
    # page, not on each item, so the caller fetches them once and passes them
    # to every entry instead of asking per episode.
    if cast:
        try:
            tag.setCast([xbmc.Actor(person["name"], person.get("role") or "",
                                    order, (person.get("art") or {}).get("thumb") or "")
                         for order, person in enumerate(cast)])
        except (TypeError, ValueError, AttributeError):
            pass
    item.setProperty("IsPlayable", "true")
    # Episodes and sporting events carry no extras shelves of their own, but
    # anything playable can go on the watchlist.
    if kind in ("Movie", "Show", "Vod", "MovieBundle"):
        extras_context_menu(item, entry["id"], kind)
    elif kind == "SportingEvent":
        # A match links to the other games in its league and to its clubs.
        item.addContextMenuItems([
            (L("related"), "Container.Update(%s)" % url(
                action="related", item_id=entry["id"],
                league=entry.get("league_id") or "")),
            (L("clubs"), "Container.Update(%s)" % url(
                action="clubs", item_id=entry["id"])),
            (L("highlights"), "Container.Update(%s)" % url(
                action="event_extras", kind="highlights", item_id=entry["id"])),
            (L("spotlight"), "Container.Update(%s)" % url(
                action="event_extras", kind="spotlight", item_id=entry["id"])),
        ] + ([(L("race_weekend"), "Container.Update(%s)" % url(
                action="event_extras", kind="weekend", item_id=entry["id"]))]
             # Only Motorsports fixtures have a weekend of sessions; a match
             # in any other sport carries clubs and no weekend at all.
             if entry.get("sport") == "Motorsports" else [])
          + watchlist_menu_items(entry["id"]))
    # Everything else -- episodes, and the sports clip types that carry their
    # stream inline -- gets no watchlist entry: Apple's watchlist takes films,
    # shows and fixtures only, which is what every captured write sends, so
    # offering it on an episode was offering something that cannot work.
    xbmcplugin.addDirectoryItem(
        HANDLE,
        url(action="play", item_id=entry["id"], item_type=entry.get("type", "Movie")),
        item,
        isFolder=False,
    )


# -- menus ---------------------------------------------------------------

def main_menu(auth):
    # One entry per brand tab along the top of tv.apple.com's home page.
    for channel_id, name in CHANNELS:
        label = L("originals") if channel_id == APPLE_TV_PLUS_CHANNEL else name
        add_dir(label, "channel", channel_id=channel_id)
    store = ItunesStore(auth.session)
    if store.is_signed_in() or store.pasted_cookies() or auth.is_authenticated():
        add_dir(L("itunes_library"), "itunes")
    add_dir(L("search"), "search")
    if kodiutils.get_setting("manifest_url_override"):
        add_dir("[Debug] Test playback (manifest override)", "debug_play")
    if auth.is_authenticated():
        add_dir(L("sign_out"), "sign_out")
    else:
        add_dir(L("sign_in"), "sign_in")
    # The store is a separate service with its own login, so signing in to
    # Apple TV+ does not sign in to it.
    add_dir(L("itunes_sign_out") if store.is_signed_in() else L("itunes_sign_in"),
            "itunes_sign_out" if store.is_signed_in() else "itunes_sign_in")
    xbmcplugin.endOfDirectory(HANDLE)


def show_shelves(api, shelves, cache_key=APPLE_TV_PLUS_CHANNEL,
                 brand=APPLE_TV_PLUS_CHANNEL):
    """List a canvas' shelves.

    cache_key names the canvas whose cache holds these shelves (a channel or
    a room); brand stays the owning channel, which is what rooms nested
    further down need for their ctx_brand.
    """
    if not shelves:
        kodiutils.notify(L("no_results"))
    # Apple's own favourites shelf is an empty marker the website fills in
    # itself; the club tiles say who is followed, so do the same here.
    followed = [i for s in shelves for i in s.get("items") or []
                if str(i.get("type")) == "Team" and i.get("favourite")]
    if followed:
        add_dir("%s (%d)" % (L("following"), len(followed)),
                "following", cache_key=cache_key, brand=brand)
    for shelf in shelves:
        if shelf.get("items"):
            # A shelf with a paging token has more than the canvas returned;
            # they are fetched when it is opened.
            count = "%d+" % len(shelf["items"]) if shelf.get("next") \
                else str(len(shelf["items"]))
            add_dir("%s (%s)" % (shelf["title"], count),
                    "shelf", shelf_id=shelf["id"], title=shelf["title"],
                    cache_key=cache_key, brand=brand)
    xbmcplugin.endOfDirectory(HANDLE)


def add_item(entry, channel_id=APPLE_TV_PLUS_CHANNEL, cast=None):
    """Add a catalogue entry: shows and rooms are folders, the rest play."""
    kind = str(entry.get("type"))
    if kind == "Show":
        add_dir(entry["title"], "show", art=entry.get("art"),
                extras_for=(entry["id"], "Show"), info=entry,
                media_type="tvshow", show_id=entry["id"])
    elif kind == "Room":
        # A room is a browse category (Kids & Family, Sci-Fi, ...) with a
        # canvas of shelves behind it.
        add_dir(entry["title"], "room", art=entry.get("art"),
                room_id=entry["id"], channel_id=channel_id)
    elif kind == "GrandPrix":
        # A race weekend: its sessions and highlights, again its own canvas.
        add_dir(entry["title"], "grandprix", art=entry.get("art"),
                gp_id=entry["id"], channel_id=channel_id)
    elif kind == "Team":
        # A club page, likewise its own canvas. Clubs report whether the
        # account follows them and the flag updates after a change, so the
        # menu offers the one action that applies.
        followed = entry.get("favourite")
        label = ("* " + entry["title"]) if followed else entry["title"]
        action = (L("unfollow_team"), "0") if followed else (L("follow_team"), "1")
        menu = [(action[0], "RunPlugin(%s)" % url(
            action="follow_team", team_id=entry["id"], on=action[1]))]
        add_dir(label, "team", art=entry.get("art"), context=menu,
                team_id=entry["id"], channel_id=channel_id)
    else:
        add_playable(entry, cast)


def show_people(people):
    """List a title's cast and crew. Nobody here is playable."""
    if not people:
        kodiutils.notify(L("no_results"))
    for person in people:
        label = person["name"]
        if person.get("role"):
            label = "%s - %s" % (label, person["role"])
        entry = xbmcgui.ListItem(label=label)
        if person.get("art"):
            entry.setArt(person["art"])
        xbmcplugin.addDirectoryItem(HANDLE, "", entry, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def show_items(items, content="movies", channel_id=APPLE_TV_PLUS_CHANNEL,
               cast=None):
    if not items:
        kodiutils.notify(L("no_results"))
    for entry in items:
        add_item(entry, channel_id, cast)
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE)


# -- actions -------------------------------------------------------------

def do_sign_in(auth, api):
    account = kodiutils.input_text(L("enter_apple_id"))
    if not account:
        return
    password = kodiutils.input_text(L("enter_password"), hidden=True)
    if not password:
        return

    status = auth.login(account, password)
    if status == STATUS_NEEDS_2FA:
        code = kodiutils.input_numeric(L("enter_2fa"))
        if not code:
            return
        status = auth.submit_2fa_code(code)

    if status == STATUS_OK:
        # Mint the media-user-token now, while the fresh myacinfo cookie is in
        # the session, so playback (a separate process) does not have to. This
        # must never break the sign-in result, so guard it.
        try:
            if not api._media_user_token():
                kodiutils.log_error("Signed in but could not mint media-user-token")
        except Exception as exc:
            kodiutils.log_error("Token mint error after sign-in: %s" % exc)
        kodiutils.notify(L("sign_in_ok"))
    else:
        kodiutils.ok_dialog(L("sign_in_failed"))


def do_sign_out(auth, api):
    if not xbmcgui.Dialog().yesno(kodiutils.ADDON_NAME, L("confirm_sign_out")):
        return
    # Tell Apple the session is over, then forget it here regardless: a
    # failed call must never leave the addon signed in locally.
    try:
        if not api.logout():
            kodiutils.log("Apple did not acknowledge the sign-out")
    except Exception as exc:
        kodiutils.log_error("Sign-out request failed: %s" % exc)
    auth.clear()
    kodiutils.notify(L("sign_out"))


def do_show(api, show_id):
    """A show opens to its seasons, or straight to its episodes if it has one."""
    seasons = api.get_show_seasons(show_id)
    show_info = api.get_title_info(show_id, "Show") if seasons else None
    if not seasons:
        show_items(api.get_show_episodes(show_id), content="episodes",
                   cast=api.get_cast(show_id, "Show"))
        return
    for season in seasons:
        label = season["title"]
        if season.get("count"):
            label = "%s (%d)" % (label, season["count"])
        # Seasons carry only a number and a count of their own, so the show's
        # own description travels with them rather than leaving them bare.
        add_dir(label, "season", info=dict(show_info or {}, title=season["title"]),
                media_type="season", show_id=show_id, season=season["number"])
    xbmcplugin.setContent(HANDLE, "seasons")
    xbmcplugin.endOfDirectory(HANDLE)


def do_itunes_sign_in(auth):
    """Sign in to the store, which is not the Apple TV+ sign-in.

    Apple takes the password directly here rather than through the SRP flow
    the website uses, so this asks for it again rather than reusing anything.
    """
    apple_id = kodiutils.input_text(L("enter_apple_id"))
    if not apple_id:
        return
    password = kodiutils.input_text(L("enter_password"), hidden=True)
    if not password:
        return
    store = ItunesStore(auth.session)
    if store.sign_in(apple_id, password):
        kodiutils.notify(L("itunes_sign_in_ok"))
    else:
        kodiutils.ok_dialog("%s\n%s" % (L("itunes_sign_in_failed"),
                                        store.last_error or ""))


def do_itunes_library(api, auth):
    """The two halves of the store locker, which are separate requests."""
    add_dir(L("films"), "itunes_movies")
    add_dir(L("tv_shows"), "itunes_tv")
    xbmcplugin.endOfDirectory(HANDLE)


def _with_resume(store, items):
    """Attach where each purchase was left, where the store knows."""
    resume = store.resume_points()
    for entry in items:
        position = resume.get(entry.get("adam_id"))
        if position and entry.get("duration"):
            entry["resume"] = {"position": position,
                               "total": float(entry["duration"])}
    return items


def do_itunes_movies(api, auth):
    """Films the account owns, from the store rather than the catalogue."""
    store = ItunesStore(auth.session)
    # The JSON locker is the better route and works with any session that the
    # store accepts, pasted or otherwise. The DAAP one needs a store sign-in,
    # which Apple refuses here, so it is only worth trying if one succeeded.
    items = store.owned_movies()
    if not items and store.is_signed_in():
        items = store.library()
    # show_items says "nothing here" on its own; this is for when Apple gave a
    # reason, which is more use than an empty list.
    if not items and store.last_error:
        kodiutils.notify(store.last_error)
    show_items(_with_resume(store, items))


def do_itunes_tv(api, auth):
    """Owned television, as the seasons its episodes belong to.

    Apple's locker lists episodes flat -- fifteen episodes across five
    seasons of five different shows, in the capture this was built from --
    so the seasons here are grouped from what the episodes say about
    themselves rather than fetched as rows of their own.
    """
    store = ItunesStore(auth.session)
    seasons = store.owned_tv_seasons()
    if not seasons:
        kodiutils.notify(store.last_error or L("no_results"))
    for season in seasons:
        add_dir(season["title"], "itunes_season", art=season.get("art"),
                info=season, media_type="season", season_id=season["id"])
    xbmcplugin.setContent(HANDLE, "seasons")
    xbmcplugin.endOfDirectory(HANDLE)


def do_itunes_season(api, auth, season_id):
    store = ItunesStore(auth.session)
    episodes = store.owned_season(season_id)
    if not episodes and store.last_error:
        kodiutils.notify(store.last_error)
    show_items(_with_resume(store, episodes), content="episodes")


def do_search(api):
    query = kodiutils.input_text(L("search_heading"))
    if not query:
        # Nothing typed: show Apple's browse page rather than an empty list.
        show_shelves(api, api.get_search_landing(), "search_landing")
        return

    # Offer Apple's suggestions, with what was typed kept as the first choice.
    options = [(query, query)]
    for hint in api.search_hints(query):
        if hint["term"] != query:
            options.append((hint["label"], hint["term"]))
    if len(options) > 1:
        index = xbmcgui.Dialog().select(L("search_suggestions"),
                                        [label for label, _ in options])
        if index < 0:
            xbmcplugin.endOfDirectory(HANDLE)
            return
        query = options[index][1]
    show_items(api.search(query))


def choose_feed(api, item_id, item_type):
    """Let the viewer pick when a match offers more than one feed.

    A game is published as a full replay beside a short recap, and once per
    commentary language, so picking the first would be arbitrary.
    """
    feeds = api.list_playables(item_id, item_type)
    if not feeds:
        return None
    labels = []
    for feed in feeds:
        label = feed["title"] or L("play_feed")
        if feed.get("duration"):
            mins = int(feed["duration"]) // 60
            label = "%s (%d min)" % (label, mins)
        if feed.get("language"):
            label = "%s - %s" % (label, feed["language"])
        labels.append(label)
    index = xbmcgui.Dialog().select(L("choose_feed"), labels)
    if index < 0:
        return False  # cancelled, as distinct from "only one feed"
    return feeds[index]["external_id"]


def do_play(api, item_id, item_type):
    external_id = None
    if str(item_type) == "SportingEvent":
        chosen = choose_feed(api, item_id, item_type)
        if chosen is False:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        external_id = chosen
    playback = api.get_playback(item_id, item_type, external_id)
    if not playback:
        kodiutils.ok_dialog(api.last_error or L("playback_failed"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    kodiutils.notify(L("sd_notice"))
    write_report_context(playback, content_id=item_id)
    play_item = build_isa_listitem(playback)
    # Playback resolves from an id, so the item Kodi shows while playing knew
    # nothing about the title and its plot read "Not available". A title's own
    # page carries the description and the cast, which shelf items do not.
    apply_title_info(play_item, api.get_title_info(item_id, item_type))

    xbmcplugin.setResolvedUrl(HANDLE, True, play_item)


def apply_title_info(item, info):
    """Fill a playing item's info screen from a title's own page."""
    if not info:
        return
    tag = item.getVideoInfoTag()
    for value, setter in (
            (info.get("title"), "setTitle"),
            (info.get("plot"), "setPlot"),
            (info.get("mpaa"), "setMpaa"),
            (info.get("tagline"), "setTagLine"),
            (info.get("premiered"), "setPremiered"),
            (info.get("year"), "setYear"),
            (info.get("show_title"), "setTvShowTitle")):
        if not value:
            continue
        try:
            getattr(tag, setter)(value)
        except (TypeError, ValueError, AttributeError):
            pass
    if info.get("genres"):
        try:
            tag.setGenres(list(info["genres"]))
        except (TypeError, ValueError, AttributeError):
            pass
    if info.get("studio"):
        try:
            tag.setStudios([info["studio"]])
        except (TypeError, ValueError, AttributeError):
            pass
    if info.get("cast"):
        try:
            tag.setCast([xbmc.Actor(p["name"], p.get("role") or "", order,
                                    (p.get("art") or {}).get("thumb") or "")
                         for order, p in enumerate(info["cast"])])
        except (TypeError, ValueError, AttributeError):
            pass


def write_report_context(playback, duration=None, content_id=None):
    """Leave the service what it needs to report this stream to Apple.

    Playback runs in a different process from this one, so the ids that mint
    a now-playing token are handed over on disk. Written empty when a stream
    cannot be reported, so the service does not report the previous title.
    """
    report = dict(playback.get("report") or {})
    if duration:
        report["duration"] = duration
    if content_id:
        # Also what Continue Watching sends as its playerContentId.
        report["content_id"] = content_id
    if playback.get("adam_id"):
        # A purchase reports to the store's key-value bookkeeper instead of
        # the now-playing service, and is keyed by its store id.
        report["adam_id"] = playback["adam_id"]
    kodiutils.write_json(PLAYBACK_REPORT_CACHE, report)


def do_extras(api, item_id, item_type, kind="trailers"):
    """Context-menu action: pick a trailer or bonus feature and play it.

    Run via RunPlugin rather than as a resolved item, so it works from any
    list without disturbing the item the user is standing on.
    """
    extras = api.get_extras(item_id, item_type, kind)
    if not extras:
        kodiutils.ok_dialog(api.last_error or L(
            "no_trailer" if kind == "trailers" else "no_bonus"))
        return

    index = 0
    if len(extras) > 1:
        index = xbmcgui.Dialog().select(
            L("choose_trailer" if kind == "trailers" else "choose_bonus"),
            [e["label"] for e in extras])
        if index < 0:
            return
    chosen = extras[index]

    playback = api.get_extra_playback(item_id, item_type, chosen["id"])
    if not playback:
        kodiutils.ok_dialog(api.last_error or L("playback_failed"))
        return

    # Trailers and bonus features are not the title itself; reporting them
    # would put the wrong thing in Continue Watching.
    write_report_context({})
    play_item = build_isa_listitem(playback)
    play_item.setLabel(chosen["title"])
    if chosen.get("art"):
        play_item.setArt(chosen["art"])
    tag = play_item.getVideoInfoTag()
    tag.setTitle(chosen["title"])
    tag.setMediaType("video")
    xbmc.Player().play(playback["manifest"], play_item)


def do_subscription(api):
    """Settings action: report the Apple TV+ subscription state."""
    status = api.subscription_status()
    if not status:
        kodiutils.ok_dialog(L("sub_unknown"))
        return
    # Fields Apple actually returns here: status, expireDate (epoch ms),
    # isPurchaser and isFamilySharable.
    tv = status.get("tv") or {}
    expires = tv.get("expireDate")
    lines = [L("sub_active") if expires else L("sub_none")]
    if expires:
        try:
            import time
            lines.append(L("sub_renews") % time.strftime(
                "%d %b %Y", time.localtime(int(expires) / 1000)))
        except (TypeError, ValueError, OverflowError):
            pass
    if tv.get("isFamilySharable"):
        lines.append(L("sub_family"))
    if not tv.get("isPurchaser") and expires:
        # Sharable but not the buyer: the subscription comes from elsewhere.
        lines.append(L("sub_shared_with_you"))
    xbmcgui.Dialog().ok(kodiutils.ADDON_NAME, "\n".join(lines))


def do_watchlist(api, item_id, add):
    """Context-menu action: put a title or event on Up Next, or take it off."""
    if not item_id:
        return
    if api.set_watchlisted(item_id, add):
        kodiutils.notify(L("watchlist_added" if add else "watchlist_removed"))
    else:
        kodiutils.ok_dialog(L("watchlist_failed"))


def do_follow_team(api, team_id, follow):
    """Context-menu action: add or remove a club from Apple's favourites."""
    if not team_id:
        return
    if api.set_team_favourite(team_id, follow):
        kodiutils.notify(L("followed" if follow else "unfollowed"))
    else:
        kodiutils.ok_dialog(L("follow_failed"))


def build_isa_listitem(playback):
    """Wire an Apple HLS+Widevine stream into InputStream Adaptive.

    Widevine key delivery goes through the addon's local licence proxy, which
    wraps the challenge in Apple's JSON envelope (see lib/license_proxy.py).
    """
    is_helper_ok = ensure_widevine()
    item = xbmcgui.ListItem(path=playback["manifest"])

    item.setProperty("inputstream", "inputstream.adaptive")
    item.setMimeType("application/vnd.apple.mpegurl")
    item.setContentLookup(False)

    headers = playback.get("stream_headers") or {}
    if headers:
        header_str = "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in headers.items())
        item.setProperty("inputstream.adaptive.manifest_headers", header_str)
        item.setProperty("inputstream.adaptive.stream_headers", header_str)

    # The DRM object carries options the older individual licence properties
    # could not express: secure_decoder applies to this stream alone, and
    # pre_init_data sets up the decrypter before the first encrypted chapter
    # (Apple leaves the opening chapters clear, which otherwise leaves
    # InputStream Adaptive with no decrypter when encryption starts).
    if not playback.get("encrypted", True):
        # Nothing to decrypt: with no Widevine key in the manifest, a DRM
        # session would only stall waiting for a licence that never comes.
        kodiutils.log("Stream carries no Widevine keys; playing without DRM")
        if not is_helper_ok:
            kodiutils.log_error("Widevine CDM not confirmed present")
        return item

    config = {"priority": 1, "secure_decoder": False, "force_single_session": True}
    license_url = playback.get("license_url")
    if license_url:
        config["license"] = {
            "server_url": license_url,
            "req_headers": "Content-Type=application%2Foctet-stream",
        }
        cert = playback.get("certificate_b64")
        if cert:
            config["license"]["server_certificate"] = cert
    if playback.get("pre_init_data"):
        config["pre_init_data"] = playback["pre_init_data"]
    item.setProperty("inputstream.adaptive.drm",
                     json.dumps({"com.widevine.alpha": config}))
    kodiutils.log("DRM property set (pre_init=%s)" % bool(playback.get("pre_init_data")))

    if not is_helper_ok:
        kodiutils.log_error("Widevine CDM not confirmed present; playback may fail")
    return item


def ensure_widevine():
    """Use inputstreamhelper to install/verify the Widevine CDM if available."""
    try:
        import inputstreamhelper  # noqa: provided by script.module.inputstreamhelper
        helper = inputstreamhelper.Helper("mpd", drm="com.widevine.alpha")
        return helper.check_inputstream()
    except ImportError:
        return True
    except Exception as exc:
        kodiutils.log_error("inputstreamhelper error: %s" % exc)
        return False


# -- router --------------------------------------------------------------

def router(paramstring):
    params = dict(parse_qsl(paramstring))
    action = params.get("action")

    auth = AppleAuth()
    api = AppleTVApi(auth)

    if not action:
        main_menu(auth)
    elif action == "originals":
        show_shelves(api, api.get_originals_shelves())
    elif action == "channel":
        channel_id = params.get("channel_id") or APPLE_TV_PLUS_CHANNEL
        show_shelves(api, api.get_channel_shelves(channel_id),
                     channel_id, channel_id)
    elif action == "room":
        room_id = params.get("room_id")
        brand = params.get("channel_id") or APPLE_TV_PLUS_CHANNEL
        show_shelves(api, api.get_room_shelves(room_id, brand), room_id, brand)
    elif action == "team":
        team_id = params.get("team_id")
        brand = params.get("channel_id") or APPLE_TV_PLUS_CHANNEL
        show_shelves(api, api.get_team_shelves(team_id), team_id, brand)
    elif action == "following":
        brand = params.get("brand") or APPLE_TV_PLUS_CHANNEL
        show_items(api.get_followed_teams(params.get("cache_key")),
                   channel_id=brand)
    elif action == "grandprix":
        gp_id = params.get("gp_id")
        brand = params.get("channel_id") or F1_CHANNEL
        show_shelves(api, api.get_grand_prix_shelves(gp_id, brand), gp_id, brand)
    elif action == "follow_team":
        do_follow_team(api, params.get("team_id"), params.get("on") == "1")
    elif action == "watchlist":
        do_watchlist(api, params.get("item_id"), params.get("on") == "1")
    elif action == "related":
        show_items(api.get_related(params.get("item_id"),
                                   params.get("league") or None))
    elif action == "cast":
        show_people(api.get_cast(params.get("item_id"),
                                 params.get("item_type", "Movie")))
    elif action == "event_extras":
        show_items(api.get_event_extras(params.get("item_id"),
                                        params.get("kind", "highlights")))
    elif action == "clubs":
        show_items(api.get_event_clubs(params.get("item_id")),
                   channel_id=params.get("channel_id") or APPLE_TV_PLUS_CHANNEL)
    elif action == "subscription":
        do_subscription(api)
    elif action == "shelf":
        brand = params.get("brand") or APPLE_TV_PLUS_CHANNEL
        show_items(api.get_shelf_items(params.get("shelf_id"),
                                       params.get("cache_key")),
                   channel_id=brand)
    elif action == "show":
        do_show(api, params.get("show_id"))
    elif action == "season":
        show_id = params.get("show_id")
        show_items(api.get_show_episodes(show_id, season=params.get("season")),
                   content="episodes",
                   cast=api.get_cast(show_id, "Show"))
    elif action == "itunes_sign_in":
        do_itunes_sign_in(auth)
        main_menu(auth)
    elif action == "itunes_sign_out":
        ItunesStore(auth.session).sign_out()
        main_menu(auth)
    elif action == "itunes":
        do_itunes_library(api, auth)
    elif action == "itunes_movies":
        do_itunes_movies(api, auth)
    elif action == "itunes_tv":
        do_itunes_tv(api, auth)
    elif action == "itunes_season":
        do_itunes_season(api, auth, params.get("season_id"))
    elif action == "search":
        do_search(api)
    elif action == "play":
        do_play(api, params.get("item_id"), params.get("item_type", "Movie"))
    elif action == "extras":
        do_extras(api, params.get("item_id"), params.get("item_type", "Movie"),
                  params.get("kind", "trailers"))
    elif action == "sign_in":
        do_sign_in(auth, api)
        main_menu(auth)
    elif action == "sign_out":
        do_sign_out(auth, api)
        main_menu(auth)
    elif action == "debug_play":
        do_play(api, "debug", "Movie")
    else:
        main_menu(auth)


if __name__ == "__main__":
    router(sys.argv[2][1:])
