"""Apple TV addon entry point and router."""

import json
import sys
from urllib.parse import urlencode, parse_qsl, quote

import xbmc
import xbmcaddon
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
    xbmcplugin.addDirectoryItem(
        HANDLE,
        url(action="play", item_id=entry["id"], item_type=entry.get("type", "Movie")),
        item,
        isFolder=False,
    )


# -- menus ---------------------------------------------------------------

def main_menu(auth):
    add_dir(L("originals"), "originals")
    add_dir(L("search"), "search")
    if kodiutils.get_setting("manifest_url_override"):
        add_dir("[Debug] Test playback (manifest override)", "debug_play")
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


def add_item(entry):
    """Add a catalogue entry: shows become folders, everything else plays."""
    if str(entry.get("type")) == "Show":
        add_dir(entry["title"], "show", art=entry.get("art"), show_id=entry["id"])
    else:
        add_playable(entry)


def show_items(items, content="movies"):
    if not items:
        kodiutils.notify(L("no_results"))
    for entry in items:
        add_item(entry)
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


def build_isa_listitem(playback):
    """Wire an Apple HLS+Widevine stream into InputStream Adaptive.

    Widevine key delivery goes through the addon's local licence proxy, which
    wraps the challenge in Apple's JSON envelope (see lib/license_proxy.py).
    """
    configure_inputstream()
    is_helper_ok = ensure_widevine()
    item = xbmcgui.ListItem(path=playback["manifest"])
    manifest_type = playback.get("manifest_type", "hls")

    item.setProperty("inputstream", "inputstream.adaptive")
    item.setMimeType("application/vnd.apple.mpegurl")
    item.setContentLookup(False)

    isa_major = inputstream_major_version()
    if isa_major < 22:
        # Removed in Kodi 22; the mime type is enough for newer ISA.
        item.setProperty("inputstream.adaptive.manifest_type", manifest_type)

    headers = playback.get("stream_headers") or {}
    if headers:
        header_str = "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in headers.items())
        item.setProperty("inputstream.adaptive.manifest_headers", header_str)
        item.setProperty("inputstream.adaptive.stream_headers", header_str)

    cert = playback.get("certificate_b64")
    license_url = playback.get("license_url")

    if isa_major >= 22:
        # ISA 22 replaced the individual licence properties with one DRM object,
        # which also carries options the old properties had no way to express:
        # secure_decoder overrides the user setting for this stream only, and
        # pre_init_data sets up the decrypter before the first encrypted chapter
        # (Apple leaves the opening chapters clear, which otherwise leaves ISA
        # with no decrypter when encryption starts).
        config = {"priority": 1, "secure_decoder": False, "force_single_session": True}
        if license_url:
            config["license"] = {
                "server_url": license_url,
                "req_headers": "Content-Type=application%2Foctet-stream",
            }
            if cert:
                config["license"]["server_certificate"] = cert
        if playback.get("pre_init_data"):
            config["pre_init_data"] = playback["pre_init_data"]
        item.setProperty("inputstream.adaptive.drm",
                         json.dumps({"com.widevine.alpha": config}))
        kodiutils.log("ISA %d: using drm property (secure_decoder=false, pre_init=%s)"
                      % (isa_major, bool(playback.get("pre_init_data"))))
    else:
        item.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        if cert:
            item.setProperty("inputstream.adaptive.server_certificate", cert)
        if license_url:
            # ISA posts the raw challenge to our proxy; the proxy returns the raw
            # licence. Format: url|request_headers|request_data|response_data
            license_key = "%s|Content-Type=application/octet-stream|R{SSM}|" % license_url
            item.setProperty("inputstream.adaptive.license_key", license_key)

    if not is_helper_ok:
        kodiutils.log_error("Widevine CDM not confirmed present; playback may fail")
    return item


def inputstream_major_version():
    """Major version of the installed InputStream Adaptive, or 0 if unknown."""
    try:
        isa = xbmcaddon.Addon("inputstream.adaptive")
        try:
            return int(isa.getAddonInfo("version").split(".")[0])
        finally:
            del isa
    except Exception:
        return 0


def configure_inputstream():
    """Ask InputStream Adaptive not to use the CDM's own video decoder.

    When a test decryption fails, ISA flags the stream SECURE_PATH and decodes
    video inside the CDM (VideoCodec::Open / DecryptAndDecodeVideo), which is
    where Apple's video fails with kNoKey while audio -- decrypted the ordinary
    way and decoded by FFmpeg -- succeeds. ISA checks its NOSECUREDECODER
    setting on exactly that branch, so enabling it keeps video on the
    decrypt-only path. Other Widevine addons set this the same way for
    software (L3) Widevine.
    """
    if not kodiutils.get_setting_bool("disable_secure_decoder", False):
        return
    try:
        isa = xbmcaddon.Addon("inputstream.adaptive")
        try:
            if isa.getSetting("NOSECUREDECODER") != "true":
                isa.setSetting("NOSECUREDECODER", "true")
                kodiutils.log("Set InputStream Adaptive NOSECUREDECODER=true")
        finally:
            del isa  # Kodi 22 warns about Addon instances left behind
    except Exception as exc:
        kodiutils.log_error("Could not set NOSECUREDECODER: %s" % exc)


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
    elif action == "shelf":
        show_items(api.get_shelf_items(params.get("shelf_id")))
    elif action == "show":
        show_items(api.get_show_episodes(params.get("show_id")), content="episodes")
    elif action == "search":
        do_search(api)
    elif action == "play":
        do_play(api, params.get("item_id"), params.get("item_type", "Movie"))
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
