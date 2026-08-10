# -*- coding: utf-8 -*-
# KodiAddon
#
import re
import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import iptv
from resources.lib.scraper import myAddon
from resources.lib.tubi_api import TubiApi
from resources.lib.tubi_auth import TubiAuth, TubiAuthError

ADDON_ID = 'plugin.video.tubitv'
LIVE_PATH = ''.join(['plugin://', ADDON_ID, '/?mode=LV&url=%s'])

__settings__ = xbmcaddon.Addon(ADDON_ID)

# Start of Module
addonName = re.search(r'plugin\://plugin\.video\.(.+?)/', str(sys.argv[0])).group(1)
ma = myAddon(addonName)

# Tubi is free to browse, so a missing or rejected sign-in is not fatal - the
# addon carries on with the anonymous device token and only the account
# features and the sign-in gated titles are lost.
ma.auth = TubiAuth(__settings__)
try:
    ma.auth.apply(ma.defaultHeaders)
except TubiAuthError as err:
    xbmc.log(msg='%s : sign-in failed : %s' % (ADDON_ID, err), level=xbmc.LOGWARNING)
    if err.fresh:
        xbmcgui.Dialog().notification(__settings__.getAddonInfo('name'),
                                      __settings__.getLocalizedString(30021) % err,
                                      xbmcgui.NOTIFICATION_WARNING)

ma.api = TubiApi(ma.defaultHeaders, ma.auth.deviceId, userId=ma.auth.userId)

# Kodi hands back a query with the '=' trimmed off any empty value, e.g.
# "?mode=GM&url", which t1mlib splits on '=' and chokes on. Put it back.
if len(sys.argv) > 2 and sys.argv[2].startswith('?'):
    args = [arg if '=' in arg else ''.join([arg, '=']) for arg in sys.argv[2][1:].split('&') if arg]
    sys.argv[2] = ''.join(['?', '&'.join(args)])

# IPTV Manager calls /iptv/channels and /iptv/epg and wants the answer on the
# socket it names, not a Kodi directory listing.
query = urllib.parse.parse_qs(sys.argv[2][1:]) if len(sys.argv) > 2 else {}
port = (query.get('port') or [None])[0]
if not iptv.handle(sys.argv[0], port, ma.api, LIVE_PATH, log=ma.log):
    ma.processAddonEvent()
