#!/usr/bin/env python3
"""Does Tubi still answer the way plugin.video.tubitv expects?

The declarative engine in .github/endpoint-check/ cannot check Tubi's content
endpoints: every one of them authenticates with a bearer token, and minting
the anonymous device token needs a TUBI-HMAC-SHA256 request signature (an AWS
SigV4 lookalike) that a JSON spec cannot compute. So this is a script.

It does not reimplement any of that. It stands in for Kodi's two modules and
then drives *the addon's own* TubiAuth and TubiApi - the same handshake, the
same hosts, the same query parameters, the same response reading - and asserts
the keys the addon goes on to use. So it fails both when Tubi changes and when
a change to the addon stops matching Tubi, which are the same news.

Ids are never hard-coded. A film, a series, an episode and a channel are taken
out of the live browse and line-up responses, so the check cannot rot when a
title leaves the catalogue.

    python3 plugin.video.tubitv/tools/check_endpoints.py

Exits non-zero if any check fails. Needs `requests`, which the addon needs
too; nothing else outside the standard library.

Anonymous only. My List, Continue Watching, ratings and progress reporting all
need a signed-in account, so they are not checked here - there are no
credentials in CI and there should not be.
"""
import os
import sys
import tempfile
import types

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How much of a list is worth printing, and the longest line of detail.
SAMPLE = 3
WIDTH = 26


def stand_in_for_kodi(profile):
    """xbmc and xbmcvfs, as far as tubi_auth and tubi_api touch them."""
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    # The addon logs its own progress; only a warning is worth a line here.
    xbmc.log = lambda msg='', level=0: print('     addon: %s' % msg) if level >= 2 else None
    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda path: path
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda path: os.makedirs(path, exist_ok=True)
    sys.modules['xbmc'], sys.modules['xbmcvfs'] = xbmc, xbmcvfs

    class Addon(object):
        """No account: every setting reads empty, so the addon stays anonymous."""

        def getAddonInfo(self, key):
            return {'name': 'Tubi (endpoint check)', 'profile': profile}.get(key, '')

        def getSetting(self, key):
            return ''

        def setSetting(self, key, value):
            pass

    return Addon()


class Checks(object):
    """Runs the checks in order, printing the engine's PASS/FAIL style."""

    def __init__(self):
        self.failures = []

    def run(self, name, note, fn, fatal=True):
        """fn returns a one-line summary, or raises to fail the check."""
        try:
            detail = fn()
        except Exception as err:
            label = 'FAIL' if fatal else 'NOTE'
            print('%s %-*s %s: %s' % (label, WIDTH, name, type(err).__name__, err))
            print('     %s' % note)
            if fatal:
                self.failures.append(name)
            return None
        print('OK   %-*s %s' % (WIDTH, name, detail))
        return detail

    def report(self):
        if self.failures:
            print('\n%d check(s) failed: %s' % (len(self.failures), ', '.join(self.failures)))
            return 1
        print('\nAll checks passed')
        return 0


def need(value, what):
    """Assert a payload carried something, and hand it back."""
    if not value:
        raise AssertionError('missing %s' % what)
    return value


def titles(items):
    """A few titles, for a line of output that says what came back."""
    names = [str(i.get('title') or i.get('name') or i.get('id')) for i in items[:SAMPLE]]
    return ', '.join(names) + (' ...' if len(items) > SAMPLE else '')


def main():
    addon = stand_in_for_kodi(tempfile.mkdtemp())
    sys.path.insert(0, os.path.join(ADDON, 'resources', 'lib'))
    import tubi_api
    from tubi_api import SERIES
    from tubi_auth import TubiAuth

    print('Tubi endpoint check (anonymous, driving the addon\'s own code)')
    checks = Checks()
    found = {}

    # ------------------------------------------------------------------ auth
    def auth():
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:131.0) '
                                 'Gecko/20100101 Firefox/131.0',
                   'Accept-Language': 'en-US'}
        TubiAuth(addon).apply(headers)
        bearer = headers.get('Authorization') or ''
        if not bearer.startswith('Bearer '):
            raise AssertionError('no bearer token')
        found['headers'] = headers
        return 'signing key, then a signed device token (%d chars)' % len(bearer)

    # The handshake in two calls, both of them real: POST
    # account/device/anonymous/signing_key, then the TUBI-HMAC-SHA256 signed
    # POST .../token. Nothing else here can run without it.
    if checks.run('anonymous_token', 'The auth handshake. Signing in and every content call '
                  'depend on it, so the addon is dead until this is corrected.', auth) is None:
        return checks.report()

    auth_obj = TubiAuth(addon)
    api = tubi_api.TubiApi(found['headers'], auth_obj.deviceId)

    # ---------------------------------------------------------------- browse
    def browse_list():
        containers = need(api.browseList(), 'containers')
        found['container_id'] = str(containers[0].get('id'))
        return '%d categories: %s' % (len(containers), titles(containers))

    def container():
        items, cursor = api.container(found['container_id'])
        need(items, 'contents in the container')
        # A film and a series to carry into the title checks below, rather
        # than ids written down here that go stale when a title leaves.
        for item in items:
            key = 'series_id' if item.get('type') == SERIES else 'film_id'
            found.setdefault(key, str(item.get('id')))
            found.setdefault(key.replace('_id', '_title'), item.get('title') or '')
        return '%d titles, next cursor %s: %s' % (len(items), cursor, titles(items))

    def homescreen():
        rows, cursor = api.homescreen()
        need(rows, 'home rows')
        found['group_cursor'] = cursor
        return '%d rows, group_cursor %r: %s' % (len(rows), cursor, titles(rows))

    def home_second_call():
        # Home is two calls; without the cursor from the first, it stops at
        # seven rows, which is the bug fixed in 2026.8.11.11.
        need(found.get('group_cursor'), 'group_cursor on the first home call')
        rows = api.homescreenAll()
        if len(rows) <= tubi_api.HOME_GROUP_SIZE:
            raise AssertionError('%d rows in all, no more than the first group of %d'
                                 % (len(rows), tubi_api.HOME_GROUP_SIZE))
        return '%d rows in all, from %d in the first group' % (len(rows), tubi_api.HOME_GROUP_SIZE)

    def search():
        # Search for a title that is demonstrably in the catalogue: the one
        # the browse call just handed back.
        query = found.get('film_title') or found.get('series_title') or 'the'
        results = need(api.search(query), 'search results')
        return '%r: %d results: %s' % (query, len(results), titles(results))

    checks.run('browse_list', 'The category list - the addon\'s front page.', browse_list)
    if found.get('container_id'):
        checks.run('container', 'A category\'s contents. Every category listing goes through it.',
                   container)
    checks.run('homescreen', 'Tubi\'s own personalised rows, the Home entry.', homescreen)
    checks.run('homescreen_all', 'The second home call. Without it Home is its first seven rows.',
               home_second_call)
    checks.run('search', 'Catalogue search.', search)

    # ---------------------------------------------------------------- titles
    def content():
        film = need(found.get('film_id'), 'a film id from the browse call')
        data = api.content(film)
        resources = need(data.get('video_resources'), 'video_resources on %s' % film)
        urls = [r for r in resources if (r.get('manifest') or {}).get('url')]
        need(urls, 'a manifest url in any of the %d resources' % len(resources))
        return '%s: %d playable of %d, types %s' % (
            data.get('title'), len(urls), len(resources), tubi_api.offeredTypes(data))

    def series_episodes():
        series = need(found.get('series_id'), 'a series id from the browse call')
        seasons = need(api.seasons(series), 'episodes_by_season on %s' % series)
        numbered = [s for s in seasons if s.get('episodes')]
        need(numbered, 'a season with episodes')
        found['season'] = numbered[0].get('season')
        return '%s: %d seasons, first is season %s with %d episodes' % (
            series, len(seasons), found['season'], len(numbered[0].get('episodes') or []))

    def episodes():
        episodes = need(api.episodes(found['series_id'], found['season']),
                        'episodes in season %s' % found['season'])
        need([e for e in episodes if e.get('id')], 'an episode with an id')
        found['episode'] = episodes[0]
        return '%d episodes, first %s' % (len(episodes), episodes[0].get('id'))

    def cuepoints():
        # The skip buttons' only source. Keys are always present and read 0.0
        # when there is no marker, so the field going missing is the news.
        episode = need(found.get('episode'), 'an episode to look at')
        cues = episode.get('credit_cuepoints')
        if not isinstance(cues, dict):
            raise AssertionError('credit_cuepoints is %s, not an object'
                                 % type(cues).__name__)
        need(set(cues) & {'intro_start', 'intro_end', 'postlude'},
             'any of intro_start/intro_end/postlude in %s' % sorted(cues))
        return '%s: %s' % (episode.get('id'),
                           {k: v for k, v in cues.items() if v})

    def related():
        film = need(found.get('film_id'), 'a film id from the browse call')
        items = need(api.related(film), 'related titles for %s' % film)
        return '%d titles: %s' % (len(items), titles(items))

    checks.run('content', 'A title and its playable streams. Nothing plays without it.', content)
    checks.run('series_episodes', 'A series\' seasons.', series_episodes)
    if found.get('season') is not None:
        checks.run('episodes', 'One season\'s episodes, which is what an episode plays from.',
                   episodes)
        checks.run('credit_cuepoints', 'Where the intro and credits are. Only the skip buttons '
                   'use it, so the rest of the addon is unaffected.', cuepoints, fatal=False)
    checks.run('related', '"Related titles" on a film or series.', related)

    # -------------------------------------------------------------- live tv
    def live_channels():
        channels, groups = api.liveChannels()
        need(channels, 'live channels')
        playable = [c for c in channels if tubi_api.pickResource(c)[0]]
        need(playable, 'a channel with a manifest, of %d' % len(channels))
        found['channels'] = [c.get('id') for c in channels[:SAMPLE] if c.get('id')]
        return '%d channels in %d groups, %d playable: %s' % (
            len(channels), len(set(groups.values())), len(playable), titles(channels))

    def live_programming():
        ids = need(found.get('channels'), 'channel ids from the line-up')
        rows = need(api.liveProgramming(ids), 'programming rows for %s' % ids)
        need([r for r in rows if r.get('content_id')], 'a row naming its channel')
        # What iptv.py reads off each programme to build the Kodi guide.
        programs = [p for row in rows for p in row.get('programs') or []]
        need(programs, 'programmes in any of the %d rows' % len(rows))
        need([p for p in programs
              if p.get('start_time') and p.get('end_time') and p.get('title')],
             'a programme with a start, an end and a title')
        return '%d rows for %d channels, %d programmes' % (len(rows), len(ids), len(programs))

    checks.run('live_channels', 'The linear channel line-up and the Live TV folder.',
               live_channels)
    if found.get('channels'):
        checks.run('live_programming', 'The programme guide, which IPTV Manager asks for.',
                   live_programming)

    return checks.report()


if __name__ == '__main__':
    sys.exit(main())
