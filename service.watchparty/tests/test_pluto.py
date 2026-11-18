"""Pluto TV links -> the stream from Pluto's public catalog (pluto.py)."""
import json
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS_DIR), 'resources', 'lib'))

import pluto  # noqa: E402

EPISODE_LINK = ('https://pluto.tv/on-demand/series/forensic-files-las/season/1/episode/'
                'la-bala-magica-1996-1-2?utm_medium=deeplink&utm_source=justwatch')
STITCHED = ('https://service-stitcher-ipv4.clusters.pluto.tv/stitch/hls/episode/'
            '6062668b3bbc1b001aedf35d/master.m3u8?advertisingId=&appName=&appVersion=&deviceDNT=&deviceId=&sid=')

# trimmed from api.pluto.tv/v3/vod/slugs/forensic-files-las (2026-11-18)
SERIES = {'_id': 'show', 'slug': 'forensic-files-las', 'seasons': [{'number': 1, 'episodes': [
    {'_id': '6062668a3bbc1b001aedf301', 'slug': 'el-estrangulador-del-lado-sur-1996-1-6',
     'stitched': {'urls': [{'type': 'hls', 'url': STITCHED.replace('6062668b3bbc1b001aedf35d', '6062668a3bbc1b001aedf301')}]}},
    {'_id': '6062668b3bbc1b001aedf35d', 'slug': 'la-bala-magica-1996-1-2', 'name': 'La Bala Mágica',
     'stitched': {'urls': [{'type': 'hls', 'url': STITCHED}]}},
]}]}


class PlutoTest(unittest.TestCase):
    def test_links(self):
        self.assertEqual(pluto.target(EPISODE_LINK), ('forensic-files-las', 'la-bala-magica-1996-1-2'))
        self.assertEqual(pluto.target('https://pluto.tv/us/on-demand/movies/the-terminator-1984-1-1'),
                         ('the-terminator-1984-1-1', 'the-terminator-1984-1-1'))
        # a show's page, live TV and other sites aren't titles
        for link in ('https://pluto.tv/on-demand/series/forensic-files-las',
                     'https://pluto.tv/en/live-tv/5bb1af6a268cae539bcedb0a/details/5bb1af6a268cae539bcedb0a',
                     'https://notpluto.tv/on-demand/movies/x', ''):
            self.assertIsNone(pluto.target(link))

    def test_the_episode_by_its_slug(self):
        asked = []

        def fetch(url):
            asked.append(url)
            return json.dumps(SERIES)
        url = pluto.resolve(EPISODE_LINK, fetch, device_id='dev-1')
        self.assertEqual(asked, ['https://api.pluto.tv/v3/vod/slugs/forensic-files-las?deviceType=web'])
        self.assertIn('/stitch/hls/episode/6062668b3bbc1b001aedf35d/master.m3u8?', url)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
        # the device fields that got a playlist (page probe, 2026-11-18)
        self.assertEqual((q['deviceId'], q['deviceType'], q['deviceMake'], q['appName'], q['deviceDNT']),
                         ('dev-1', 'web', 'Chrome', 'web', '0'))
        self.assertTrue(q['sid'])

    def test_a_movie_is_its_own_entry(self):
        movie = {'_id': 'm1', 'slug': 'the-terminator-1984-1-1', 'stitched': {'urls': [{'type': 'hls', 'url': STITCHED}]}}
        url = pluto.resolve('https://pluto.tv/on-demand/movies/the-terminator-1984-1-1', lambda u: json.dumps(movie))
        self.assertTrue(url.startswith('https://service-stitcher-ipv4.clusters.pluto.tv/'))

    def test_nothing_when_missing_or_failing(self):
        self.assertIsNone(pluto.resolve(EPISODE_LINK.replace('la-bala', 'no-such'), lambda u: json.dumps(SERIES)))
        self.assertIsNone(pluto.resolve(EPISODE_LINK, lambda u: '{"statusCode":404}'))

        def broken(url):
            raise OSError('offline')
        self.assertIsNone(pluto.resolve(EPISODE_LINK, broken))


if __name__ == '__main__':
    unittest.main()
