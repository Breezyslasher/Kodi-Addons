"""The relay this addon hosts inside Kodi (resources/lib/relay.py) and its
remote page (resources/remote/) work in the addon's layout.

Both are copies of WatchParty's watchparty-relay/ (tools/vendor-kodi.sh),
where the relay's full tests live; this checks the copy runs here."""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ADDON_DIR, 'resources', 'lib'))

from client import RelayClient, RelayError  # noqa: E402
from relay import RelayServer  # noqa: E402

ITEM = {'file': 'smb://nas/movies/Inception (2010).mkv', 'label': 'Inception',
        'type': 'movie', 'title': 'Inception', 'year': 2010}


class EmbeddedRelayTest(unittest.TestCase):
    def setUp(self):
        self.server = RelayServer(port=0, room_code='TEST')
        self.port = self.server._httpd.server_address[1]
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def get(self, path):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (self.port, path)) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_a_party_runs_on_it(self):
        host = RelayClient('127.0.0.1', self.port, 'TEST')
        host.join('host')
        guest = RelayClient('127.0.0.1', self.port, 'TEST')
        guest.join('guest')
        host.command('open', position=12.0, item=ITEM)
        state = guest.poll(0, False, '')
        self.assertEqual(state['item']['title'], 'Inception')
        self.assertEqual(sorted(m['name'] for m in state['members']), ['guest', 'host'])

    def test_it_serves_the_remote_page(self):
        status, body = self.get('/remote')
        self.assertEqual(status, 200)
        self.assertIn(b'/remote/remote.js', body)
        for path in ('/remote/remote.js', '/remote/screens.js', '/remote/justwatch.js',
                     '/remote/room.css', '/remote/fonts/Figtree-Regular.ttf', '/icon.png'):
            self.assertEqual(self.get(path)[0], 200, path)

    def test_the_copies_are_all_there(self):
        remote = os.path.join(ADDON_DIR, 'resources', 'remote')
        for name in ('remote.html', 'remote.js', 'remote.css', 'links.js', 'justwatch.js', 'hbomax.js',
                     'relay.js', 'ui.js', 'screens.js', 'room.css', 'VENDORED.txt'):
            self.assertTrue(os.path.isfile(os.path.join(remote, name)), name)
        with open(os.path.join(ADDON_DIR, 'resources', 'lib', 'relay.py'), encoding='utf-8') as f:
            self.assertIn('tools/vendor-kodi.sh', f.read())


class KeptAliveTest(unittest.TestCase):
    """The client keeps one connection per thread instead of one per
    request (over HTTPS each new one is a TLS handshake)."""

    def test_polls_share_one_connection(self):
        server = RelayServer(port=0, room_code='TEST')
        server.start()
        try:
            c = RelayClient('127.0.0.1', server._httpd.server_address[1], 'TEST')
            c.join('kodi')
            conn = c._local.conn
            for t in range(5):
                c.poll(float(t), False, '')
            self.assertIs(c._local.conn, conn)
            self.assertIsNotNone(conn.sock)
        finally:
            c.close()
            server.stop()

    def test_it_reconnects_after_the_relay_restarts(self):
        server = RelayServer(port=0, room_code='TEST')
        port = server._httpd.server_address[1]
        server.start()
        c = RelayClient('127.0.0.1', port, 'TEST')
        c.join('kodi')
        c.poll(0.0, False, '')
        server.stop()
        # the stopped relay no longer answers on the old connection
        with self.assertRaises(RelayError):
            c.poll(1.0, False, '')
        server = RelayServer(port=port, room_code='TEST')
        server.start()
        try:
            # a new relay: a new connection, and it doesn't know us yet
            with self.assertRaisesRegex(RelayError, 'not a member'):
                c.poll(2.0, False, '')
            c.join('kodi')
            self.assertIn('item', c.poll(3.0, False, ''))
        finally:
            c.close()
            server.stop()

    def test_a_proxy_in_the_environment_goes_through_urllib(self):
        old = os.environ.get('HTTPS_PROXY'), os.environ.get('NO_PROXY'), os.environ.get('no_proxy')
        os.environ['HTTPS_PROXY'] = 'http://proxy.invalid:3128'
        os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost'
        try:
            self.assertTrue(RelayClient('https://relay.example.com', 0, 'TEST')._use_urllib)
            self.assertFalse(RelayClient('https://localhost:8765', 0, 'TEST')._use_urllib)
        finally:
            for key, value in zip(('HTTPS_PROXY', 'NO_PROXY', 'no_proxy'), old):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == '__main__':
    unittest.main()
