# -*- coding: utf-8 -*-
# KodiAddon (tubitv)
#
import os
import re
import sys
import urllib.parse

import requests
import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from t1mlib import t1mAddon

uqp = urllib.parse.unquote_plus
qp = urllib.parse.quote_plus
quote = urllib.parse.quote

BASE = 'https://tubitv.com'
MENU_URL = ''.join([BASE, '/oz/containers?isKidsModeEnabled=false&groupStart=0'])
SEARCH_URL = ''.join([BASE, '/oz/search/'])
CONTAINER_URL = ''.join([BASE, '/oz/containers/%s/content?cursor=0&limit=400&expand=0'])
SUBCONTAINER_URL = ''.join([BASE, '/oz/containers/%s/content?parentId=%s&cursor=0&limit=50&expand=0'])
SERIES_URL = ''.join([BASE, '/oz/videos/0%s/content'])
VIDEO_URL = ''.join([BASE, '/oz/videos/%s/content'])
TIMEOUT = 30

SEASON_EPISODE = re.compile(r'S(..)\:E(..) ')


class myAddon(t1mAddon):

    def __init__(self, aname):
        t1mAddon.__init__(self, aname)
        # t1mlib expects resources/icon.png and mis-joins the fanart path,
        # this addon keeps its artwork under resources/images/
        self.addonIcon = xbmcvfs.translatePath(os.path.join(self.homeDir, 'resources', 'images', 'icon.png'))
        self.addonFanart = xbmcvfs.translatePath(os.path.join(self.homeDir, 'resources', 'images', 'fanart.jpg'))
        # default.py hands the signed in session over, when there is one
        self.auth = None

    def getJson(self, url):
        """GET a Tubi endpoint, reporting failures instead of raising."""
        try:
            response = requests.get(url, headers=self.defaultHeaders, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as err:
            self.log(''.join(['request failed : ', url, ' : ', str(err)]))
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30022),
                                          xbmcgui.NOTIFICATION_ERROR)
            return None

    @staticmethod
    def absoluteUrl(url):
        """Tubi hands out plenty of protocol relative image urls."""
        if url and not url.startswith('http'):
            return ''.join(['https:', url])
        return url

    def getAddonMenu(self, url, ilist):
        a = self.getJson(MENU_URL)
        if a is None:
            return ilist
        for key, b in a['hash'].items():
            infoList = {'Title': b['title'],
                        'Plot': b.get('description')}
            thumb = b.get('thumbnail')
            ilist = self.addMenuItem(b['title'], 'GS', ilist, b['slug'], thumb, thumb,
                                     infoList, isFolder=True)
        infoList = {'Title': self.localLang(30010)}
        ilist = self.addMenuItem(self.localLang(30010), 'GM', ilist, SEARCH_URL,
                                 self.addonIcon, self.addonFanart, infoList, isFolder=True)
        return(ilist)

    def getAddonShows(self, url, ilist):
        isSearch = url.startswith(SEARCH_URL)
        if not isSearch:
            try:
                catid = url.rsplit('/', 1)[1]
            except Exception:
                catid = url
        else:
            catid = url
        if 'parentId=' in url:
            catid = url.split('/containers/', 1)[1]
            catid = catid.split('/', 1)[0]

        catid = catid.split('?', 1)[0]

        if not url.startswith('http'):
            url = CONTAINER_URL % url.split('?', 1)[0]
        c = self.getJson(url)
        if c is None:
            return ilist

        if not isSearch:
            a = c['containersHash'][catid]['children']
        else:
            a = c

        for b in a:
            if not isSearch:
                d = c['contents'].get(b)
                if d is None:
                    d = c['containersHash'].get(b)
                if d is None:
                    continue
                b = d

            infoList = {}
            infoList['Plot'] = b.get('description')
            infoList['Year'] = b.get('year')
            infoList['duration'] = b.get('duration')
            infoList['cast'] = b.get('actors') or []
            infoList['genre'] = ' / '.join(b.get('tags') or [])
            directors = b.get('directors') or []
            if len(directors) > 0:
                infoList['director'] = directors[0]
            mpaa = b.get('ratings') or []
            if len(mpaa) > 0:
                infoList['MPAA'] = mpaa[0].get('value')

            url = b['id']
            backgrounds = b.get('backgrounds') or []
            if len(backgrounds) > 0:
                fanart = self.absoluteUrl(backgrounds[0])
            else:
                fanart = self.addonFanart

            posters = b.get('posterarts') or []
            if b['type'] == 's':
                name = ''.join([b['title'], self.localLang(30019)])
                infoList['Title'] = name
                infoList['mediatype'] = 'tvshow'
                if len(posters) > 0:
                    mode = 'GE'
                    img = self.absoluteUrl(posters[0])
                else:
                    # a container of shows rather than a single series
                    mode = 'GS'
                    img = b.get('thumbnail')
                    url = '/'.join([catid, url])
                contextMenu = [(self.localLang(30024),
                                'RunPlugin(%s?mode=AS&url=%s)' % (sys.argv[0], qp(url)))]
                ilist = self.addMenuItem(name, mode, ilist, url, img, fanart, infoList,
                                         isFolder=True, cm=contextMenu)
            else:
                name = b['title']
                infoList['Title'] = name
                infoList['mediatype'] = 'movie'
                contextMenu = [(self.localLang(30025),
                                'RunPlugin(%s?mode=AM&url=%s)' % (sys.argv[0], qp(url)))]
                if len(posters) > 0:
                    mode = 'GV'
                    folderType = False
                    img = self.absoluteUrl(posters[0])
                else:
                    # a container of movies rather than a single title
                    mode = 'GS'
                    folderType = True
                    img = b.get('thumbnail')
                    url = SUBCONTAINER_URL % (url, catid)
                ilist = self.addMenuItem(name, mode, ilist, url, img, fanart, infoList,
                                         isFolder=folderType, cm=contextMenu)
        return(ilist)

    def getAddonEpisodes(self, url, ilist):
        a = self.getJson(SERIES_URL % url)
        if a is None:
            return ilist
        sname = xbmc.getInfoLabel('ListItem.Title').replace(self.localLang(30019), '')
        for b in a['children']:
            for c in b['children']:
                name = c.get('title')
                z = SEASON_EPISODE.search(name)
                if z is not None:
                    season, episode = z.groups()
                    title = name.split(':', 1)[1].split(' ', 1)[1].strip(' \t-')
                else:
                    season, episode, title = [0, 0, name]

                infoList = {}
                infoList['TVShowTitle'] = sname
                infoList['Title'] = title
                infoList['Season'] = season
                infoList['Episode'] = episode
                infoList['Plot'] = c.get('description')
                infoList['cast'] = c.get('actors') or []
                infoList['duration'] = c.get('duration')
                infoList['Year'] = c.get('year')
                infoList['mediatype'] = 'episode'
                mpaa = c.get('ratings') or []
                if len(mpaa) > 0:
                    infoList['MPAA'] = mpaa[0].get('value')

                thumbs = c.get('thumbnails') or []
                img = self.absoluteUrl(thumbs[0]) if len(thumbs) > 0 else self.addonIcon
                backgrounds = c.get('backgrounds') or []
                fanart = self.absoluteUrl(backgrounds[0]) if len(backgrounds) > 0 else self.addonFanart

                ilist = self.addMenuItem(title, 'GV', ilist, qp(c.get('id')), img, fanart,
                                         infoList, isFolder=False)
        return(ilist)

    def getAddonMovies(self, url, ilist):
        answer = xbmcgui.Dialog().input(self.localLang(30010))
        if len(answer) > 0:
            ilist = self.getAddonShows(''.join([url, quote(answer)]), ilist)
        return(ilist)

    def doFunction(self, url):
        if url == 'logout' and self.auth is not None:
            self.auth.clear()
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30023))

    def getAddonVideo(self, url):
        a = self.getJson(VIDEO_URL % uqp(url))
        if a is None:
            return
        path = a.get('url')
        if path is None:
            # Tubi withholds the stream for titles that need an account
            xbmcgui.Dialog().notification(self.addonName, self.localLang(30018),
                                          xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem(offscreen=True))
            return
        liz = xbmcgui.ListItem(path=path, offscreen=True)
        liz.setSubtitles([sub['url'] for sub in a.get('subtitles') or []])
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, liz)
