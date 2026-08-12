# -*- coding: utf-8 -*-
# KodiAddon (tubitv)
#
import json
import os
import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from t1mlib import t1mAddon

from resources.lib.tubi_api import TubiApi, TubiApiError, pickResource
from resources.lib.tubi_api import EPISODE as HISTORY_EPISODE
from resources.lib.tubi_api import MOVIE as HISTORY_MOVIE
from resources.lib.tubi_api import SERIES as QUEUE_SERIES
from resources.lib.tubi_api import (DISLIKE, LIKE, RATING_DISLIKED, RATING_LIKED,
                                    UNDISLIKE, UNLIKE)

uqp = urllib.parse.unquote_plus
qp = urllib.parse.quote_plus

# Tubi's own letter for a series in a content payload. Its watch history calls
# the same thing "series", hence the aliased import above for the other one.
SERIES = 's'
# The window property the service reads to know what is playing
PLAYING = 'tubitv.playing'

WIDEVINE_KEYSYSTEM = 'com.widevine.alpha'
# Tubi's licence server takes the raw challenge and answers with the raw
# licence, so only these two headers need setting.
LICENSE_HEADERS = [('Content-Type', 'application/octet-stream'),
                   ('Origin', 'https://tubitv.com')]
# inputstream.adaptive 21 introduced drm_legacy and deprecated the license_*
# properties. Older builds only understand the old ones.
DRM_PROPERTIES_SINCE = 21


# InfoTagVideo's own setters, and the stream detail classes, arrived in Kodi
# 20. Kodi 19 only has ListItem.setInfo()/addStreamInfo(), which everything
# after it warns about on every single list item.
INFOTAG_SETTERS = hasattr(xbmc, 'VideoStreamDetail')


def asInt(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def inputstreamMajor():
    """Major version of the installed inputstream.adaptive, 0 if unknown."""
    try:
        version = xbmcaddon.Addon('inputstream.adaptive').getAddonInfo('version')
        return int(version.split('.')[0])
    except Exception:
        return 0


class myAddon(t1mAddon):

    def __init__(self, aname):
        t1mAddon.__init__(self, aname)
        # t1mlib expects resources/icon.png and mis-joins the fanart path,
        # this addon keeps its artwork under resources/images/
        self.addonIcon = xbmcvfs.translatePath(os.path.join(self.homeDir, 'resources', 'images', 'icon.png'))
        self.addonFanart = xbmcvfs.translatePath(os.path.join(self.homeDir, 'resources', 'images', 'fanart.jpg'))
        # default.py fills these in once the tokens are sorted out
        self.auth = None
        self.api = None
        # The saved list and the part-watched list, each read once per
        # listing rather than once per item
        self._saved = None
        self._watching = None

    def addMenuItem(self, name, mode, ilist=None, url=None, thumb=None, fanart=None,
                    videoInfo=None, videoStream=None, audioStream=None,
                    subtitleStream=None, cm=None, isFolder=True):
        """As t1mlib's, but filling the info tag through its own setters.

        t1mlib still builds list items with ListItem.setInfo() and
        addStreamInfo(), which Kodi deprecated and warns about once per item -
        several lines of log for every directory. Overriding it here leaves
        the shared module alone for whatever else depends on it.
        """
        if not INFOTAG_SETTERS:
            return t1mAddon.addMenuItem(self, name, mode, ilist, url, thumb, fanart,
                                        videoInfo, videoStream, audioStream,
                                        subtitleStream, cm, isFolder)
        liz = xbmcgui.ListItem(name, offscreen=True)
        liz.setArt({'thumb': thumb, 'fanart': fanart, 'poster': thumb})
        self.fillInfoTag(liz, videoInfo or {})
        if cm is not None:
            liz.addContextMenuItems(cm)
        if not isFolder:
            liz.setProperty('IsPlayable', 'true')
        u = ''.join([sys.argv[0], '?mode=', str(mode), '&url='])
        if url is not None:
            u = ''.join([u, qp(url)])
        ilist.append((u, liz, isFolder))
        return ilist

    def fillInfoTag(self, liz, info):
        """Put an addMenuItem info dict onto a list item's video tag."""
        tag = liz.getVideoInfoTag()
        if info.get('mediatype'):
            tag.setMediaType(info['mediatype'])
        if info.get('Title'):
            tag.setTitle(info['Title'])
        if info.get('TVShowTitle'):
            tag.setTvShowTitle(info['TVShowTitle'])
        if info.get('Plot'):
            tag.setPlot(info['Plot'])
        if info.get('MPAA'):
            tag.setMpaa(info['MPAA'])
        if info.get('director'):
            tag.setDirectors([info['director']])
        if info.get('genre'):
            # The dict carries them joined for the old setInfo, split them back
            tag.setGenres([g.strip() for g in str(info['genre']).split('/') if g.strip()])
        if info.get('cast'):
            tag.setCast([xbmc.Actor(str(person)) for person in info['cast']])
        for key, setter in (('Year', tag.setYear),
                            ('duration', tag.setDuration),
                            ('Season', tag.setSeason),
                            ('Episode', tag.setEpisode)):
            value = asInt(info.get(key))
            if value is not None:
                setter(value)
        # t1mlib attaches the same nominal stream details to every item
        tag.addVideoStream(xbmc.VideoStreamDetail(
            width=self.defaultVidStream['width'],
            height=self.defaultVidStream['height'],
            aspect=self.defaultVidStream['aspect'],
            codec=self.defaultVidStream['codec']))
        tag.addAudioStream(xbmc.AudioStreamDetail(
            codec=self.defaultAudStream['codec'],
            language=self.defaultAudStream['language']))
        tag.addSubtitleStream(xbmc.SubtitleStreamDetail(
            language=self.defaultSubStream['language']))

    def note(self, txt, level=None):
        """Log at a level a normal Kodi log will actually show.

        t1mlib's log() is fixed at debug, which is fine for the routine
        chatter but hides anything worth knowing about from anyone not
        already running a debug log.
        """
        xbmc.log(msg=''.join([self.addonName, ' : ', str(txt)]),
                 level=xbmc.LOGINFO if level is None else level)

    def report(self, err):
        """Surface an API failure without taking the whole directory down."""
        self.note(''.join(['api call failed : ', str(err)]), level=xbmc.LOGWARNING)
        xbmcgui.Dialog().notification(self.addonName, self.localLang(30022),
                                      xbmcgui.NOTIFICATION_ERROR)

    # ------------------------------------------------------------- listings

    @staticmethod
    def firstOf(content, *keys):
        for key in keys:
            values = content.get(key)
            if values:
                return values[0]
        return None

    def infoOf(self, content):
        infoList = {'Title': content.get('title'),
                    'Plot': content.get('description'),
                    'Year': content.get('year'),
                    'duration': content.get('duration'),
                    'cast': content.get('actors') or [],
                    'genre': ' / '.join(content.get('tags') or [])}
        directors = content.get('directors') or []
        if len(directors) > 0:
            infoList['director'] = directors[0]
        ratings = content.get('ratings') or []
        if len(ratings) > 0:
            infoList['MPAA'] = ratings[0].get('value')
        return infoList

    def addContent(self, content, ilist, inList=False):
        """Add one film or series from the API to a directory listing."""
        contentId = str(content.get('id'))
        infoList = self.infoOf(content)
        img = self.firstOf(content, 'posterarts', 'thumbnails') or self.addonIcon
        fanart = self.firstOf(content, 'backgrounds', 'hero_images', 'landscape_images') or self.addonFanart

        if content.get('type') == SERIES:
            name = ''.join([content.get('title') or '', self.localLang(30019)])
            infoList['Title'] = name
            infoList['mediatype'] = 'tvshow'
            contextMenu = [(self.localLang(30024),
                            'RunPlugin(%s?mode=AS&url=%s)' % (sys.argv[0], qp(contentId)))]
            contextMenu.extend(self.extrasFor(contentId, content, inList))
            return self.addMenuItem(name, 'GE', ilist, contentId, img, fanart, infoList,
                                    isFolder=True, cm=contextMenu)

        infoList['mediatype'] = 'movie'
        contextMenu = [(self.localLang(30025),
                        'RunPlugin(%s?mode=AM&url=%s)' % (sys.argv[0], qp(contentId)))]
        contextMenu.extend(self.extrasFor(contentId, content, inList))
        ilist = self.addMenuItem(content.get('title'), 'GV', ilist, contentId, img, fanart,
                                 infoList, isFolder=False, cm=contextMenu)
        return self.applyResume(ilist, contentId)

    @property
    def saved(self):
        """Content ids on the viewer's saved list.

        Read once per directory so each title can be offered the right
        action, rather than asking about every item or guessing. Signed out
        there is no list to read.
        """
        if self._saved is None:
            self._saved = set()
            if self.auth is not None and self.auth.signedIn:
                try:
                    self._saved = set(str(e['content_id']) for e in self.api.queue())
                except TubiApiError as err:
                    self.log(''.join(['could not read the saved list : ', str(err)]))
        return self._saved

    @property
    def watching(self):
        """What the account has part-watched, keyed by content id.

        Each value carries the position to resume from and, for a title
        Tubi tracks in its own right, the history entry id that removing it
        from Continue Watching needs. A series is tracked as itself with its
        episodes nested, so both are indexed.
        """
        if self._watching is None:
            entries = []
            if self.auth is not None and self.auth.signedIn:
                try:
                    entries = self.api.history()
                except TubiApiError as err:
                    self.log(''.join(['could not read the watch history : ', str(err)]))
            self._watching = self.watchMap(entries)
        return self._watching

    @staticmethod
    def watchMap(entries):
        """Index a watch history by content id, episodes included."""
        watching = {}
        for entry in entries:
            watching[str(entry.get('content_id'))] = {
                'history_id': entry.get('id'),
                'position': entry.get('position'),
                'length': entry.get('content_length')}
            for episode in entry.get('episodes') or []:
                watching[str(episode.get('content_id'))] = {
                    'history_id': None,
                    'position': episode.get('position'),
                    'length': episode.get('content_length')}
        return watching

    def applyResume(self, ilist, contentId):
        """Let Kodi offer to resume where Tubi says the viewer stopped."""
        entry = self.watching.get(str(contentId)) or {}
        position, length = entry.get('position'), entry.get('length')
        if ilist and position and length:
            liz = ilist[-1][1]
            liz.setProperty('ResumeTime', str(position))
            liz.setProperty('TotalTime', str(length))
        return ilist

    def extrasFor(self, contentId, content, inList=False):
        """The context menu entries every title carries."""
        kind = QUEUE_SERIES if content.get('type') == SERIES else HISTORY_MOVIE
        inList = inList or contentId in self.saved
        listing = ['queue', 'remove' if inList else 'add', contentId, kind]
        extras = [(self.localLang(30042 if inList else 30041),
                   'RunPlugin(%s?mode=DF&url=%s)' % (sys.argv[0], qp('|'.join(listing)))),
                  (self.localLang(30034),
                   'Container.Update(%s?mode=SE&url=%s)' % (sys.argv[0], qp('|'.join(['related', contentId]))))]
        if content.get('has_trailer') or content.get('trailers'):
            extras.append((self.localLang(30035),
                           'PlayMedia(%s?mode=GV&url=%s)' % (sys.argv[0], qp('|'.join(['trailer', contentId])))))
        historyId = (self.watching.get(contentId) or {}).get('history_id')
        if historyId:
            extras.append((self.localLang(30054),
                           'RunPlugin(%s?mode=DF&url=%s)' % (
                               sys.argv[0], qp('|'.join(['unwatch', historyId])))))
        series = '1' if content.get('type') == SERIES else '0'
        for label, action in ((30045, LIKE), (30046, DISLIKE)):
            extras.append((self.localLang(label),
                           'RunPlugin(%s?mode=DF&url=%s)' % (
                               sys.argv[0], qp('|'.join(['rate', action, contentId, series])))))
        return extras

    def getAddonMenu(self, url, ilist):
        try:
            containers = self.api.browseList()
        except TubiApiError as err:
            self.report(err)
            return ilist

        # Home is Tubi's own screen and carries its Continue Watching and My
        # List rows already, so neither gets a second entry here.
        infoList = {'Title': self.localLang(30036)}
        ilist = self.addMenuItem(self.localLang(30036), 'SE', ilist, 'home',
                                 self.addonIcon, self.addonFanart, infoList, isFolder=True)
        for container in containers:
            infoList = {'Title': container.get('title'),
                        'Plot': container.get('description')}
            thumb = container.get('thumbnail') or self.addonIcon
            ilist = self.addMenuItem(container.get('title'), 'GS', ilist,
                                     container.get('id') or container.get('slug'),
                                     thumb, self.addonFanart, infoList, isFolder=True)
        infoList = {'Title': self.localLang(30031)}
        ilist = self.addMenuItem(self.localLang(30031), 'GC', ilist, 'live',
                                 self.addonIcon, self.addonFanart, infoList, isFolder=True)
        infoList = {'Title': self.localLang(30010)}
        # Never hand out an empty url - Kodi trims the '=' off an empty query
        # value and t1mlib's parameter parser cannot read the result back.
        ilist = self.addMenuItem(self.localLang(30010), 'GM', ilist, 'search',
                                 self.addonIcon, self.addonFanart, infoList, isFolder=True)
        return(ilist)

    def getAddonCats(self, url, ilist):
        """Tubi's linear channels, browsable without IPTV Manager."""
        try:
            lineUp, groups = self.api.liveChannels()
        except TubiApiError as err:
            self.report(err)
            return ilist
        for channel in lineUp:
            channelId = str(channel.get('id'))
            infoList = {'Title': channel.get('title'),
                        'Plot': channel.get('description'),
                        'genre': groups.get(channelId),
                        'mediatype': 'video'}
            images = channel.get('images') or {}
            img = self.firstOf(images, 'thumbnail', 'poster', 'landscape') or self.addonIcon
            fanart = self.firstOf(images, 'background', 'landscape') or self.addonFanart
            ilist = self.addMenuItem(channel.get('title'), 'LV', ilist, channelId, img, fanart,
                                     infoList, isFolder=False)
        return(ilist)

    def getAddonLiveVideo(self, url):
        """Resolve a linear channel by id - its manifest carries a token."""
        channelId = uqp(url)
        try:
            lineUp, _ = self.api.liveChannels()
        except TubiApiError as err:
            self.report(err)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return
        manifest = None
        for channel in lineUp:
            if str(channel.get('id')) == channelId:
                manifest, _ = pickResource(channel)
                break
        if manifest is None:
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30028),
                                          xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return
        liz = xbmcgui.ListItem(path=manifest, offscreen=True)
        liz.setMimeType('application/x-mpegURL')
        liz.setContentLookup(False)
        liz.setProperty('inputstream', 'inputstream.adaptive')
        liz.setProperty('inputstream.adaptive.manifest_type', 'hls')
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, liz)

    def getAddonShows(self, url, ilist):
        containerId, cursor = (url.split('|', 1) + ['0'])[:2]
        try:
            contents, nextCursor = self.api.container(containerId, cursor=int(cursor))
        except TubiApiError as err:
            self.report(err)
            return ilist
        for content in contents:
            ilist = self.addContent(content, ilist)
        if nextCursor is not None:
            infoList = {'Title': self.localLang(30026)}
            ilist = self.addMenuItem(self.localLang(30026), 'GS', ilist,
                                     '|'.join([containerId, str(nextCursor)]),
                                     self.addonIcon, self.addonFanart, infoList, isFolder=True)
        return(ilist)

    def getAddonSearch(self, url, ilist):
        """The listings that are not a plain category: home, list, related."""
        parts = url.split('|')
        inList = False
        try:
            if parts[0] == 'home':
                return self.homeRows(ilist)
            if parts[0] == 'mylist':
                contents = self.api.contents([str(e['content_id']) for e in self.api.queue()])
                inList = True
            elif parts[0] == 'related' and len(parts) > 1:
                contents = self.api.related(parts[1])
            else:
                return ilist
        except TubiApiError as err:
            self.report(err)
            return ilist
        for content in contents:
            ilist = self.addContent(content, ilist, inList=inList)
        return(ilist)

    def homeRows(self, ilist):
        """Tubi's own home screen, its rows as folders."""
        rows, _ = self.api.homescreen()
        for row in rows:
            infoList = {'Title': row.get('title'),
                        'Plot': row.get('description')}
            thumb = row.get('thumbnail') or self.addonIcon
            ilist = self.addMenuItem(row.get('title'), 'GS', ilist,
                                     row.get('id') or row.get('slug'),
                                     thumb, self.addonFanart, infoList, isFolder=True)
        return(ilist)

    def getAddonMovies(self, url, ilist):
        query = xbmcgui.Dialog().input(self.localLang(30010))
        if len(query) == 0:
            return ilist
        try:
            contents = self.api.search(query)
        except TubiApiError as err:
            self.report(err)
            return ilist
        for content in contents:
            ilist = self.addContent(content, ilist)
        return(ilist)

    # -------------------------------------------------------------- series

    def seasonList(self, seriesId):
        """Seasons of a series, newest API shape, or None when it has none."""
        seasons = self.api.seasons(seriesId)
        return [s for s in seasons if s.get('episodes')]

    def episodeItems(self, seriesId, season, ilist, showTitle=None):
        try:
            episodes = self.api.episodes(seriesId, season.get('season'),
                                         expected=len(season.get('episodes') or []))
        except TubiApiError as err:
            self.report(err)
            return ilist
        if showTitle is None:
            showTitle = xbmc.getInfoLabel('ListItem.TVShowTitle') or ''
        for episode in episodes:
            infoList = self.infoOf(episode)
            title = episode.get('title') or ''
            # Titles arrive as "S01:E01 - Day One", the numbers are separate
            # fields so the prefix is only noise in the episode list.
            if ' - ' in title and title.startswith('S'):
                title = title.split(' - ', 1)[1]
            infoList['Title'] = title
            infoList['TVShowTitle'] = showTitle
            infoList['Season'] = season.get('season')
            infoList['Episode'] = episode.get('episode_number')
            infoList['mediatype'] = 'episode'
            img = self.firstOf(episode, 'thumbnails', 'posterarts') or self.addonIcon
            fanart = self.firstOf(episode, 'backgrounds', 'hero_images') or self.addonFanart
            # Episodes resolve back through their season rather than by id -
            # the season payload is where Tubi publishes their streams, and
            # the manifest urls themselves carry a token that expires.
            playUrl = '|'.join([str(episode.get('id')), str(seriesId), str(season.get('season'))])
            ilist = self.addMenuItem(title, 'GV', ilist, playUrl, img, fanart,
                                     infoList, isFolder=False)
            ilist = self.applyResume(ilist, episode.get('id'))
        return ilist

    def getAddonEpisodes(self, url, ilist):
        """A series' seasons, or one season's episodes for `seriesId|season`."""
        seriesId, season = (url.split('|', 1) + [None])[:2]
        try:
            seasons = self.seasonList(seriesId)
        except TubiApiError as err:
            self.report(err)
            return ilist

        if season is not None:
            wanted = [s for s in seasons if str(s.get('season')) == str(season)]
            if len(wanted) == 0:
                return ilist
            return self.episodeItems(seriesId, wanted[0], ilist)

        if len(seasons) == 1:
            return self.episodeItems(seriesId, seasons[0], ilist)

        showTitle = xbmc.getInfoLabel('ListItem.Title').replace(self.localLang(30019), '')
        for entry in seasons:
            name = entry.get('name') or ' '.join([self.localLang(30027), str(entry.get('season'))])
            infoList = {'Title': name,
                        'TVShowTitle': showTitle,
                        'Season': entry.get('season'),
                        'mediatype': 'season'}
            ilist = self.addMenuItem(name, 'GE', ilist,
                                     '|'.join([seriesId, str(entry.get('season'))]),
                                     self.addonIcon, self.addonFanart, infoList, isFolder=True)
        return(ilist)

    def addShowToLibrary(self, url):
        """Export every episode of a series, across all of its seasons."""
        seriesId = uqp(url)
        try:
            content = self.api.content(seriesId)
            seasons = self.seasonList(seriesId)
        except TubiApiError as err:
            self.report(err)
            return
        ilist = []
        showTitle = content.get('title') or xbmc.getInfoLabel('ListItem.Title')
        for entry in seasons:
            ilist = self.episodeItems(seriesId, entry, ilist, showTitle=showTitle)
        movieDir = self.makeLibraryPath('shows', name=self.cleanFilename(showTitle))
        for itemUrl, liz, isFolder in ilist:
            tag = liz.getVideoInfoTag()
            name = ''.join(['S', str(tag.getSeason()), 'E', str(tag.getEpisode()), '  ',
                            self.cleanFilename(str(tag.getTitle())), '.strm'])
            with open(xbmcvfs.translatePath(os.path.join(movieDir, name)), 'w') as outfile:
                outfile.write(itemUrl)
        self.doScan(movieDir)

    # ------------------------------------------------------------ playback

    def doFunction(self, url):
        if url == 'logout' and self.auth is not None:
            self.auth.signOut()
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30023))
            return
        parts = uqp(url).split('|')
        if parts[0] == 'queue' and len(parts) > 2:
            self.toggleQueue(parts[1], parts[2], parts[3] if len(parts) > 3 else HISTORY_MOVIE)
        elif parts[0] == 'rate' and len(parts) > 3:
            self.toggleRating(parts[1], parts[2], parts[3] == '1')
        elif parts[0] == 'unwatch' and len(parts) > 1:
            self.forget(parts[1])

    def forget(self, historyId):
        """Take a title out of Continue Watching."""
        try:
            self.api.removeFromHistory(historyId)
        except TubiApiError as err:
            self.report(err)
            return
        self._watching = None
        self.note('forgot %s from continue watching' % historyId)
        xbmcgui.Dialog().notification(self.addonName, self.localLang(30055))
        xbmc.executebuiltin('Container.Refresh')

    def toggleQueue(self, action, contentId, kind):
        """Put a title on the saved list, or take it off.

        Removing needs the list entry's own id rather than the title's, so
        the list is looked up first either way - which also keeps a second
        Add from stacking a duplicate entry.
        """
        try:
            entry = self.api.queueEntry(contentId)
            if action == 'remove':
                if entry is not None:
                    self.api.removeFromQueue(entry['id'])
                message = 30044
            else:
                if entry is None:
                    self.api.addToQueue(contentId, kind)
                message = 30043
        except TubiApiError as err:
            self.report(err)
            return
        self._saved = None
        self.note('%s my list : %s' % (action, contentId))
        xbmcgui.Dialog().notification(self.addonName, self.localLang(message))
        xbmc.executebuiltin('Container.Refresh')

    def toggleRating(self, wanted, contentId, isSeries):
        """Like or dislike a title, or take back the one already given."""
        try:
            current = self.api.rating(contentId, isSeries)
            if wanted == LIKE:
                action, message = (UNLIKE, 30049) if current == RATING_LIKED else (LIKE, 30047)
            else:
                action, message = (UNDISLIKE, 30049) if current == RATING_DISLIKED else (DISLIKE, 30048)
            self.api.rate(contentId, isSeries, action)
        except TubiApiError as err:
            self.report(err)
            return
        self.note('%s : %s' % (action, contentId))
        xbmcgui.Dialog().notification(self.addonName, self.localLang(message))

    def resolve(self, url):
        """Look up a playable title.

        A film is fetched by its own id. An episode arrives as
        `episodeId|seriesId|season` and is picked out of its season, because
        that is where Tubi publishes episode streams. `trailer|id` asks for a
        title's trailer rather than the title itself.
        """
        parts = uqp(url).split('|')
        if parts[0] == 'trailer' and len(parts) > 1:
            trailers = self.api.content(parts[1]).get('trailers') or []
            # A trailer is a plain manifest, never encrypted
            return {'title': self.localLang(30035),
                    'video_resources': [],
                    'url': trailers[0].get('url') if trailers else None}
        if len(parts) < 3:
            return self.api.content(parts[0])
        episodeId, seriesId, season = parts[:3]
        for episode in self.api.episodes(seriesId, season):
            if str(episode.get('id')) == episodeId:
                return episode
        return {}

    def rememberPlaying(self, url, content):
        """Leave the service what it needs to report progress on stop.

        A film reports as itself. An episode reports as itself too, with its
        series named as the parent. Trailers report nothing.
        """
        window = xbmcgui.Window(10000)
        window.clearProperty(PLAYING)
        parts = uqp(url).split('|')
        if parts[0] == 'trailer' or self.addon.getSetting('report_progress') != 'true':
            return
        contentId = asInt(content.get('id'))
        if contentId is None:
            return
        playing = {'content_id': contentId,
                   'content_type': HISTORY_MOVIE,
                   'duration': content.get('duration')}
        if len(parts) >= 3:
            playing['content_type'] = HISTORY_EPISODE
            playing['parent_id'] = parts[1]
        window.setProperty(PLAYING, json.dumps(playing))

    def getAddonVideo(self, url):
        try:
            content = self.resolve(url)
        except TubiApiError as err:
            self.report(err)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return

        allowHdcp = self.addon.getSetting('allow_hdcp') == 'true'
        manifest, licenseUrl = pickResource(content, allowHdcp=allowHdcp)
        if manifest is None:
            # Either the title needs an account or it is not playable here
            message = 30018 if content.get('needs_login') else 30028
            self.note('no playable stream for %s (needs_login=%s)' % (
                content.get('id'), content.get('needs_login')), level=xbmc.LOGWARNING)
            xbmcgui.Dialog().notification(self.addonName, self.localLang(message),
                                          xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return

        self.rememberPlaying(url, content)
        liz = xbmcgui.ListItem(path=manifest, offscreen=True)
        liz.setSubtitles([sub['url'] for sub in content.get('subtitles') or [] if sub.get('url')])
        if licenseUrl is not None:
            # Tubi encrypts some titles, which needs inputstream.adaptive and a
            # working Widevine CDM. Clear streams play without either.
            liz.setMimeType('application/x-mpegURL')
            liz.setContentLookup(False)
            liz.setProperty('inputstream', 'inputstream.adaptive')
            liz.setProperty('inputstream.adaptive.manifest_type', 'hls')
            for key, value in self.drmProperties(licenseUrl):
                liz.setProperty(key, value)
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, liz)

    @staticmethod
    def drmProperties(licenseUrl):
        """Describe the Widevine licence request to inputstream.adaptive.

        Newer builds want drm_legacy and warn about the license_* properties;
        older ones only understand license_*, so pick by what is installed
        rather than dropping DRM playback on Kodi 19 and 20.
        """
        if inputstreamMajor() >= DRM_PROPERTIES_SINCE:
            headers = urllib.parse.urlencode(LICENSE_HEADERS)
            return [('inputstream.adaptive.drm_legacy',
                     '|'.join([WIDEVINE_KEYSYSTEM, licenseUrl, headers]))]
        headers = '&'.join(['='.join(header) for header in LICENSE_HEADERS])
        # url | request headers | request data | response format
        return [('inputstream.adaptive.license_type', WIDEVINE_KEYSYSTEM),
                ('inputstream.adaptive.license_key',
                 '|'.join([licenseUrl, headers, 'R{SSM}', '']))]
