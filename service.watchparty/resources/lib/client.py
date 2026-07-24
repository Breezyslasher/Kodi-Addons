"""
Watch Party relay client.

Thin JSON-over-HTTP wrapper used by the sync engine to talk to a relay
(the hosting Kodi's embedded server). Also maintains an estimate of the
clock offset between this device and the relay, measured on every request
using the request round-trip midpoint, and smoothed with an EMA so all
party members can agree on "server time" positions.

Pure standard library — no Kodi imports.
"""
import json
import time
import urllib.request

# Kodi's Python would otherwise send "Python-urllib/3.x", which CDN bot
# protection (e.g. Cloudflare Bot Fight Mode) blocks with a 403 before
# the request ever reaches the relay.
USER_AGENT = 'WatchParty/1.0 (Kodi addon)'

# Protocol version this client speaks; must match relay.PROTOCOL_VERSION.
PROTOCOL_VERSION = 2


class RelayError(Exception):
    pass


class RelayClient:
    def __init__(self, host, port, room_code, timeout=5.0):
        # `host` may be a plain hostname/IP (paired with `port`) or a full
        # base URL like https://party.example.com[/path] — the latter is
        # how guests reach a standalone relay behind TLS or a tunnel.
        if host.startswith(('http://', 'https://')):
            self.base = host.rstrip('/')
        else:
            self.base = f"http://{host}:{port}"
        self.room = room_code
        self.timeout = timeout
        self.member_id = None
        self.relay_protocol = 1   # learned from /join; pre-v2 relays omit it
        self.clock_offset = 0.0   # server_time - local_time (EMA-smoothed)
        self._offset_samples = 0

    # -- transport ---------------------------------------------------------

    def _post(self, path, payload):
        payload = dict(payload)
        payload['room'] = self.room
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self.base + path, data=body,
            headers={'Content-Type': 'application/json',
                     'User-Agent': USER_AGENT}, method='POST')
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode('utf-8'))
            except Exception:
                # Not relay JSON: a proxy/CDN/WAF in front answered, not
                # the relay itself. Say so — it changes where to look.
                raise RelayError(
                    f"HTTP {e.code} from proxy/CDN, not the relay "
                    f"(bot protection or wrong URL?)")
            raise RelayError(data.get('error') or f"HTTP {e.code}")
        except Exception as e:
            raise RelayError(str(e))
        t1 = time.time()
        self._update_offset(data.get('server_time'), t0, t1)
        if not data.get('ok'):
            raise RelayError(data.get('error') or 'relay error')
        return data

    def _update_offset(self, server_time, t0, t1):
        if not server_time:
            return
        # Assume the server stamped the response halfway through the RTT.
        sample = float(server_time) - (t0 + t1) / 2.0
        if self._offset_samples == 0:
            self.clock_offset = sample
        else:
            self.clock_offset = 0.8 * self.clock_offset + 0.2 * sample
        self._offset_samples += 1

    def server_now(self):
        return time.time() + self.clock_offset

    # -- API ---------------------------------------------------------------

    def ping(self):
        req = urllib.request.Request(self.base + '/ping',
                                     headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data.get('app') == 'watchparty'

    def join(self, name):
        data = self._post('/join', {'name': name})
        self.member_id = data['member_id']
        self.relay_protocol = int(data.get('protocol') or 1)
        return data['state']

    def leave(self):
        if self.member_id:
            try:
                self._post('/leave', {'member_id': self.member_id})
            except RelayError:
                pass
            self.member_id = None

    def command(self, cmd, position=0.0, item=None, lock=False):
        payload = {'member_id': self.member_id, 'cmd': cmd,
                   'position': position}
        if item:
            payload['item'] = item
        if lock:
            payload['lock'] = True
        return self._post('/command', payload)['state']

    def poll(self, position, paused, file, caching=False, on_item=False):
        data = self._post('/poll', {'member_id': self.member_id,
                                    'position': position, 'paused': paused,
                                    'file': file, 'caching': caching,
                                    'on_item': on_item})
        return data['state']
