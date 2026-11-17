"""Finding an HBO Max / Discovery+ episode by browsing SlyGuy's addon."""
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS_DIR), 'resources', 'lib'))

import addon_search  # noqa: E402

SHOW = 'f1b6b2a1-0000-4000-8000-000000000001'


def args(path):
    return {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}


def ep(n, season=3, show='Gold Rush'):
    return {'file': 'plugin://slyguy.max/?_=play&edit_id=edit-%d-%d' % (season, n),
            'label': 'Episode %d' % n, 'showtitle': show, 'season': season, 'episode': n}


class Addon:
    """Files.GetDirectory on a pretend slyguy.max."""

    def __init__(self, search, seasons):
        self.search, self.seasons, self.asked = search, seasons, []

    def __call__(self, path):
        self.asked.append(path)
        a = args(path)
        if a['_'] == 'search':
            return self.search
        if a['_'] == 'series':
            pages = self.seasons.get((a['id'], int(a['season'])), [])
            page = int(a.get('page', 1))
            entries = list(pages[page - 1]) if page <= len(pages) else []
            if page < len(pages):
                entries.append({'file': path.split('&page=')[0] + '&page=%d' % (page + 1),
                                'label': 'Next Page', 'season': -1, 'episode': -1})
            return entries
        return []


SERIES = {'file': 'plugin://slyguy.max/?_=series&id=%s' % SHOW, 'label': 'Gold Rush',
          'title': 'Gold Rush', 'season': -1, 'episode': -1}


class FindEpisodeTest(unittest.TestCase):
    def test_through_the_show_and_season(self):
        addon = Addon([{'file': 'plugin://slyguy.max/?_=series&id=other', 'label': 'Gold Rush: White Water'},
                       SERIES],
                      {(SHOW, 3): [[ep(1), ep(2)]]})
        found = addon_search.find_episode('slyguy.max', 'Gold Rush', '3', '2', addon)
        self.assertEqual(found, ep(2)['file'])
        self.assertEqual(args(addon.asked[0]), {'_': 'search', 'query': 'Gold Rush'})
        self.assertEqual(args(addon.asked[1]), {'_': 'series', 'id': SHOW, 'season': '3'})

    def test_follows_the_season_pages(self):
        addon = Addon([SERIES], {(SHOW, 3): [[ep(1), ep(2)], [ep(3), ep(4)]]})
        self.assertEqual(addon_search.find_episode('slyguy.max', 'Gold Rush', 3, 4, addon), ep(4)['file'])
        self.assertEqual(args(addon.asked[-1])['page'], '2')

    def test_an_episode_in_the_search_results(self):
        addon = Addon([ep(7, season=1, show='Gold Rush'), SERIES], {})
        self.assertEqual(addon_search.find_episode('slyguy.max', 'Gold Rush', 1, 7, addon),
                         ep(7, season=1)['file'])
        self.assertEqual(len(addon.asked), 1)

    def test_names_match_loosely(self):
        addon = Addon([dict(SERIES, label='Grey’s Anatomy', title='Grey’s Anatomy')],
                      {(SHOW, 3): [[ep(2)]]})
        self.assertEqual(addon_search.find_episode('slyguy.max', "Grey's Anatomy", 3, 2, addon), ep(2)['file'])

    def test_not_the_asl_edition(self):
        addon = Addon([dict(SERIES, label='The Last of Us (with ASL)', title='The Last of Us (with ASL)')],
                      {(SHOW, 3): [[ep(2)]]})
        self.assertIsNone(addon_search.find_episode('slyguy.max', 'The Last of Us', 3, 2, addon))

    def test_nothing_without_a_match_or_details(self):
        addon = Addon([SERIES], {(SHOW, 3): [[ep(1)]]})
        self.assertIsNone(addon_search.find_episode('slyguy.max', 'Gold Rush', 3, 9, addon))
        self.assertIsNone(addon_search.find_episode('slyguy.max', 'Gold Rush', None, 9, addon))
        self.assertIsNone(addon_search.find_episode('slyguy.netflix', 'Gold Rush', 3, 1, addon))



class FindByShowTest(unittest.TestCase):
    WBD = 'EB26E00E-9582-4790-a61c-48d785926f58/s2/e1'
    SHOW_ID = 'eb26e00e-9582-4790-a61c-48d785926f58'

    def test_opens_the_season_by_the_shows_id(self):
        addon = Addon([], {(self.SHOW_ID, 2): [[ep(1, season=2)]]})
        self.assertEqual(addon_search.find_by_show('slyguy.discovery.plus', self.WBD, addon), ep(1, season=2)['file'])
        self.assertEqual([args(p) for p in addon.asked], [{'_': 'series', 'id': self.SHOW_ID, 'season': '2'}])

    def test_nothing_without_a_usable_value(self):
        addon = Addon([], {})
        for value in (None, '', 'abc/s1/e1', self.SHOW_ID):
            self.assertIsNone(addon_search.find_by_show('slyguy.max', value, addon))
        self.assertIsNone(addon_search.find_by_show('plugin.video.netflix', self.WBD, addon))
        self.assertEqual(addon.asked, [])


if __name__ == '__main__':
    unittest.main()
