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
        code = code.strip().upper()
        with self._lock:
            room = self._rooms.get(code)
            if room is not None:
                return room
            if not self.open_mode:
                return None
            if not ROOM_CODE_RE.match(code) or len(self._rooms) >= MAX_ROOMS:
                return None
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
    handler = type('RegistryHandler', (_Handler,), {
        'lookup_room': lambda self, code: registry.lookup(code),
    })
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    httpd.daemon_threads = True

    mode = f"fixed rooms: {', '.join(fixed)}" if fixed else 'open mode'
    print(f"[relay] listening on {args.bind}:{args.port} ({mode})",
          flush=True)

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
