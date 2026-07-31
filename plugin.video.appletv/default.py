"""Apple TV addon entry point and router."""

import json
import sys
from urllib.parse import urlencode, parse_qsl, quote

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import AppleAuth, STATUS_OK, STATUS_NEEDS_2FA, STATUS_ERROR
from lib.api import AppleTVApi, CHANNELS, APPLE_TV_PLUS_CHANNEL

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
    ])


def add_dir(label, action, art=None, extras_for=None, **params):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if extras_for:
        extras_context_menu(item, extras_for[0], extras_for[1])
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action=action, **params), item, isFolder=True
    )


def add_playable(entry):
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
    if entry.get("duration"):
        try:
            tag.setDuration(int(entry["duration"]))
        except (TypeError, ValueError):
            pass
    item.setProperty("IsPlayable", "true")
    # Episodes and sporting events carry no extras shelves of their own.
    if kind in ("Movie", "Show", "Vod", "MovieBundle"):
        extras_context_menu(item, entry["id"], kind)
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
    add_dir(L("search"), "search")
    if kodiutils.get_setting("manifest_url_override"):
        add_dir("[Debug] Test playback (manifest override)", "debug_play")
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


def add_item(entry, channel_id=APPLE_TV_PLUS_CHANNEL):
    """Add a catalogue entry: shows and rooms are folders, the rest play."""
    kind = str(entry.get("type"))
    if kind == "Show":
        add_dir(entry["title"], "show", art=entry.get("art"),
                extras_for=(entry["id"], "Show"), show_id=entry["id"])
    elif kind == "Room":
        # A room is a browse category (Kids & Family, Sci-Fi, ...) with a
        # canvas of shelves behind it.
        add_dir(entry["title"], "room", art=entry.get("art"),
                room_id=entry["id"], channel_id=channel_id)
    else:
        add_playable(entry)


def show_items(items, content="movies", channel_id=APPLE_TV_PLUS_CHANNEL):
    if not items:
        kodiutils.notify(L("no_results"))
    for entry in items:
        add_item(entry, channel_id)
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


def do_sign_out(auth):
    if xbmcgui.Dialog().yesno(kodiutils.ADDON_NAME, L("confirm_sign_out")):
        auth.clear()
        kodiutils.notify(L("sign_out"))


def do_search(api):
    query = kodiutils.input_text(L("search_heading"))
    if not query:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    show_items(api.search(query))


def do_play(api, item_id, item_type):
    playback = api.get_playback(item_id, item_type)
    if not playback:
        kodiutils.ok_dialog(api.last_error or L("playback_failed"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    kodiutils.notify(L("sd_notice"))
    play_item = build_isa_listitem(playback)
    xbmcplugin.setResolvedUrl(HANDLE, True, play_item)


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

    play_item = build_isa_listitem(playback)
    play_item.setLabel(chosen["title"])
    if chosen.get("art"):
        play_item.setArt(chosen["art"])
    tag = play_item.getVideoInfoTag()
    tag.setTitle(chosen["title"])
    tag.setMediaType("video")
    xbmc.Player().play(playback["manifest"], play_item)


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
    elif action == "shelf":
        brand = params.get("brand") or APPLE_TV_PLUS_CHANNEL
        show_items(api.get_shelf_items(params.get("shelf_id"),
                                       params.get("cache_key")),
                   channel_id=brand)
    elif action == "show":
        show_items(api.get_show_episodes(params.get("show_id")), content="episodes")
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
        do_sign_out(auth)
        main_menu(auth)
    elif action == "debug_play":
        do_play(api, "debug", "Movie")
    else:
        main_menu(auth)


if __name__ == "__main__":
    router(sys.argv[2][1:])
