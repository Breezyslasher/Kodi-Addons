#!/usr/bin/env python3
"""Does the Tubi service drive the skip buttons correctly, outside Kodi?

Stands in for Kodi's modules and for script.module.skipbuttons' dialog, then
plays an episode past its intro and into its credits a second at a time and
checks what the buttons were asked to do.

    python3 -I tools/test_skipbuttons.py

The payloads are the ones measured on 2026-11-19 with the anonymous token
(see skipMarkers' docstring): Forensic Files S1 E1 438034 and Rush Hour
570192.
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
ROOT = os.path.dirname(ADDON)

EPISODE_438034 = {
    'id': '438034', 'detailed_type': 'episode', 'series_id': '2914',
    'episode_number': '1', 'duration': 1369,
    'credit_cuepoints': {'intro_start': 82.98, 'intro_end': 111.9,
                         'recap_start': 0.0, 'recap_end': 0.0,
                         'earlycredits_start': 0.0, 'earlycredits_end': 0.0,
                         'postlude': 1333.65},
}
FILM_570192 = {
    'id': '570192', 'detailed_type': 'movie', 'duration': 5760,
    'credit_cuepoints': {'intro_start': 0.0, 'intro_end': 0.0,
                         'recap_start': 0.0, 'recap_end': 0.0,
                         'earlycredits_start': 0.0, 'earlycredits_end': 0.0,
                         'postlude': 5715.18},
}
# The film's postlude is 44.8s from the end, further than CREDITS_TAIL, so it
# counts. A trailer-length one would not.


def stub_kodi():
    """Enough of xbmc, xbmcgui, xbmcaddon and xbmcvfs for both modules."""
    window = {}

    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    xbmc.log = lambda msg='', level=0: LOG.append(msg)
    xbmc.executebuiltin = lambda cmd, wait=False: LOG.append('builtin: %s' % cmd)
    xbmc.getCondVisibility = lambda cond: False
    xbmc.getInfoLabel = lambda label: ''
    xbmc.sleep = lambda ms: None
    xbmc.translatePath = lambda p: p

    class Player(object):
        def __init__(self, *a, **k):
            pass

    class Monitor(object):
        def abortRequested(self):
            return False

        def waitForAbort(self, secs=0):
            return False

    xbmc.Player, xbmc.Monitor = Player, Monitor
    # The addon checks for this to decide which InfoTagVideo calls to make.
    xbmc.VideoStreamDetail = object

    xbmcgui = types.ModuleType('xbmcgui')

    class Window(object):
        def __init__(self, wid=0):
            pass

        def getProperty(self, key):
            return window.get(key, '')

        def setProperty(self, key, value):
            window[key] = value

        def clearProperty(self, key):
            window.pop(key, None)

    class WindowXMLDialog(object):
        """The buttons' dialog: records what it was shown, never draws."""

        def __init__(self, *a, **k):
            pass

        def show(self):
            SHOWN.append(getattr(self, 'label', '?'))

        def close(self):
            SHOWN.append('closed')

        def doModal(self):
            pass

        def getControl(self, cid):
            raise RuntimeError('no skin in a test')

        def setProperty(self, key, value):
            pass

    xbmcgui.Window = Window
    xbmcgui.WindowXMLDialog = WindowXMLDialog
    xbmcgui.WindowXML = WindowXMLDialog
    xbmcgui.Dialog = lambda: types.SimpleNamespace(
        notification=lambda *a, **k: None, ok=lambda *a, **k: None)
    xbmcgui.ListItem = lambda *a, **k: types.SimpleNamespace()
    xbmcgui.ACTION_NAV_BACK = 92
    xbmcgui.ACTION_PREVIOUS_MENU = 10
    xbmcgui.ControlButton = object

    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        def __init__(self, id=None):
            self.id = id or 'plugin.video.tubitv'

        def getAddonInfo(self, key):
            return {'id': self.id, 'name': 'TubiTV', 'version': '0',
                    'path': os.path.join(ROOT, 'script.module.skipbuttons'),
                    'profile': '/tmp'}.get(key, '')

        def getSetting(self, key):
            return SETTINGS.get(key, '')

        def getSettingBool(self, key):
            return SETTINGS.get(key) == 'true'

        def getLocalizedString(self, sid):
            # The buttons' own four labels; 30002 carries the countdown.
            return {30000: 'recap', 30001: 'intro', 30002: 'next %d',
                    30003: 'credits'}.get(sid, '')

    xbmcaddon.Addon = Addon

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda p: None

    xbmcplugin = types.ModuleType('xbmcplugin')
    for name in ('addDirectoryItems', 'endOfDirectory', 'setContent',
                 'addSortMethod'):
        setattr(xbmcplugin, name, lambda *a, **k: None)

    # script.module.t1mlib is a separate add-on, not part of this repository;
    # skipMarkers is a module-level function and needs none of it.
    t1mlib = types.ModuleType('t1mlib')

    class t1mAddon(object):
        def __init__(self, *a, **k):
            pass

    t1mlib.t1mAddon = t1mAddon

    # Nothing here talks to Tubi; a stand-in keeps the test to the standard
    # library, and makes a request that slipped through fail loudly.
    def refuse(*a, **k):
        raise AssertionError('the test must not reach the network')

    requests = types.ModuleType('requests')
    for verb in ('get', 'post', 'patch', 'delete'):
        setattr(requests, verb, refuse)
    requests.exceptions = types.SimpleNamespace(RequestException=Exception)

    for name, module in (('xbmc', xbmc), ('xbmcgui', xbmcgui),
                         ('xbmcaddon', xbmcaddon), ('xbmcvfs', xbmcvfs),
                         ('xbmcplugin', xbmcplugin), ('t1mlib', t1mlib),
                         ('requests', requests)):
        sys.modules[name] = module
    return window


LOG, SHOWN = [], []
SETTINGS = {'autoplay_next': 'true', 'report_progress': 'false'}
WINDOW = stub_kodi()

sys.path.insert(0, os.path.join(ROOT, 'script.module.skipbuttons', 'lib'))
sys.path.insert(0, ADDON)
sys.path.insert(0, os.path.join(ADDON, 'resources', 'lib'))

failures = []


def check(what, got, want):
    if got == want:
        print('  ok   %s' % what)
    else:
        print('  FAIL %s : %r, wanted %r' % (what, got, want))
        failures.append(what)


# ----------------------------------------------------------------- markers
print('skipMarkers')
from scraper import skipMarkers        # noqa: E402

check('episode intro', skipMarkers(EPISODE_438034).get('intro'), (82.98, 111.9))
check('episode credits', skipMarkers(EPISODE_438034).get('credits'), 1333.65)
check('episode has no recap', 'recap' in skipMarkers(EPISODE_438034), False)
check('film has only credits', skipMarkers(FILM_570192), {'credits': 5715.18})
check('no cuepoints at all', skipMarkers({'duration': 100}), {})
check('a zero intro is no intro',
      skipMarkers({'credit_cuepoints': {'intro_start': 0.0, 'intro_end': 0.0}}), {})
check('a 3s intro is noise',
      'intro' in skipMarkers({'credit_cuepoints': {'intro_start': 10.0, 'intro_end': 13.0}}),
      False)
check('credits at the very end are the end',
      skipMarkers({'duration': 1369, 'credit_cuepoints': {'postlude': 1367.0}}), {})

# --------------------------------------------------------- the whole chain
print('\nthe service, playing episode 438034')
import service                          # noqa: E402

# What the plugin leaves behind, built by the real rememberPlaying path.
WINDOW['tubitv.playing'] = json.dumps(
    {'content_id': 438034, 'content_type': 'episode', 'duration': 1369,
     'report': False, 'markers': skipMarkers(EPISODE_438034),
     'parent_id': '2914', 'series_id': '2914', 'season': '1',
     'episode_number': '1'})

asked = []


class Controller(object):
    """Stands in for SkipController: records, so the wiring is what is tested."""

    def __init__(self, player, play_next=None, labels=None, autoplay=None):
        self.player, self.play_next, self.autoplay = player, play_next, autoplay

    def reset(self, marks=None, has_next=False, credits=True):
        asked.append(('reset', marks, has_next, credits))

    def tick(self, position, now):
        asked.append(('tick', int(position)))


service.SkipController = Controller
# No Tubi from here: the next episode is answered from the season above.
service.api = lambda: types.SimpleNamespace(
    episodes=lambda seriesId, season, expected=None:
        [{'id': '438034'}, {'id': '438035'}] if str(season) == '1' else [],
    seasons=lambda seriesId: [{'season': 1, 'episodes': [1, 2]}],
    reportProgress=lambda *a, **k: None)

player = service.TubiMonitor()
player.isPlayingVideo = lambda: True
position = {'at': 0.0}
player.getTime = lambda: position['at']

player.onAVStarted()
check('the title was read from the property', player.item['content_id'], 438034)
# JSON has no tuple, so the pair arrives as a list. The module indexes it,
# which is the same either way - and the real controller is driven below.
check('the buttons got the markers', asked[0][1].get('intro'), [82.98, 111.9])

# The look-up runs on a thread of its own; wait for it as the loop would.
for _ in range(200):
    if player.nextUrl is not None:
        break
    import time as _t
    _t.sleep(0.01)
check('the next episode was found', player.nextUrl, '438035|2914|1')
check('the buttons were told there is a next',
      [a for a in asked if a[0] == 'reset'][-1][2], True)

for second in (10, 90, 120, 1200, 1335, 1340):
    position['at'] = float(second)
    player.tick()
check('every second was passed on',
      [a[1] for a in asked if a[0] == 'tick'], [10, 90, 120, 1200, 1335, 1340])
check('the position is remembered for reporting', player.position, 1340)

# The credits countdown ran out: play the next one.
LOG[:] = []
player.playNext()
check('the next episode was started',
      [l for l in LOG if l.startswith('builtin:')],
      ['builtin: PlayMedia(plugin://plugin.video.tubitv/?mode=GV&url=438035%7C2914%7C1)'])
check('the property was cleared', WINDOW.get('tubitv.playing'), None)

print('\nthe service, playing film 570192')
asked[:] = []
WINDOW['tubitv.playing'] = json.dumps(
    {'content_id': 570192, 'content_type': 'movie', 'duration': 5760,
     'report': False, 'markers': skipMarkers(FILM_570192)})
player.onAVStarted()
check('a film gets its credits time', asked[0][1], {'credits': 5715.18})
check('and is never asked about a next episode', player.nextUrl, None)
player.onPlayBackEnded()
check('stopping closes the buttons', asked[-1], ('reset', None, False, True))

print('\nsomeone else\'s video')
asked[:] = []
player.onAVStarted()
check('nothing of ours is playing', player.item, None)
check('the buttons were closed', asked, [('reset', None, False, True)])

print('\nthe real controller, driven a second at a time')
# No recording stand-in this time: the module itself, with only its dialog
# replaced, so what the markers do is what Kodi would do with them.
import skipbuttons                       # noqa: E402

opened, started = [], []


class Dialog(object):
    def __init__(self, *a, **k):
        self.choice = None
        self.primary_label = self.secondary_label = ''

    def show(self):
        opened.append(self.primary_label)

    def close(self):
        pass

    def set_primary(self, label):
        self.primary_label = label


skipbuttons.SkipDialog = Dialog
controller = skipbuttons.SkipController(
    types.SimpleNamespace(seekTime=lambda t: started.append('seek %s' % t)),
    play_next=lambda: started.append('next episode'),
    autoplay=lambda: True)
# Exactly what crosses the window property: a list, not a tuple.
controller.reset(json.loads(json.dumps(skipMarkers(EPISODE_438034))), has_next=True)

now = 1000.0
for second in range(0, 1400, 1):
    controller.tick(float(second), now + second)

check('Skip intro was offered once', opened.count('intro'), 1)
check('it went away again, and the credits came up',
      [l for l in opened if l != 'intro'][:1], ['next 10'])
check('the next episode started by itself', started, ['next episode'])

print('\nand the viewer pressing Skip intro')
opened[:], started[:] = [], []
controller.reset(json.loads(json.dumps(skipMarkers(EPISODE_438034))), has_next=True)
controller.tick(90.0, 2000.0)
controller.dialog.choice = 'primary'
controller.tick(91.0, 2001.0)
check('it jumped to the end of the intro', started, ['seek 111.9'])

print('\nthe poll interval')
check('idle', service.POLL_IDLE, 5)
check('playing', service.POLL_PLAYING, 1)
player.item = {'markers': {}}
check('watched closely while ours plays', player.playing(), True)
player.item = None
check('and not otherwise', player.playing(), False)

print('\n%d failed' % len(failures) if failures else '\nall ok')
sys.exit(1 if failures else 0)
