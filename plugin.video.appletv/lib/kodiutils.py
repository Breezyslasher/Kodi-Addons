"""Small helpers around the Kodi Python API: settings, logging, storage."""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (ADDON_ID, message), level)


def log_error(message):
    log(message, xbmc.LOGERROR)


def get_setting(setting_id, default=""):
    try:
        value = ADDON.getSetting(setting_id)
        return value if value else default
    except Exception:
        return default


def set_setting(setting_id, value):
    try:
        ADDON.setSetting(setting_id, str(value))
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
    return ADDON.getLocalizedString(string_id)


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


def open_settings():
    ADDON.openSettings()


def profile_dir():
    path = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def read_json(filename, default=None):
    path = os.path.join(profile_dir(), filename)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception as exc:
        log_error("Failed reading %s: %s" % (filename, exc))
    return default


def write_json(filename, data):
    path = os.path.join(profile_dir(), filename)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return True
    except Exception as exc:
        log_error("Failed writing %s: %s" % (filename, exc))
        return False


def delete_file(filename):
    path = os.path.join(profile_dir(), filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
