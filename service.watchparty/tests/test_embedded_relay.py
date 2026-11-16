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

from client import RelayClient  # noqa: E402
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


if __name__ == '__main__':
    unittest.main()
