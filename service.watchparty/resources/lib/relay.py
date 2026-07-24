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

# Bumped whenever the protocol gains features. 1 = original release,
# 2 = buffer hold, control lock, library-id item fields.
PROTOCOL_VERSION = 2


def mask_code(code):
    """Obscure a room code — codes are the access tokens."""
    if len(code) <= 2:
        return '·' * len(code)
    return code[0] + '·' * (len(code) - 2) + code[-1]


def rooms_stats(rooms, show_codes=False):
    """Dashboard snapshot for [(code, RoomState), ...]: members, item,
    live position."""
    now = time.time()
    out = []
    for code, room in sorted(rooms, key=lambda pair: pair[0]):
        snap = room.snapshot()
        item = snap.get('item') or {}
        position = float(snap.get('position') or 0.0)
        if item and not snap.get('paused'):
            position += max(0.0, now - float(snap.get('set_at') or now)) \
                * float(snap.get('speed') or 1.0)
        out.append({
            'room': code if show_codes else mask_code(code),
            'members': [
                {'name': m.get('name'), 'position': m.get('position'),
                 'paused': m.get('paused')}
                for m in snap.get('members') or []
            ],
            'item': item.get('label') or item.get('plugin')
                    or item.get('file') or None,
            'paused': bool(snap.get('paused')),
            'position': position,
        })
    return out


DASH_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Watch Party relay</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;background:#14161a;color:#e8e8e8;
      margin:0;padding:1.5rem}
 h1{font-size:1.2rem;margin:0 0 .25rem}
 .sub{color:#8a919c;font-size:.85rem;margin-bottom:1.25rem}
 .room{background:#1d2026;border-radius:10px;padding:1rem 1.25rem;
       margin-bottom:1rem;max-width:44rem}
 .code{font-weight:700;letter-spacing:.12em}
 .item{color:#9ecbff;margin:.35rem 0;word-break:break-all}
 .state{font-size:.85rem;color:#8a919c}
 .paused{color:#ffb86b}.playing{color:#7ee08a}
 ul{margin:.5rem 0 0;padding-left:1.25rem}
 li{margin:.15rem 0}
 .pos{color:#8a919c;font-size:.85rem}
 .empty{color:#8a919c;font-style:italic}
</style></head><body>
<h1>Watch Party relay</h1>
<div class="sub" id="sub">loading…</div>
<div id="rooms"></div>
<script>
function fmt(s){s=Math.max(0,Math.floor(s||0));
 const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
 return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(x).padStart(2,'0');}
function esc(t){const d=document.createElement('div');
 d.textContent=t==null?'':String(t);return d.innerHTML;}
async function tick(){
 try{
  const r=await fetch('/status.json'); const d=await r.json();
  document.getElementById('sub').textContent=
   d.rooms.length+' room(s) — updated '+new Date().toLocaleTimeString();
  const el=document.getElementById('rooms');
  if(!d.rooms.length){el.innerHTML='<div class="empty">No active rooms</div>';return;}
  el.innerHTML=d.rooms.map(room=>{
   const st=room.item?(room.paused?'<span class="paused">paused</span>'
     :'<span class="playing">playing</span>')+' at '+fmt(room.position):'idle';
   const mem=room.members.length?('<ul>'+room.members.map(m=>
     '<li>'+esc(m.name)+' <span class="pos">'+fmt(m.position)+
     (m.paused?' (paused)':'')+'</span></li>').join('')+'</ul>')
     :'<div class="empty">nobody connected</div>';
   return '<div class="room"><span class="code">'+esc(room.room)+'</span> '+
    '<span class="state">'+st+'</span>'+
    (room.item?'<div class="item">'+esc(room.item)+'</div>':'')+mem+'</div>';
  }).join('');
 }catch(e){document.getElementById('sub').textContent='relay unreachable';}
}
tick();setInterval(tick,2000);
</script></body></html>
"""


class RoomState:
    """Thread-safe state for a single party."""

    def __init__(self, room_code):
        self.lock = threading.Lock()
        self.room_code = room_code
        self.members = {}   # member_id -> dict
        self.seq = 0
        self.item = None    # {'file', 'label', 'plugin', + library ids} or None
        self.position = 0.0
        self.paused = False
        self.speed = 1.0
        self.set_at = time.time()
        self.set_by = None
        self.locked_by = None    # member allowed to control, or None = anyone
        self.buffer_hold = None  # member we auto-paused for, or None

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
                'caching': False,
                'on_item': False,
            }
        return member_id

    def leave(self, member_id):
        with self.lock:
            self.members.pop(member_id, None)

    def touch(self, member_id, position=None, paused=None, file=None,
              caching=None, on_item=None):
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
            if caching is not None:
                m['caching'] = bool(caching)
            if on_item is not None:
                m['on_item'] = bool(on_item)
            return True

    def _prune(self):
        # caller holds lock
        cutoff = time.time() - MEMBER_TIMEOUT
        stale = [mid for mid, m in self.members.items()
                 if m['last_seen'] < cutoff]
        for mid in stale:
            del self.members[mid]
        if self.locked_by and self.locked_by not in self.members:
            self.locked_by = None
        if self.buffer_hold and self.buffer_hold not in self.members:
            self.buffer_hold = None

    def buffer_check(self):
        """Pause the party while a member playing the item is buffering,
        and resume once nobody is. A manual command in between cancels
        the hold, so a deliberate pause is never overridden."""
        with self.lock:
            if not self.item:
                self.buffer_hold = None
                return
            now = time.time()
            caching = [mid for mid, m in self.members.items()
                       if m.get('caching') and m.get('on_item')]
            if caching and not self.paused:
                self.position += max(0.0, now - self.set_at) \
                    * (self.speed or 1.0)
                self.paused = True
                self.set_at = now
                self.set_by = caching[0]
                self.buffer_hold = caching[0]
                self.seq += 1
            elif self.buffer_hold and self.paused and not caching:
                self.paused = False
                self.set_at = now
                self.set_by = self.buffer_hold
                self.buffer_hold = None
                self.seq += 1
            elif self.buffer_hold and not self.paused:
                self.buffer_hold = None

    # -- playback ----------------------------------------------------------

    # keys an 'open' command may attach beyond file/label/plugin —
    # library identity so guests can match their own copy
    ITEM_EXTRA_KEYS = ('type', 'ids', 'title', 'year',
                       'show', 'season', 'episode', 'artist', 'album')

    def command(self, member_id, cmd, payload):
        """Apply a control command and bump the sequence number.
        Returns True, False (bad command), or 'locked'."""
        now = time.time()
        with self.lock:
            if member_id not in self.members:
                return False
            if self.locked_by and member_id != self.locked_by:
                return 'locked'
            position = float(payload.get('position') or 0.0)
            if cmd == 'open':
                item = payload.get('item') or {}
                if not item.get('file'):
                    return False
                stored = {'file': str(item['file']),
                          'label': str(item.get('label') or ''),
                          'plugin': str(item.get('plugin') or '')}
                for key in self.ITEM_EXTRA_KEYS:
                    value = item.get(key)
                    if value not in (None, '', {}):
                        stored[key] = value
                self.item = stored
                self.position = position
                self.paused = False
                self.speed = 1.0
                # opener may claim sole control of the party
                self.locked_by = member_id if payload.get('lock') else None
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
                self.locked_by = None
            else:
                return False
            self.buffer_hold = None  # a deliberate command wins over a hold
            self.set_at = now
            self.set_by = member_id
            self.seq += 1
            return True

    # -- persistence (used by the standalone relay) ------------------------

    def state_dict(self):
        """Serializable playback state. Members are deliberately not
        included — they re-poll and auto-rejoin within seconds."""
        with self.lock:
            return {'code': self.room_code,
                    'item': dict(self.item) if self.item else None,
                    'position': self.position,
                    'paused': self.paused,
                    'speed': self.speed,
                    'set_at': self.set_at,
                    'seq': self.seq}

    @classmethod
    def from_state(cls, data):
        room = cls(str(data.get('code') or ''))
        item = data.get('item')
        room.item = dict(item) if item else None
        room.position = float(data.get('position') or 0.0)
        room.paused = bool(data.get('paused'))
        room.speed = float(data.get('speed') or 1.0)
        room.set_at = float(data.get('set_at') or time.time())
        room.seq = int(data.get('seq') or 0)
        # locks and holds reference member ids that no longer exist
        room.locked_by = None
        room.buffer_hold = None
        return room

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
                'locked_by': self.locked_by,
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
    show_codes = False  # full room codes on /status (they're the tokens)

    def log_message(self, fmt, *args):  # silence per-request stderr noise
        pass

    def iter_rooms(self):
        """[(code, RoomState), ...] for the dashboard. The embedded relay
        has one room; the standalone registry overrides this."""
        room = self.room
        return [(room.room_code, room)] if room is not None else []

    def lookup_room(self, code):
        """Map a room code to a RoomState, or an error string to reject.

        The embedded (in-Kodi) relay hosts exactly one room; the
        standalone relay overrides this with a multi-room registry.
        """
        room = self.room
        if room is not None and code == room.room_code:
            return room
        return 'wrong room code'

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
        path = self.path.split('?', 1)[0]
        if path == '/ping':
            self._send(200, {'ok': True, 'app': 'watchparty',
                             'protocol': PROTOCOL_VERSION,
                             'server_time': time.time()})
        elif path == '/status':
            body = DASH_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == '/status.json':
            self._send(200, {'ok': True,
                             'rooms': rooms_stats(self.iter_rooms(),
                                                  self.show_codes),
                             'server_time': time.time()})
        elif path == '/':
            # friendly landing for humans checking the relay in a browser
            self._send(200, {
                'ok': True,
                'app': 'watchparty',
                'message': 'Watch Party relay is running. In the Kodi '
                           'addon, choose "Join a party" and enter this '
                           "server's address plus a room code.",
                'health': '/ping',
                'dashboard': '/status',
                'server_time': time.time(),
            })
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        data = self._read_body()
        if data is None:
            self._send(400, {'ok': False, 'error': 'bad request'})
            return
        room = self.lookup_room(str(data.get('room') or ''))
        if not isinstance(room, RoomState):
            self._send(403, {'ok': False,
                             'error': room or 'wrong room code'})
            return

        now = time.time()
        if self.path == '/join':
            member_id = room.join(str(data.get('name') or ''))
            self._send(200, {'ok': True, 'member_id': member_id,
                             'protocol': PROTOCOL_VERSION,
                             'state': room.snapshot(), 'server_time': now})
            return

        member_id = str(data.get('member_id') or '')
        if self.path == '/leave':
            room.leave(member_id)
            self._send(200, {'ok': True, 'server_time': now})
        elif self.path == '/command':
            result = room.command(member_id, str(data.get('cmd') or ''), data)
            if result == 'locked':
                self._send(403, {'ok': False,
                                 'error': 'controls are locked by the host',
                                 'server_time': now})
                return
            if not result:
                self._send(400, {'ok': False, 'error': 'bad command',
                                 'server_time': now})
                return
            self._send(200, {'ok': True, 'state': room.snapshot(),
                             'server_time': now})
        elif self.path == '/poll':
            if not room.touch(member_id,
                              position=data.get('position'),
                              paused=data.get('paused'),
                              file=data.get('file'),
                              caching=data.get('caching'),
                              on_item=data.get('on_item')):
                self._send(410, {'ok': False, 'error': 'not a member',
                                 'server_time': now})
                return
            room.buffer_check()
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
