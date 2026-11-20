"""A24 Screening Room add-on entry point and router."""

import sys
from urllib.parse import urlencode, parse_qsl, quote

import requests
import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils, listing
from lib.auth import A24Auth, A24AuthError, NotReadyError
from lib.api import A24Api, RentalExpiredError

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
_ADDON_PATH = kodiutils.addon().getAddonInfo("path")
FANART = _ADDON_PATH + "/resources/fanart.jpg"

FOLDERS = ("library", "member_free", "store", "search", "extras")

# settings.xml "max_height" enum index -> ISA chooser_resolution_max value.
MAX_HEIGHTS = ["480p", "720p", "1080p", "max"]


def L(string_id):
    return kodiutils.localize(string_id)


def url(**kwargs):
    return BASE_URL + "?" + urlencode(kwargs)


def dev_log(message):
    if kodiutils.get_setting_bool("dev_log"):
        kodiutils.log(message)


# -- listing helpers -------------------------------------------------------

def add_dir(label, action, **params):
    li = xbmcgui.ListItem(label)
    li.setArt({"fanart": FANART})
    xbmcplugin.addDirectoryItem(HANDLE, url(action=action, **params), li, True)


def add_action(label, action, **params):
    li = xbmcgui.ListItem(label)
    li.setArt({"fanart": FANART})
    li.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(HANDLE, url(action=action, **params), li, False)


def show_items(items):
    xbmcplugin.setContent(HANDLE, "movies")
    if not items:
        kodiutils.notify(L(32053))
    for item in items:
        li = listing.build(item, FANART)
        menu = []
        if item.get("has_extras"):
            menu.append((L(32017), "Container.Update(%s)"
                         % url(action="extras", title_id=item["id"])))
        if menu:
            li.addContextMenuItems(menu)
        if listing.playable(item):
            xbmcplugin.addDirectoryItem(HANDLE, url(action="play", title_id=item["id"]),
                                        li, False)
        else:
            # Not entitled: say where to rent instead of failing at play time.
            xbmcplugin.addDirectoryItem(HANDLE, url(action="not_entitled"), li, False)
    xbmcplugin.endOfDirectory(HANDLE)


def main_menu(auth):
    if auth.signed_in:
        add_dir(L(32010), "library")
        add_dir(L(32011), "member_free")
        add_dir(L(32012), "store")
        add_dir(L(32013), "search")
        add_action(L(32016), "account")
        add_action(L(32015), "sign_out")
    else:
        add_action(L(32014), "sign_in")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


# -- account ---------------------------------------------------------------

def do_sign_in(auth):
    # Personal-use scope is stated up front: the stream carries this account.
    kodiutils.ok_dialog(L(32070))
    email = xbmcgui.Dialog().input(L(32020), defaultt=auth.email or "")
    if not email:
        return
    password = kodiutils.input_text(L(32021), hidden=True)
    if not password:
        return
    auth.sign_in(email.strip(), password)
    kodiutils.notify(L(32022) % email.strip())


def do_sign_out(auth):
    auth.sign_out()
    kodiutils.notify(L(32023))


def do_account(auth):
    status = auth.account_status()
    kodiutils.ok_dialog("\n".join("%s: %s" % (k, v) for k, v in status.items() if v))


def after_account_action():
    # A settings action has no directory to end; a menu entry refreshes it.
    if HANDLE >= 0:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    xbmc.executebuiltin("Container.Refresh")


# -- playback --------------------------------------------------------------

def ensure_widevine(manifest_type):
    try:
        import inputstreamhelper  # noqa: script.module.inputstreamhelper
        helper = inputstreamhelper.Helper(manifest_type, drm="com.widevine.alpha")
        return helper.check_inputstream()
    except ImportError:
        return True
    except Exception as exc:
        kodiutils.log_error("inputstreamhelper error: %s" % exc)
        return False


def _header_string(headers):
    """name=value&name=value, values URL-quoted, as ISA expects."""
    return "&".join("%s=%s" % (k, quote(str(v), safe=""))
                    for k, v in (headers or {}).items())


def _parse_headers(text):
    """"Name: value" lines or name=value&name=value -> dict (test playback)."""
    out = {}
    for part in (text or "").replace("\n", "&").split("&"):
        sep = ":" if ":" in part and "=" not in part.split(":", 1)[0] else "="
        name, _, value = part.partition(sep)
        if name.strip():
            out[name.strip()] = value.strip()
    return out


def build_isa_listitem(playback):
    """A ListItem that plays the stream through ISA + Widevine, as served:
    the overlay watermark may be burned into the picture and stays there."""
    li = xbmcgui.ListItem(path=playback["manifest_url"])
    li.setProperty("inputstream", "inputstream.adaptive")
    if playback.get("license_url"):
        li.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        li.setProperty("inputstream.adaptive.license_key", "%s|%s|R{SSM}|" % (
            playback["license_url"], _header_string(playback.get("license_headers"))))
    if playback.get("stream_headers"):
        li.setProperty("inputstream.adaptive.stream_headers",
                       _header_string(playback["stream_headers"]))
    cap = MAX_HEIGHTS[min(kodiutils.get_setting_int("max_height", 3), len(MAX_HEIGHTS) - 1)]
    if cap != "max":
        li.setProperty("inputstream.adaptive.chooser_resolution_max", cap)
        li.setProperty("inputstream.adaptive.chooser_resolution_secure_max", cap)
    if playback.get("subtitles") and kodiutils.get_setting_bool("subtitles", True):
        li.setSubtitles(playback["subtitles"])
    li.setMimeType("application/dash+xml" if playback.get("manifest_type") == "mpd"
                   else "application/vnd.apple.mpegurl")
    li.setContentLookup(False)
    return li


def _resolve(playback):
    if not ensure_widevine(playback.get("manifest_type", "hls")):
        kodiutils.ok_dialog(L(32051))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    dev_log("Playing %s (licence %s)" % (playback["manifest_url"].split("?", 1)[0],
                                        (playback.get("license_url") or "none").split("?", 1)[0]))
    xbmcplugin.setResolvedUrl(HANDLE, True, build_isa_listitem(playback))


def do_play(api, title_id):
    try:
        playback = api.playback(title_id)
    except RentalExpiredError as exc:
        kodiutils.ok_dialog(str(exc))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    _resolve(playback)


def do_test_playback():
    """Advanced > Test playback: plays a manifest and licence URL copied from
    a capture, to prove the ISA/Widevine wiring (row E/F) before the Play
    call is mapped. Runs from settings, so it starts the player itself."""
    manifest = kodiutils.get_setting("manifest_override")
    if not manifest:
        kodiutils.ok_dialog(L(32045))
        return
    playback = {
        "manifest_url": manifest,
        "manifest_type": "mpd" if ".mpd" in manifest.split("?", 1)[0] else "hls",
        "license_url": kodiutils.get_setting("license_override"),
        "license_headers": _parse_headers(kodiutils.get_setting("license_headers_override")),
    }
    if not ensure_widevine(playback["manifest_type"]):
        kodiutils.ok_dialog(L(32051))
        return
    xbmc.Player().play(manifest, build_isa_listitem(playback))


# -- router ----------------------------------------------------------------

def router(paramstring):
    params = dict(parse_qsl(paramstring))
    action = params.get("action")
    auth = A24Auth()
    api = A24Api(auth)
    try:
        if action == "sign_in":
            do_sign_in(auth)
            after_account_action()
        elif action == "sign_out":
            do_sign_out(auth)
            after_account_action()
        elif action == "account":
            do_account(auth)
        elif action == "test_playback":
            do_test_playback()
        elif action in FOLDERS + ("play",) and not auth.signed_in:
            kodiutils.ok_dialog(L(32033))
            if action == "play":
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        elif action == "library":
            show_items(api.library())
        elif action == "member_free":
            show_items(api.member_free())
        elif action == "store":
            show_items(api.store())
        elif action == "extras":
            show_items(api.extras(params.get("title_id")))
        elif action == "search":
            query = params.get("query") or kodiutils.input_text(L(32013))
            if query:
                show_items(api.search(query))
        elif action == "not_entitled":
            kodiutils.ok_dialog(L(32050))
        elif action == "play":
            do_play(api, params.get("title_id"))
        else:
            main_menu(auth)
    except NotReadyError as exc:
        kodiutils.log(str(exc))
        kodiutils.ok_dialog("%s\n\n%s" % (L(32030), exc))
        if action == "play":
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        elif action in FOLDERS and HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    except A24AuthError as exc:
        kodiutils.log_error(str(exc))
        kodiutils.ok_dialog(str(exc))
    except requests.RequestException as exc:
        kodiutils.log_error("Network error: %s" % exc)
        kodiutils.ok_dialog("%s\n\n%s" % (L(32032), exc))


if __name__ == "__main__":
    router(sys.argv[2][1:] if len(sys.argv) > 2 else "")
