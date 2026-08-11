"""Apple TV addon entry point and router."""

import json
import sys
from urllib.parse import urlencode, parse_qsl, quote

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import AppleAuth, STATUS_OK, STATUS_NEEDS_2FA
from lib.api import (AppleTVApi, CHANNELS, APPLE_TV_PLUS_CHANNEL, F1_CHANNEL,
                     MLB_ROOM, PLAYBACK_REPORT_CACHE)

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

# String ids (see resources/language/.../strings.po).
S = {
    "originals": 32010,
    "movies": 32011,
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
    "about": 32090,
    "mark_watched": 32087,
    "watched_marked": 32088,
    "watched_failed": 32089,
    "sub_active": 32052,
    "sub_none": 32053,
    "sub_renews": 32054,
    "sub_family": 32055,
    "sub_unknown": 32056,
    "sub_shared_with_you": 32058,
    "search_suggestions": 32061,
    "choose_feed": 32062,
    "play_feed": 32063,
    "live_options": 32091,
    "watch_live": 32092,
    "watch_from_start": 32093,
    "catch_up": 32094,
    "key_plays": 32095,
    "no_key_plays": 32096,
    "resume_live": 32098,
    "related": 32064,
    "clubs": 32065,
    "highlights": 32066,
    "spotlight": 32067,
    "race_weekend": 32068,
    "cast": 32069,
    "featured": 32082,
    "continue_watching": 32060,
    "itunes_library": 32012,
    "films": 32077,
    "tv_shows": 32078,
    "your_films": 32107,
    "your_tv_shows": 32108,
    "shared_by": 32104,
    "season_n": 32105,
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
    ] + watchlist_menu_items(item_id) + [mark_watched_menu_item(item_id)])


def mark_watched_menu_item(item_id):
    """Mark a title watched on the Apple account (POST /play-history).

    Kodi's own "Mark as watched" only sets its local flag; this tells Apple, so
    the title counts as watched on your other devices and leaves Continue
    Watching. Marking unwatched is not offered -- it was not captured.
    """
    return (L("mark_watched"), "RunPlugin(%s)" % url(
        action="mark_watched", item_id=item_id))


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
        try:
            tag.setPlot(entry["plot"])
        except (TypeError, ValueError, AttributeError):
            pass
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
    # resumed here at the point another Apple client left it. A game airing
    # live is the exception: its "resume" is a live position that Kodi would
    # turn into a fixed seek, overriding (and so silently ignoring) the Watch
    # Live / from Start / Catch Up / Resume choice, so it carries no Kodi resume
    # point -- the menu owns where it starts. A finished game is an ordinary
    # replay and resumes like a film.
    live_sport = kind == "SportingEvent" and entry.get("live")
    resume = entry.get("resume") or {}
    if not live_sport and resume.get("position") and resume.get("total"):
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
        # A match links to the other games in its league, and to its two clubs.
        sport = entry.get("sport")
        item.addContextMenuItems([
            (L("related"), "Container.Update(%s)" % url(
                action="related", item_id=entry["id"],
                league=entry.get("league_id") or "")),
        ]
            # Clubs are the two sides of a match -- a Soccer idea (MLS, Leagues
            # Cup). A Motorsports race and a Baseball game are not club-vs-club,
            # so the entry is offered for Soccer only rather than on every sport.
          + ([(L("clubs"), "Container.Update(%s)" % url(
                action="clubs", item_id=entry["id"]))]
             if sport == "Soccer" else [])
            # Key Plays are a live game's moments so far, to catch up on the
            # broadcast while it airs. A finished game has Highlights instead,
            # so Key Plays is offered only while the game is live.
          + ([(L("key_plays"), "Container.Update(%s)" % url(
                action="key_plays", item_id=entry["id"]))]
             if entry.get("live") else [])
          + [
            (L("highlights"), "Container.Update(%s)" % url(
                action="event_extras", kind="highlights", item_id=entry["id"])),
            (L("spotlight"), "Container.Update(%s)" % url(
                action="event_extras", kind="spotlight", item_id=entry["id"])),
        ] + ([(L("race_weekend"), "Container.Update(%s)" % url(
                action="event_extras", kind="weekend", item_id=entry["id"]))]
             # Only Motorsports fixtures have a weekend of sessions.
             if sport == "Motorsports" else [])
          + watchlist_menu_items(entry["id"])
          + [mark_watched_menu_item(entry["id"])])
    elif kind == "Episode":
        # An episode takes no watchlist (Apple lists films, shows and fixtures
        # only), but it can be marked watched on the account.
        item.addContextMenuItems([mark_watched_menu_item(entry["id"])])
    # Everything else -- the sports clip types that carry their stream inline --
    # gets no watchlist entry: Apple's watchlist takes films, shows and fixtures
    # only, which is what every captured write sends, so offering it on an
    # episode was offering something that cannot work.
    play_params = {"item_id": entry["id"], "item_type": entry.get("type", "Movie")}
    # A sporting event listed with its own feed (Continue Watching gives one)
    # plays that feed directly, so resuming does not re-open the feed picker.
    if entry.get("external_id"):
        play_params["external_id"] = entry["external_id"]
    # A live game's saved position rides along so the play menu can offer it as
    # an explicit "Resume" -- a live game carries no Kodi resume point (it would
    # seek over the menu choice), so this is the only route left for it. A
    # finished game keeps its Kodi resume point, so it needs no such hand-off.
    if live_sport and (entry.get("resume") or {}).get("position"):
        play_params["resume_pos"] = int(entry["resume"]["position"])
    xbmcplugin.addDirectoryItem(
        HANDLE,
        url(action="play", **play_params),
        item,
        isFolder=False,
    )


# -- menus ---------------------------------------------------------------

def main_menu(auth):
    # The featured Apple Originals hero shelf, as the TV app leads with it.
    add_dir(L("featured"), "featured")
    # One entry per brand tab along the top of tv.apple.com's home page.
    for channel_id, name in CHANNELS:
        label = L("originals") if channel_id == APPLE_TV_PLUS_CHANNEL else name
        add_dir(label, "channel", channel_id=channel_id)
    # MLB rides on Apple TV+ as an editorial room rather than a brand channel,
    # so it is listed here as a room instead of a CHANNELS entry.
    add_dir("MLB", "room", room_id=MLB_ROOM, channel_id=APPLE_TV_PLUS_CHANNEL)
    # The account-wide resume row: personalised, so only when signed in to Apple
    # TV+. Unlike the per-tab Up Next, it mixes in iTunes films in progress.
    if auth.is_authenticated():
        add_dir(L("continue_watching"), "continue_watching")
    # The iTunes library (and purchase playback) now works off the ordinary
    # Apple TV+ sign-in -- listing via the MediaAPI, the redownload offer via
    # the store caller's dev token, and licensing with the bearer +
    # media-user-token. No separate store login is required any more.
    if auth.is_authenticated():
        add_dir(L("itunes_library"), "itunes")
    add_dir(L("search"), "search")
    if kodiutils.get_setting("manifest_url_override"):
        # Not a folder. do_play answers with setResolvedUrl, which Kodi only
        # honours for a playable item; as a folder it opened a directory that
        # never ended, which is the "Error getting plugin://" in the log.
        debug = xbmcgui.ListItem(label="[Debug] Test playback (manifest override)")
        debug.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(HANDLE, url(action="debug_play"), debug,
                                    isFolder=False)
    if auth.is_authenticated():
        add_dir(L("sign_out"), "sign_out")
    else:
        add_dir(L("sign_in"), "sign_in")
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
    if kind == "Show" and entry.get("itunes"):
        # An owned show opens to the episodes you own (via the MediaAPI
        # tv-episodes filter), not the whole catalogue show.
        add_dir(entry["title"], "itunes_show", art=entry.get("art"),
                info=entry, media_type="tvshow",
                show_id=entry.get("adam_id") or entry["id"],
                member_id=entry.get("member_id") or "")
    elif kind == "Show":
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
    """List a title's cast and crew; each opens the person's own page."""
    if not people:
        kodiutils.notify(L("no_results"))
    for person in people:
        label = person["name"]
        if person.get("role"):
            label = "%s - %s" % (label, person["role"])
        entry = xbmcgui.ListItem(label=label)
        if person.get("art"):
            entry.setArt(person["art"])
        # A person Apple gives an id opens their own page -- their other films
        # and shows. One without (rare) stays a plain, unopenable credit.
        pid = person.get("id")
        if pid:
            xbmcplugin.addDirectoryItem(
                HANDLE, url(action="person", person_id=pid, name=person["name"]),
                entry, isFolder=True)
        else:
            xbmcplugin.addDirectoryItem(HANDLE, "", entry, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def show_person(api, person_id, name=""):
    """A person's own page: their details, then their films and shows."""
    if not person_id:
        return
    info = api.get_person_info(person_id)
    title = info.get("name") or name
    if title:
        xbmcplugin.setPluginCategory(HANDLE, title)
    # A first "About" entry carries the bio, birth and headshot, so the
    # Information button on it shows what Apple holds on the person.
    if info.get("plot"):
        about = xbmcgui.ListItem(label=L("about") % (title or name))
        tag = about.getVideoInfoTag()
        tag.setPlot(info["plot"])
        tag.setTitle(title or name)
        if info.get("art"):
            about.setArt(info["art"])
        xbmcplugin.addDirectoryItem(HANDLE, "", about, isFolder=False)
    show_shelves(api, api.get_person_shelves(person_id), cache_key=person_id)


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
        # Remember the Apple ID email: the account's own entry in the Family
        # Sharing list carries it as accountName, so this is how we recognise
        # (and hide) yourself there -- the API marks no self member otherwise.
        try:
            kodiutils.set_setting("account_email", account)
        except Exception:
            pass
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


def _combined_library():
    """Setting: fold every family member's shared purchases into one Films/TV
    Shows library, rather than a folder per member."""
    return kodiutils.get_setting_bool("itunes_library_combined", False)


def _library_purchases(api, kind):
    """Owned titles of one kind, plus -- when the combined-library setting is on
    -- every sharing family member's, merged and de-duplicated by id."""
    items = api.media_purchases(kind)
    if _combined_library():
        seen = {it.get("id") for it in items}
        for member in api.media_family_members():
            for it in api.media_purchases(kind, family_member=member["id"]):
                if it.get("id") not in seen:
                    seen.add(it.get("id"))
                    items.append(it)
    return items


def _has_purchases(api, kind, member_id=None):
    """Cheap one-page probe: is there any title of this kind (yours, or a family
    member's when member_id is given)? Skips the reverse-lookup enrichment the
    full listing does, so it is only used to decide folder visibility."""
    return bool(api.media_purchases(kind, family_member=member_id,
                                    max_pages=1, enrich=False))


def _library_has(api, kind, members=None):
    """Would the Films/TV Shows folder have anything in it? In combined mode a
    sharing member's copy counts too, so the folder is not hidden when only they
    own the kind."""
    if _has_purchases(api, kind):
        return True
    if _combined_library():
        for member in (members if members is not None
                       else api.media_family_members()):
            if _has_purchases(api, kind, member["id"]):
                return True
    return False


def _member_shares_anything(api, member_id):
    """Whether a family member shares any film or show with this account, so an
    empty 'Shared by' folder is not shown."""
    return (_has_purchases(api, "movie", member_id)
            or _has_purchases(api, "tv", member_id))


def do_itunes_library(api, auth):
    """The library's sections. Combined, Films and TV Shows list everyone's
    shared titles together. Otherwise they are your own, labelled "Your Films"/
    "Your TV Shows" to set them apart from a folder per family member who shares
    purchases (the app's Family Sharing view), with your own entry hidden."""
    combined = _combined_library()
    members = api.media_family_members()
    # Only show a Films / TV Shows folder when there is something in it, so an
    # account that owns only films (or only shows) is not given an empty folder.
    if _library_has(api, "movie", members):
        add_dir(L("films") if combined else L("your_films"), "itunes_movies")
    if _library_has(api, "tv", members):
        add_dir(L("tv_shows") if combined else L("your_tv_shows"), "itunes_tv")
    if not combined:
        for member in members:
            # Skip a member who shares nothing you can see, so their folder does
            # not open onto an empty library.
            if _member_shares_anything(api, member["id"]):
                add_dir(L("shared_by") % member["name"], "itunes_family",
                        member_id=member["id"])
    xbmcplugin.endOfDirectory(HANDLE)


def do_itunes_family(api, member_id):
    """A family member's shared library: Films and TV Shows, kept apart like
    your own library -- and each shown only when they share that kind."""
    if _has_purchases(api, "movie", member_id):
        add_dir(L("films"), "itunes_family_movies", member_id=member_id)
    if _has_purchases(api, "tv", member_id):
        add_dir(L("tv_shows"), "itunes_family_tv", member_id=member_id)
    xbmcplugin.endOfDirectory(HANDLE)


def do_itunes_family_movies(api, member_id):
    show_items(api.media_purchases("movie", family_member=member_id))


def do_itunes_family_tv(api, member_id):
    show_items(api.media_purchases("tv", family_member=member_id),
               content="tvshows")


def do_itunes_show(api, show_id, member_id=None, season=None):
    """The episodes of an owned show that the account (or a family member)
    actually owns, via the MediaAPI tv-episodes filter.

    A show with more than one owned season opens to a folder per season; a
    single-season show (or a chosen season) lists its episodes directly.
    """
    episodes = api.media_purchases("episodes", show_id=show_id,
                                   family_member=member_id)
    seasons = sorted({e.get("season") for e in episodes
                      if e.get("season") is not None})
    if season is None and len(seasons) > 1:
        for s in seasons:
            count = sum(1 for e in episodes if e.get("season") == s)
            add_dir(L("season_n") % s, "itunes_show", media_type="season",
                    show_id=show_id, member_id=member_id or "", season=str(s))
        xbmcplugin.setContent(HANDLE, "seasons")
        xbmcplugin.endOfDirectory(HANDLE)
        return
    if season is not None:
        episodes = [e for e in episodes if str(e.get("season")) == str(season)]
    show_items(episodes, content="episodes")


def do_itunes_movies(api, auth):
    """Films the account owns, from the MediaAPI /v1/me/purchases route (the
    Apple TV app's own Library call) -- the ordinary Apple TV+ bearer +
    media-user-token, no store session."""
    items = _library_purchases(api, "movie")
    if not items:
        kodiutils.notify(L("no_results"))
    show_items(items)


def do_itunes_tv(api, auth):
    """Owned television as a flat list of shows, from the MediaAPI route -- the
    ordinary Apple TV+ session, no store session. An owned show opens to the
    episodes you own via do_itunes_show."""
    shows = _library_purchases(api, "tv")
    if not shows:
        kodiutils.notify(L("no_results"))
    show_items(shows, content="tvshows")


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


def show_key_plays(api, item_id, item_type, external_id=None):
    """List a live game's key moments; each plays the game jumped to it."""
    plays = api.get_key_plays(item_id, item_type, external_id)
    if not plays:
        kodiutils.notify(L("no_key_plays"))
        xbmcplugin.endOfDirectory(HANDLE)
        return
    for kp in plays:
        item = xbmcgui.ListItem(label=kp["title"])
        if kp.get("art"):
            item.setArt(kp["art"])
        tag = item.getVideoInfoTag()
        tag.setMediaType("video")
        tag.setTitle(kp["title"])
        tag.setPlot(kp.get("plot") or "")
        item.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(
            HANDLE,
            url(action="play", item_id=item_id, item_type=item_type,
                external_id=external_id or "",
                kp_start=kp["start_time"], kp_end=kp.get("end_time") or ""),
            item, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def pick_feed(feeds):
    """Ask which feed to play; returns an external_id, or False if cancelled.

    A game is published as a full replay beside a short recap, and once per
    commentary language, so picking the first would be arbitrary.
    """
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
        return False
    return feeds[index]["external_id"]


def pick_live_mode(resume_seconds=None):
    """How to watch a live game: live edge, from the start, catch up, or resume.

    Returns "live", "start", "catchup", "resume", or None if cancelled. Catch
    Up plays the key moments in turn (Apple's own is the start-over stream
    skipped through the key plays). Resume is offered only when Apple reports a
    saved position for the game, and jumps the start-over stream to it -- the
    seek Kodi used to do on its own, now a choice rather than an override.
    """
    labels, modes = [], []
    if resume_seconds:
        labels.append(L("resume_live"))
        modes.append("resume")
    labels += [L("watch_live"), L("watch_from_start"), L("catch_up")]
    modes += ["live", "start", "catchup"]
    index = xbmcgui.Dialog().select(L("live_options"), labels)
    if index < 0:
        return None
    return modes[index]


def do_play(api, item_id, item_type, external_id=None, kp_start=None, kp_end=None,
            resume_pos=None):
    # An event carried in with its own feed (Continue Watching) plays that
    # feed directly. Only when the feed is not already known is the picker
    # offered, and only for an event that has more than one.
    start_over = False
    seek_plays = None
    seek_seconds = None
    is_live = False
    if kp_start:
        # A pick from the Key Plays list: play the game jumped to that moment.
        seek_plays = [{"start_time": int(kp_start),
                       "end_time": int(kp_end) if kp_end else None}]
    elif str(item_type) == "SportingEvent":
        options = api.list_playables(item_id, item_type)
        feeds = options.get("feeds") or []
        # The feed picker is only for choosing between commentaries, so it is
        # skipped when the feed is already known (Continue Watching binds one).
        # The live-mode choice below is offered regardless: a game resumed from
        # Continue Watching is still live, and "where to start" is Watch Live /
        # from Start / Catch Up / Resume, not the fixed resume point Apple
        # happened to report -- which is why picking a mode used to be ignored.
        if not external_id and len(feeds) > 1:
            external_id = pick_feed(feeds)
            if external_id is False:
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
        # A game airing live can be joined at the live edge, watched from the
        # start, or caught up on; a finished or upcoming one has no such choice.
        if options.get("live"):
            is_live = True
            resume_seconds = int(resume_pos) if resume_pos else None
            mode = pick_live_mode(resume_seconds=resume_seconds)
            if mode is None:
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
            if mode == "start":
                start_over = True
            elif mode == "resume":
                # Where you left off: the start-over stream sought to Apple's
                # saved position for the game (seconds from the broadcast start).
                start_over = True
                seek_seconds = resume_seconds
            elif mode == "catchup":
                seek_plays = api.get_key_plays(item_id, item_type, external_id)
                if not seek_plays:
                    # Nothing has happened yet: watch from the start instead.
                    kodiutils.notify(L("no_key_plays"))
                    start_over = True
    playback = api.get_playback(item_id, item_type, external_id,
                                start_over=start_over, seek_plays=seek_plays,
                                seek_seconds=seek_seconds)
    if not playback:
        kodiutils.ok_dialog(api.last_error or L("playback_failed"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    kodiutils.notify(L("sd_notice"))
    write_report_context(playback, content_id=item_id)
    play_item = build_isa_listitem(playback)
    if is_live:
        # A live game starts where the menu said (live edge, from start, catch
        # up, or the Resume seek above) -- never where Kodi last left it. Kodi
        # otherwise resumes a sporting event from its own stored bookmark for
        # this path, seeking on top of the choice; resumetime 0 tells it not to.
        # A finished game is left alone, so it resumes like any on-demand title.
        play_item.setProperty("resumetime", "0")
    # Playback resolves from an id, so the item Kodi shows while playing knew
    # nothing about the title and its plot read "Not available". A title's own
    # page carries the description and the cast, which shelf items do not.
    # A pasted manifest has no catalogue id behind it, so asking would only
    # log Apple refusing "debug" as an invalid id.
    if not playback.get("override"):
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
        # The Watchlist list is fetched fresh from Apple, so reloading the
        # current screen shows the change without leaving the tab first.
        xbmc.executebuiltin("Container.Refresh")
    else:
        kodiutils.ok_dialog(L("watchlist_failed"))


def do_mark_watched(api, item_id):
    """Context-menu action: mark a title watched on the Apple account."""
    if not item_id:
        return
    if api.set_watched(item_id):
        kodiutils.notify(L("watched_marked"))
        # Marking watched removes the title from Apple's Continue Watching, so
        # reload the screen to reflect it without leaving the tab first.
        xbmc.executebuiltin("Container.Refresh")
    else:
        kodiutils.ok_dialog(L("watched_failed"))


def do_follow_team(api, team_id, follow):
    """Context-menu action: add or remove a club from Apple's favourites."""
    if not team_id:
        return
    if api.set_team_favourite(team_id, follow):
        # The Following folder and the club tiles read the follow state from
        # the cached canvas, so patch it before refreshing the screen -- else
        # the change would not show until the tab was fetched again.
        api.mark_cached_favourite(team_id, follow)
        kodiutils.notify(L("followed" if follow else "unfollowed"))
        xbmc.executebuiltin("Container.Refresh")
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

    # Apple's WebVTT subtitles, fetched to files because ISA lists but does not
    # render them. Given to Kodi as external subtitles so its own renderer shows
    # them. Empty for a live event (its captions are CEA-608 inside the video).
    subs = playback.get("subtitles")
    if subs:
        try:
            item.setSubtitles(subs)
        except Exception as exc:
            kodiutils.log_error("Could not attach subtitles: %s" % exc)

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
    elif action == "grandprix":
        gp_id = params.get("gp_id")
        brand = params.get("channel_id") or F1_CHANNEL
        show_shelves(api, api.get_grand_prix_shelves(gp_id, brand), gp_id, brand)
    elif action == "follow_team":
        do_follow_team(api, params.get("team_id"), params.get("on") == "1")
    elif action == "watchlist":
        do_watchlist(api, params.get("item_id"), params.get("on") == "1")
    elif action == "mark_watched":
        do_mark_watched(api, params.get("item_id"))
    elif action == "related":
        show_items(api.get_related(params.get("item_id"),
                                   params.get("league") or None))
    elif action == "cast":
        show_people(api.get_cast(params.get("item_id"),
                                 params.get("item_type", "Movie")))
    elif action == "person":
        show_person(api, params.get("person_id"), params.get("name") or "")
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
    elif action == "featured":
        show_items(api.get_featured())
    elif action == "continue_watching":
        show_items(api.get_continue_watching())
    elif action == "itunes":
        do_itunes_library(api, auth)
    elif action == "itunes_movies":
        do_itunes_movies(api, auth)
    elif action == "itunes_tv":
        do_itunes_tv(api, auth)
    elif action == "itunes_family":
        do_itunes_family(api, params.get("member_id"))
    elif action == "itunes_family_movies":
        do_itunes_family_movies(api, params.get("member_id"))
    elif action == "itunes_family_tv":
        do_itunes_family_tv(api, params.get("member_id"))
    elif action == "itunes_show":
        do_itunes_show(api, params.get("show_id"), params.get("member_id"),
                       params.get("season"))
    elif action == "search":
        do_search(api)
    elif action == "play":
        do_play(api, params.get("item_id"), params.get("item_type", "Movie"),
                params.get("external_id"),
                kp_start=params.get("kp_start"), kp_end=params.get("kp_end"),
                resume_pos=params.get("resume_pos"))
    elif action == "key_plays":
        show_key_plays(api, params.get("item_id"),
                       params.get("item_type", "SportingEvent"),
                       params.get("external_id") or None)
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
