"""
Watch Party relay server (host side).

A small threaded HTTP server that keeps the shared playback state for one
party ("room") in memory. Every member — including the hosting Kodi, which
connects to itself over localhost — talks to it with short JSON POSTs:

    POST /join     {room, name}                    -> {ok, member_id, state, server_time}
    POST /leave    {room, member_id}               -> {ok}
    POST /command  {room, member_id, cmd, ...}     -> {ok, state, server_time}
    POST /poll     {room, member_id, position,
                    paused, file}                  -> {ok, state, server_time}
    GET  /ping                                     -> {ok, app, server_time}

The playback state anchors a position to a server timestamp (set_at), so
any member can compute the expected "now" position as
position + (server_now - set_at) * speed while playing. All requests carry
the room code, which doubles as the access token.

Pure standard library — no Kodi imports — so it is unit-testable outside
Kodi.
"""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MEMBER_TIMEOUT = 15.0    # seconds without a poll before a member is pruned
MAX_BODY = 64 * 1024


class RoomState:
    """Thread-safe state for a single party."""

    def __init__(self, room_code):
        self.lock = threading.Lock()
        self.room_code = room_code
        self.members = {}   # member_id -> dict
        self.seq = 0
        self.item = None    # {'file': str, 'label': str, 'plugin': str} or None
        self.position = 0.0
        self.paused = False
        self.speed = 1.0
        self.set_at = time.time()
        self.set_by = None

    # -- members -----------------------------------------------------------

    def join(self, name):
        member_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.members[member_id] = {
                'name': name or 'Kodi',
                'joined': time.time(),
                'last_seen': time.time(),
                'position': 0.0,
                'paused': True,
                'file': '',
            }
        return member_id

    def leave(self, member_id):
        with self.lock:
            self.members.pop(member_id, None)

    def touch(self, member_id, position=None, paused=None, file=None):
        with self.lock:
            m = self.members.get(member_id)
            if m is None:
                return False
            m['last_seen'] = time.time()
            if position is not None:
                m['position'] = float(position)
            if paused is not None:
                m['paused'] = bool(paused)
            if file is not None:
                m['file'] = str(file)
            return True

    def _prune(self):
        # caller holds lock
        cutoff = time.time() - MEMBER_TIMEOUT
        stale = [mid for mid, m in self.members.items()
                 if m['last_seen'] < cutoff]
        for mid in stale:
            del self.members[mid]

    # -- playback ----------------------------------------------------------

    def command(self, member_id, cmd, payload):
        """Apply a control command and bump the sequence number."""
        now = time.time()
        with self.lock:
            if member_id not in self.members:
                return False
            position = float(payload.get('position') or 0.0)
            if cmd == 'open':
                item = payload.get('item') or {}
                if not item.get('file'):
                    return False
                self.item = {'file': str(item['file']),
                             'label': str(item.get('label') or ''),
                             'plugin': str(item.get('plugin') or '')}
                self.position = position
                self.paused = False
                self.speed = 1.0
            elif cmd == 'play':
                self.position = position
                self.paused = False
            elif cmd == 'pause':
                self.position = position
                self.paused = True
            elif cmd == 'seek':
                self.position = position
            elif cmd == 'stop':
                self.item = None
                self.position = 0.0
                self.paused = False
            else:
                return False
            self.set_at = now
            self.set_by = member_id
            self.seq += 1
            return True

    def snapshot(self):
        with self.lock:
            self._prune()
            return {
                'seq': self.seq,
                'item': dict(self.item) if self.item else None,
                'position': self.position,
                'paused': self.paused,
                'speed': self.speed,
                'set_at': self.set_at,
                'set_by': self.set_by,
                'members': [
                    {
                        'name': m['name'],
                        'position': m['position'],
                        'paused': m['paused'],
                        'file': m['file'],
                    }
                    for m in self.members.values()
                ],
            }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'WatchParty/1.0'

    # the RelayServer sets this on the handler class
    room = None

    def log_message(self, fmt, *args):  # silence per-request stderr noise
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return None

    def do_GET(self):
        if self.path == '/ping':
            self._send(200, {'ok': True, 'app': 'watchparty',
                             'server_time': time.time()})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        data = self._read_body()
        if data is None:
            self._send(400, {'ok': False, 'error': 'bad request'})
            return
        room = self.room
        if str(data.get('room') or '') != room.room_code:
            self._send(403, {'ok': False, 'error': 'wrong room code'})
            return

        now = time.time()
        if self.path == '/join':
            member_id = room.join(str(data.get('name') or ''))
            self._send(200, {'ok': True, 'member_id': member_id,
                             'state': room.snapshot(), 'server_time': now})
            return

        member_id = str(data.get('member_id') or '')
        if self.path == '/leave':
            room.leave(member_id)
            self._send(200, {'ok': True, 'server_time': now})
        elif self.path == '/command':
            ok = room.command(member_id, str(data.get('cmd') or ''), data)
            if not ok:
                self._send(400, {'ok': False, 'error': 'bad command',
                                 'server_time': now})
                return
            self._send(200, {'ok': True, 'state': room.snapshot(),
                             'server_time': now})
        elif self.path == '/poll':
            if not room.touch(member_id,
                              position=data.get('position'),
                              paused=data.get('paused'),
                              file=data.get('file')):
                self._send(410, {'ok': False, 'error': 'not a member',
                                 'server_time': now})
                return
            self._send(200, {'ok': True, 'state': room.snapshot(),
                             'server_time': now})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})


class RelayServer:
    """Owns the ThreadingHTTPServer and its serve_forever thread."""

    def __init__(self, port, room_code):
        self.room = RoomState(room_code)
        handler = type('BoundHandler', (_Handler,), {'room': self.room})
        self._httpd = ThreadingHTTPServer(('0.0.0.0', port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name='watchparty-relay', daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
        self._thread.join(timeout=5)
