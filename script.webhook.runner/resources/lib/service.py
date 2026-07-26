"""
Webhook Runner background service.

Listens for Kodi playback / system events and fires the webhook mapped
to each event in events.json. Mappings are configured from the main UI
in default.py.
"""
import json
import os
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcvfs


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_DATA = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
WEBHOOKS_FILE = os.path.join(ADDON_DATA, 'webhooks.json')
EVENTS_FILE = os.path.join(ADDON_DATA, 'events.json')


SUPPORTED_EVENTS = [
    # Playback
    'playback_open',
    'playback_start',
    'playback_stop',
    'playback_pause',
    'playback_resume',
    'playback_seek',
    'playback_seek_chapter',
    'playback_speed_changed',
    'playback_av_change',
    'playback_queue_next',
    'playback_error',
    # Screensaver / display power
    'screensaver_on',
    'screensaver_off',
    'dpms_on',
    'dpms_off',
    'player_property_changed',
    # Playlist
    'playlist_add',
    'playlist_remove',
    'playlist_clear',
    # System / power
    'system_sleep',
    'system_wake',
    'system_quit',
    'system_restart',
    'system_low_battery',
    'kodi_start',
    'kodi_stop',
    # Library
    'library_scan_start',
    'library_scan_finish',
    'library_clean_start',
    'library_clean_finish',
    'database_scan_start',
    'database_updated',
    'video_library_update',
    'video_library_remove',
    'video_library_export',
    'video_library_refresh',
    'audio_library_update',
    'audio_library_remove',
    'audio_library_export',
    # Input / application
    'input_requested',
    'input_finished',
    'volume_changed',
    # Misc
    'settings_changed',
]


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[Webhook Runner Service] {msg}", level)


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception as e:
        _log(f"Failed to read {path}: {e}", xbmc.LOGERROR)
        return {}


def _fire(event_name):
    if not ADDON.getSettingBool('enable_event_triggers'):
        return
    events = _load_json(EVENTS_FILE)
    webhook_id = events.get(event_name)
    if not webhook_id:
        return
    webhooks = _load_json(WEBHOOKS_FILE)
    webhook = webhooks.get(str(webhook_id))
    if not webhook or not webhook.get('enabled', True) or not webhook.get('url'):
        return
    try:
        _log(f"event={event_name} -> webhook {webhook_id} ({webhook.get('name', '')})")
        data = urllib.parse.urlencode({}).encode()
        req = urllib.request.Request(webhook['url'], data=data, method='POST')
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        _log(f"webhook for {event_name} failed: {e}", xbmc.LOGERROR)


class _Player(xbmc.Player):
    def onPlayBackStarted(self):
        # Fires when Kodi opens the file, before the A/V stream is ready.
        # 'playback_start' (onAVStarted) fires slightly later when the
        # stream is actually rendering.
        _fire('playback_open')

    def onAVStarted(self):
        _fire('playback_start')

    def onPlayBackStopped(self):
        _fire('playback_stop')

    def onPlayBackEnded(self):
        _fire('playback_stop')

    def onPlayBackPaused(self):
        _fire('playback_pause')

    def onPlayBackResumed(self):
        _fire('playback_resume')

    def onPlayBackSeek(self, time, seekOffset):
        _fire('playback_seek')

    def onPlayBackSeekChapter(self, chapter):
        _fire('playback_seek_chapter')

    def onPlayBackSpeedChanged(self, speed):
        _fire('playback_speed_changed')

    def onAVChange(self):
        _fire('playback_av_change')

    def onQueueNextItem(self):
        _fire('playback_queue_next')

    def onPlayBackError(self):
        _fire('playback_error')


# These Kodi notifications have no dedicated xbmc.Monitor/xbmc.Player callback,
# so they are picked up via onNotification. Events that DO have a direct
# callback (screensaver, DPMS, library scan/clean, playback, etc.) are handled
# in the callback classes above and deliberately not mapped here, to avoid
# firing a webhook twice for the same event.
_NOTIFICATION_EVENTS = {
    # System / power
    'System.OnSleep': 'system_sleep',
    'System.OnWake': 'system_wake',
    'System.OnQuit': 'system_quit',
    'System.OnRestart': 'system_restart',
    'System.OnLowBattery': 'system_low_battery',
    # Application
    'Application.OnVolumeChanged': 'volume_changed',
    # Playlist
    'Playlist.OnAdd': 'playlist_add',
    'Playlist.OnRemove': 'playlist_remove',
    'Playlist.OnClear': 'playlist_clear',
    # Input
    'Input.OnInputRequested': 'input_requested',
    'Input.OnInputFinished': 'input_finished',
    # Player (no direct callback for this one)
    'Player.OnPropertyChanged': 'player_property_changed',
    # Video library (scan/clean already handled by direct callbacks)
    'VideoLibrary.OnUpdate': 'video_library_update',
    'VideoLibrary.OnRemove': 'video_library_remove',
    'VideoLibrary.OnExport': 'video_library_export',
    'VideoLibrary.OnRefresh': 'video_library_refresh',
    # Audio library
    'AudioLibrary.OnUpdate': 'audio_library_update',
    'AudioLibrary.OnRemove': 'audio_library_remove',
    'AudioLibrary.OnExport': 'audio_library_export',
}


class _Monitor(xbmc.Monitor):
    def onScreensaverActivated(self):
        _fire('screensaver_on')

    def onScreensaverDeactivated(self):
        _fire('screensaver_off')

    def onDPMSActivated(self):
        _fire('dpms_on')

    def onDPMSDeactivated(self):
        _fire('dpms_off')

    def onScanStarted(self, library):
        _fire('library_scan_start')

    def onScanFinished(self, library):
        _fire('library_scan_finish')

    def onCleanStarted(self, library):
        _fire('library_clean_start')

    def onCleanFinished(self, library):
        _fire('library_clean_finish')

    def onDatabaseScanStarted(self, database):
        _fire('database_scan_start')

    def onDatabaseUpdated(self, database):
        _fire('database_updated')

    def onSettingsChanged(self):
        _fire('settings_changed')

    def onNotification(self, sender, method, data):
        event_name = _NOTIFICATION_EVENTS.get(method)
        if event_name:
            _fire(event_name)


def main():
    _log("starting")
    monitor = _Monitor()
    player = _Player()  # noqa: F841 — must stay referenced for callbacks
    _fire('kodi_start')
    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break
    _fire('kodi_stop')
    _log("stopped")


if __name__ == '__main__':
    main()
