# -*- coding: utf-8 -*-
# KodiAddon
#
import re
import sys

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib.scraper import myAddon
from resources.lib.tubi_auth import TubiAuth, TubiAuthError

ADDON_ID = 'plugin.video.tubitv'

__settings__ = xbmcaddon.Addon(ADDON_ID)

# Start of Module
addonName = re.search(r'plugin\://plugin\.video\.(.+?)/', str(sys.argv[0])).group(1)
ma = myAddon(addonName)

# Tubi is free to browse, so a missing or rejected sign-in is not fatal - the
# addon carries on anonymously and only the account features are lost.
ma.auth = TubiAuth(__settings__)
try:
    ma.auth.apply(ma.defaultHeaders)
except TubiAuthError as err:
    xbmc.log(msg='%s : sign-in failed : %s' % (ADDON_ID, err), level=xbmc.LOGWARNING)
    if err.fresh:
        xbmcgui.Dialog().notification(__settings__.getAddonInfo('name'),
                                      __settings__.getLocalizedString(30021) % err,
                                      xbmcgui.NOTIFICATION_WARNING)

ma.processAddonEvent()
