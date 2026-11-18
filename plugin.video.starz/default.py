"""Starz addon entry point and router."""

import sys
from urllib.parse import urlencode, parse_qsl, quote

import xbmcgui
import xbmcplugin

from lib import kodiutils
from lib.auth import StarzAuth, NotReadyError, normalise_username
from lib.api import StarzApi

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]


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
    if item.get("year"):
        tag.setYear(int(item["year"]))
    if item.get("duration"):
        tag.setDuration(int(item["duration"]))
    kind = item.get("type")
    tag.setMediaType({"movie": "movie", "series": "tvshow",
                      "season": "season", "episode": "episode"}.get(kind, "video"))
    if item.get("season") is not None:
        tag.setSeason(int(item["season"]))
    if item.get("episode") is not None:
        tag.setEpisode(int(item["episode"]))
    if item.get("art"):
        li.setArt(item["art"])


def add_item(item):
    kind = item.get("type")
    li = xbmcgui.ListItem(item.get("title", ""))
    _apply_info(li, item)
    if kind == "series":
        target, folder = url(action="series", series_id=item["id"]), True
    elif kind == "season":
        target, folder = url(action="season", series_id=item.get("series_id", ""),
                             season_id=item["id"]), True
    else:
        li.setProperty("IsPlayable", "true")
        target, folder = url(action="play", content_id=item["id"]), False
    xbmcplugin.addDirectoryItem(HANDLE, target, li, folder)


def show_items(items, content="videos"):
    xbmcplugin.setContent(HANDLE, content)
    for item in items:
        add_item(item)
    xbmcplugin.endOfDirectory(HANDLE)


# -- views -------------------------------------------------------------------

def main_menu(auth, api):
    if not auth.signed_in:
        add_dir(L(32014), "sign_in")
    else:
        try:
            for row in api.home_rows():
                add_dir(row.get("title", ""), "row", row_id=row["id"])
        except NotReadyError as exc:
            kodiutils.log(str(exc))
        add_dir(L(32013), "search")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def do_sign_in(auth):
    username = kodiutils.input_text(L(32020))
    if not username:
        return
    password = kodiutils.input_text(L(32021), hidden=True)
    if not password:
        return
    auth.sign_in(normalise_username(username), password)
    kodiutils.notify(L(32023))


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

def router(paramstring):
    params = dict(parse_qsl(paramstring))
    action = params.get("action")

    auth = StarzAuth()
    api = StarzApi(auth)

    try:
        if action == "sign_in":
            do_sign_in(auth)
            main_menu(auth, api)
        elif action == "sign_out":
            do_sign_out(auth)
            main_menu(auth, api)
        elif action == "row":
            show_items(api.row_items(params.get("row_id")))
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
            main_menu(auth, api)
    except NotReadyError as exc:
        kodiutils.log(str(exc))
        kodiutils.ok_dialog("%s\n\n%s" % (L(32040), exc))


if __name__ == "__main__":
    router(sys.argv[2][1:])
