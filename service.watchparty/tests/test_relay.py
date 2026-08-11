"""Relay protocol tests — embedded server and standalone multi-room mode.
Pure stdlib; runs anywhere: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ADDON_DIR, 'resources', 'lib'))

from client import RelayClient, RelayError  # noqa: E402
from relay import RelayServer  # noqa: E402

STANDALONE = os.path.join(ADDON_DIR, 'relay_standalone.py')

ITEM = {'file': 'smb://nas/movies/Inception (2010).mkv',
        'label': 'Inception', 'plugin': 'plugin://x/?id=1',
        'type': 'movie', 'title': 'Inception', 'year': 2010,
        'ids': {'imdb': 'tt1375666', 'tmdb': '27205'}}


class EmbeddedRelayTest(unittest.TestCase):
    def setUp(self):
        self.server = RelayServer(port=0, room_code='TEST')
        self.port = self.server._httpd.server_address[1]
        self.server.start()
        self.host = RelayClient('127.0.0.1', self.port, 'TEST')
        self.host.join('host')
        self.guest = RelayClient('127.0.0.1', self.port, 'TEST')
        self.guest.join('guest')

    def tearDown(self):
        self.server.stop()

    def test_item_fields_roundtrip(self):
        self.host.command('open', position=12.0, item=ITEM)
        state = self.guest.poll(0, False, '')
        self.assertEqual(state['item']['plugin'], ITEM['plugin'])
        self.assertEqual(state['item']['ids']['imdb'], 'tt1375666')
        self.assertEqual(state['item']['title'], 'Inception')
        self.assertEqual(state['item']['year'], 2010)
        self.assertEqual(state['set_by'], self.host.member_id)

    def test_wrong_room_rejected(self):
        bad = RelayClient('127.0.0.1', self.port, 'WRONG')
        with self.assertRaises(RelayError):
            bad.join('x')

    def test_stop_clears_item(self):
        self.host.command('open', position=0.0, item=ITEM)
        self.host.command('stop')
        state = self.guest.poll(0, False, '')
        self.assertIsNone(state['item'])

    def test_member_listing(self):
        state = self.guest.poll(1.5, True, 'somefile')
        names = sorted(m['name'] for m in state['members'])
        self.assertEqual(names, ['guest', 'host'])

    def test_buffer_hold_pause_and_resume(self):
        self.host.command('open', position=0.0, item=ITEM)
        state = self.guest.poll(0, False, 'x', caching=True, on_item=True)
        self.assertTrue(state['paused'])
        self.assertEqual(state['set_by'], self.guest.member_id)
        state = self.guest.poll(0, True, 'x', caching=False, on_item=True)
        self.assertFalse(state['paused'])

    def test_buffer_hold_ignores_off_item_caching(self):
        self.host.command('open', position=0.0, item=ITEM)
        state = self.guest.poll(0, False, 'y', caching=True, on_item=False)
        self.assertFalse(state['paused'])

    def test_manual_pause_survives_buffer_resume(self):
        self.host.command('open', position=0.0, item=ITEM)
        self.guest.poll(0, False, 'x', caching=True, on_item=True)
        self.host.command('pause', position=10.0)
        state = self.guest.poll(10, True, 'x', caching=False, on_item=True)
        self.assertTrue(state['paused'])

    def test_pruned_member_gets_specific_error_and_can_rejoin(self):
        import relay as relay_mod
        original_timeout = relay_mod.MEMBER_TIMEOUT
        relay_mod.MEMBER_TIMEOUT = 0.2
        try:
            time.sleep(0.4)
            self.host.poll(0, False, '')  # host's poll prunes silent guest
            with self.assertRaises(RelayError) as ctx:
                self.guest.poll(0, False, '')
            self.assertIn('not a member', str(ctx.exception))
            self.guest.join('guest')       # rejoin works with same client
            state = self.guest.poll(0, False, '')
            names = sorted(m['name'] for m in state['members'])
            self.assertIn('guest', names)
        finally:
            relay_mod.MEMBER_TIMEOUT = original_timeout

    def test_playlist_passthrough_cap_and_identity(self):
        entries = [{'file': f'https://x.plex.direct/{i}.m4a',
                    'label': f'Track {i}', 'type': 'song',
                    'title': f'Track {i}', 'artist': ['Ed Sheeran'],
                    'album': '='} for i in range(120)]
        item = dict(ITEM, type='song', playlist=entries, playlist_pos=2)
        self.host.command('open', position=0.0, item=item)
        state = self.guest.poll(0, False, '')
        stored = state['item']['playlist']
        self.assertEqual(len(stored), 100)              # capped
        self.assertEqual(stored[0]['artist'], ['Ed Sheeran'])
        self.assertEqual(stored[0]['album'], '=')
        self.assertEqual(state['item']['playlist_pos'], 2)

    def test_modes_command_updates_without_moving_the_anchor(self):
        self.host.command('open', position=100.0, item=ITEM)
        before = self.guest.poll(0, False, '')
        time.sleep(0.05)
        self.host.command('modes', item={'repeat': 'all',
                                         'shuffled': True})
        state = self.guest.poll(0, False, '')
        self.assertEqual(state['item']['repeat'], 'all')
        self.assertTrue(state['item']['shuffled'])
        # playback anchor untouched: no position jump for the party
        self.assertEqual(state['position'], before['position'])
        self.assertEqual(state['set_at'], before['set_at'])
        self.assertGreater(state['seq'], before['seq'])
        # shuffle off again removes the flag
        self.host.command('modes', item={'shuffled': False})
        state = self.guest.poll(0, False, '')
        self.assertNotIn('shuffled', state['item'])

    def test_modes_can_carry_a_reshuffled_queue(self):
        entries = [{'file': f'smb://nas/t{i}.mp3', 'label': f't{i}'}
                   for i in range(5)]
        item = dict(ITEM, type='song', playlist=entries, playlist_pos=0)
        self.host.command('open', position=0.0, item=item)
        reshuffled = list(reversed(entries))
        self.host.command('modes', item={'shuffled': True,
                                         'playlist': reshuffled,
                                         'playlist_pos': 4})
        state = self.guest.poll(0, False, '')
        stored = state['item']['playlist']
        self.assertEqual(stored[0]['file'], 'smb://nas/t4.mp3')
        self.assertEqual(state['item']['playlist_pos'], 4)

    def test_modes_without_item_rejected(self):
        self.host.command('open', position=0.0, item=ITEM)
        self.host.command('stop')
        with self.assertRaises(RelayError):
            self.host.command('modes', item={'repeat': 'all'})

    def test_repeat_and_shuffle_passthrough(self):
        item = dict(ITEM, repeat='one', shuffled=True)
        self.host.command('open', position=0.0, item=item)
        state = self.guest.poll(0, False, '')
        self.assertEqual(state['item']['repeat'], 'one')
        self.assertTrue(state['item']['shuffled'])
        # 'off' passes through too (guests reconcile back to off)
        self.host.command('open', position=0.0,
                          item=dict(ITEM, repeat='off'))
        state = self.guest.poll(0, False, '')
        self.assertEqual(state['item']['repeat'], 'off')
        self.assertNotIn('shuffled', state['item'])

    def test_single_entry_playlist_dropped(self):
        item = dict(ITEM, playlist=[{'file': 'smb://nas/x.mkv',
                                     'label': 'x'}], playlist_pos=0)
        self.host.command('open', position=0.0, item=item)
        state = self.guest.poll(0, False, '')
        self.assertNotIn('playlist', state['item'])

    def test_join_reports_protocol_version(self):
        import relay as relay_mod
        self.assertEqual(self.host.relay_protocol,
                         relay_mod.PROTOCOL_VERSION)

    def test_state_roundtrip(self):
        from relay import RoomState
        self.host.command('open', position=42.0, item=ITEM, lock=True)
        room = self.server.room
        restored = RoomState.from_state(room.state_dict())
        self.assertEqual(restored.item['ids']['imdb'], 'tt1375666')
        self.assertEqual(restored.position, 42.0)
        self.assertEqual(restored.room_code, 'TEST')
        # member-bound state must not survive a restart
        self.assertIsNone(restored.locked_by)
        self.assertIsNone(restored.buffer_hold)
        self.assertEqual(len(restored.members), 0)

    def test_art_upload_and_serving(self):
        png = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
        self.host.command('open', position=0.0, item=ITEM)
        path = self.host.upload_art('image/png', png)
        self.assertTrue(path.startswith('/art/'))
        # the opaque id must not be derived from the room code
        self.assertNotIn('TEST', path)
        base = f'http://127.0.0.1:{self.port}'
        with urllib.request.urlopen(base + path) as resp:
            self.assertEqual(resp.read(), png)
            self.assertEqual(resp.headers.get('Content-Type'), 'image/png')
        # and the dashboard is pointed at it
        with urllib.request.urlopen(base + '/status.json') as resp:
            room = json.load(resp)['rooms'][0]
        self.assertEqual(room['now']['art_url'], path)

    def test_art_rejects_non_images_and_unknown_rooms(self):
        with self.assertRaises(RelayError):
            self.host.upload_art('text/html', b'<script>alert(1)</script>')
        stranger = RelayClient('127.0.0.1', self.port, 'WRONG')
        stranger.member_id = 'nobody'
        with self.assertRaises(RelayError):
            stranger.upload_art('image/png', b'\x89PNG\r\n\x1a\n')

    def test_unknown_art_id_is_404(self):
        url = f'http://127.0.0.1:{self.port}/art/deadbeef'
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url)
        self.assertEqual(ctx.exception.code, 404)
    def test_status_json_never_publishes_credentials(self):
        # exactly what a Plex/PKC party puts on the wire
        token = 'X-Plex-Token=f2GG45UKwr4yEKJc3Q8u'
        stream = ('https://192-168-1-28.plex.direct:32400/library/parts/'
                  '9906/1661732320/file.m4a?' + token)
        art = ('https://192-168-1-28.plex.direct:32400/photo/:/transcode'
               '?width=1920&height=1920&url=/library/metadata/5248/thumb'
               '&' + token)
        self.host.command('open', position=0.0,
                          item={'file': stream, 'label': '', 'plugin': '',
                                'type': 'song', 'title': 'Whispers',
                                'art': art})
        self.guest.poll(1.0, False, stream, on_item=True)
        url = f'http://127.0.0.1:{self.port}/status.json'
        with urllib.request.urlopen(url) as resp:
            body = resp.read().decode()
        self.assertNotIn('X-Plex-Token', body)
        self.assertNotIn('f2GG45UKwr4yEKJc3Q8u', body)
        room = json.loads(body)['rooms'][0]
        # the rest of the URL survives, only the credential is dropped
        self.assertIn('/library/parts/9906/', room['item'])
        self.assertIn('width=1920', room['now']['art'])
        guest = [m for m in room['members'] if m['name'] == 'guest'][0]
        self.assertIn('/library/parts/9906/', guest['file'])

    def test_redaction_leaves_clean_urls_alone(self):
        from relay import redact_url
        plain = 'https://img.youtube.com/vi/abc/hqdefault.jpg'
        self.assertEqual(redact_url(plain), plain)
        self.assertEqual(redact_url('smb://nas/movies/x.mkv'),
                         'smb://nas/movies/x.mkv')
        self.assertEqual(redact_url('plugin://plugin.video.x/?id=7'),
                         'plugin://plugin.video.x/?id=7')
        self.assertEqual(redact_url(''), '')
        self.assertEqual(
            redact_url('https://s.example/a.jpg?api_key=k&w=5'),
            'https://s.example/a.jpg?w=5')

    def test_status_json_exposes_dashboard_fields(self):
        rich = dict(ITEM, duration=612.0, artist=['Ed Sheeran'],
                    album='=', repeat='all')
        self.host.command('open', position=30.0, item=rich, lock=True)
        self.guest.poll(24.0, False, 'x', caching=True, on_item=True,
                        corrections=2)
        url = f'http://127.0.0.1:{self.port}/status.json'
        with urllib.request.urlopen(url) as resp:
            data = json.load(resp)
        self.assertEqual(data['protocol'], 3)
        self.assertGreaterEqual(data['uptime'], 0)
        room = data['rooms'][0]
        # item detail for the now-playing block
        self.assertEqual(room['now']['title'], 'Inception')
        self.assertEqual(room['now']['artist'], ['Ed Sheeran'])
        self.assertEqual(room['now']['repeat'], 'all')
        self.assertEqual(room['duration'], 612.0)
        self.assertIsNotNone(room['seq'])
        # flat label kept for older clients
        self.assertEqual(room['item'], 'Inception')
        # names resolved, ids never leaked
        self.assertEqual(room['locked_by_name'], 'host')
        self.assertEqual(room['buffer_hold_name'], 'guest')
        self.assertNotIn('locked_by', room)
        self.assertEqual(room['corrections'], 2)
        self.assertGreaterEqual(room['commands_per_min'], 1)
        guest = [m for m in room['members'] if m['name'] == 'guest'][0]
        self.assertTrue(guest['caching'])
        self.assertTrue(guest['on_item'])
        self.assertIsNotNone(guest['drift'])
        self.assertLess(guest['age'], 5)
        host = [m for m in room['members'] if m['name'] == 'host'][0]
        self.assertTrue(host['is_host'])

    def test_drift_is_none_off_item(self):
        self.host.command('open', position=0.0, item=ITEM)
        self.guest.poll(0.0, False, '', on_item=False)
        url = f'http://127.0.0.1:{self.port}/status.json'
        with urllib.request.urlopen(url) as resp:
            room = json.load(resp)['rooms'][0]
        guest = [m for m in room['members'] if m['name'] == 'guest'][0]
        self.assertIsNone(guest['drift'])

    def test_root_serves_the_dashboard(self):
        base = f'http://127.0.0.1:{self.port}'
        with urllib.request.urlopen(base + '/') as resp:
            body = resp.read().decode()
            self.assertIn('text/html', resp.headers.get('Content-Type'))
        self.assertIn('Watch Party relay', body)
        with urllib.request.urlopen(base + '/status') as resp:
            self.assertEqual(body, resp.read().decode())

    def test_embedded_dashboard(self):
        # the embedded (in-Kodi) relay serves the same dashboard as the
        # standalone one, scoped to its single room, code masked
        base = f'http://127.0.0.1:{self.port}'
        with urllib.request.urlopen(f'{base}/status.json') as resp:
            data = json.load(resp)
        self.assertEqual([r['room'] for r in data['rooms']], ['T··T'])
        names = sorted(m['name'] for m in data['rooms'][0]['members'])
        self.assertEqual(names, ['guest', 'host'])
        with urllib.request.urlopen(f'{base}/status') as resp:
            self.assertIn('Watch Party relay', resp.read().decode())

    def test_lock_lifecycle(self):
        self.host.command('open', position=0.0, item=ITEM, lock=True)
        state = self.guest.poll(0, False, '')
        self.assertEqual(state['locked_by'], self.host.member_id)
        with self.assertRaises(RelayError) as ctx:
            self.guest.command('pause', position=5.0)
        self.assertIn('locked', str(ctx.exception))
        self.host.command('pause', position=5.0)  # controller still can
        self.host.command('stop')                 # stop releases the lock
        state = self.guest.poll(0, False, '')
        self.assertIsNone(state['locked_by'])
        self.guest.command('open', position=0.0, item=ITEM)  # free again


class StandaloneRelayTest(unittest.TestCase):
    PORT = 28765

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, STANDALONE, '--port', str(cls.PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = RelayClient('127.0.0.1', cls.PORT, 'PING')
        for _ in range(50):
            try:
                client.ping()
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError('standalone relay did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def test_open_mode_url_base_and_code_normalization(self):
        a = RelayClient(f'http://127.0.0.1:{self.PORT}', 0, 'MOVIE')
        a.join('device-a')
        b = RelayClient('127.0.0.1', self.PORT, 'movie')  # lowercase
        b.join('device-b')
        state = b.poll(0, False, '')
        self.assertEqual(len(state['members']), 2)

    def test_room_isolation(self):
        a = RelayClient('127.0.0.1', self.PORT, 'ROOMA')
        a.join('a')
        a.command('open', position=0.0, item=ITEM)
        b = RelayClient('127.0.0.1', self.PORT, 'ROOMB')
        b.join('b')
        state = b.poll(0, False, '')
        self.assertIsNone(state['item'])
        self.assertEqual(len(state['members']), 1)

    def test_junk_code_rejected_with_reason(self):
        bad = RelayClient('127.0.0.1', self.PORT, 'x' * 40)
        with self.assertRaises(RelayError) as ctx:
            bad.join('bad')
        self.assertIn('letters or digits', str(ctx.exception))

    def test_status_json_masks_codes(self):
        c = RelayClient('127.0.0.1', self.PORT, 'DASH')
        c.join('living-room')
        url = f'http://127.0.0.1:{self.PORT}/status.json'
        with urllib.request.urlopen(url) as resp:
            data = json.load(resp)
        rooms = {r['room'] for r in data['rooms']}
        self.assertIn('D··H', rooms)
        self.assertNotIn('DASH', rooms)

    def test_status_page_serves(self):
        url = f'http://127.0.0.1:{self.PORT}/status'
        with urllib.request.urlopen(url) as resp:
            html = resp.read().decode()
        self.assertIn('Watch Party relay', html)


class PersistenceTest(unittest.TestCase):
    PORT = 28767

    def _start(self, state_file):
        proc = subprocess.Popen(
            [sys.executable, STANDALONE, '--port', str(self.PORT),
             '--state-file', state_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = RelayClient('127.0.0.1', self.PORT, 'PING')
        for _ in range(50):
            try:
                client.ping()
                return proc
            except Exception:
                time.sleep(0.2)
        proc.terminate()
        raise RuntimeError('standalone relay did not start')

    def test_party_survives_relay_restart(self):
        import tempfile
        state_file = os.path.join(tempfile.mkdtemp(), 'state.json')
        proc = self._start(state_file)
        try:
            c = RelayClient('127.0.0.1', self.PORT, 'MOVIE')
            c.join('host')
            c.command('open', position=90.0, item=ITEM)
        finally:
            proc.terminate()       # SIGTERM — clean shutdown saves state
            proc.wait(timeout=5)

        proc = self._start(state_file)
        try:
            c = RelayClient('127.0.0.1', self.PORT, 'MOVIE')
            c.join('host-again')
            state = c.poll(0, False, '')
            self.assertIsNotNone(state['item'])
            self.assertEqual(state['item']['title'], 'Inception')
            self.assertEqual(state['item']['ids']['imdb'], 'tt1375666')
            self.assertGreaterEqual(state['position'], 90.0)
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class FixedModeTest(unittest.TestCase):
    PORT = 28766

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, STANDALONE, '--port', str(cls.PORT),
             '--room', 'ABCD'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = RelayClient('127.0.0.1', cls.PORT, 'ABCD')
        for _ in range(50):
            try:
                client.ping()
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError('standalone relay did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def test_fixed_room_joinable(self):
        c = RelayClient('127.0.0.1', self.PORT, 'ABCD')
        c.join('ok')

    def test_unknown_room_rejected(self):
        c = RelayClient('127.0.0.1', self.PORT, 'ZZZZ')
        with self.assertRaises(RelayError) as ctx:
            c.join('nope')
        self.assertIn('fixed room codes', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
