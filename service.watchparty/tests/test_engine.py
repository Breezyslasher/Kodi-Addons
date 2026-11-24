"""Engine unit tests with stubbed Kodi modules — URL policy, library
matching, auto-open bookkeeping, member join/leave notifications."""
import os
import sys
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.join(ADDON_DIR, 'resources', 'lib'))

import kodi_stubs  # noqa: E402

LIBRARY = {
    'VideoLibrary.GetMovies': {'movies': [
        {'movieid': 1, 'label': 'Inception',
         'file': 'nfs://mynas/films/inception.mkv',
         'uniqueid': {'imdb': 'tt1375666'}, 'year': 2010},
        {'movieid': 2, 'label': 'Inception',
         'file': 'nfs://mynas/films/decoy.mkv',
         'uniqueid': {'imdb': 'tt9999999'}, 'year': 1999},
        {'movieid': 3, 'label': 'Thunderbolts*',
         'file': 'nfs://mynas/films/thunderbolts.mkv',
         'uniqueid': {'tmdb': '986056', 'imdb': 'tt20969586'}, 'year': 2025},
    ]},
    'VideoLibrary.GetTVShows': {'tvshows': [
        {'tvshowid': 7, 'label': 'Severance'},
    ]},
    'VideoLibrary.GetEpisodes': {'episodes': [
        {'episodeid': 70, 'episode': 3,
         'file': 'smb://guestnas/tv/severance/s02e03.mkv'},
        {'episodeid': 71, 'episode': 4,
         'file': 'smb://guestnas/tv/severance/s02e04.mkv'},
    ]},
    'AudioLibrary.GetSongs': {'songs': [
        {'songid': 5, 'file': 'smb://guestnas/music/live/karma.mp3',
         'musicbrainztrackid': 'mb-live-999'},
        {'songid': 6, 'file': 'smb://guestnas/music/ok/karma.mp3',
         'musicbrainztrackid': 'mb-studio-123'},
    ]},
}

_, common = kodi_stubs.install(json_rpc_responses=LIBRARY)
import engine  # noqa: E402


class PlayableUrlTest(unittest.TestCase):
    def test_plugin_preferred(self):
        item = {'file': 'http://127.0.0.1:8095/x', 'plugin': 'plugin://a/'}
        self.assertEqual(engine._playable_url(item), 'plugin://a/')

    def test_http_urls_refused(self):
        self.assertEqual(engine._playable_url(
            {'file': 'http://127.0.0.1:8095/x', 'plugin': ''}), '')
        self.assertEqual(engine._playable_url(
            {'file': 'https://cdn.example.com/x.m3u8', 'plugin': ''}), '')

    def test_plain_paths_shared(self):
        self.assertEqual(engine._playable_url(
            {'file': 'smb://nas/x.mkv', 'plugin': ''}), 'smb://nas/x.mkv')

    def test_empty_item(self):
        self.assertEqual(engine._playable_url({'label': 'x'}), '')


class LibraryMatchTest(unittest.TestCase):
    def test_movie_by_unique_id_beats_decoy(self):
        item = {'type': 'movie', 'title': 'Inception', 'year': 2010,
                'ids': {'imdb': 'tt1375666'}, 'file': 'smb://host/x.mkv'}
        ref, file = engine._find_in_library(item)
        self.assertEqual(ref, {'movieid': 1})
        self.assertEqual(file, 'nfs://mynas/films/inception.mkv')

    def test_movie_by_title_year_fallback(self):
        item = {'type': 'movie', 'title': 'Inception', 'year': 2010,
                'ids': {'tvdb': 'unknown'}, 'file': 'x'}
        ref, _ = engine._find_in_library(item)
        self.assertEqual(ref, {'movieid': 1})

    def test_episode_by_show_season_episode(self):
        item = {'type': 'episode', 'show': 'Severance',
                'season': 2, 'episode': 4, 'file': 'plugin://y'}
        ref, file = engine._find_in_library(item)
        self.assertEqual(ref, {'episodeid': 71})
        self.assertEqual(file, 'smb://guestnas/tv/severance/s02e04.mkv')

    ANIME = {'tvshowid': 1, 'label': 'One Piece', 'year': 1999, 'uniqueid': {'tmdb': '37854', 'tvdb': '81797'}}
    LIVE = {'tvshowid': 2, 'label': 'One Piece', 'year': 2023, 'uniqueid': {'tmdb': '111110', 'imdb': 'tt11737520'}}

    def test_same_named_shows_by_the_shows_id(self):
        """Live action One Piece played the anime (2026-11-17): the
        show's ids pick the right one, or none."""
        item = {'type': 'episode', 'show': 'One Piece', 'season': 1, 'episode': 1,
                'ids': {'show_tmdb': '111110'}}
        self.assertEqual(engine._same_show([self.ANIME, self.LIVE], item), [self.LIVE])
        self.assertEqual(engine._same_show([self.ANIME], item), [])

    def test_same_named_shows_by_year(self):
        item = {'type': 'episode', 'show': 'One Piece', 'season': 1, 'episode': 1, 'year': 2023}
        self.assertEqual(engine._same_show([self.ANIME, self.LIVE], item), [self.LIVE])
        self.assertEqual(engine._same_show([self.ANIME], item), [])

    def test_same_named_shows_without_details_match_none(self):
        item = {'type': 'episode', 'show': 'One Piece', 'season': 1, 'episode': 1}
        self.assertEqual(engine._same_show([self.ANIME, self.LIVE], item), [])
        # one show of that name: it, as before
        self.assertEqual(engine._same_show([self.ANIME], item), [self.ANIME])
        bare = {'tvshowid': 7, 'label': 'Severance'}
        self.assertEqual(engine._same_show([bare], dict(item, ids={'show_tmdb': '95396'}, year=2022)), [bare])

    def test_the_shows_identity_is_shared(self):
        saved = engine._json_rpc
        engine._json_rpc = lambda method, params=None: (
            {'tvshowdetails': {'year': 2023, 'uniqueid': {'tmdb': '111110', 'imdb': 'tt11737520', 'unknown': 'x'}}}
            if method == 'VideoLibrary.GetTVShowDetails' and params['tvshowid'] == 2 else None)
        try:
            self.assertEqual(engine._show_identity(2, {'tmdb': '2229460'}),
                             {'ids': {'tmdb': '2229460', 'show_tmdb': '111110', 'show_imdb': 'tt11737520'},
                              'year': 2023})
            self.assertEqual(engine._show_identity(-1, {}), {})
            self.assertEqual(engine._show_identity(None, {}), {})
        finally:
            engine._json_rpc = saved

    def test_no_identity_no_match(self):
        self.assertEqual(engine._find_in_library({'file': 'x'}),
                         (None, ''))

    def test_song_by_musicbrainz_id_beats_first_result(self):
        item = {'type': 'song', 'title': 'Karma Police',
                'artist': ['Radiohead'], 'album': 'OK Computer',
                'ids': {'mbtrack': 'mb-studio-123'}, 'file': 'x'}
        ref, file = engine._find_in_library(item)
        self.assertEqual(ref, {'songid': 6})
        self.assertEqual(file, 'smb://guestnas/music/ok/karma.mp3')

    def test_song_without_mb_takes_first_filtered(self):
        item = {'type': 'song', 'title': 'Karma Police',
                'artist': ['Radiohead'], 'file': 'x'}
        ref, _ = engine._find_in_library(item)
        self.assertEqual(ref, {'songid': 5})

    def test_song_without_title_no_match(self):
        self.assertEqual(engine._find_in_library(
            {'type': 'song', 'artist': ['X'], 'file': 'x'}), (None, ''))


class AppItemTest(unittest.TestCase):
    """Watch Party's Android app and browser extension share streaming
    links (Kodi can't open them) with the title's identity in these same
    fields (RelayClient.appItem / links.js roomItem): Kodi plays its own
    library copy instead."""

    def test_android_movie_plays_the_library_copy(self):
        item = {'file': 'https://www.disneyplus.com/play/f51dce23',
                'label': 'Thunderbolts*', 'type': 'movie',
                'title': 'Thunderbolts*', 'year': 2025,
                'ids': {'android': 'com.disney.disneyplus', 'tmdb': '986056'}}
        self.assertEqual(engine._playable_url(item), '')
        ref, file = engine._find_in_library(item)
        self.assertEqual(ref, {'movieid': 3})
        self.assertEqual(file, 'nfs://mynas/films/thunderbolts.mkv')

    def test_android_episode_plays_the_library_copy(self):
        item = {'file': 'https://www.netflix.com/watch/81721676',
                'label': 'Severance · S2 · E4 · Woe\'s Hollow',
                'title': 'Severance · S2 · E4 · Woe\'s Hollow',
                'type': 'episode', 'show': 'Severance', 'season': 2,
                'episode': 4, 'ids': {'android': 'com.netflix.ninja'}}
        self.assertEqual(engine._playable_url(item), '')
        ref, file = engine._find_in_library(item)
        self.assertEqual(ref, {'episodeid': 71})

    def test_pasted_link_without_identity_is_not_playable(self):
        item = {'file': 'https://www.netflix.com/watch/1', 'label': 'X',
                'title': 'X', 'ids': {'android': 'com.netflix.ninja'}}
        self.assertEqual(engine._playable_url(item), '')
        self.assertEqual(engine._find_in_library(item), (None, ''))


class AddonRefTest(unittest.TestCase):
    """A web link from the Android app or browser plays in an installed
    streaming addon (addon_links.py) when there's no library copy."""
    item = {'file': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'label': 'Song',
            'ids': {'android': 'com.google.android.youtube.tv'}}

    def tearDown(self):
        sys.modules['xbmc'].getCondVisibility = lambda cond: False

    def test_only_when_the_addon_is_installed(self):
        self.assertIsNone(engine._addon_ref(self.item))
        asked = []
        sys.modules['xbmc'].getCondVisibility = lambda cond: asked.append(cond) or True
        self.assertEqual(engine._addon_ref(self.item), {'file': 'plugin://plugin.video.youtube/play/?video_id=dQw4w9WgXcQ'})
        self.assertEqual(asked, ['System.HasAddon(plugin.video.youtube)'])

    def test_kodi_items_keep_their_own_path(self):
        sys.modules['xbmc'].getCondVisibility = lambda cond: True
        self.assertIsNone(engine._addon_ref({'file': 'http://127.0.0.1:8095/x', 'plugin': 'plugin://slyguy.hulu/?_=play&id=1'}))


class GateRpc:
    """Kodi's answers about its addons and sources, for the shared-path gate."""
    ADDONS = {'video': ['plugin.video.youtube', 'slyguy.max'], 'audio': ['plugin.audio.x']}
    SOURCES = {'video': [{'file': 'smb://nas/movies/'},
                         {'file': 'multipath://nfs%3a%2f%2fbox%2ftv%2f/%2fstorage%2fvideos%2f/'}],
               'music': [{'file': 'smb://nas/'}]}

    def __enter__(self):
        self.saved = engine._json_rpc
        engine._gate_cache.clear()

        def rpc(method, params=None):
            if method == 'Addons.GetAddons':
                return {'addons': [{'addonid': a} for a in self.ADDONS.get(params['content'], [])]}
            if method == 'Files.GetSources':
                return {'sources': self.SOURCES.get(params['media'], [])}
            return self.saved(method, params)
        engine._json_rpc = rpc
        return self

    def __exit__(self, *exc):
        engine._json_rpc = self.saved
        engine._gate_cache.clear()


class SharedPathGateTest(unittest.TestCase):
    """The audit, 2026-11-22: a member's item mustn't run any addon, or
    send this device to someone else's SMB server."""

    def test_media_addons_only(self):
        with GateRpc():
            self.assertTrue(engine._allowed_path('plugin://plugin.video.youtube/play/?video_id=x'))
            self.assertTrue(engine._allowed_path('plugin://plugin.audio.x/?id=1'))
            for path in ('plugin://plugin.program.backup/?action=restore',
                         'plugin://script.module.x/', 'plugin://not.installed/?a=1'):
                self.assertFalse(engine._allowed_path(path), path)

    def test_paths_under_this_kodis_sources_only(self):
        with GateRpc():
            for path in ('smb://nas/movies/Inception (2010).mkv', 'SMB://NAS/Movies/x.mkv',
                         'nfs://box/tv/show/s01e01.mkv', '/storage/videos/home.mp4',
                         'smb://nas/music/a.flac'):
                self.assertTrue(engine._allowed_path(path), path)
            for path in ('smb://attacker/share/x.mkv', 'smb://nasty/movies/x.mkv',
                         'smb://nas/movies/../../other/x', '/etc/passwd', 'http://x/y', ''):
                self.assertFalse(engine._allowed_path(path), path)


class CleanItemTest(unittest.TestCase):
    def test_malformed_fields_dropped(self):
        item = engine._clean_item({'file': 'smb://nas/a.mkv', 'season': 'S01', 'episode': '3',
                                   'year': 2.5, 'ids': [], 'title': 5,
                                   'playlist': [{'file': 'a', 'season': 'x'}, 'junk']})
        self.assertEqual(item['episode'], 3)
        for key in ('season', 'year', 'ids', 'title'):
            self.assertNotIn(key, item)
        self.assertEqual(item['playlist'], [{'file': 'a'}])
        self.assertIsNone(engine._clean_item(['not', 'an', 'item']))
        self.assertIsNone(engine._clean_item({'label': 'nothing to play'}))


class EntryRefTest(unittest.TestCase):
    def setUp(self):
        self.gate = GateRpc().__enter__()
        self.addCleanup(self.gate.__exit__)

    def test_plugin_entries_pass_through(self):
        ref, key = engine._local_entry_ref(
            {'file': 'plugin://plugin.audio.x/?id=1'})
        self.assertEqual(ref, {'file': 'plugin://plugin.audio.x/?id=1'})
        self.assertEqual(key, 'plugin://plugin.audio.x/?id=1')

    def test_media_server_entries_map_to_library_ids(self):
        # a Plex direct https entry maps to this device's songid
        entry = {'file': 'https://x.plex.direct:32400/parts/1/f.m4a'
                         '?X-Plex-Token=abc',
                 'type': 'song', 'title': 'Karma Police',
                 'artist': ['Radiohead'],
                 'ids': {'mbtrack': 'mb-studio-123'}}
        ref, key = engine._local_entry_ref(entry)
        self.assertEqual(ref, {'songid': 6})
        self.assertEqual(key, 'smb://guestnas/music/ok/karma.mp3')

    def test_plain_paths_pass_through(self):
        ref, key = engine._local_entry_ref({'file': 'smb://nas/t.mp3'})
        self.assertEqual(ref, {'file': 'smb://nas/t.mp3'})
        self.assertEqual(key, 'smb://nas/t.mp3')

    def test_other_paths_refused(self):
        self.assertEqual(engine._local_entry_ref({'file': 'smb://attacker/t.mp3'}), (None, ''))
        self.assertEqual(engine._local_entry_ref({'file': 'plugin://plugin.program.x/?a=1'}), (None, ''))

    def test_unmatchable_streams_rejected(self):
        self.assertEqual(engine._local_entry_ref(
            {'file': 'http://127.0.0.1:8095/stream'}), (None, ''))
        self.assertEqual(engine._local_entry_ref({'file': ''}), (None, ''))


class ArtTest(unittest.TestCase):
    def test_http_art_shared(self):
        self.assertEqual(
            engine._shareable_art('http://img.youtube.com/vi/a/hq.jpg'),
            'http://img.youtube.com/vi/a/hq.jpg')

    def test_kodi_image_wrapper_unwrapped(self):
        self.assertEqual(
            engine._shareable_art('image://http%3a%2f%2fnas%2fp.jpg/'),
            'http://nas/p.jpg')

    def test_local_paths_not_shared(self):
        self.assertEqual(
            engine._shareable_art('/storage/.kodi/thumbs/a.jpg'), '')
        self.assertEqual(
            engine._shareable_art('image://%2fstorage%2fart.jpg/'), '')

    def test_square_request_box_is_dropped(self):
        # Kodi asks Plex for a square box; a wide or tall source fitted
        # into it comes back padded, and those bars are baked in
        url = ('https://plex.local:32400/photo/:/transcode?width=1920'
               '&height=1920&minSize=1&upscale=0&url=/library/x')
        out = engine._shrink_art(url)
        self.assertIn('width=640', out)
        self.assertNotIn('height=', out)
        self.assertIn('minSize=1', out)     # other params untouched
        self.assertIn('url=%2Flibrary%2Fx', out)

    def test_non_square_request_is_only_clamped(self):
        url = 'https://s.example/p.jpg?width=1600&height=900'
        out = engine._shrink_art(url)
        self.assertIn('width=640', out)
        self.assertIn('height=640', out)    # kept, just clamped

    def test_youtube_letterboxed_thumbnails_swapped(self):
        # hqdefault/sddefault/default are 4:3 with bars burnt in
        for boxed in ('hqdefault', 'sddefault', 'default'):
            out = engine._shrink_art(
                f'https://i.ytimg.com/vi/CFdOh0bUoKg/{boxed}.jpg')
            self.assertEqual(
                out, 'https://i.ytimg.com/vi/CFdOh0bUoKg/mqdefault.jpg')

    def test_youtube_clean_thumbnails_untouched(self):
        for clean in ('mqdefault', 'maxresdefault', 'hq720'):
            url = f'https://i.ytimg.com/vi/abc/{clean}.jpg'
            self.assertEqual(engine._shrink_art(url), url)

    def test_art_without_size_params_untouched(self):
        url = 'https://images.example.com/poster.jpg'
        self.assertEqual(engine._shrink_art(url), url)

    def test_credentialed_art_never_shared(self):
        # the dashboard is unauthenticated — a media server token must
        # not be published on it
        self.assertEqual(engine._shareable_art(
            'https://plex.local:32400/photo/x?X-Plex-Token=SECRET'), '')
        self.assertEqual(engine._shareable_art(
            'https://s.example/p.jpg?api_key=k'), '')


class IdentityTest(unittest.TestCase):
    def test_song_identity(self):
        info = {'type': 'song', 'title': 'Stop the Rain',
                'artist': ['Ed Sheeran'], 'album': '=',
                'musicbrainztrackid': ''}
        out = engine._identity(info)
        self.assertEqual(out['type'], 'song')
        self.assertEqual(out['artist'], ['Ed Sheeran'])
        self.assertNotIn('ids', out)  # empty mb id not shared

    def test_unknown_type_with_artist_counts_as_song(self):
        out = engine._identity({'type': 'unknown', 'title': 'T',
                                'artist': ['A']})
        self.assertEqual(out['type'], 'song')

    def test_movie_identity(self):
        out = engine._identity({'type': 'movie', 'title': 'Inception',
                                'year': 2010,
                                'uniqueid': {'imdb': 'tt1375666'}})
        self.assertEqual(out['type'], 'movie')
        self.assertEqual(out['ids'], {'imdb': 'tt1375666'})


class BookkeepingTest(unittest.TestCase):
    def _engine(self):
        e = engine.SyncEngine.__new__(engine.SyncEngine)
        e._opening_until = 0.0
        e._local_starting_until = 0.0
        e._opened_key = ''
        e._local_key = ''
        e._member_names = None
        return e

    def test_user_open_not_auto(self):
        e = self._engine()
        self.assertFalse(
            e.note_av_started({'file': 'f', 'plugin': 'plugin://x/'}))
        self.assertEqual(e._local_key, 'plugin://x/')

    def test_auto_open_adopts_party_key(self):
        e = self._engine()
        e._opening_until = time.time() + 20
        e._opened_key = 'plugin://party/item'
        self.assertTrue(
            e.note_av_started({'file': 'g', 'plugin': 'plugin://other/'}))
        self.assertEqual(e._local_key, 'plugin://party/item')

    def test_local_open_window_cleared_on_av(self):
        e = self._engine()
        e.note_local_open()
        self.assertGreater(e._local_starting_until, time.time())
        e.note_av_started(None)
        self.assertEqual(e._local_starting_until, 0.0)

    def test_stop_clears_local_state(self):
        e = self._engine()
        e._local_key = 'k'
        e.note_local_open()
        e.note_stopped()
        self.assertEqual(e._local_key, '')
        self.assertEqual(e._local_starting_until, 0.0)


class QueueReorderTest(unittest.TestCase):
    """In-place reordering after the announcer shuffles."""

    def setUp(self):
        import json as _json
        import xbmc as _xbmc
        self.xbmc = _xbmc
        self.original_rpc = _xbmc.executeJSONRPC
        self.queue = ['a', 'b', 'c', 'd']
        self.swaps = []

        def rpc(query):
            req = _json.loads(query)
            method, params = req['method'], req.get('params') or {}
            if method == 'Player.GetActivePlayers':
                return _json.dumps({'result': [{'playerid': 0,
                                                'type': 'audio'}]})
            if method == 'Player.GetProperties':
                return _json.dumps({'result': {'playlistid': 0,
                                               'position': 0}})
            if method == 'Playlist.GetItems':
                return _json.dumps({'result': {
                    'items': [{'file': f} for f in self.queue],
                    'limits': {'total': len(self.queue)}}})
            if method == 'Playlist.Swap':
                i, j = params['position1'], params['position2']
                self.queue[i], self.queue[j] = self.queue[j], self.queue[i]
                self.swaps.append((i, j))
                return _json.dumps({'result': 'OK'})
            return _json.dumps({'error': {'code': -1}})

        _xbmc.executeJSONRPC = rpc

    def tearDown(self):
        self.xbmc.executeJSONRPC = self.original_rpc

    def _engine(self):
        e = engine.SyncEngine.__new__(engine.SyncEngine)
        e._queue_map = {'pa': 'a', 'pb': 'b', 'pc': 'c', 'pd': 'd'}
        e._queue_order = ['pa', 'pb', 'pc', 'pd']
        e._queue_index = {'pa': 0, 'pb': 1, 'pc': 2, 'pd': 3}
        e._queue_playlistid = 0
        e._party_keys = set()
        e._local_key = 'b'
        return e

    def _entries(self, order):
        return {'playlist': [{'file': k} for k in order]}

    def test_permutes_queue_without_rebuilding(self):
        e = self._engine()
        shuffled = ['pd', 'pb', 'pa', 'pc']
        # 'pb' is the playing track and moves slots, as Kodi's shuffle does
        self.assertTrue(e._resync_queue(self._entries(shuffled), 'pb'))
        self.assertEqual(self.queue, ['d', 'b', 'a', 'c'])
        self.assertTrue(self.swaps)          # reordered by swapping
        self.assertEqual(e._queue_order, shuffled)
        self.assertEqual(e._queue_index, {'pd': 0, 'pb': 1,
                                          'pa': 2, 'pc': 3})

    def test_already_in_order_is_a_no_op(self):
        e = self._engine()
        same = ['pa', 'pb', 'pc', 'pd']
        self.assertTrue(e._resync_queue(self._entries(same), 'pb'))
        self.assertEqual(self.swaps, [])
        self.assertEqual(self.queue, ['a', 'b', 'c', 'd'])

    def test_unknown_entry_falls_back(self):
        e = self._engine()
        with_new = ['pa', 'pb', 'pc', 'pd', 'pNEW']
        self.assertFalse(e._resync_queue(self._entries(with_new), 'pb'))
        self.assertEqual(self.swaps, [])     # queue left untouched

    def test_playing_item_outside_queue_falls_back(self):
        e = self._engine()
        self.assertFalse(e._resync_queue(
            self._entries(['pa', 'pb', 'pc', 'pd']), 'pOTHER'))

    def test_slot_is_resolved_live_not_from_stale_index(self):
        # the local queue drifted from the shared order; a cached index
        # would point at the wrong song, so the slot is looked up live
        e = self._engine()
        self.queue = ['d', 'c', 'b', 'a']
        e._queue_index = {'pa': 0, 'pb': 1, 'pc': 2, 'pd': 3}  # stale
        self.assertEqual(e._queue_slot_of('pa'), (0, 3))
        self.assertEqual(e._queue_slot_of('pd'), (0, 0))

    def test_slot_missing_when_item_left_the_queue(self):
        e = self._engine()
        self.queue = ['b', 'c', 'd']          # 'a' removed locally
        self.assertEqual(e._queue_slot_of('pa'), (-1, -1))
        self.assertEqual(e._queue_slot_of('pUNKNOWN'), (-1, -1))

    def test_cursor_identifies_the_playing_item(self):
        # cursor is at position 0 in the stub — 'a' / party key 'pa'
        e = self._engine()
        self.assertTrue(e._cursor_is('pa'))
        self.assertFalse(e._cursor_is('pb'))

    def test_cursor_beats_a_rewritten_stream_url(self):
        # the media server handed out a fresh token for the same song:
        # the resolved URL no longer matches, but the queue cursor does
        e = self._engine()
        e._local_key = 'a?token=CHANGED'
        self.assertNotEqual(e._queue_map['pa'], e._local_key)
        self.assertTrue(e._cursor_is('pa'))


class SingleWriterTest(unittest.TestCase):
    def _engine(self, opened_current, party_keys=frozenset()):
        e = engine.SyncEngine.__new__(engine.SyncEngine)
        e.suppress = engine._Suppressor()
        e._opening_until = 0.0
        e._local_starting_until = 0.0
        e._opened_key = ''
        e._open_fallbacks = []
        e._local_key = 'k'
        e._failed_keys = set()
        e._i_opened_current = opened_current
        e._party_keys = set(party_keys)
        pushed = []
        p = engine._PartyPlayer.__new__(engine._PartyPlayer)
        p.engine = e
        p._push = lambda cmd, **kw: pushed.append(cmd)
        return e, p, pushed

    def test_follower_queue_advance_not_announced(self):
        e, _, _ = self._engine(False, {'smb://nas/t1.mp3',
                                       'smb://nas/t2.mp3'})
        self.assertTrue(e.is_natural_advance('smb://nas/t2.mp3'))

    def test_driver_announces_advances(self):
        e, _, _ = self._engine(True, {'smb://nas/t2.mp3'})
        self.assertFalse(e.is_natural_advance('smb://nas/t2.mp3'))

    def test_off_queue_items_still_announced(self):
        e, _, _ = self._engine(False, {'smb://nas/t1.mp3'})
        self.assertFalse(e.is_natural_advance('smb://nas/other.mkv'))

    def test_old_item_teardown_is_not_a_failed_open(self):
        e, p, pushed = self._engine(False)
        e._opening_until = time.time() + 20   # our new open in flight
        e._opened_key = 'newitem'
        e.suppress.arm('stop')                # play() replaced old item
        p.onPlayBackStopped()
        self.assertEqual(pushed, [])
        self.assertNotIn('newitem', e._failed_keys)

    def test_unexpected_stop_during_open_is_a_failure(self):
        e, p, pushed = self._engine(False)
        e._opening_until = time.time() + 20
        e._opened_key = 'newitem'
        p.onPlayBackStopped()                 # no suppression armed
        self.assertEqual(pushed, [])          # failure never stops party
        self.assertIn('newitem', e._failed_keys)

    def test_user_stop_reaches_the_party(self):
        e, p, pushed = self._engine(False)
        p.onPlayBackStopped()
        self.assertEqual(pushed, ['stop'])

    def test_follower_natural_end_is_silent(self):
        e, p, pushed = self._engine(False)
        p.onPlayBackEnded()
        self.assertEqual(pushed, [])

    def test_driver_natural_end_announces(self):
        e, p, pushed = self._engine(True)
        p.onPlayBackEnded()
        self.assertEqual(pushed, ['stop'])


class MemberNotifyTest(unittest.TestCase):
    def test_join_and_leave_toasts(self):
        e = engine.SyncEngine.__new__(engine.SyncEngine)
        e._member_names = None
        common.notifications.clear()
        e._notify_member_changes({'members': [{'name': 'host'}]})
        self.assertEqual(common.notifications, [])  # baseline is silent
        e._notify_member_changes({'members': [{'name': 'host'},
                                              {'name': 'bedroom'}]})
        self.assertEqual(common.notifications, ['bedroom joined the party'])
        common.notifications.clear()
        e._notify_member_changes({'members': [{'name': 'host'}]})
        self.assertEqual(common.notifications, ['bedroom left the party'])
        common.notifications.clear()
        e._notify_member_changes({'members': [{'name': 'host'}]})
        self.assertEqual(common.notifications, [])  # no change, no spam


class SuppressorTest(unittest.TestCase):
    """The engine marks its own seek with where it seeked to: only a seek
    event that lands there is its own; the user's goes to the party."""

    def test_our_seek_is_ours(self):
        sup = engine._Suppressor()
        sup.arm('seek', target=110.0)
        self.assertTrue(sup.consume('seek', position=110.4))
        # used up: a later seek is the user's
        self.assertFalse(sup.consume('seek', position=111.0))

    def test_the_users_seek_before_ours_lands_is_theirs(self):
        sup = engine._Suppressor()
        sup.arm('seek', target=110.0)
        # Kodi never reported ours (refused), and the user skips ahead
        self.assertFalse(sup.consume('seek', position=1343.0))
        # ours, if it does come, is still recognised
        self.assertTrue(sup.consume('seek', position=109.0))

    def test_tokens_expire(self):
        sup = engine._Suppressor()
        sup.arm('seek', target=10.0)
        sup._tokens['seek'] = (time.time() - 1, 10.0)
        self.assertFalse(sup.consume('seek', position=10.0))
        self.assertNotIn('seek', sup._tokens)

    def test_other_actions_need_no_position(self):
        sup = engine._Suppressor()
        sup.arm('pause')
        self.assertTrue(sup.consume('pause'))
        self.assertFalse(sup.consume('pause'))


class AddonPathFallbackTest(unittest.TestCase):
    """An addon that hands the player only its resolved stream (SlyGuy's
    HBO Max: a proxy on 127.0.0.1) still shares the addon path picked."""

    PROXY = ('http://127.0.0.1:8095/https://cf.dplus.us.prd.media.max.com/gcs/x/dash.mpd'
             '?sessionId=abc')
    PICKED = 'plugin://slyguy.max/?_=play&slug=abc'

    def setUp(self):
        self.saved = (engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility,
                      engine._active_player_id, engine._json_rpc)

    def tearDown(self):
        (engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility,
         engine._active_player_id, engine._json_rpc) = self.saved

    def _player(self, eng):
        test = self

        class Player:
            engine = eng

            def getPlayingFile(self):
                return test.PROXY

            def getTotalTime(self):
                return 2881.6
        return Player()

    def _engine(self):
        eng = engine.SyncEngine.__new__(engine.SyncEngine)
        eng._started_plugin = ('', 0.0)
        eng._local_starting_until = 0.0
        return eng

    def test_the_picked_addon_item_is_shared(self):
        eng = self._engine()
        engine.xbmc.getInfoLabel = lambda label: self.PICKED if label == 'ListItem.FileNameAndPath' else ''
        engine.xbmc.getCondVisibility = lambda cond: False
        eng.note_local_open(*engine._started_item(self._player(eng)))
        engine._active_player_id = lambda: 1
        engine._json_rpc = lambda method, params=None: (
            {'item': {'file': self.PROXY, 'type': 'movie', 'title': 'Dune', 'year': 2021}}
            if method == 'Player.GetItem' else {})
        item = engine._now_playing_item(self._player(eng))
        self.assertEqual(item['plugin'], self.PICKED)
        self.assertEqual(item['title'], 'Dune')

    def test_a_real_plugin_path_from_the_player_wins(self):
        eng = self._engine()
        eng.note_local_open('plugin://other.addon/?x=1')
        engine._active_player_id = lambda: 1
        engine._json_rpc = lambda method, params=None: (
            {'item': {'file': self.PICKED}} if method == 'Player.GetItem' else {})
        self.assertEqual(engine._now_playing_item(self._player(eng))['plugin'], self.PICKED)

    def test_nothing_picked_or_too_long_ago(self):
        eng = self._engine()
        engine.xbmc.getInfoLabel = lambda label: 'smb://nas/movies/Dune.mkv'
        engine.xbmc.getCondVisibility = lambda cond: False
        eng.note_local_open(*engine._started_item(self._player(eng)))
        self.assertEqual(eng.started_plugin(), '')
        eng._started_plugin = (self.PICKED, time.time() - 120)
        self.assertEqual(eng.started_plugin(), '')


class StartedPathTest(unittest.TestCase):
    """Where the starting item's addon path comes from (kodi.log,
    2026-11-17: the player opened .../?_=play&_play=1&edit_id=4f97...,
    the focused list item was the season)."""

    EPISODE = 'plugin://slyguy.discovery.plus/?_=play&_play=1&edit_id=4f975d61-3f7f-4926-af3a-783be607e115'
    SEASON = 'plugin://slyguy.discovery.plus/?_=series&id=eb26e00e-9582-4790-a61c-48d785926f58&season=17'

    def setUp(self):
        self.saved = (engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility)

    def tearDown(self):
        engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility = self.saved

    def player(self, playing):
        class P:
            def getPlayingFile(self):
                if playing is None:
                    raise RuntimeError('not playing')
                return playing
        return P()

    def test_the_players_path_first(self):
        engine.xbmc.getInfoLabel = lambda label: self.SEASON
        engine.xbmc.getCondVisibility = lambda cond: True
        self.assertEqual(engine._started_item(self.player(self.EPISODE))[0], self.EPISODE)

    def test_a_focused_folder_is_not_what_started(self):
        engine.xbmc.getInfoLabel = lambda label: self.SEASON
        engine.xbmc.getCondVisibility = lambda cond: cond == 'ListItem.IsFolder'
        self.assertEqual(engine._started_item(self.player('http://127.0.0.1:8095/x.mpd'))[0], '')

    def test_a_focused_playable_item_when_the_player_has_none(self):
        engine.xbmc.getInfoLabel = lambda label: self.EPISODE
        engine.xbmc.getCondVisibility = lambda cond: False
        self.assertEqual(engine._started_item(self.player(None))[0], self.EPISODE)


class StartedIdentityTest(unittest.TestCase):
    """The Discovery+ addon's episode reaches the player with no details,
    but its list item has them: shared when it's the item that started."""

    PLAYED = 'plugin://slyguy.discovery.plus/?_=play&_play=1&edit_id=4f975d61-3f7f-4926-af3a-783be607e115'
    LISTED = 'plugin://slyguy.discovery.plus/?_=play&edit_id=4f975d61-3f7f-4926-af3a-783be607e115'
    LABELS = {'ListItem.FileNameAndPath': LISTED, 'ListItem.DBTYPE': 'episode',
              'ListItem.Title': 'Moving Day', 'ListItem.TVShowTitle': 'House Hunters',
              'ListItem.Season': '17', 'ListItem.Episode': '4'}

    def setUp(self):
        self.saved = (engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility,
                      engine._active_player_id, engine._json_rpc)
        engine.xbmc.getInfoLabel = lambda label: self.LABELS.get(label, '')
        engine.xbmc.getCondVisibility = lambda cond: False

    def tearDown(self):
        (engine.xbmc.getInfoLabel, engine.xbmc.getCondVisibility,
         engine._active_player_id, engine._json_rpc) = self.saved

    def player(self, playing, eng=None):
        class P:
            engine = eng

            def getPlayingFile(self):
                return playing

            def getTotalTime(self):
                return 1290.0
        return P()

    def test_the_episode_is_shared_with_its_show_season_and_episode(self):
        eng = engine.SyncEngine.__new__(engine.SyncEngine)
        eng.note_local_open(*engine._started_item(self.player(self.PLAYED)))
        engine._active_player_id = lambda: 1
        engine._json_rpc = lambda method, params=None: (
            {'item': {'file': 'http://127.0.0.1:8095/https://cf.dplus.us.prd.media.max.com/x/dash.mpd', 'type': 'unknown'}}
            if method == 'Player.GetItem' else {})
        item = engine._now_playing_item(self.player('http://127.0.0.1:8095/https://x/dash.mpd', eng))
        self.assertEqual(item['plugin'], self.PLAYED)
        self.assertEqual((item['type'], item['show'], item['season'], item['episode']),
                         ('episode', 'House Hunters', 17, 4))

    def test_another_focused_item_says_nothing_about_this_one(self):
        self.LABELS = dict(self.LABELS, **{'ListItem.FileNameAndPath':
                           'plugin://slyguy.discovery.plus/?_=play&edit_id=1ff45ee7-c040-4ff6-8345-59bee0075e88'})
        path, info = engine._started_item(self.player(self.PLAYED))
        self.assertEqual((path, info), (self.PLAYED, {}))


class WbdEpisodeTest(unittest.TestCase):
    """A one-id HBO Max episode link opens the episode found in the addon;
    a movie's opens the addon's id route (Batman Begins played, 2026-11-17)."""

    LINK = 'https://play.hbomax.com/video/watch/95b04291-15f3-4318-93d8-193ee1948559'

    def setUp(self):
        self.saved = (engine.xbmc.getCondVisibility, engine._json_rpc)
        engine.xbmc.getCondVisibility = lambda cond: True

    def tearDown(self):
        engine.xbmc.getCondVisibility, engine._json_rpc = self.saved

    def test_episode(self):
        asked = []

        def rpc(method, params=None):
            asked.append(params['directory'])
            if '_=search' in params['directory']:
                return {'files': [{'file': 'plugin://slyguy.max/?_=series&id=abc', 'label': 'Gold Rush'}]}
            return {'files': [{'file': 'plugin://slyguy.max/?_=play&edit_id=e1', 'season': 12, 'episode': 5}]}
        engine._json_rpc = rpc
        item = {'file': self.LINK, 'type': 'episode', 'show': 'Gold Rush', 'season': 12, 'episode': 5}
        self.assertEqual(engine._addon_ref(item), {'file': 'plugin://slyguy.max/?_=play&edit_id=e1'})
        self.assertEqual(asked[1], 'plugin://slyguy.max/?_=series&id=abc&season=12')

    def test_episode_by_the_shows_id(self):
        asked = []

        def rpc(method, params=None):
            asked.append(params['directory'])
            if 'id=eb26e00e-9582-4790-a61c-48d785926f58&season=2' in params['directory']:
                return {'files': [{'file': 'plugin://slyguy.max/?_=play&edit_id=e2', 'season': 2, 'episode': 1}]}
            return {'files': []}
        engine._json_rpc = rpc
        item = {'file': self.LINK, 'type': 'episode', 'show': 'Gold Rush', 'season': 2, 'episode': 1,
                'ids': {'wbd': 'eb26e00e-9582-4790-a61c-48d785926f58/s2/e1'}}
        self.assertEqual(engine._addon_ref(item), {'file': 'plugin://slyguy.max/?_=play&edit_id=e2'})
        self.assertEqual(len(asked), 1)   # no search

    def test_a_wrong_show_id_falls_back_to_the_search(self):
        def rpc(method, params=None):
            if '_=search' in params['directory']:
                return {'files': [{'file': 'plugin://slyguy.max/?_=series&id=abc', 'label': 'Gold Rush'}]}
            if 'id=abc' in params['directory']:
                return {'files': [{'file': 'plugin://slyguy.max/?_=play&edit_id=e3', 'season': 2, 'episode': 1}]}
            return {'files': []}
        engine._json_rpc = rpc
        item = {'file': self.LINK, 'type': 'episode', 'show': 'Gold Rush', 'season': 2, 'episode': 1,
                'ids': {'wbd': 'eb26e00e-9582-4790-a61c-48d785926f58/s2/e1'}}
        self.assertEqual(engine._addon_ref(item), {'file': 'plugin://slyguy.max/?_=play&edit_id=e3'})

    def test_episode_not_found_skips_the_addon(self):
        engine._json_rpc = lambda method, params=None: {'files': []}
        item = {'file': self.LINK, 'type': 'episode', 'show': 'Gold Rush', 'season': 12, 'episode': 5}
        self.assertIsNone(engine._addon_ref(item))

    def test_movie(self):
        engine._json_rpc = lambda method, params=None: self.fail('no browsing for a movie')
        ref = engine._addon_ref({'file': self.LINK, 'type': 'movie', 'title': 'Batman Begins'})
        self.assertEqual(ref, {'file': 'plugin://slyguy.max/?_=play&id=95b04291-15f3-4318-93d8-193ee1948559'})


if __name__ == '__main__':
    unittest.main()


class PauseReconcileTest(unittest.TestCase):
    """The audit, 2026-11-22: our own pause, then the player resumed without
    its event reaching the party, left us playing in a paused party."""

    def _engine(self):
        import queue
        e = engine.SyncEngine.__new__(engine.SyncEngine)
        e._cmd_q = queue.Queue()
        e._pause_mismatch_since = 0.0
        return e

    def test_someone_elses_pause_applies_at_once(self):
        self.assertTrue(self._engine()._pause_to_apply(True, False, own_change=False, now=100.0))

    def test_our_own_state_wins_after_a_moment(self):
        e = self._engine()
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=100.0))
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=101.0))
        self.assertTrue(e._pause_to_apply(True, False, own_change=True, now=100.0 + engine.PAUSE_RECONCILE))
        self.assertEqual(e._pause_mismatch_since, 0.0)

    def test_not_while_our_command_is_on_its_way(self):
        e = self._engine()
        e._cmd_q.put(('play', 0.0, None, False, ''))
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=100.0))
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=110.0))
        e._cmd_q.get()   # sent: the wait starts again from now
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=111.0))
        self.assertTrue(e._pause_to_apply(True, False, own_change=True, now=114.0))

    def test_agreeing_resets(self):
        e = self._engine()
        e._pause_to_apply(True, False, own_change=True, now=100.0)
        self.assertFalse(e._pause_to_apply(True, True, own_change=True, now=101.0))
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=102.0))
        self.assertFalse(e._pause_to_apply(True, False, own_change=True, now=104.0))
        self.assertTrue(e._pause_to_apply(True, False, own_change=True, now=105.0))
