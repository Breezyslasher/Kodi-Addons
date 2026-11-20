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


def show_items(items, next_page=None):
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
            xbmcplugin.addDirectoryItem(HANDLE, url(action="play", title_id=item["id"],
                                                    video_id=item.get("video_id") or ""),
                                        li, False)
        else:
            # Not entitled: say where to rent instead of failing at play time.
            xbmcplugin.addDirectoryItem(HANDLE, url(action="not_entitled"), li, False)
    if next_page:
        add_dir(L(32037), next_page[0], **next_page[1])
    xbmcplugin.endOfDirectory(HANDLE)


def main_menu(auth):
    if auth.signed_in:
        add_dir(L(32010), "library")
        # Only AAA24 members have a member title; the folder is not built
        # until a member account's capture is in (recon row C2).
        if kodiutils.get_setting_bool("is_member"):
            add_dir(L(32011), "member_free")
        add_dir(L(32012), "store")
        # A button, not a folder: it asks, then opens the results at a URL
        # that carries the query, so returning from playback (which reloads
        # the folder) shows the results instead of asking again.
        add_action(L(32013), "search")
        add_action(L(32016), "account")
        add_action(L(32015), "sign_out")
    else:
        add_action(L(32014), "sign_in")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def do_search(api, query=None):
    """As plugin.video.starz does: results live at their own URL."""
    if not query:
        # Run as a button (handle -1): ask, then open the results URL.
        query = (kodiutils.input_text(L(32013)) or "").strip()
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
        if query:
            xbmc.executebuiltin("Container.Update(%s)" % url(action="search", query=query))
        return
    xbmcplugin.setPluginCategory(HANDLE, query)
    show_items(api.search(query))


# -- account ---------------------------------------------------------------

def do_sign_in(auth):
    """Email -> A24 emails a code -> code typed here -> session cookie.
    No password is asked for or stored."""
    # Personal-use scope is stated up front: the stream carries this account.
    kodiutils.ok_dialog(L(32070))
    email = xbmcgui.Dialog().input(L(32020), defaultt=auth.email or "")
    email = (email or "").strip()
    if not email:
        return
    auth.request_code(email)
    kodiutils.notify(L(32027))
    code = kodiutils.input_numeric(L(32021))
    if not code:
        return
    account = auth.validate_code(email, code)
    kodiutils.set_setting("is_member", "true" if account.get("current_plan") else "false")
    name = ((account.get("user_profile") or {}).get("first_name") or email)
    kodiutils.notify(L(32022) % name)


def do_sign_out(auth):
    auth.sign_out()
    kodiutils.notify(L(32023))


def do_account(auth):
    if not auth.signed_in:
        kodiutils.ok_dialog(L(32033))
        return
    account = auth.account()
    profile = account.get("user_profile") or {}
    plan = account.get("current_plan")
    kodiutils.set_setting("is_member", "true" if plan else "false")
    if isinstance(plan, dict):
        plan = plan.get("name") or plan.get("title") or plan.get("slug") or L(32028)
    credit = (profile.get("credit") or {}).get("current_balance")
    lines = [
        " ".join(n for n in (profile.get("first_name"), profile.get("last_name")) if n),
        profile.get("email") or auth.email,
        "%s: %s" % (L(32029), plan or L(32034)),
    ]
    if credit:
        lines.append("%s: $%.2f" % (L(32035), float(credit)))
    kodiutils.ok_dialog("\n".join(l for l in lines if l))


def after_account_action():
    # A settings action has no directory to end; a menu entry refreshes it.
    if HANDLE >= 0:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    # Only refresh our own listing: from Settings the current container is
    # something else, and refreshing it logs "Unable to find plugin".
    if xbmc.getInfoLabel("Container.FolderPath").startswith(BASE_URL.rstrip("/")):
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
        # drm_legacy (ISA 21+, our minimum; plugin.video.tubitv uses the same):
        # key system | licence URL | request headers. The raw challenge goes
        # out and the raw licence comes back, as Brightcove's player does.
        li.setProperty("inputstream.adaptive.drm_legacy", "|".join([
            "com.widevine.alpha", playback["license_url"],
            _header_string(playback.get("license_headers"))]))
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


def do_play(api, title_id, video_id=None):
    try:
        playback = api.playback(title_id, video_id or None)
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
            page = int(params.get("page") or 1)
            items, has_next = api.store(page)
            show_items(items, ("store", {"page": page + 1}) if has_next else None)
        elif action == "extras":
            show_items(api.extras(params.get("title_id")))
        elif action == "search":
            do_search(api, params.get("query"))
        elif action == "not_entitled":
            kodiutils.ok_dialog(L(32050))
        elif action == "play":
            do_play(api, params.get("title_id"), params.get("video_id"))
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
