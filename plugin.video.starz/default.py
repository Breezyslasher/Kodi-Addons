"""Starz addon entry point and router."""

import sys
import time
from urllib.parse import urlencode, parse_qsl, quote

import requests
import xbmc
import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import StarzAuth, StarzAuthError, NotReadyError
from lib.api import StarzApi

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

# How long to wait for an activation code to be entered, and how often to ask.
ACTIVATION_TIMEOUT_SECS = 10 * 60
ACTIVATION_POLL_SECS = 5


def L(string_id):
    return kodiutils.localize(string_id)


def url(**kwargs):
    return BASE_URL + "?" + urlencode(kwargs)


# -- listing helpers -------------------------------------------------------

def add_dir(label, action, **params):
    li = xbmcgui.ListItem(label)
    xbmcplugin.addDirectoryItem(HANDLE, url(action=action, **params), li, True)


def _apply_info(li, item):
    tag = li.getVideoInfoTag()
    tag.setTitle(item.get("title", ""))
    tag.setPlot(item.get("plot", ""))
    try:
        if item.get("year"):
            tag.setYear(int(item["year"]))
        if item.get("duration"):
            tag.setDuration(int(item["duration"]))
        if item.get("season") is not None:
            tag.setSeason(int(item["season"]))
        if item.get("episode") is not None:
            tag.setEpisode(int(item["episode"]))
        if item.get("resume") and item.get("duration"):
            tag.setResumePoint(float(item["resume"]), float(item["duration"]))
    except (TypeError, ValueError):
        pass
    if item.get("mpaa"):
        tag.setMpaa(item["mpaa"])
    if item.get("genres"):
        tag.setGenres(item["genres"])
    tag.setMediaType({"movie": "movie", "series": "tvshow",
                      "episode": "episode"}.get(item.get("type"), "video"))
    if item.get("art"):
        li.setArt(item["art"])


def add_item(item):
    li = xbmcgui.ListItem(item.get("title", ""))
    _apply_info(li, item)
    if item.get("type") == "series":
        target, folder = url(action="series", series_id=item["id"]), True
    else:
        li.setProperty("IsPlayable", "true")
        target, folder = url(action="play", content_id=item["id"]), False
    xbmcplugin.addDirectoryItem(HANDLE, target, li, folder)


def show_items(items, content="videos"):
    xbmcplugin.setContent(HANDLE, content)
    for item in items:
        add_item(item)
    xbmcplugin.endOfDirectory(HANDLE)


def _content_for(items):
    kinds = {i.get("type") for i in items}
    if kinds == {"movie"}:
        return "movies"
    if kinds == {"series"}:
        return "tvshows"
    if kinds == {"episode"}:
        return "episodes"
    return "videos"


# -- views -------------------------------------------------------------------

def main_menu(auth):
    if auth.signed_in:
        add_dir(L(32050), "continue")
    else:
        add_dir(L(32014), "sign_in")
    add_dir(L(32051), "page", page="HOME")
    add_dir(L(32052), "page", page="SERIES")
    add_dir(L(32053), "page", page="MOVIES")
    add_dir(L(32054), "page", page="BROWSE")
    add_dir(L(32013), "search")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_page(api, page):
    for row in api.page_rows(page):
        add_dir(row["title"], "row", row_id=row["id"])
    xbmcplugin.endOfDirectory(HANDLE)


def show_row(api, row_id):
    rows, items = api.row(row_id)
    if rows:
        for row in rows:
            add_dir(row["title"], "row", row_id=row["id"])
        xbmcplugin.endOfDirectory(HANDLE)
    else:
        show_items(items, _content_for(items))


# -- account -----------------------------------------------------------------

def do_sign_in(auth):
    choice = xbmcgui.Dialog().select(L(32014), [L(32055), L(32056)])
    if choice == 0:
        _sign_in_password(auth)
    elif choice == 1:
        _sign_in_code(auth)
    else:
        return
    if auth.signed_in:
        pick_profile(auth)
        kodiutils.notify(L(32023) + (": " + auth.username if auth.username else ""))


def _sign_in_password(auth):
    username = kodiutils.input_text(L(32020))
    if not username:
        return
    password = kodiutils.input_text(L(32021), hidden=True)
    if not password:
        return
    try:
        auth.sign_in(username, password)
    except StarzAuthError as exc:
        kodiutils.log_error("Sign-in failed: %s" % exc)
        kodiutils.ok_dialog("%s\n\n%s" % (L(32024), exc))


def _sign_in_code(auth):
    try:
        code, link = auth.activation_code()
    except StarzAuthError as exc:
        kodiutils.ok_dialog("%s\n\n%s" % (L(32024), exc))
        return
    progress = xbmcgui.DialogProgress()
    progress.create(kodiutils.ADDON_NAME, L(32057) % (link, code))
    monitor = xbmc.Monitor()
    started = time.time()
    try:
        while time.time() - started < ACTIVATION_TIMEOUT_SECS:
            if progress.iscanceled() or monitor.waitForAbort(ACTIVATION_POLL_SECS):
                return
            elapsed = time.time() - started
            progress.update(int(100 * elapsed / ACTIVATION_TIMEOUT_SECS))
            if auth.poll_activation():
                return
        kodiutils.ok_dialog(L(32058))
    finally:
        progress.close()


def pick_profile(auth):
    try:
        profiles = auth.profiles()
    except StarzAuthError as exc:
        kodiutils.log_error("Profiles: %s" % exc)
        return
    if not profiles:
        return
    if len(profiles) == 1:
        index = 0
    else:
        index = xbmcgui.Dialog().select(L(32059), [p.get("name", "?") for p in profiles])
        if index < 0:
            return
    try:
        auth.select_profile(profiles[index])
    except StarzAuthError as exc:
        kodiutils.log_error("Profile select: %s" % exc)


def do_sign_out(auth):
    if xbmcgui.Dialog().yesno(kodiutils.ADDON_NAME, L(32030)):
        auth.clear()


def do_search(api):
    query = kodiutils.input_text(L(32026))
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    show_items(api.search(query))


# -- playback ------------------------------------------------------------

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


def _header_string(headers):
    """name=value&name=value with values URL-quoted, as ISA expects in the
    request-headers part of license_key and in stream_headers."""
    return "&".join("%s=%s" % (k, quote(str(v), safe=""))
                    for k, v in (headers or {}).items())


def build_isa_listitem(playback):
    """A ListItem that plays a DASH/HLS + Widevine stream through ISA.

    Uses ISA's 4-part license_key (url|headers|body|response) so ISA posts the
    raw challenge (R{SSM}) straight to the licence server with no local proxy.
    Only replace this with a proxy if recon row G proves the server needs a
    wrapped body or a per-request computed header.
    """
    li = xbmcgui.ListItem(path=playback["manifest_url"])
    li.setProperty("inputstream", "inputstream.adaptive")
    # Kodi 21 still reads manifest_type; Kodi 22 detects it and ignores this.
    li.setProperty("inputstream.adaptive.manifest_type",
                   playback.get("manifest_type", "mpd"))
    li.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
    li.setProperty("inputstream.adaptive.license_key", "%s|%s|R{SSM}|" % (
        playback["license_url"], _header_string(playback.get("license_headers"))))
    if playback.get("stream_headers"):
        li.setProperty("inputstream.adaptive.stream_headers",
                       _header_string(playback["stream_headers"]))
    if playback.get("subtitles"):
        li.setSubtitles(playback["subtitles"])
    li.setMimeType("application/dash+xml"
                   if playback.get("manifest_type", "mpd") == "mpd"
                   else "application/vnd.apple.mpegurl")
    li.setContentLookup(False)
    return li


def do_play(api, content_id):
    if not ensure_widevine():
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        playback = api.playback(content_id)
    except Exception:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        raise
    xbmcplugin.setResolvedUrl(HANDLE, True, build_isa_listitem(playback))


# -- router --------------------------------------------------------------

def _after_account_action(auth):
    """Finish an account action however it was started.

    From the main menu it is opened as a folder (a real handle), so Kodi waits
    for a listing: give it the refreshed main menu, or it logs "Error getting
    plugin://...". From Settings it runs via RunPlugin (handle -1), where there
    is no listing to end; refresh whatever is showing instead.
    """
    if HANDLE >= 0:
        main_menu(auth)
    else:
        xbmc.executebuiltin("Container.Refresh")


def router(paramstring):
    params = dict(parse_qsl(paramstring))
    action = params.get("action")

    auth = StarzAuth()
    api = StarzApi(auth)

    try:
        if action == "sign_in":
            do_sign_in(auth)
            _after_account_action(auth)
        elif action == "profile":
            pick_profile(auth)
            _after_account_action(auth)
        elif action == "sign_out":
            do_sign_out(auth)
            _after_account_action(auth)
        elif action == "continue":
            items = api.continue_watching()
            show_items(items, _content_for(items))
        elif action == "page":
            show_page(api, params.get("page", "HOME"))
        elif action == "row":
            show_row(api, params.get("row_id"))
        elif action == "series":
            show_items(api.seasons(params.get("series_id")), "seasons")
        elif action == "season":
            show_items(api.episodes(params.get("series_id"),
                                    params.get("season_id")), "episodes")
        elif action == "search":
            do_search(api)
        elif action == "play":
            do_play(api, params.get("content_id"))
        else:
            main_menu(auth)
    except NotReadyError as exc:
        kodiutils.log(str(exc))
        kodiutils.ok_dialog("%s\n\n%s" % (L(32040), exc))
    except StarzAuthError as exc:
        kodiutils.log_error(str(exc))
        kodiutils.ok_dialog(str(exc))
    except requests.RequestException as exc:
        kodiutils.log_error("Network error: %s" % exc)
        kodiutils.ok_dialog("%s\n\n%s" % (L(32061), exc))


if __name__ == "__main__":
    router(sys.argv[2][1:])
