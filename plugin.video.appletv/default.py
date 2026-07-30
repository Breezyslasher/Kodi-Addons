"""Apple TV addon entry point and router."""

import sys
from urllib.parse import urlencode, parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import AppleAuth, STATUS_OK, STATUS_NEEDS_2FA, STATUS_ERROR
from lib.api import AppleTVApi

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
}


def L(key):
    return kodiutils.localize(S[key])


def url(**kwargs):
    return "%s?%s" % (BASE_URL, urlencode(kwargs))


def add_dir(label, action, art=None, **params):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt({"icon": art, "thumb": art, "poster": art})
    xbmcplugin.addDirectoryItem(
        HANDLE, url(action=action, **params), item, isFolder=True
    )


def add_playable(entry):
    item = xbmcgui.ListItem(label=entry["title"])
    if entry.get("art"):
        item.setArt({"thumb": entry["art"], "poster": entry["art"], "fanart": entry["art"]})
    tag = item.getVideoInfoTag()
    tag.setTitle(entry["title"])
    tag.setMediaType("movie" if str(entry.get("type", "")).lower() == "movie" else "tvshow")
    if entry.get("plot"):
        tag.setPlot(entry["plot"])
    if entry.get("year"):
        try:
            tag.setYear(int(entry["year"]))
        except (TypeError, ValueError):
            pass
    item.setProperty("IsPlayable", "true")
    xbmcplugin.addDirectoryItem(
        HANDLE,
        url(action="play", item_id=entry["id"], item_type=entry.get("type", "Movie")),
        item,
        isFolder=False,
    )


# -- menus ---------------------------------------------------------------

def main_menu(auth):
    add_dir(L("originals"), "originals")
    add_dir(L("movies"), "movies")
    if auth.is_authenticated():
        add_dir(L("itunes_library"), "itunes_library")
    add_dir(L("search"), "search")
    if auth.is_authenticated():
        add_dir(L("sign_out"), "sign_out")
    else:
        add_dir(L("sign_in"), "sign_in")
    xbmcplugin.endOfDirectory(HANDLE)


def show_shelves(api, shelves):
    if not shelves:
        kodiutils.notify(L("no_results"))
    for shelf in shelves:
        if shelf.get("items"):
            add_dir("%s (%d)" % (shelf["title"], len(shelf["items"])),
                    "shelf", shelf_id=shelf["id"], title=shelf["title"])
    xbmcplugin.endOfDirectory(HANDLE)


def show_items(items):
    if not items:
        kodiutils.notify(L("no_results"))
    for entry in items:
        add_playable(entry)
    xbmcplugin.setContent(HANDLE, "movies")
    xbmcplugin.endOfDirectory(HANDLE)


# -- actions -------------------------------------------------------------

def do_sign_in(auth):
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
        kodiutils.ok_dialog(L("playback_failed"))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    kodiutils.notify(L("sd_notice"))
    play_item = build_isa_listitem(playback)
    xbmcplugin.setResolvedUrl(HANDLE, True, play_item)


def build_isa_listitem(playback):
    """Wire a stream into InputStream Adaptive with Widevine."""
    is_helper_ok = ensure_widevine()
    item = xbmcgui.ListItem(path=playback["manifest"])
    manifest_type = playback.get("manifest_type", "hls")

    item.setProperty("inputstream", "inputstream.adaptive")
    # Kodi 21+/22 uses manifest_type; keep the legacy key for older builds too.
    item.setProperty("inputstream.adaptive.manifest_type", manifest_type)
    item.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")

    license_url = playback.get("license_url")
    if license_url:
        headers = playback.get("license_headers") or {}
        header_str = "&".join("%s=%s" % (k, v) for k, v in headers.items())
        # ISA license_key format: url|headers|postdata(B{SSM})|response
        license_key = "%s|%s|R{SSM}|" % (license_url, header_str)
        item.setProperty("inputstream.adaptive.license_key", license_key)

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
    elif action == "movies":
        show_shelves(api, api.get_canvas("MoviesGenre"))
    elif action == "shelf":
        show_items(api.get_shelf_items(params.get("shelf_id")))
    elif action == "itunes_library":
        if not auth.is_authenticated():
            kodiutils.ok_dialog(L("sign_in_required"))
            return
        show_items(api.get_itunes_library())
    elif action == "search":
        do_search(api)
    elif action == "play":
        do_play(api, params.get("item_id"), params.get("item_type", "Movie"))
    elif action == "sign_in":
        do_sign_in(auth)
    elif action == "sign_out":
        do_sign_out(auth)
    else:
        main_menu(auth)


if __name__ == "__main__":
    router(sys.argv[2][1:])
