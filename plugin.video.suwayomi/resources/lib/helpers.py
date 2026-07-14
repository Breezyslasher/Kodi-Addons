"""
Helper functions and utilities for Suwayomi addon
"""

import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import xbmcvfs
import os
import json

from urllib.parse import urlencode, parse_qs, quote

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = ADDON.getAddonInfo('path')
ADDON_DATA = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))


def log_info(msg):
    xbmc.log(f"[{ADDON_ID}] {msg}", xbmc.LOGINFO)


def log_error(msg):
    xbmc.log(f"[{ADDON_ID}] ERROR: {msg}", xbmc.LOGERROR)


def get_setting(key):
    return ADDON.getSetting(key)


def get_setting_bool(key):
    return ADDON.getSettingBool(key)


def get_setting_int(key):
    try:
        return int(ADDON.getSetting(key))
    except:
        return 0


def show_notification(message, time=3000):
    xbmcgui.Dialog().notification(ADDON_NAME, message, xbmcgui.NOTIFICATION_INFO, time)


def show_error(message, time=5000):
    xbmcgui.Dialog().notification(ADDON_NAME, message, xbmcgui.NOTIFICATION_ERROR, time)


def show_yesno_dialog(title, message):
    return xbmcgui.Dialog().yesno(title, message)


def get_keyboard_input(title, default='', hidden=False):
    keyboard = xbmc.Keyboard(default, title, hidden)
    keyboard.doModal()
    if keyboard.isConfirmed():
        return keyboard.getText()
    return None


def build_url(base_url, **kwargs):
    return f"{base_url}?{urlencode(kwargs)}"


def parse_url(url):
    return {k: v[0] for k, v in parse_qs(url.lstrip('?')).items()}


def create_list_item(label, label2='', thumb='', icon='', fanart='', plot=''):
    li = xbmcgui.ListItem(label=label, label2=label2)
    li.setArt({
        'thumb': thumb or icon,
        'icon': icon or thumb,
        'fanart': fanart
    })
    li.setInfo('video', {'title': label, 'plot': plot})
    return li


def add_directory_item(handle, url, list_item, is_folder=True):
    xbmcplugin.addDirectoryItem(handle, url, list_item, isFolder=is_folder)


def end_directory(handle, content_type=None, sort_methods=None):
    if content_type:
        xbmcplugin.setContent(handle, content_type)
    if sort_methods:
        for method in sort_methods:
            xbmcplugin.addSortMethod(handle, method)
    xbmcplugin.endOfDirectory(handle)


# ==================== MANGA SETTINGS ====================

def get_manga_settings_path():
    if not os.path.exists(ADDON_DATA):
        os.makedirs(ADDON_DATA)
    return os.path.join(ADDON_DATA, 'manga_settings.json')


def load_manga_settings():
    path = get_manga_settings_path()
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_manga_settings(settings):
    path = get_manga_settings_path()
    try:
        with open(path, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        log_error(f"Failed to save manga settings: {e}")
        return False


def get_manga_reading_settings(manga_id):
    settings = load_manga_settings()
    return settings.get(str(manga_id), {})


def set_manga_reading_settings(manga_id, reading_settings):
    all_settings = load_manga_settings()
    all_settings[str(manga_id)] = reading_settings
    return save_manga_settings(all_settings)


def is_manga_configured(manga_id):
    settings = get_manga_reading_settings(manga_id)
    return settings.get('_configured', False)


def mark_manga_configured(manga_id):
    settings = get_manga_reading_settings(manga_id)
    settings['_configured'] = True
    set_manga_reading_settings(manga_id, settings)


# ==================== SOURCE FILTERS ====================

def get_language_filter():
    lang_setting = get_setting('source_languages') or ''
    if not lang_setting.strip():
        return None
    languages = [l.strip().lower() for l in lang_setting.split(',') if l.strip()]
    return languages if languages else None


def get_hidden_sources():
    hidden_setting = get_setting('hidden_sources') or ''
    if not hidden_setting.strip():
        return []
    return [s.strip().lower() for s in hidden_setting.split(',') if s.strip()]


def is_source_visible(source, lang_filter, hidden_sources):
    source_name = (source.get('displayName') or source.get('name', '')).lower()
    source_lang = source.get('lang', '').lower()
    
    for hidden in hidden_sources:
        if hidden in source_name:
            return False
    
    if lang_filter:
        if source_lang not in lang_filter:
            return False
    
    return True
