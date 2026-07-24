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


class EntryRefTest(unittest.TestCase):
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

    def test_unmatchable_streams_rejected(self):
        self.assertEqual(engine._local_entry_ref(
            {'file': 'http://127.0.0.1:8095/stream'}), (None, ''))
        self.assertEqual(engine._local_entry_ref({'file': ''}), (None, ''))


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


if __name__ == '__main__':
    unittest.main()
