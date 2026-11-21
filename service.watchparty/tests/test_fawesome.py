"""Fawesome links -> the stream in the title's page (fawesome.py)."""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS_DIR), 'resources', 'lib'))

import fawesome  # noqa: E402

EPISODE_LINK = ('https://fawesome.tv/tv-shows/10241098/s01-e01-the-disappearance-of-helle-crafts-forensic-files'
                '?utm_source=justwatch&utm_medium=feed')
HLS = 'https://ftmain.cachefly.net/files/a8wn4hw/vi/e6/be/10241098/s10241098.m3u8'
MP4 = 'https://ftmain.cachefly.net/files/a8wn4hw/vi/e6/be/a4e25b86ff642e68a936eae31135751d.mp4'


def title(nid, hls, mp4, drm=0):
    """A title's JSON as the page has it (the page probe, 2026-11-21): escaped
    slashes, its URLs before its node_id."""
    esc = lambda u: u.replace('/', '\\/')  # noqa: E731
    return ('{"like_count":false,"drm":%d,"video_url":"%s","video_hls_url":"%s","video_flv_url":"%s",'
            '"nodeRestrictionType":null,"video_format":"hls","ad":"no","node_id":%s,"runtime":1362}'
            % (drm, esc(mp4), esc(hls), esc(mp4), nid))


class FawesomeTest(unittest.TestCase):
    def test_links(self):
        self.assertEqual(fawesome.target(EPISODE_LINK), (
            'https://fawesome.tv/tv-shows/10241098/s01-e01-the-disappearance-of-helle-crafts-forensic-files',
            '10241098'))
        self.assertEqual(fawesome.target('https://fawesome.tv/movies/10809157/the-little-shop-of-horrors?utm_source=justwatch'),
                         ('https://fawesome.tv/movies/10809157/the-little-shop-of-horrors', '10809157'))
        # only the number: the page shell is empty, a name gives the full page
        self.assertEqual(fawesome.target('https://fawesome.tv/tv-shows/10241098'),
                         ('https://fawesome.tv/tv-shows/10241098/watch', '10241098'))
        for link in ('https://fawesome.tv/', 'https://fawesome.tv/movies', 'https://notfawesome.tv/movies/1/x',
                     'https://tubitv.com/movies/1'):
            self.assertIsNone(fawesome.target(link))

    def test_stream_of_this_title(self):
        page = ('<script>window.nodeId = "10241098"; window.dvvideo_url = "Dg..";</script>'
                + title(555, 'https://x/other.m3u8', 'https://x/other.mp4')
                + title(10241098, HLS, MP4)
                + title(777, 'https://x/next.m3u8', 'https://x/next.mp4'))
        self.assertEqual(fawesome.stream_in(page, '10241098'), HLS)
        self.assertEqual(fawesome.stream_in(page, '555'), 'https://x/other.m3u8')
        self.assertIsNone(fawesome.stream_in(page, '999'))

    def test_mp4_without_drm_only(self):
        no_hls = title(1, '', MP4).replace('"video_hls_url":"",', '')
        self.assertEqual(fawesome.stream_in(no_hls, '1'), MP4)
        self.assertIsNone(fawesome.stream_in(title(1, '', MP4, drm=1).replace('"video_hls_url":"",', ''), '1'))
        self.assertIsNone(fawesome.stream_in(title(1, 'https://x/comingsoon.m3u8', 'https://x/comingsoon.mp4'), '1'))

    def test_resolve(self):
        asked = []

        def fetch(url, headers):
            asked.append((url, headers['User-Agent']))
            return title(10241098, HLS, MP4)
        url = fawesome.resolve(EPISODE_LINK, fetch)
        self.assertTrue(url.startswith(HLS + '|User-Agent=Mozilla/5.0%20'))
        self.assertEqual(asked[0][0], fawesome.target(EPISODE_LINK)[0])
        self.assertIsNone(fawesome.resolve('https://tubitv.com/movies/1', fetch))

        def broken(url, headers):
            raise OSError('offline')
        self.assertIsNone(fawesome.resolve(EPISODE_LINK, broken))


if __name__ == '__main__':
    unittest.main()
