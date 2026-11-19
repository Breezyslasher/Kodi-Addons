# -*- coding: utf-8 -*-
# Background service
#
# Three jobs, all of which need to outlive a single plugin invocation:
#
#   * register the addon's folders with PseudoTV Live
#   * tell Tubi how far into a film the viewer got, so its own apps and this
#     addon's Continue Watching agree
#   * show the Skip recap, Skip intro and Next episode buttons
#
# The plugin cannot do the last two itself: by the time playback starts, the
# process that resolved the stream is already on its way out. It leaves what
# is playing in a window property and this service watches the player.
#
import json
import threading
import time
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui

from skipbuttons import SkipController

from resources.lib.pseudotv import regPseudoTV
from resources.lib.tubi_api import EPISODE, TubiApi, TubiApiError
from resources.lib.tubi_auth import TubiAuth, TubiAuthError

ADDON_ID = 'plugin.video.tubitv'
PLAYING = 'tubitv.playing'
PLAY_URL = ''.join(['plugin://', ADDON_ID, '/?mode=GV&url=%s'])
# Below this Tubi is better off not hearing about it, and near the end the
# viewer has finished rather than paused.
MIN_POSITION = 30
# The buttons appear and hide on the second and the credits count down in
# seconds, so a Tubi video is watched closely. Nothing else here needs it.
POLL_PLAYING = 1
POLL_IDLE = 5


def asInt(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def api():
    """An authenticated TubiApi, or None if Tubi cannot be reached."""
    try:
        auth = TubiAuth(xbmcaddon.Addon(ADDON_ID))
        headers = {}
        auth.apply(headers)
        return TubiApi(headers, auth.deviceId, userId=auth.userId)
    except (TubiApiError, TubiAuthError) as err:
        xbmc.log(msg='%s : no api in the service : %s' % (ADDON_ID, err),
                 level=xbmc.LOGWARNING)
        return None


def nextEpisode(tubi, item):
    """The episode after this one, as the `episode|series|season` url.

    Within the season first; at the end of one, the first episode of the
    next season that has any. None for a film, for the last episode of the
    last season, and when the season is unknown - an episode opened by its
    own id names its series but no season, and a season is what Tubi
    publishes the streams in.
    """
    seriesId, season = item.get('series_id'), item.get('season')
    if not seriesId or season is None:
        return None
    here = str(item.get('content_id'))
    try:
        ids = [str(episode.get('id')) for episode in tubi.episodes(seriesId, season)]
        if here in ids:
            at = ids.index(here)
            if at + 1 < len(ids):
                return '|'.join([ids[at + 1], str(seriesId), str(season)])
        else:
            # The season holding this episode is not the one asked for, so
            # which episode comes next cannot be said.
            return None
        for entry in sorted([s for s in tubi.seasons(seriesId)
                             if s.get('episodes')
                             and (asInt(s.get('season')) or 0) > (asInt(season) or 0)],
                            key=lambda s: asInt(s.get('season')) or 0):
            following = tubi.episodes(seriesId, entry.get('season'))
            if following:
                return '|'.join([str(following[0].get('id')), str(seriesId),
                                 str(entry.get('season'))])
    except (TubiApiError, KeyError, ValueError) as err:
        xbmc.log(msg='%s : could not look up the next episode : %s' % (ADDON_ID, err),
                 level=xbmc.LOGWARNING)
    return None


class TubiMonitor(xbmc.Player):
    """Keeps the last known position of whatever Tubi title is playing."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self.item = None
        self.position = 0
        self.nextUrl = None
        # Which playback a background look-up belongs to, so an answer that
        # arrives after the viewer has moved on is dropped.
        self.generation = 0
        self.skip = SkipController(
            self, self.playNext,
            autoplay=lambda: self.setting('autoplay_next') != 'false')

    @staticmethod
    def setting(key):
        try:
            return xbmcaddon.Addon(ADDON_ID).getSetting(key)
        except RuntimeError:
            return ''

    def onAVStarted(self):
        # The property belongs to this playback - the plugin wrote it on the
        # way to resolving the stream - so it is taken before reporting the
        # last title, which is what clears it.
        starting = self.currentItem()
        self.report()           # a previous title that never reported stopping
        self.item = starting
        self.position = 0
        self.nextUrl = None
        self.generation += 1
        if self.item is None:
            # Not ours: a live channel, a trailer, or another addon's video.
            self.skip.reset()
            return
        marks = self.item.get('markers') or {}
        # The markers come with the title itself, so the buttons can start
        # straight away; only the next episode needs asking about, and that
        # is not wanted until the credits.
        self.skip.reset(marks)
        xbmc.log(msg='%s : markers %s' % (ADDON_ID, marks or 'none'),
                 level=xbmc.LOGDEBUG)
        if self.item.get('content_type') == EPISODE:
            threading.Thread(target=self.findNext,
                             args=(self.generation, self.item, marks)).start()

    def findNext(self, generation, item, marks):
        """Look up the next episode without holding playback up."""
        tubi = api()
        found = nextEpisode(tubi, item) if tubi is not None else None
        if generation != self.generation:
            return              # playback moved on meanwhile
        self.nextUrl = found
        self.skip.reset(marks, has_next=bool(found))
        xbmc.log(msg='%s : next episode %s' % (ADDON_ID, found or 'none'),
                 level=xbmc.LOGDEBUG)

    def playNext(self):
        """Start the next episode, telling Tubi about this one first."""
        following = self.nextUrl
        self.report()
        if not following:
            return
        xbmc.log(msg='%s : playing the next episode %s' % (ADDON_ID, following),
                 level=xbmc.LOGINFO)
        xbmc.executebuiltin('PlayMedia(%s)' % (PLAY_URL % urllib.parse.quote_plus(following)))

    def onPlayBackStopped(self):
        self.skip.reset()
        self.report()

    def onPlayBackEnded(self):
        self.skip.reset()
        self.report()

    @staticmethod
    def currentItem():
        try:
            playing = xbmcgui.Window(10000).getProperty(PLAYING)
            return json.loads(playing) if playing else None
        except ValueError:
            return None

    def playing(self):
        """Whether one of ours is on screen, i.e. whether to watch closely."""
        if self.item is None:
            return False
        try:
            return self.isPlayingVideo()
        except RuntimeError:
            return False

    def tick(self):
        """Remember where we are - getTime() is gone once playback stops."""
        if self.item is None:
            return
        try:
            if not self.isPlayingVideo():
                return
            position = self.getTime()
        except RuntimeError:
            return
        self.position = int(position)
        self.skip.tick(position, time.time())

    def report(self):
        item, position = self.item, self.position
        self.item = None
        self.nextUrl = None
        xbmcgui.Window(10000).clearProperty(PLAYING)
        # The property is written for the buttons whether or not progress
        # reporting is wanted; `report` is which of the two was asked for.
        if item is None or not item.get('report'):
            return
        if position < MIN_POSITION:
            xbmc.log(msg='%s : only %ss watched, not reporting' % (ADDON_ID, position),
                     level=xbmc.LOGDEBUG)
            return
        tubi = api()
        if tubi is None:
            return
        try:
            tubi.reportProgress(item['content_id'], item['content_type'], position,
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
        # PseudoTV only needs looking at occasionally, the player does not.
        # waited counts in seconds either way, so PseudoTV's own timing is
        # unchanged by watching the player more closely.
        if waited <= 0:
            waited = pseudotv.refresh()
        interval = POLL_PLAYING if player.playing() else POLL_IDLE
        waited -= interval
        if monitor.waitForAbort(interval):
            break


if __name__ == '__main__':
    run()
