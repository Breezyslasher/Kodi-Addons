"""
Watch Party - shared helpers.

Session state is a small JSON file in the addon profile folder written by
the UI (default.py) and consumed by the background service (service.py).
The engine writes status.json each poll so the UI can show live party
status without talking to the relay itself.
"""
import json
import os

import xbmc
import xbmcaddon
import xbmcvfs


ADDON_ID = 'service.watchparty'


def addon():
    # A fresh Addon object per call so settings reads are always live.
    return xbmcaddon.Addon(ADDON_ID)


def profile_dir():
    path = xbmcvfs.translatePath(addon().getAddonInfo('profile'))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def session_file():
    return os.path.join(profile_dir(), 'session.json')


def status_file():
    return os.path.join(profile_dir(), 'status.json')


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[Watch Party] {msg}", level)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"failed to read {path}: {e}", xbmc.LOGERROR)
        return default if default is not None else {}


def save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def device_name():
    name = addon().getSetting('device_name').strip()
    if not name:
        name = xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    return name


def notify(message, time_ms=4000):
    if addon().getSettingBool('show_notifications'):
        icon = addon().getAddonInfo('icon')
        xbmc.executebuiltin(
            f'Notification(Watch Party,{message},{time_ms},{icon})')
