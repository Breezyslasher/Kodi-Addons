"""Minimal stand-ins for the Kodi Python modules, so the engine can be
imported and unit-tested outside Kodi. Import this before `engine`."""
import json
import sys
import types


def install(json_rpc_responses=None):
    """Install xbmc* and common stubs into sys.modules.

    json_rpc_responses: optional dict mapping JSON-RPC method name to the
    'result' payload xbmc.executeJSONRPC should return for it.
    """
    responses = json_rpc_responses or {}

    xbmc = types.ModuleType('xbmc')
    xbmc.LOGINFO, xbmc.LOGDEBUG = 1, 0
    xbmc.LOGWARNING, xbmc.LOGERROR = 2, 3
    xbmc.log = lambda msg, level=1: None
    xbmc.getInfoLabel = lambda label: ''
    xbmc.getCondVisibility = lambda cond: False

    def execute_json_rpc(query):
        method = json.loads(query).get('method')
        if method in responses:
            return json.dumps({'result': responses[method]})
        return json.dumps({'error': {'code': -1}})

    xbmc.executeJSONRPC = execute_json_rpc
    xbmc.Player = type('Player', (), {'__init__': lambda self: None})
    xbmc.Monitor = object
    xbmc.PLAYLIST_MUSIC = 0
    xbmc.PLAYLIST_VIDEO = 1

    class PlayList:
        instances = []

        def __init__(self, playlist_type):
            self.playlist_type = playlist_type
            self.entries = []
            PlayList.instances.append(self)

        def clear(self):
            self.entries = []

        def add(self, url, listitem=None, index=-1):
            self.entries.append(url)

    xbmc.PlayList = PlayList
    sys.modules['xbmc'] = xbmc

    for name in ('xbmcaddon', 'xbmcgui', 'xbmcvfs'):
        sys.modules[name] = types.ModuleType(name)

    common = types.ModuleType('common')
    common.notifications = []
    for fn in ('log', 'addon', 'device_name', 'session_file',
               'status_file', 'load_json', 'save_json'):
        setattr(common, fn, lambda *a, **k: None)
    common.notify = lambda msg, *a, **k: common.notifications.append(msg)
    sys.modules['common'] = common
    return xbmc, common
