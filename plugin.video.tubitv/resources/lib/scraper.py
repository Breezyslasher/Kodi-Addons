# -*- coding: utf-8 -*-
# KodiAddon (tubitv)
#
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

uqp = urllib.parse.unquote_plus
qp = urllib.parse.quote_plus

SERIES = 's'

WIDEVINE_KEYSYSTEM = 'com.widevine.alpha'
# Tubi's licence server takes the raw challenge and answers with the raw
# licence, so only these two headers need setting.
LICENSE_HEADERS = [('Content-Type', 'application/octet-stream'),
                   ('Origin', 'https://tubitv.com')]
# inputstream.adaptive 21 introduced drm_legacy and deprecated the license_*
# properties. Older builds only understand the old ones.
DRM_PROPERTIES_SINCE = 21


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

    def report(self, err):
        """Surface an API failure without taking the whole directory down."""
        self.log(''.join(['api call failed : ', str(err)]))
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

    def addContent(self, content, ilist):
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
            return self.addMenuItem(name, 'GE', ilist, contentId, img, fanart, infoList,
                                    isFolder=True, cm=contextMenu)

        infoList['mediatype'] = 'movie'
        contextMenu = [(self.localLang(30025),
                        'RunPlugin(%s?mode=AM&url=%s)' % (sys.argv[0], qp(contentId)))]
        return self.addMenuItem(content.get('title'), 'GV', ilist, contentId, img, fanart,
                                infoList, isFolder=False, cm=contextMenu)

    def getAddonMenu(self, url, ilist):
        try:
            containers = self.api.browseList()
        except TubiApiError as err:
            self.report(err)
            return ilist
        for container in containers:
            infoList = {'Title': container.get('title'),
                        'Plot': container.get('description')}
            thumb = container.get('thumbnail') or self.addonIcon
            ilist = self.addMenuItem(container.get('title'), 'GS', ilist,
                                     container.get('id') or container.get('slug'),
                                     thumb, self.addonFanart, infoList, isFolder=True)
        infoList = {'Title': self.localLang(30010)}
        # Never hand out an empty url - Kodi trims the '=' off an empty query
        # value and t1mlib's parameter parser cannot read the result back.
        ilist = self.addMenuItem(self.localLang(30010), 'GM', ilist, 'search',
                                 self.addonIcon, self.addonFanart, infoList, isFolder=True)
        return(ilist)

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
            self.auth.clear()
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30023))

    def resolve(self, url):
        """Look up a playable title.

        A film is fetched by its own id. An episode arrives as
        `episodeId|seriesId|season` and is picked out of its season, because
        that is where Tubi publishes episode streams.
        """
        parts = uqp(url).split('|')
        if len(parts) < 3:
            return self.api.content(parts[0])
        episodeId, seriesId, season = parts[:3]
        for episode in self.api.episodes(seriesId, season):
            if str(episode.get('id')) == episodeId:
                return episode
        return {}

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
            xbmcgui.Dialog().notification(self.addonName, self.localLang(message),
                                          xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return

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
