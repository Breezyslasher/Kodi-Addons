# -*- coding: utf-8 -*-
# Background service
#
# Two jobs, both of which need to outlive a single plugin invocation:
#
#   * register the addon's folders with PseudoTV Live
#   * tell Tubi how far into a film the viewer got, so its own apps and this
#     addon's Continue Watching agree
#
# The plugin cannot do the second itself: by the time playback stops, the
# process that resolved the stream is long gone. It leaves what is playing in
# a window property and this service watches the player.
#
import json

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib.pseudotv import regPseudoTV
from resources.lib.tubi_api import TubiApi, TubiApiError
from resources.lib.tubi_auth import TubiAuth, TubiAuthError

ADDON_ID = 'plugin.video.tubitv'
PLAYING = 'tubitv.playing'
# Below this Tubi is better off not hearing about it, and near the end the
# viewer has finished rather than paused.
MIN_POSITION = 30
POLL = 5


class TubiMonitor(xbmc.Player):
    """Keeps the last known position of whatever Tubi title is playing."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self.item = None
        self.position = 0

    def onAVStarted(self):
        self.item = self.currentItem()
        self.position = 0

    def onPlayBackStopped(self):
        self.report()

    def onPlayBackEnded(self):
        self.report()

    @staticmethod
    def currentItem():
        try:
            playing = xbmcgui.Window(10000).getProperty(PLAYING)
            return json.loads(playing) if playing else None
        except ValueError:
            return None

    def tick(self):
        """Remember where we are - getTime() is gone once playback stops."""
        if self.item is None:
            return
        try:
            if self.isPlayingVideo():
                self.position = int(self.getTime())
        except RuntimeError:
            pass

    def report(self):
        item, position = self.item, self.position
        self.item = None
        xbmcgui.Window(10000).clearProperty(PLAYING)
        if item is None:
            return
        if position < MIN_POSITION:
            xbmc.log(msg='%s : only %ss watched, not reporting' % (ADDON_ID, position),
                     level=xbmc.LOGDEBUG)
            return
        try:
            addon = xbmcaddon.Addon(ADDON_ID)
            auth = TubiAuth(addon)
            headers = {}
            auth.apply(headers)
            api = TubiApi(headers, auth.deviceId, userId=auth.userId)
            api.reportProgress(item['content_id'], item['content_type'], position,
                               parentId=item.get('parent_id'))
            xbmc.log(msg='%s : reported %ss of %s %s' % (ADDON_ID, position,
                                                         item['content_type'], item['content_id']),
                     level=xbmc.LOGDEBUG)
        except (TubiApiError, TubiAuthError, KeyError) as err:
            xbmc.log(msg='%s : could not report progress : %s' % (ADDON_ID, err),
                     level=xbmc.LOGWARNING)


def run():
    xbmc.log(msg='%s : service started' % ADDON_ID, level=xbmc.LOGINFO)
    monitor = xbmc.Monitor()
    player = TubiMonitor()
    pseudotv = regPseudoTV()
    waited = 0
    while not monitor.abortRequested():
        player.tick()
        # PseudoTV only needs looking at occasionally, the player does not
        if waited <= 0:
            waited = pseudotv.refresh()
        waited -= POLL
        if monitor.waitForAbort(POLL):
            break


if __name__ == '__main__':
    run()
