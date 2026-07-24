"""Relay protocol tests — embedded server and standalone multi-room mode.
Pure stdlib; runs anywhere: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import time
import unittest
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

    def test_playlist_passthrough_and_cap(self):
        entries = [{'file': f'smb://nas/music/t{i}.mp3', 'label': f't{i}'}
                   for i in range(120)]
        item = dict(ITEM, type='song', playlist=entries, playlist_pos=3)
        self.host.command('open', position=0.0, item=item)
        state = self.guest.poll(0, False, '')
        stored = state['item']['playlist']
        self.assertEqual(len(stored), 100)          # capped
        self.assertEqual(stored[0]['file'], 'smb://nas/music/t0.mp3')
        self.assertEqual(state['item']['playlist_pos'], 3)

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
