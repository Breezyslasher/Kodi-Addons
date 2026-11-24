#!/usr/bin/env python3
"""The fixes for the 2026-11-22 audit of plugin.video.tubitv, outside Kodi.

One section per finding. Each drives the real code with Kodi's modules and
`requests` stood in for, so what is checked is the addon's own behaviour.

    python3 -I tools/test_audit_fixes.py
"""
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
ROOT = os.path.dirname(ADDON)

LOG, BUILTINS, SHOWN, PROMPTS = [], [], [], []
SETTINGS = {}
WINDOW = {}
failures = []


def check(what, got, want):
    if got == want:
        print('  ok   %s' % what)
    else:
        print('  FAIL %s : %r, wanted %r' % (what, got, want))
        failures.append(what)


def stub_kodi(profile):
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    xbmc.log = lambda msg='', level=0: LOG.append(msg)
    xbmc.executebuiltin = lambda cmd, wait=False: BUILTINS.append(cmd)
    xbmc.getCondVisibility = lambda cond: False
    xbmc.getInfoLabel = lambda label: ''
    xbmc.sleep = lambda ms: None
    xbmc.VideoStreamDetail = object

    class Player(object):
        def __init__(self, *a, **k):
            pass

    class Monitor(object):
        def abortRequested(self):
            return False

        def waitForAbort(self, secs=0):
            return False

    xbmc.Player, xbmc.Monitor = Player, Monitor

    xbmcgui = types.ModuleType('xbmcgui')

    class Window(object):
        def __init__(self, wid=0):
            pass

        def getProperty(self, key):
            return WINDOW.get(key, '')

        def setProperty(self, key, value):
            WINDOW[key] = value

        def clearProperty(self, key):
            WINDOW.pop(key, None)

    class WindowXMLDialog(object):
        def __init__(self, *a, **k):
            pass

        def show(self):
            SHOWN.append('shown')

        def close(self):
            pass

    xbmcgui.Window = Window
    xbmcgui.WindowXMLDialog = xbmcgui.WindowXML = WindowXMLDialog
    xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU = 92, 10
    xbmcgui.ListItem = lambda *a, **k: types.SimpleNamespace()
    xbmcgui.Dialog = lambda: types.SimpleNamespace(
        notification=lambda *a, **k: None, ok=lambda *a, **k: None,
        input=lambda *a, **k: (PROMPTS.append(1), ASKED['answer'])[1])

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)

    xbmcplugin = types.ModuleType('xbmcplugin')
    for name in ('addDirectoryItems', 'endOfDirectory', 'setContent', 'addSortMethod'):
        setattr(xbmcplugin, name, lambda *a, **k: None)

    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        def __init__(self, id=None):
            self.id = id or 'plugin.video.tubitv'

        def getAddonInfo(self, key):
            return {'id': self.id, 'name': 'TubiTV', 'version': '0',
                    'path': os.path.join(ROOT, 'script.module.skipbuttons'),
                    'profile': profile}.get(key, '')

        def getSetting(self, key):
            return SETTINGS.get(key, '')

        def setSetting(self, key, value):
            SETTINGS[key] = value

        def getLocalizedString(self, sid):
            return {30000: 'recap', 30001: 'intro', 30002: 'next %d',
                    30003: 'credits'}.get(sid, '')

    xbmcaddon.Addon = Addon

    t1mlib = types.ModuleType('t1mlib')

    class t1mAddon(object):
        def __init__(self, *a, **k):
            pass

    t1mlib.t1mAddon = t1mAddon

    # requests, with every verb refusing by default. A test that wants a
    # particular answer sets REPLY.
    requests = types.ModuleType('requests')

    class RequestException(Exception):
        pass

    def verb(*a, **k):
        reply = REPLY['post']
        if isinstance(reply, Exception):
            raise reply
        return reply

    for name in ('get', 'post', 'patch', 'delete'):
        setattr(requests, name, verb)
    requests.exceptions = types.SimpleNamespace(RequestException=RequestException)
    requests.RequestException = RequestException

    for name, module in (('xbmc', xbmc), ('xbmcgui', xbmcgui), ('xbmcaddon', xbmcaddon),
                         ('xbmcvfs', xbmcvfs), ('xbmcplugin', xbmcplugin),
                         ('t1mlib', t1mlib), ('requests', requests)):
        sys.modules[name] = module
    return Addon(), requests


ASKED = {'answer': ''}
REPLY = {'post': None}

import tempfile                                                    # noqa: E402
PROFILE = tempfile.mkdtemp()
ADDON_OBJ, REQUESTS = stub_kodi(PROFILE)
sys.path.insert(0, os.path.join(ROOT, 'script.module.skipbuttons', 'lib'))
sys.path.insert(0, ADDON)
sys.path.insert(0, os.path.join(ADDON, 'resources', 'lib'))

sys.argv = ['plugin://plugin.video.tubitv/', '1', '']


def reply(status=200, body=None):
    """A stand-in requests response."""
    return types.SimpleNamespace(status_code=status, json=lambda: body or {},
                                 content=b'{}', cookies={}, text='')


# ------------------------------------------------- 1. offline is not a crash
print('1. a connection error arrives as TubiAuthError (tubi_auth.py _post)')
from tubi_auth import TubiAuth, TubiAuthError                       # noqa: E402

auth = TubiAuth(ADDON_OBJ)
REPLY['post'] = REQUESTS.exceptions.RequestException('connection refused')
try:
    auth.anonymousToken()
    got = 'no error'
except TubiAuthError as err:
    got = 'TubiAuthError'
except Exception as err:
    got = type(err).__name__
check('anonymousToken wraps it', got, 'TubiAuthError')

# apply() catches TubiAuthError and nothing else, so this is what decides
# whether default.py survives being offline.
try:
    auth.apply({})
    got = 'no error'
except TubiAuthError:
    got = 'TubiAuthError'
except Exception as err:
    got = type(err).__name__
check('apply reports it rather than letting it escape', got, 'TubiAuthError')

# ------------------------------------- 4. new credentials, no old account
print('\n4. changing to credentials that fail forgets the old account')
auth.cache = {'device_id': 'dev',
              'user': {'access_token': 'OLD-ACCOUNT-TOKEN',
                       'expires_at': time.time() + 9999,
                       'credentials': 'fingerprint-of-the-old-pair'}}
SETTINGS['login_name'], SETTINGS['login_pass'] = 'new@example.com', 'whatever'
REPLY['post'] = REQUESTS.exceptions.RequestException('sign-in refused')
try:
    auth.session()
except TubiAuthError:
    pass
check('the old session is gone from the cache', auth.cache.get('user'), None)
# bearer() reads the cache, which is the path the old token came back on.
try:
    bearer = auth.bearer()
except TubiAuthError:
    bearer = None
check('and is not handed out as the bearer', bearer, None)

# -------------------------------------------- 2. the service stays anonymous
print('\n2. a refused sign-in does not cost the service its api')
import service                                                      # noqa: E402


class Auth(object):
    """apply() fills the headers, then reports the sign-in that failed."""

    deviceId, userId = 'dev', None

    def __init__(self, addon):
        pass

    def apply(self, headers):
        headers['Authorization'] = 'Bearer anonymous-device-token'
        # service.py imports its own reference to this class; raising any
        # other copy of it would not be caught, which is a property of the
        # test's import path and not of the addon.
        raise service.TubiAuthError('login failed (401)')


service.TubiAuth = Auth
got = service.api()
check('an api is still built', got is not None, True)
check('on the anonymous bearer', (got.headers if got else {}).get('Authorization'),
      'Bearer anonymous-device-token')


class DeadAuth(Auth):
    def apply(self, headers):
        raise service.TubiAuthError('could not reach Tubi')


service.TubiAuth = DeadAuth
check('but nothing at all means no api', service.api(), None)

# ------------------------------------------------ 3. no stale playing property
print('\n3. a property that was never played is not applied to the next video')
asked = []


class Controller(object):
    def __init__(self, player, play_next=None, labels=None, autoplay=None):
        pass

    def reset(self, marks=None, has_next=False, credits=True):
        asked.append(('reset', marks))

    def tick(self, position, now):
        pass


service.SkipController = Controller
player = service.TubiMonitor()
player.isPlayingVideo = lambda: True
player.getTime = lambda: 0.0

fresh = {'content_id': 1, 'content_type': 'movie', 'report': False,
         'markers': {'intro': (10, 40)}, 'at': time.time()}
WINDOW['tubitv.playing'] = json.dumps(fresh)
check('a fresh property is used', (player.currentItem() or {}).get('content_id'), 1)

stale = dict(fresh, content_id=2, at=time.time() - service.STALE - 1)
WINDOW['tubitv.playing'] = json.dumps(stale)
check('an old one is not', player.currentItem(), None)
check('and is cleared out of the way', WINDOW.get('tubitv.playing'), None)

# A title that fails to open never reaches onAVStarted.
WINDOW['tubitv.playing'] = json.dumps(fresh)
player.item = None
player.onPlayBackError()
check('a failed play clears the property', WINDOW.get('tubitv.playing'), None)
WINDOW['tubitv.playing'] = json.dumps(dict(fresh, content_id=3))
player.onAVStarted()
check('so the next video is not taken for it',
      (player.item or {}).get('content_id'), 3)

# ------------------------------------------------------ 5. an int history id
print('\n5. a history id Tubi sends as a number')
import scraper                                                      # noqa: E402

me = object.__new__(scraper.myAddon)
me._watching = {'555': {'history_id': 12345, 'position': 10, 'length': 100}}
me._saved = set()
me.localLang = lambda sid: 'label %s' % sid
me.addonIcon = me.addonFanart = ''
extras = me.extrasFor('555', {'type': 'v'}, False)
unwatch = [a for _, a in extras if 'unwatch' in a]
check('the context menu entry is built', len(unwatch), 1)
check('with the id as text', 'unwatch%7C12345' in unwatch[0], True)

# ----------------------------------------------- 6. search survives a refresh
print('\n6. a refreshed search repeats the search, not the keyboard')
me.api = types.SimpleNamespace(search=lambda q: [{'id': 9, 'title': q, 'type': 'v'}])
me.addContent = lambda content, ilist, inList=False: ilist + [content['title']]


def listing(url):
    """What Kodi is handed, the way t1mlib 4.0.9's procDir builds it:

        ilist = dirFunc(url, [])
        xbmcplugin.addDirectoryItems(ih, ilist, len(ilist))
        xbmcplugin.endOfDirectory(ih, cacheToDisc=cache2Disc)

    The listing is whatever the handler returns, then closed as a success.
    The first attempt at this fix returned nothing from the entry and
    relied on a queued Container.Update - which this would have shown as
    the empty search it was in Kodi, had the test asked.
    """
    BUILTINS[:], PROMPTS[:] = [], []
    return me.getAddonMovies(url, [])


WINDOW.pop(scraper.LAST_SEARCH, None)
WINDOW.pop(scraper.REFRESHING, None)
ASKED['answer'] = 'blade runner'
check('opening search asks', (listing('search'), len(PROMPTS)), (['blade runner'], 1))
check('and lists the results itself, nothing queued', BUILTINS, [])

# "Add to My List" on a result: the action refreshes the listing it was in.
me.refreshListing()
check('the action refreshes', BUILTINS, ['Container.Refresh'])
ASKED['answer'] = 'SHOULD NOT BE ASKED'
check('the refreshed search repeats the search', listing('search'), ['blade runner'])
check('without the keyboard', len(PROMPTS), 0)

# The note is for that one refresh only.
ASKED['answer'] = 'alien'
check('opening search afterwards asks again', (listing('search'), len(PROMPTS)), (['alien'], 1))

# A note too old to be that refresh is not taken for one.
WINDOW[scraper.REFRESHING] = str(time.time() - scraper.REFRESH_WINDOW - 1)
ASKED['answer'] = 'brazil'
check('an old note is ignored', (listing('search'), len(PROMPTS)), (['brazil'], 1))

# Backing out of the keyboard lists nothing and forgets nothing.
ASKED['answer'] = ''
check('an empty answer lists nothing', listing('search'), [])
check('and keeps the last search', WINDOW.get(scraper.LAST_SEARCH), 'brazil')

# A refresh of some other listing, then search opened straight after, would
# repeat the last search rather than ask: the one imprecision left, and the
# reason the window is ten seconds and not longer.

print('\n%d failed' % len(failures) if failures else '\nall ok')
sys.exit(1 if failures else 0)
