"""Small helpers around the Kodi Python API: settings, logging, storage."""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


def addon():
    """A fresh Addon handle.

    Kodi 22 warns when a script leaves xbmcaddon.Addon instances behind
    ("has left several classes in memory that we couldn't clean up"), which is
    what a module-level instance does: it lives until interpreter teardown, by
    which point Kodi has already checked. Create one per call and let it go out
    of scope instead.
    """
    return xbmcaddon.Addon()


ADDON_ID = addon().getAddonInfo("id")
ADDON_NAME = addon().getAddonInfo("name")


def log(message, level=xbmc.LOGINFO):
    # Never raise from logging: this runs during teardown too, and on an
    # uninstall the addon id is already gone.
    try:
        xbmc.log("[%s] %s" % (ADDON_ID, message), level)
    except Exception:
        pass


def log_error(message):
    log(message, xbmc.LOGERROR)


def get_setting(setting_id, default=""):
    try:
        value = addon().getSetting(setting_id)
        return value if value else default
    except Exception:
        return default


def set_setting(setting_id, value):
    try:
        addon().setSetting(setting_id, str(value))
    except Exception:
        pass


def get_setting_bool(setting_id, default=False):
    value = get_setting(setting_id, "true" if default else "false")
    return str(value).lower() == "true"


def get_setting_int(setting_id, default=0):
    try:
        return int(get_setting(setting_id, str(default)))
    except (TypeError, ValueError):
        return default


def localize(string_id):
    return addon().getLocalizedString(string_id)


def notify(message, heading=None, icon=xbmcgui.NOTIFICATION_INFO, time_ms=5000):
    xbmcgui.Dialog().notification(heading or ADDON_NAME, message, icon, time_ms)


def ok_dialog(message, heading=None):
    xbmcgui.Dialog().ok(heading or ADDON_NAME, message)


def input_text(heading, hidden=False):
    keyboard_type = xbmcgui.INPUT_ALPHANUM
    option = xbmcgui.ALPHANUM_HIDE_INPUT if hidden else 0
    result = xbmcgui.Dialog().input(heading, type=keyboard_type, option=option)
    return result or None


def input_numeric(heading):
    result = xbmcgui.Dialog().input(heading, type=xbmcgui.INPUT_NUMERIC)
    return result or None


def profile_dir():
    """The addon's data directory, created if absent.

    Raises once the addon has been uninstalled -- xbmcaddon.Addon() cannot
    resolve an id Kodi has already dropped. Callers that run during teardown
    (the service stopping, the licence proxy cleaning up) must treat that as
    normal, so read_json/write_json/delete_file each compute their path inside
    their own try block rather than ahead of it.
    """
    path = xbmcvfs.translatePath(addon().getAddonInfo("profile"))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def subtitle_languages():
    """Two-letter codes of the subtitles to fetch: Kodi's language, then
    English as a fallback. Kept short so a play does not fetch dozens of
    tracks it will never show."""
    langs = []
    try:
        code = (xbmc.getLanguage(xbmc.ISO_639_1) or "").strip().lower()
        if code:
            langs.append(code)
    except Exception:
        pass
    if "en" not in langs:
        langs.append("en")
    return langs


def _safe_name(filename):
    """A profile-dir filename with any path component stripped.

    read_json/write_json/delete_file join their argument onto the addon profile
    dir. Some callers build the name from ids that ultimately come from Kodi
    routing or an Apple response, so collapse it to a bare basename and reject a
    traversal name -- a value like "../../x" can never escape the profile dir.
    """
    name = os.path.basename(str(filename))
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("unsafe filename: %r" % filename)
    return name


def read_json(filename, default=None):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception as exc:
        log_error("Failed reading %s: %s" % (filename, exc))
    return default


def write_json(filename, data):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return True
    except Exception as exc:
        log_error("Failed writing %s: %s" % (filename, exc))
        return False


def delete_file(filename):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
