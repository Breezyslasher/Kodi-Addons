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
from lib.api import StarzApi, PLAYING_FILE
from lib.license_relay import relay_url, LICENSE_FILE
from lib import iptv
from lib.channels import CHANNELS, NAMES, logo
from lib.api import to_item

HANDLE = int(sys.argv[1])
_ADDON_PATH = kodiutils.addon().getAddonInfo("path")
ADDON_ART = {"icon": _ADDON_PATH + "/resources/icon.png",
             "fanart": _ADDON_PATH + "/resources/fanart.jpg"}
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
    # Background only: the default folder icon stays, so menus don't repeat
    # the add-on icon on every entry.
    li.setArt({"fanart": ADDON_ART["fanart"]})
    xbmcplugin.addDirectoryItem(HANDLE, url(action=action, **params), li, True)


def add_action(label, action, **params):
    """A button-like entry: not a folder and not playable, so Kodi runs the
    plugin as a script (handle -1) instead of loading a directory. Needed
    when the action then navigates with Container.Update, which Kodi drops
    while it is still loading a folder."""
    li = xbmcgui.ListItem(label)
    li.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(HANDLE, url(action=action, **params), li, False)


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
        if item.get("playcount"):
            tag.setPlaycount(int(item["playcount"]))
        if item.get("resume") and item.get("duration"):
            tag.setResumePoint(float(item["resume"]), float(item["duration"]))
    except (TypeError, ValueError):
        pass
    if item.get("mpaa"):
        tag.setMpaa(item["mpaa"])
    if item.get("genres"):
        tag.setGenres(item["genres"])
    if item.get("cast"):
        tag.setCast([xbmc.Actor(c["name"], "", c["order"])
                     for c in item["cast"]])
    if item.get("directors"):
        tag.setDirectors(item["directors"])
    if item.get("writers"):
        tag.setWriters(item["writers"])
    if item.get("studio"):
        tag.setStudios([item["studio"]])
    tag.setMediaType({"movie": "movie", "series": "tvshow", "season": "season",
                      "episode": "episode"}.get(item.get("type"), "video"))
    if item.get("series_title"):
        tag.setTvShowTitle(item["series_title"])
    if item.get("art"):
        li.setArt(item["art"])


# Set by the router: My List context items only make sense when signed in,
# and the ids already on the list decide Add vs Remove.
STATE = {"signed_in": False, "my_list": None}


def _list_menu(item):
    if not STATE["signed_in"] or item.get("type") not in ("movie", "series"):
        return []
    mine = STATE["my_list"] or {}
    on_list = item["id"] in mine
    action, label = ("list_remove", L(32066)) if on_list else ("list_add", L(32065))
    menu = [(label, "RunPlugin(%s)" % url(action=action, content_id=item["id"]))]
    if item.get("coming_soon"):
        reminding = bool(mine.get(item["id"]))
        menu.append((L(32118) if reminding else L(32117), "RunPlugin(%s)" % url(
            action="remind", content_id=item["id"], on="0" if reminding else "1")))
    return menu


def _people_menu(item):
    """Cast & crew, and If you like X (for an episode: its series, as the
    website does)."""
    if item.get("type") not in ("movie", "series", "episode"):
        return []
    like_id = item["id"]
    like_title = item.get("title", "")
    if item.get("type") == "episode" and item.get("series_id"):
        like_id, like_title = item["series_id"], item.get("series_title") or like_title
    return [
        (L(32096), "Container.Update(%s)" % url(action="people", content_id=item["id"])),
        (L(32097) % like_title, "Container.Update(%s)" % url(
            action="similar", content_id=like_id, title=like_title)),
    ]


def show_people(api, content_id):
    for artist_id, name, roles in api.people(content_id):
        label = "%s  [COLOR grey]%s[/COLOR]" % (name, ", ".join(roles)) if roles else name
        add_dir(label, "artist", artist_id=artist_id)
    xbmcplugin.endOfDirectory(HANDLE)


def add_item(item):
    label = item.get("title", "")
    if item.get("label_extra"):
        label = "%s  [COLOR grey]%s[/COLOR]" % (label, item["label_extra"])
    li = xbmcgui.ListItem(label)
    _apply_info(li, item)
    menu = _list_menu(item) + _people_menu(item)
    if item.get("in_continue"):
        # Starz hides an episode by its series id (episode hide HAR: the
        # Power S1E1 entry was removed with {"ids": [21220]}, the series).
        hide_id = item["id"]
        if item.get("type") == "episode" and item.get("series_id"):
            hide_id = item["series_id"]
        menu.append((L(32070), "RunPlugin(%s)" % url(action="continue_hide",
                                                     content_id=hide_id)))
    if menu:
        li.addContextMenuItems(menu)
    if item.get("type") == "series":
        target, folder = url(action="series", series_id=item["id"]), True
    elif item.get("type") == "season":
        target, folder = url(action="season", series_id=item.get("series_id", ""),
                             season_id=item["id"]), True
    elif item.get("coming_soon"):
        li.setLabel("%s  [COLOR grey]%s[/COLOR]" % (li.getLabel(), L(32116)))
        target, folder = url(action="noop"), False
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
    if kinds == {"season"}:
        return "seasons"
    if kinds == {"episode"}:
        return "episodes"
    return "videos"


# -- views -------------------------------------------------------------------

def main_menu(auth):
    if auth.signed_in:
        add_dir(L(32050), "continue")
        add_dir(L(32069), "my_list")
        if auth.profile_id:
            add_dir(L(32085), "recommended")
    else:
        add_dir(L(32014), "sign_in")
    add_dir(L(32051), "page", page="HOME")
    add_dir(L(32052), "page", page="SERIES")
    add_dir(L(32053), "page", page="MOVIES")
    add_dir(L(32054), "page", page="BROWSE")
    add_dir(L(32110), "channels")
    add_dir(L(32074), "genres")
    add_dir(L(32075), "categories")
    add_dir(L(32013), "search")
    if auth.signed_in:
        add_dir(L(32062) % (auth.profile_name or "-"), "profile")
        add_action(L(32106), "devices")
        add_dir(L(32063) % (auth.username or "-"), "sign_out")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_page(api, page):
    if page == "MOVIES":
        add_dir(L(32072), "browse", source="all", type="movie", sort="az")
    elif page == "SERIES":
        add_dir(L(32073), "browse", source="all", type="series", sort="az")
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
        auth.sign_out()


def do_search(api):
    query = kodiutils.input_text(L(32026))
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    items = api.search(query)
    if not items:
        kodiutils.notify(L(32064))
    show_items(items, _content_for(items))


def do_series(api, series_id):
    """Seasons, or straight to the episodes when there is only one."""
    series, seasons = api.seasons(series_id)
    add_dir(L(32096), "people", content_id=series_id)
    add_dir(L(32097) % series.get("title", ""), "similar", content_id=series_id,
            title=series.get("title", ""))
    if len(seasons) == 1:
        show_items(api.episodes(series_id, seasons[0]["id"]), "episodes")
    else:
        show_items(seasons, "seasons")


SORT_LABELS = {"az": 32076, "popular": 32077, "year": 32078, "": 32093}
TYPE_LABELS = {"": 32092, "movie": 32053, "series": 32052}
DECADE_LABELS = {"2020": "2020s", "2010": "2010s", "2000": "2000s",
                 "1990": "1990s", "1980": "1980s", "1970": "1970s",
                 "1960": "1960s", "pre1960": "< 1960s"}
RUNTIME_LABELS = {"lt20": "< 20 mins", "20-40": "20 - 40 mins",
                  "40-60": "40 - 60 mins", "gt60": "> 60 mins"}

# Browse state lives in the URL, so Back and favourites keep a filtered list:
# source (all | recommended), type, years, runtime, genre (comma lists), sort.
BROWSE_KEYS = ("source", "type", "years", "runtime", "genre", "sort")


def _csv(value):
    return [v for v in (value or "").split(",") if v]


def _state(params):
    return {k: params.get(k, "") for k in BROWSE_KEYS}


def _filters_summary(st):
    parts = [L(TYPE_LABELS.get(st["type"], 32092))]
    if _csv(st["years"]):
        parts.append(", ".join(DECADE_LABELS.get(d, d) for d in _csv(st["years"])))
    if _csv(st["runtime"]):
        parts.append(", ".join(RUNTIME_LABELS.get(r, r) for r in _csv(st["runtime"])))
    if _csv(st["genre"]):
        parts.append(", ".join(_csv(st["genre"])))
    return " · ".join(parts)


def show_browse(api, st):
    """A full list (whole catalogue or recommendations) with the website's
    view-all controls: a Sort entry and a separate Filters entry."""
    add_action(L(32094) % L(SORT_LABELS.get(st["sort"], 32093)), "pick_sort", **st)
    add_action(L(32095) % _filters_summary(st), "pick_filters", **st)
    items = api.browse(st["source"] or "all", st["type"] or None,
                       _csv(st["years"]), _csv(st["runtime"]), _csv(st["genre"]),
                       st["sort"])
    show_items(items, _content_for(items))


def _go(st):
    if HANDLE >= 0:
        # Opened as a folder (e.g. from a list cached by an older build).
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    xbmc.executebuiltin("Container.Update(%s,replace)" % url(action="browse", **st))


def do_pick_sort(st):
    sorts = list(StarzApi.SORTS)
    if st["source"] == "recommended":
        sorts = [""] + sorts          # Starz's own order
    i = xbmcgui.Dialog().select(L(32084), [L(SORT_LABELS[x]) for x in sorts],
                                preselect=sorts.index(st["sort"]) if st["sort"] in sorts else 0)
    if i >= 0:
        st["sort"] = sorts[i]
        _go(st)
    elif HANDLE >= 0:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def _multi(heading, keys, labels, chosen):
    """Checkbox picker. Nothing or everything ticked means no filter, as
    the website's all-ticked default does."""
    pre = [i for i, k in enumerate(keys) if k in chosen] or list(range(len(keys)))
    picked = xbmcgui.Dialog().multiselect(heading, labels, preselect=pre)
    if picked is None:
        return chosen
    if not picked or len(picked) == len(keys):
        return []
    return [keys[i] for i in picked]


def do_pick_filters(api, st):
    """The website's Filters panel: Show, Release Year, Run Time (plus
    Genre), then Reset / Apply. Back cancels without changing anything."""
    st = dict(st)
    years, runtime, genre = _csv(st["years"]), _csv(st["runtime"]), _csv(st["genre"])
    dialog = xbmcgui.Dialog()
    while True:
        all_label = L(32092)
        entries = [
            "%s: %s" % (L(32082), L(TYPE_LABELS.get(st["type"], 32092))),
            "%s: %s" % (L(32088), ", ".join(DECADE_LABELS[d] for d in years) or all_label),
            "%s: %s" % (L(32089), ", ".join(RUNTIME_LABELS[r] for r in runtime) or all_label),
            "%s: %s" % (L(32083), ", ".join(genre) or all_label),
            "[B]%s[/B]" % L(32090),
            "[B]%s[/B]" % L(32091),
        ]
        choice = dialog.select(L(32087), entries)
        if choice < 0:
            if HANDLE >= 0:
                xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return
        if choice == 0:
            kinds = ["", "movie", "series"]
            i = dialog.select(L(32082), [L(TYPE_LABELS[k]) for k in kinds],
                              preselect=kinds.index(st["type"]) if st["type"] in kinds else 0)
            if i >= 0:
                st["type"] = kinds[i]
        elif choice == 1:
            keys = list(StarzApi.DECADES)
            years = _multi(L(32088), keys, [DECADE_LABELS[k] for k in keys], years)
        elif choice == 2:
            keys = list(StarzApi.RUNTIMES)
            runtime = _multi(L(32089), keys, [RUNTIME_LABELS[k] for k in keys], runtime)
        elif choice == 3:
            gcs = api.genres(st["type"] or None)
            keys = [g for g, _ in gcs]
            genre = _multi(L(32083), keys, ["%s (%d)" % gc for gc in gcs], genre)
        elif choice == 4:
            st["type"], years, runtime, genre = "", [], [], []
        else:
            st.update(years=",".join(years), runtime=",".join(runtime),
                      genre=",".join(genre))
            _go(st)
            return


def show_genres(api):
    for genre, count in api.genres():
        add_dir("%s (%d)" % (genre, count), "browse", source="all", genre=genre,
                sort="az")
    xbmcplugin.endOfDirectory(HANDLE)


def do_list_change(api, content_id, add):
    if add:
        api.add_to_list(content_id)
        kodiutils.notify(L(32067))
    else:
        api.remove_from_list(content_id)
        kodiutils.notify(L(32068))
    xbmc.executebuiltin("Container.Refresh")


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

    ISA's 4-part license_key (url|headers|body|response) with R{SSM}: the raw
    challenge goes out, the raw licence comes back. The URL is the service's
    localhost licence relay (lib/license_relay.py) when it is running, so the
    long percent-encoded token never passes through Kodi's URL handling;
    otherwise the direct Starz URL.
    """
    li = xbmcgui.ListItem(path=playback["manifest_url"])
    li.setProperty("inputstream", "inputstream.adaptive")
    # No manifest_type: ISA 21 (our minimum) detects it and warns that the
    # property is deprecated; the MIME type below covers detection.
    li.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
    relay = relay_url()
    if relay:
        kodiutils.write_json(LICENSE_FILE, {
            "url": playback["license_url"],
            "headers": playback.get("license_headers") or {}})
        license_key = "%s||R{SSM}|" % relay
    else:
        kodiutils.log("Licence relay not running; licensing directly")
        license_key = "%s|%s|R{SSM}|" % (
            playback["license_url"], _header_string(playback.get("license_headers")))
    li.setProperty("inputstream.adaptive.license_key", license_key)
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


DEVICE_LIMIT = 1305   # Starz errorCode: "The device limit is reached"


def do_manage_devices(api, auth):
    """Checklist of the account's devices; the ticked ones are logged out.
    This Kodi is shown but not offered, so it cannot sign itself out here.
    Returns True if any device was logged out."""
    devices = api.devices()
    if not devices:
        kodiutils.ok_dialog(L(32101))
        return False
    mine = auth.device_id
    others = [d for d in devices if d.get("deviceId") != mine]
    labels = []
    for d in others:
        where = ", ".join(x for x in (d.get("city"), d.get("countryRegion")) if x)
        labels.append("%s  [COLOR grey]%s · %s %s[/COLOR]" % (
            d.get("name") or d.get("type") or "?", where, L(32102),
            (d.get("lastAccess") or "")[:10]))
    heading = L(32100) % len(devices)
    picked = xbmcgui.Dialog().multiselect(heading, labels)
    if not picked:
        return False
    if not xbmcgui.Dialog().yesno(kodiutils.ADDON_NAME, L(32103) % len(picked)):
        return False
    api.logout_devices([others[i]["deviceId"] for i in picked])
    kodiutils.notify(L(32104) % len(picked))
    return True


def do_play(api, content_id, auth=None, seek_to=None):
    if not ensure_widevine():
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        try:
            playback = api.playback(content_id, kodiutils.subtitle_languages())
        except StarzAuthError as exc:
            # At the device limit, offer to free a slot here instead of a
            # dead end, then try once more.
            if exc.code != DEVICE_LIMIT or auth is None or not xbmcgui.Dialog().yesno(
                    kodiutils.ADDON_NAME, "%s\n\n%s" % (exc, L(32105))):
                raise
            if not do_manage_devices(api, auth):
                raise
            playback = api.playback(content_id, kodiutils.subtitle_languages())
    except Exception:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        raise
    # Hand the play session to service.py, which reports progress and the
    # final position to Starz (Play/update, Play/stop) once this process ends.
    kodiutils.write_json(PLAYING_FILE, {
        "content_id": str(content_id), "session_id": playback.get("session_id"),
        "keep_alive": playback.get("keep_alive"),
        "manifest_url": playback["manifest_url"], "seek_to": seek_to})
    xbmcplugin.setResolvedUrl(HANDLE, True, build_isa_listitem(playback))


# -- TV schedule -------------------------------------------------------------

def _clock(ms):
    fmt = (xbmc.getRegion("time") or "%H:%M").replace(":%S", "")
    return time.strftime(fmt, time.localtime(ms / 1000.0)).lstrip("0")


def show_channels():
    for code, name, group in CHANNELS:
        li = xbmcgui.ListItem(name)
        icon = logo(_ADDON_PATH, code)
        li.setArt({"icon": icon, "thumb": icon, "fanart": ADDON_ART["fanart"]})
        li.getVideoInfoTag().setPlot(group)
        xbmcplugin.addDirectoryItem(HANDLE, url(action="channel", code=code), li, True)
    xbmcplugin.endOfDirectory(HANDLE)


def show_channel(api, code, day):
    """A channel's line-up for one local day; today starts with On now."""
    day = int(day or 0)
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) + day * 86400
    lo, hi, now = start * 1000, (start + 86400) * 1000, time.time() * 1000
    icon = logo(_ADDON_PATH, code)
    xbmcplugin.setPluginCategory(HANDLE, NAMES.get(code, code))
    elements = [e for e in api.schedule(code, day)
                if e.get("endMs", 0) > lo and e.get("startMs", 0) < hi]
    if day == 0:
        current = [e for e in elements if e.get("startMs", 0) <= now < e.get("endMs", 0)]
        if current:
            li = xbmcgui.ListItem(L(32111) % current[0].get("title", ""))
            li.setArt({"icon": icon, "thumb": icon})
            li.setProperty("IsPlayable", "true")
            xbmcplugin.addDirectoryItem(HANDLE, url(action="live", channel=code), li, False)
        add_dir(L(32112), "channel", code=code, day=1)
    for el in elements:
        content = dict(el)
        content["runtime"] = (el["endMs"] - el["startMs"]) // 1000
        item = to_item(content) or {"id": str(el.get("contentId")), "type": "movie"}
        item["title"] = el.get("title", "")
        label = "%s  %s" % (_clock(el["startMs"]), el.get("title", ""))
        if el.get("startMs", 0) <= now < el.get("endMs", 0):
            label = "[B]%s[/B]" % label
        li = xbmcgui.ListItem(label)
        _apply_info(li, item)
        if el.get("availableOnOtt") and el.get("contentId"):
            li.setProperty("IsPlayable", "true")
            target = url(action="play", content_id=el["contentId"])
        else:
            li.setLabel("[COLOR grey]%s[/COLOR]" % label)
            target = url(action="noop")
        xbmcplugin.addDirectoryItem(HANDLE, target, li, False)
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def do_live(api, code, auth):
    """Tune in: play what the channel is showing now, from where it is.
    Starz has no live streams; this is the scheduled title on demand."""
    el = api.on_now(code)
    if not el or not el.get("availableOnOtt"):
        kodiutils.notify(L(32113))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    offset = max(0, int(time.time() - el["startMs"] / 1000.0))
    kodiutils.log("Tuning in to %s: %s at %ds" % (code, el.get("title"), offset))
    do_play(api, el["contentId"], auth, seek_to=offset if offset > 30 else None)


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
    STATE["signed_in"] = auth.signed_in
    if auth.signed_in and action in ("row", "series", "search", "my_list", "all",
                                     "recommended", "browse", "artist", "similar"):
        STATE["my_list"] = api.my_list_ids()

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
            for item in items:
                item["in_continue"] = True
            show_items(items, _content_for(items))
        elif action == "continue_hide":
            api.hide_from_continue(params.get("content_id"))
            kodiutils.notify(L(32071))
            xbmc.executebuiltin("Container.Refresh")
        elif action == "page":
            show_page(api, params.get("page", "HOME"))
        elif action == "browse":
            show_browse(api, _state(params))
        elif action == "all":            # links from earlier builds
            st = _state(params)
            st["source"] = "all"
            show_browse(api, st)
        elif action == "recommended":
            st = _state(params)
            st["source"] = "recommended"
            show_browse(api, st)
        elif action == "pick_sort":
            do_pick_sort(_state(params))
        elif action in ("pick_filters", "filter"):
            do_pick_filters(api, _state(params))
        elif action == "people":
            show_people(api, params.get("content_id"))
        elif action == "artist":
            name, items = api.artist(params.get("artist_id"))
            xbmcplugin.setPluginCategory(HANDLE, name)
            show_items(items, _content_for(items))
        elif action == "similar":
            xbmcplugin.setPluginCategory(HANDLE, L(32097) % params.get("title", ""))
            items = api.similar(params.get("content_id"))
            show_items(items, _content_for(items))
        elif action == "devices":
            do_manage_devices(api, auth)
            if HANDLE >= 0:
                xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        elif action == "channels":
            show_channels()
        elif action == "channel":
            show_channel(api, params.get("code"), params.get("day"))
        elif action == "live":
            do_live(api, params.get("channel"), auth)
        elif action == "noop":
            pass
        elif action == "remind":
            on = params.get("on") == "1"
            api.set_reminder(params.get("content_id"), on)
            kodiutils.notify(L(32119) if on else L(32120))
            xbmc.executebuiltin("Container.Refresh")
        elif action == "genres":
            show_genres(api)
        elif action == "categories":
            show_row(api, api.categories_row_id())
        elif action == "row":
            show_row(api, params.get("row_id"))
        elif action == "series":
            do_series(api, params.get("series_id"))
        elif action == "season":
            show_items(api.episodes(params.get("series_id"),
                                    params.get("season_id")), "episodes")
        elif action == "search":
            do_search(api)
        elif action == "my_list":
            items = api.my_list()
            show_items(items, _content_for(items))
        elif action == "list_add":
            do_list_change(api, params.get("content_id"), True)
        elif action == "list_remove":
            do_list_change(api, params.get("content_id"), False)
        elif action == "play":
            do_play(api, params.get("content_id"), auth)
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
    # IPTV Manager calls /iptv/channels and /iptv/epg with ?port=N and wants
    # the answer on that socket, not a directory listing.
    _query = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    if not iptv.handle(sys.argv[0], _query.get("port"),
                       StarzApi(StarzAuth()), _ADDON_PATH):
        router(sys.argv[2][1:])
