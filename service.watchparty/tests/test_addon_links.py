"""Web links from Watch Party's Android app and browser → Kodi streaming
addons' plugin paths (addon_links.py: pure, no Kodi modules). The engine's
use of it is in test_engine.py (AddonRefTest)."""
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS_DIR), 'resources', 'lib'))

import addon_links  # noqa: E402


def args(path):
    return {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}


class AddonPathTest(unittest.TestCase):
    def test_netflix(self):
        self.assertEqual(addon_links.addon_path('https://www.netflix.com/watch/80057281?trackId=1', 'movie'),
                         ('plugin.video.netflix', 'plugin://plugin.video.netflix/play/movie/80057281/'))
        self.assertEqual(addon_links.addon_path('https://www.netflix.com/title/80057281')[0], 'plugin.video.netflix')
        # an episode's own id, through the movie route (the stream needs only the id)
        self.assertEqual(addon_links.addon_path('https://www.netflix.com/watch/82946792', 'episode'),
                         ('plugin.video.netflix', 'plugin://plugin.video.netflix/play/movie/82946792/'))
        # but not its show's page
        self.assertEqual(addon_links.addon_path('https://www.netflix.com/title/80057281', 'episode'), (None, None))

    def test_crunchyroll(self):
        # One Piece Film Red (the link the Android app and extension share)
        for link in ('https://www.crunchyroll.com/watch/GZ7UV2MZK/one-piece-film-red',
                     'https://www.crunchyroll.com/de/watch/GZ7UV2MZK/one-piece-film-red?t=12',
                     'https://www.crunchyroll.com/watch/GZ7UV2MZK'):
            self.assertEqual(addon_links.addon_path(link, 'episode'),
                             ('plugin.video.crunchyroll', 'plugin://plugin.video.crunchyroll/video/GZ7UV2MZK/GZ7UV2MZK'))
        self.assertEqual(addon_links.addon_path('https://www.crunchyroll.com/series/GRMG8ZQZR/one-piece'), (None, None))

    def test_youtube(self):
        for link in ('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10', 'https://youtu.be/dQw4w9WgXcQ'):
            self.assertEqual(addon_links.addon_path(link)[1], 'plugin://plugin.video.youtube/play/?video_id=dQw4w9WgXcQ')

    def test_slyguy_disney_hulu_paramount(self):
        addon, path = addon_links.addon_path('https://www.disneyplus.com/play/F51DCE23-7D8F-490A-9F0C-BE1B5432C2A9')
        self.assertEqual(addon, 'slyguy.disney.plus')
        self.assertTrue(path.startswith('plugin://slyguy.disney.plus/?'))
        self.assertEqual(args(path), {'_': 'play', 'deeplink_id': 'f51dce23-7d8f-490a-9f0c-be1b5432c2a9'})
        self.assertEqual(args(addon_links.addon_path(
            'https://www.disneyplus.com/browse/entity-f51dce23-7d8f-490a-9f0c-be1b5432c2a9')[1])['deeplink_id'],
            'f51dce23-7d8f-490a-9f0c-be1b5432c2a9')
        for link in ('https://www.hulu.com/watch/3e8b8c5f-1a2b-4c3d-9e8f-0a1b2c3d4e5f',
                     'https://www.hulu.com/movie/palm-springs-3e8b8c5f-1a2b-4c3d-9e8f-0a1b2c3d4e5f'):
            addon, path = addon_links.addon_path(link)
            self.assertEqual(addon, 'slyguy.hulu')
            self.assertEqual(args(path), {'_': 'play', 'id': '3e8b8c5f-1a2b-4c3d-9e8f-0a1b2c3d4e5f'})
        for link, vid in (('https://www.paramountplus.com/movies/top-gun-maverick/wjQ4RChi6BHHu4MVTncppVuCwu44uq2Q/', 'wjQ4RChi6BHHu4MVTncppVuCwu44uq2Q'),
                          ('https://www.paramountplus.com/shows/star-trek-strange-new-worlds/video/abc123DEF/x', 'abc123DEF'),
                          # the site's own nameless links (the named ones redirect there)
                          ('https://www.paramountplus.com/movies/video/Alcn0hcGx0HosdhcawKteH8DXh3RiOF7/', 'Alcn0hcGx0HosdhcawKteH8DXh3RiOF7'),
                          ('https://www.paramountplus.com/shows/video/7yX9OPrM_xsLmCiY_ZBNXVShlgSxglcI/', '7yX9OPrM_xsLmCiY_ZBNXVShlgSxglcI')):
            self.assertEqual(args(addon_links.addon_path(link)[1]), {'_': 'play', 'video_id': vid})
        # a movie's page without an id isn't a title
        self.assertEqual(addon_links.addon_path('https://www.paramountplus.com/movies/top-gun-maverick/'), (None, None))

    def test_hbo_max_and_discovery_plus(self):
        # one id (JustWatch's links): the addon looks up its edit id
        addon, path = addon_links.addon_path('https://play.hbomax.com/video/watch/C07AE042-082e-4b53-ba86-b6ac5e3004db')
        self.assertEqual((addon, args(path)), ('slyguy.max', {'_': 'play', 'id': 'c07ae042-082e-4b53-ba86-b6ac5e3004db'}))
        addon, path = addon_links.addon_path('https://play.discoveryplus.com/video/watch/c07ae042-082e-4b53-ba86-b6ac5e3004db/')
        self.assertEqual((addon, args(path)), ('slyguy.discovery.plus', {'_': 'play', 'id': 'c07ae042-082e-4b53-ba86-b6ac5e3004db'}))
        self.assertEqual(addon_links.addon_path('https://play.hbomax.com/page/urn:hbo:page:abc'), (None, None))
        # two ids (copied while playing): the second is the edit id
        addon, path = addon_links.addon_path(
            'https://play.max.com/video/watch/c07ae042-082e-4b53-ba86-b6ac5e3004db/0fc1d4b1-0f6a-4b5b-9c39-6a6e2ee44b3f')
        self.assertEqual((addon, args(path)), ('slyguy.max', {'_': 'play', 'edit_id': '0fc1d4b1-0f6a-4b5b-9c39-6a6e2ee44b3f'}))

    def test_amazon_apple_tubi(self):
        for link, asin in (('https://watch.amazon.com/watch?asin=B0CH88VRKB&time=0&territory=US', 'B0CH88VRKB'),
                           ('https://watch.amazon.com/watch?gti=amzn1.dv.gti.1234', 'amzn1.dv.gti.1234'),
                           ('https://www.amazon.com/gp/video/detail/B0CH88VRKB/ref=x', 'B0CH88VRKB'),
                           ('https://www.primevideo.com/detail/0QG3EXAMPLE', '0QG3EXAMPLE')):
            addon, path = addon_links.addon_path(link)
            self.assertEqual(addon, 'plugin.video.amazon-test')
            self.assertEqual((args(path)['asin'], args(path)['mode']), (asin, 'PlayVideo'))
        addon, path = addon_links.addon_path('https://tv.apple.com/us/movie/f1/umc.cmc.3t6dvnnr87zwd4wmvpdx5came?action=play')
        self.assertEqual(args(path), {'action': 'play', 'item_id': 'umc.cmc.3t6dvnnr87zwd4wmvpdx5came', 'item_type': 'Movie'})
        addon, path = addon_links.addon_path('https://tv.apple.com/us/episode/pilot/umc.cmc.1abc?showId=umc.cmc.2&action=play')
        self.assertEqual(args(path)['item_type'], 'Episode')
        # by id alone (what Android and browsers make of Kodi's Apple TV titles)
        for link, kind in (('https://tv.apple.com/movie/umc.cmc.c3xhu25rw4jxxxzq4oio6snu', 'Movie'),
                           ('https://tv.apple.com/us/episode/umc.cmc.s80mx1ic96pu6ewupz8pfasf', 'Episode')):
            self.assertEqual(args(addon_links.addon_path(link)[1])['item_type'], kind)
        self.assertEqual(addon_links.addon_path('https://tubitv.com/movies/100012345/the-movie'),
                         ('plugin.video.tubitv', 'plugin://plugin.video.tubitv/?mode=GV&url=100012345'))
        # an episode plays from its own id too (the one-part form)
        self.assertEqual(addon_links.addon_path('https://tubitv.com/tv-shows/200012345'),
                         ('plugin.video.tubitv', 'plugin://plugin.video.tubitv/?mode=GV&url=200012345'))
        # a series' page isn't a title
        self.assertEqual(addon_links.addon_path('https://tubitv.com/series/300012345/forensic-files'), (None, None))

    def test_peacock_web_links_play_in_skyott(self):
        addon, path = addon_links.addon_path(
            'https://www.peacocktv.com/watch/asset/movies/the-grinch/153a1aee-b720-356e-bac2-6a9b85fb81b3')
        self.assertEqual((addon, args(path)), ('plugin.video.skyott', {
            'action': 'play', 'slug': '/movies/the-grinch/153a1aee-b720-356e-bac2-6a9b85fb81b3'}))
        self.assertEqual(args(addon_links.addon_path(
            'https://www.peacocktv.com/watch/asset/tv/the-office/4902514835143843112/seasons/1/episodes/'
            'pilot-episode-1/58fabbf4-9291-3575-bcdc-bd25eee192c8')[1])['slug'],
            '/tv/the-office/4902514835143843112/seasons/1/episodes/pilot-episode-1/58fabbf4-9291-3575-bcdc-bd25eee192c8')
        # the play deeplink carries only the pvid, which SkyOTT can't look up
        self.assertEqual(addon_links.addon_path(
            'https://www.peacocktv.com/deeplink?deeplinkData=%7B%22pvid%22%3A%22153a1aee%22%2C%22action%22%3A%22PLAY%22%7D'),
            (None, None))

    def test_not_links(self):
        for link in ('smb://nas/movie.mkv', 'plugin://plugin.video.netflix/play/movie/1/', '', 'https://example.com/x'):
            self.assertEqual(addon_links.addon_path(link), (None, None))


if __name__ == '__main__':
    unittest.main()
