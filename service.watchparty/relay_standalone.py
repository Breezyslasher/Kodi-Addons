#!/usr/bin/env python3
"""
Watch Party standalone relay.

Run this on any always-on machine (VPS, Raspberry Pi, NAS, container) to
give a Watch Party a neutral meeting point on the internet: every Kodi
device connects *out* to it, so nobody needs port forwarding or a VPN.

    python3 relay_standalone.py                  # open mode on :8765
    python3 relay_standalone.py --room MOVIE     # only room MOVIE allowed
    python3 relay_standalone.py --port 9000 --room A --room B

Modes
  open (default)   Any well-formed room code creates a room on first
                   join, like SyncLounge. Empty rooms are pruned.
  fixed (--room)   Only the listed room codes exist; anything else is
                   rejected. Use for a private, invite-only relay.

Guests join from Kodi with the relay's address (ip:port, or an
https:// URL if you put it behind a reverse proxy / cloudflared tunnel)
plus a room code. The Kodi device that starts playback drives the room —
there is no "host relay" here, the party is wherever the room is.

Pure Python standard library; needs only relay.py from the addon
(resources/lib/relay.py, kept importable next to this file or via the
addon checkout).
"""
import argparse
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'resources', 'lib'))
from relay import RoomState, _Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


ROOM_CODE_RE = re.compile(r'^[A-Z0-9]{3,12}$')
MAX_ROOMS = 200
EMPTY_ROOM_TTL = 300.0   # seconds an empty room lingers before pruning
PRUNE_INTERVAL = 60.0


class RoomRegistry:
    """Thread-safe collection of rooms, created on demand in open mode."""

    def __init__(self, fixed_codes=None):
        self._lock = threading.Lock()
        self._rooms = {}
        self._empty_since = {}
        self.open_mode = not fixed_codes
        for code in fixed_codes or []:
            self._rooms[code] = RoomState(code)

    def lookup(self, code):
        """Return a RoomState, or an error string explaining the refusal."""
        code = code.strip().upper()
        with self._lock:
            room = self._rooms.get(code)
            if room is not None:
                return room
            if not self.open_mode:
                # don't list the valid codes — they are the access tokens
                return 'unknown room (this relay uses fixed room codes)'
            if not ROOM_CODE_RE.match(code):
                return 'room code must be 3-12 letters or digits'
            if len(self._rooms) >= MAX_ROOMS:
                return 'relay is full (too many rooms)'
            room = RoomState(code)
            self._rooms[code] = room
            print(f"[relay] room {code} created "
                  f"({len(self._rooms)} active)", flush=True)
            return room

    def prune_empty(self):
        """Drop open-mode rooms that have had no members for a while."""
        if not self.open_mode:
            return
        now = time.time()
        with self._lock:
            for code, room in list(self._rooms.items()):
                with room.lock:
                    room._prune()
                    empty = not room.members
                if not empty:
                    self._empty_since.pop(code, None)
                    continue
                since = self._empty_since.setdefault(code, now)
                if now - since > EMPTY_ROOM_TTL:
                    del self._rooms[code]
                    del self._empty_since[code]
                    print(f"[relay] room {code} pruned "
                          f"({len(self._rooms)} active)", flush=True)


def _mask(code):
    """Obscure a room code — codes are the access tokens."""
    if len(code) <= 2:
        return '·' * len(code)
    return code[0] + '·' * (len(code) - 2) + code[-1]


def _registry_stats(registry, show_codes):
    """Dashboard snapshot: rooms, members, what's playing, live position."""
    now = time.time()
    with registry._lock:
        rooms = list(registry._rooms.items())
    out = []
    for code, room in sorted(rooms):
        snap = room.snapshot()
        item = snap.get('item') or {}
        position = float(snap.get('position') or 0.0)
        if item and not snap.get('paused'):
            position += max(0.0, now - float(snap.get('set_at') or now)) \
                * float(snap.get('speed') or 1.0)
        out.append({
            'room': code if show_codes else _mask(code),
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


def main():
    parser = argparse.ArgumentParser(
        description='Watch Party standalone relay server')
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('WATCHPARTY_PORT', 8765)))
    parser.add_argument('--bind',
                        default=os.environ.get('WATCHPARTY_BIND', '0.0.0.0'))
    parser.add_argument('--room', action='append', default=None,
                        metavar='CODE',
                        help='restrict to fixed room code(s); '
                             'repeatable. Default: open mode. '
                             'Also via WATCHPARTY_ROOMS=A,B')
    args = parser.parse_args()

    rooms = args.room
    if rooms is None and os.environ.get('WATCHPARTY_ROOMS'):
        rooms = os.environ['WATCHPARTY_ROOMS'].split(',')
    fixed = []
    for code in rooms or []:
        code = code.strip().upper()
        if not ROOM_CODE_RE.match(code):
            parser.error(f"room code '{code}' must be 3-12 letters/digits")
        fixed.append(code)

    registry = RoomRegistry(fixed_codes=fixed)
    show_codes = os.environ.get('WATCHPARTY_SHOW_CODES', '').lower() \
        in ('1', 'true', 'yes')

    def do_get(self):
        path = self.path.split('?', 1)[0]
        if path == '/status':
            body = DASH_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == '/status.json':
            self._send(200, {'ok': True,
                             'rooms': _registry_stats(registry, show_codes),
                             'server_time': time.time()})
        else:
            _Handler.do_GET(self)

    handler = type('RegistryHandler', (_Handler,), {
        'lookup_room': lambda self, code: registry.lookup(code),
        'do_GET': do_get,
    })
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    httpd.daemon_threads = True

    mode = f"fixed rooms: {', '.join(fixed)}" if fixed else 'open mode'
    print(f"[relay] listening on {args.bind}:{args.port} ({mode}) — "
          f"dashboard at /status", flush=True)

    def pruner():
        while True:
            time.sleep(PRUNE_INTERVAL)
            registry.prune_empty()

    threading.Thread(target=pruner, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] shutting down", flush=True)
        httpd.server_close()


if __name__ == '__main__':
    main()
