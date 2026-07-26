"""
Watch Party UI.

Small dialog-driven front end: start a party (this device hosts the
relay), join one with an address + room code, view live status, or leave.
All it really does is write session.json — the background service notices
the change within a second and starts/stops the relay and sync engine.
"""
import random
import time

import xbmc
import xbmcaddon
import xbmcgui

import common


ADDON = xbmcaddon.Addon()

# no 0/O or 1/I — room codes get read out loud across the couch
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _session():
    return common.load_json(common.session_file(), default={'mode': 'off'})


def _write_session(data):
    common.save_json(common.session_file(), data)


def _status():
    return common.load_json(common.status_file(), default={})


def _port():
    try:
        return int(ADDON.getSettingInt('port'))
    except Exception:
        return 8765


def _random_code():
    return ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _ask_address(dialog, prompt):
    """Prompt for a relay/host address; returns (address, host, port) or
    None. Accepts ip, ip:port, or a full http(s):// base URL."""
    address = dialog.input(prompt,
                           defaultt=ADDON.getSettingString('default_address'))
    if not address:
        return None
    address = address.strip()
    if address.startswith(('http://', 'https://')):
        # Full base URL (standalone relay / tunnel) — port comes with it.
        return address.rstrip('/'), address.rstrip('/'), 0
    if ':' in address:
        host, _, port_s = address.partition(':')
        try:
            return address, host, int(port_s)
        except ValueError:
            dialog.ok('Watch Party', f"Invalid port in '{address}'")
            return None
    return address, address, _port()


def start_party_here():
    """Host on this device: Kodi runs the relay, friends connect to us."""
    room = _random_code()
    port = _port()
    ip = xbmc.getIPAddress() or '?'
    _write_session({'mode': 'host', 'room': room, 'port': port})
    xbmcgui.Dialog().ok(
        'Watch Party',
        f"Party started on this device!\n\n"
        f"Friends join with:\n"
        f"    Address:  [B]{ip}:{port}[/B]\n"
        f"    Room code:  [B]{room}[/B]")


def start_party_on_relay():
    """Open a new room on a standalone/Docker relay and join it."""
    dialog = xbmcgui.Dialog()
    parsed = _ask_address(
        dialog, 'Relay server address (ip[:port] or https://...)')
    if not parsed:
        return
    address, host, port = parsed
    room = dialog.input('Room code for the new party (share with friends)',
                        defaultt=ADDON.getSettingString('default_room')
                        or _random_code())
    if not room:
        return
    room = room.strip().upper()
    # A quick reachability check beats a silent background failure.
    try:
        from client import RelayClient
        if not RelayClient(host, port, room, timeout=4.0).ping():
            raise IOError('not a Watch Party relay')
    except Exception as e:
        if not dialog.yesno('Watch Party',
                            f"Relay not reachable ({e}).\nSave and keep "
                            f"trying in the background anyway?"):
            return
    _write_session({'mode': 'guest', 'host': host, 'port': port,
                    'room': room})
    ADDON.setSettingString('default_address', address)
    ADDON.setSettingString('default_room', room)
    dialog.ok(
        'Watch Party',
        f"Party started on the relay!\n\n"
        f"Friends join with:\n"
        f"    Address:  [B]{address}[/B]\n"
        f"    Room code:  [B]{room}[/B]")


def join_party():
    dialog = xbmcgui.Dialog()
    parsed = _ask_address(
        dialog, 'Party address (ip[:port] or https://relay.example.com)')
    if not parsed:
        return
    address, host, port = parsed
    room = dialog.input('Room code',
                        defaultt=ADDON.getSettingString('default_room'))
    if not room:
        return
    room = room.strip().upper()
    _write_session({'mode': 'guest', 'host': host, 'port': port,
                    'room': room})
    # Remember for next time so rejoining is just OK, OK
    ADDON.setSettingString('default_address', address)
    ADDON.setSettingString('default_room', room)
    xbmcgui.Dialog().notification(
        'Watch Party', 'Joining party...',
        ADDON.getAddonInfo('icon'), 3000)


def leave_party():
    _write_session({'mode': 'off'})
    xbmcgui.Dialog().notification(
        'Watch Party', 'Left the party',
        ADDON.getAddonInfo('icon'), 3000)


def show_status():
    session = _session()
    status = _status()
    # status.json is written every poll; treat a stale file as disconnected
    fresh = status.get('active') and \
        time.time() - float(status.get('updated') or 0) < 10
    lines = []
    if session.get('mode') == 'host':
        ip = xbmc.getIPAddress() or '?'
        lines.append(f"Hosting — room [B]{session.get('room')}[/B] "
                     f"at [B]{ip}:{session.get('port')}[/B]")
    else:
        lines.append(f"Guest of [B]{session.get('host')}[/B] — "
                     f"room [B]{session.get('room')}[/B]")
    if not fresh:
        lines.append('Status: [COLOR red]not connected[/COLOR]')
        if status.get('error'):
            lines.append(f"Last error: {status['error']}")
    else:
        if status.get('connected'):
            lines.append('Status: [COLOR green]connected[/COLOR]')
        else:
            lines.append('Status: [COLOR red]connection lost[/COLOR] '
                         f"({status.get('error') or 'unknown'})")
        item = status.get('item')
        if item:
            state = 'paused' if status.get('paused') else 'playing'
            lines.append(f"Now {state}: {item.get('label') or item.get('file')}")
        else:
            lines.append('Nothing playing yet')
        members = status.get('members') or []
        lines.append('')
        lines.append(f"[B]In the party ({len(members)}):[/B]")
        for m in members:
            pos = time.strftime('%H:%M:%S', time.gmtime(max(0, m.get('position') or 0)))
            state = 'paused' if m.get('paused') else 'playing'
            watching = ' — ' + f"{pos} ({state})" if m.get('file') else ''
            lines.append(f"  • {m.get('name')}{watching}")
    xbmcgui.Dialog().textviewer('Watch Party — status', '\n'.join(lines))


def main():
    session = _session()
    active = session.get('mode') in ('host', 'guest')
    if active:
        options = ['Party status', 'Leave party', 'Settings']
        actions = [show_status, leave_party, ADDON.openSettings]
    else:
        options = [
            'Host a party on this device (friends connect to this Kodi)',
            'Start a party on a relay server (Docker/VPS relay)',
            'Join an existing party',
            'Settings',
        ]
        actions = [start_party_here, start_party_on_relay, join_party,
                   ADDON.openSettings]
    choice = xbmcgui.Dialog().select('Watch Party', options)
    if choice >= 0:
        actions[choice]()


if __name__ == '__main__':
    main()
