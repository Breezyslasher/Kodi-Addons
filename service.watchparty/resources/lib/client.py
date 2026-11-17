"""
Watch Party relay client.

Thin JSON-over-HTTP wrapper used by the sync engine to talk to a relay
(the hosting Kodi's embedded server). Also maintains an estimate of the
clock offset between this device and the relay, measured on every request
using the request round-trip midpoint, and smoothed with an EMA so all
party members can agree on "server time" positions.

Pure standard library — no Kodi imports.
"""
import gzip
import http.client
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Kodi's Python would otherwise send "Python-urllib/3.x", which CDN bot
# protection (e.g. Cloudflare Bot Fight Mode) blocks with a 403 before
# the request ever reaches the relay.
USER_AGENT = 'WatchParty/1.0 (Kodi addon)'

# Protocol version this client speaks; must match relay.PROTOCOL_VERSION.
PROTOCOL_VERSION = 3


class RelayError(Exception):
    pass


# A kept-alive connection the relay (or a proxy in front of it) closed while
# it sat idle shows up as one of these on the next request. Nothing reached
# the relay, so that request is sent again once on a new connection.
_STALE = (http.client.RemoteDisconnected, BrokenPipeError,
          ConnectionResetError, ConnectionAbortedError)


def _decoded(headers, data):
    """The body as sent, before any gzip."""
    if (headers.get('Content-Encoding') or '').strip().lower() == 'gzip':
        return gzip.decompress(data)
    return data


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
        # One kept-alive connection per thread (the engine polls on one
        # thread and pushes commands and art on another), instead of a new
        # one for every request: over HTTPS each new connection repeats the
        # TLS handshake (~4-6 KB), which once a second was most of what
        # Kodi sent and received. With an HTTP(S) proxy set in the
        # environment for this host (not excluded by no_proxy), requests
        # go through urllib as before: it honours the proxy, http.client
        # doesn't.
        url = urllib.parse.urlsplit(self.base)
        self._scheme = url.scheme
        self._netloc = url.netloc
        self._prefix = url.path.rstrip('/')
        self._use_urllib = bool(
            urllib.request.getproxies().get(url.scheme)
            and not urllib.request.proxy_bypass(url.hostname or ''))
        self._local = threading.local()

    # -- transport ---------------------------------------------------------

    def _connection(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            if self._scheme == 'https':
                conn = http.client.HTTPSConnection(
                    self._netloc, timeout=self.timeout,
                    context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(self._netloc,
                                                  timeout=self.timeout)
            self._local.conn = conn
            self._local.used = False
        return conn

    def close(self):
        """Close this thread's connection (the next request opens one)."""
        conn = getattr(self._local, 'conn', None)
        self._local.conn = None
        if conn is not None:
            conn.close()

    def _request(self, method, path, body=None, headers=None):
        """(status, body bytes) for one request on this thread's kept-alive
        connection. Network errors raise OSError / HTTPException."""
        headers = dict(headers or {})
        headers.setdefault('User-Agent', USER_AGENT)
        # the relay gzips its replies for clients that ask: a poll's room
        # state is ~1.2-2.3 KB of JSON and ~0.5 KB gzipped
        headers.setdefault('Accept-Encoding', 'gzip')
        if self._use_urllib:
            req = urllib.request.Request(self.base + path, data=body,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.status, _decoded(r.headers, r.read())
            except urllib.error.HTTPError as e:
                return e.code, _decoded(e.headers, e.read())
        while True:
            conn = self._connection()
            reused = self._local.used
            try:
                conn.request(method, self._prefix + path, body=body,
                             headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except _STALE:
                self.close()
                if reused:
                    continue    # idle connection was closed; once more
                raise
            except Exception:
                self.close()
                raise
            self._local.used = True
            if resp.will_close:
                self.close()
            return resp.status, _decoded(resp.headers, data)

    def _post(self, path, payload):
        payload = dict(payload)
        payload['room'] = self.room
        body = json.dumps(payload).encode('utf-8')
        t0 = time.time()
        try:
            status, raw = self._request(
                'POST', path, body, {'Content-Type': 'application/json'})
        except Exception as e:
            raise RelayError(str(e))
        t1 = time.time()
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception as e:
            if status >= 400:
                # Not relay JSON: a proxy/CDN/WAF in front answered, not
                # the relay itself. Say so — it changes where to look.
                raise RelayError(
                    f"HTTP {status} from proxy/CDN, not the relay "
                    f"(bot protection or wrong URL?)")
            raise RelayError(str(e))
        if status >= 400:
            raise RelayError(data.get('error') or f"HTTP {status}")
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

    def upload_art(self, content_type, data):
        """Hand cover art bytes to the relay; returns its path or ''."""
        try:
            status, raw = self._request(
                'POST', '/art', data,
                {'Content-Type': content_type, 'X-Room': self.room,
                 'X-Member': self.member_id or ''})
            payload = json.loads(raw.decode('utf-8'))
        except Exception as e:
            raise RelayError(str(e))
        if not payload.get('ok'):
            raise RelayError(payload.get('error') or 'art rejected')
        return payload.get('art') or ''

    def ping(self):
        status, raw = self._request('GET', '/ping')
        if status >= 400:
            raise IOError(f"HTTP {status}")
        return json.loads(raw.decode('utf-8')).get('app') == 'watchparty'

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
            self.close()

    def command(self, cmd, position=0.0, item=None, lock=False):
        payload = {'member_id': self.member_id, 'cmd': cmd,
                   'position': position}
        if item:
            payload['item'] = item
        if lock:
            payload['lock'] = True
        return self._post('/command', payload)['state']

    def poll(self, position, paused, file, caching=False, on_item=False,
             corrections=0):
        data = self._post('/poll', {'member_id': self.member_id,
                                    'position': position, 'paused': paused,
                                    'file': file, 'caching': caching,
                                    'on_item': on_item,
                                    'corrections': corrections})
        return data['state']
