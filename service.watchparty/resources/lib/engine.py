"""
Watch Party sync engine.

Runs on every party member (host included). Two jobs:

1. Push: xbmc.Player callbacks translate local play/pause/seek/stop/open
   into relay commands, so this device can drive the party.
2. Pull: a poll loop (~1s) fetches the shared state and applies it locally
   — opening the party's item, matching pause state, and seeking when the
   local position drifts past the configured threshold. The expected "now"
   position is computed from the state's server-time anchor plus the
   client's measured clock offset, so members agree even with network
   latency.

Echo control: commands we apply programmatically also fire the local
Player callbacks. Before applying anything we arm a short-lived suppress
token for that action; the callback consumes it and skips the push.
Additionally, state changes whose set_by is our own member id are never
re-applied as commands (only used for drift correction).
"""
import threading
import time

import xbmc

import common
from client import RelayClient, RelayError


POLL_INTERVAL = 1.0
SUPPRESS_TTL = 3.0       # seconds a suppress token stays valid
CORRECTION_COOLDOWN = 4.0  # min seconds between drift-correcting seeks
OPEN_GRACE = 20.0        # seconds to let a newly opened file start up
                         # (addon streams can take a while to resolve)


def _settings():
    a = common.addon()
    return {
        'follow_item': a.getSettingBool('follow_item'),
        'allow_control': a.getSettingBool('allow_control'),
        'drift_threshold': max(1.0, float(a.getSettingInt('drift_threshold'))),
    }


def _json_rpc(method, params):
    import json
    query = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    resp = xbmc.executeJSONRPC(json.dumps(query))
    try:
        return json.loads(resp).get('result')
    except Exception:
        return None


def _active_player_id():
    players = _json_rpc('Player.GetActivePlayers', {}) or []
    for p in players:
        if p.get('type') in ('video', 'audio'):
            return p.get('playerid')
    return None


def _now_playing_item(player):
    """Describe the playing item in a way other devices can act on.

    player.getPlayingFile() returns the *resolved* stream — for addon
    content that is a tokenized (often device-bound) URL, frequently
    behind a proxy on 127.0.0.1, which no other device can open. The
    JSON-RPC item 'file' still holds the original plugin:// path, so we
    share both: guests prefer 'plugin' and resolve the stream themselves.
    """
    try:
        file = player.getPlayingFile()
    except RuntimeError:
        return None
    item = {'file': file,
            'label': xbmc.getInfoLabel('Player.Title') or ''}
    player_id = _active_player_id()
    if player_id is not None:
        result = _json_rpc('Player.GetItem',
                           {'playerid': player_id,
                            'properties': ['file']}) or {}
        jfile = (result.get('item') or {}).get('file') or ''
        if jfile.startswith('plugin://'):
            item['plugin'] = jfile
    return item


def _item_key(item):
    """Stable identity for a shared item across devices."""
    return item.get('plugin') or item.get('file') or ''


def _playable_url(item):
    """URL for *this* device to open, or '' if the item can't play here.

    Only the plugin:// path is used for addon content — resolved
    playback URLs (http streams, local proxies, tokenized CDN links)
    are host-specific and never opened on other devices. Plain file
    paths (smb://, nfs://, library files) are shared as-is.
    """
    if item.get('plugin'):
        return item['plugin']
    file = item.get('file') or ''
    if file.startswith(('http://', 'https://')):
        return ''
    return file


class _Suppressor:
    """Short-lived tokens marking actions the engine itself triggered."""

    def __init__(self):
        self._tokens = {}
        self._lock = threading.Lock()

    def arm(self, action):
        with self._lock:
            self._tokens[action] = time.time() + SUPPRESS_TTL

    def consume(self, action):
        with self._lock:
            expiry = self._tokens.pop(action, 0)
            return expiry >= time.time()


class _PartyPlayer(xbmc.Player):
    """Pushes local player events to the relay (unless suppressed)."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def _push(self, cmd, **kwargs):
        self.engine.push_command(cmd, **kwargs)

    def onAVStarted(self):
        item = _now_playing_item(self)
        was_auto_open = self.engine.note_av_started(item)
        # Suppress the push both via the token and for any auto-open that
        # was still in its grace window: addon streams (YouTube etc.) can
        # take far longer to resolve than the token TTL, and re-announcing
        # the party's own item echoes back as a restart on everyone else.
        if self.engine.suppress.consume('open') or was_auto_open:
            return
        if not item:
            return
        try:
            position = self.getTime()
        except RuntimeError:
            position = 0.0
        self._push('open', position=position, item=item)

    def onPlayBackPaused(self):
        if self.engine.suppress.consume('pause'):
            return
        self._push('pause', position=self.engine.safe_time())

    def onPlayBackResumed(self):
        if self.engine.suppress.consume('play'):
            return
        self._push('play', position=self.engine.safe_time())

    def onPlayBackSeek(self, seek_time, seek_offset):
        if self.engine.suppress.consume('seek'):
            return
        self._push('seek', position=seek_time / 1000.0)

    def onPlayBackStopped(self):
        if self.engine.suppress.consume('stop'):
            return
        # A stop while our own auto-open is still coming up means the open
        # failed locally — that must not stop the party for everyone else.
        if self.engine.note_open_failed():
            return
        self._push('stop')

    def onPlayBackEnded(self):
        if self.engine.suppress.consume('stop'):
            return
        if self.engine.note_open_failed():
            return
        self._push('stop')

    def onPlayBackError(self):
        # Local playback errors are never party-wide events.
        self.engine.note_open_failed()


class SyncEngine:
    """One running party membership. Create, start(), and stop() to leave."""

    def __init__(self, host, port, room_code):
        self.client = RelayClient(host, port, room_code)
        self.suppress = _Suppressor()
        self.player = _PartyPlayer(self)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name='watchparty-engine', daemon=True)
        self._last_correction = 0.0
        self._opening_until = 0.0
        self._opened_key = ''     # party item we last auto-opened
        self._local_key = ''      # key of whatever this device is playing
        self._failed_keys = set()  # items that would not play here
        self._last_error = ''
        self.connected = False

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        state = self.client.join(common.device_name())
        self.connected = True
        common.log(f"joined party as {self.client.member_id}")
        self._write_status(state)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=8)
        self.client.leave()
        self.connected = False
        try:
            common.save_json(common.status_file(), {'active': False})
        except Exception:
            pass

    # -- auto-open bookkeeping ---------------------------------------------

    def note_av_started(self, item):
        """Playback came up. Returns True if this was our own auto-open
        completing (so the AV-start must not be pushed to the party).

        Remembers what this device is playing so polls can recognise the
        party item. After an auto-open, adopt the *party's* item key
        rather than re-deriving our own: the same content can yield a
        differently-formatted plugin URL on each device, and a key
        mismatch would read as 'different item' and echo restarts."""
        was_auto_open = time.time() < self._opening_until
        self._opening_until = 0.0
        if was_auto_open and self._opened_key:
            self._local_key = self._opened_key
        else:
            self._local_key = _item_key(item) if item else ''
        return was_auto_open

    def note_open_failed(self):
        """Called on stop/error. Returns True if an auto-open was in
        flight (i.e. the stop is fallout from a failed open, not a user
        action). Marks the item so we don't retry it forever."""
        if not (self._opening_until and time.time() < self._opening_until):
            return False
        self._opening_until = 0.0
        if self._opened_key:
            self._failed_keys.add(self._opened_key)
        common.log(f"could not play party item here: {self._opened_key}",
                   xbmc.LOGWARNING)
        common.notify("Can't play the party item on this device")
        return True

    # -- local player helpers ---------------------------------------------

    def safe_time(self):
        try:
            return self.player.getTime()
        except RuntimeError:
            return 0.0

    def _playing_file(self):
        try:
            if self.player.isPlaying():
                return self.player.getPlayingFile()
        except RuntimeError:
            pass
        return ''

    def _is_paused(self):
        return bool(xbmc.getCondVisibility('Player.Paused'))

    def _set_paused(self, paused):
        player_id = _active_player_id()
        if player_id is None:
            return
        self.suppress.arm('pause' if paused else 'play')
        _json_rpc('Player.PlayPause',
                  {'playerid': player_id, 'play': not paused})

    def _seek(self, position):
        self.suppress.arm('seek')
        try:
            self.player.seekTime(max(0.0, position))
        except RuntimeError:
            pass

    # -- push --------------------------------------------------------------

    def push_command(self, cmd, position=0.0, item=None):
        if not _settings()['allow_control'] or not self.connected:
            return
        try:
            self.client.command(cmd, position=position, item=item)
            common.log(f"pushed {cmd} @ {position:.1f}", xbmc.LOGDEBUG)
        except RelayError as e:
            self._last_error = str(e)
            common.log(f"push {cmd} failed: {e}", xbmc.LOGERROR)

    # -- pull --------------------------------------------------------------

    def _run(self):
        failures = 0
        while not self._stop_event.wait(POLL_INTERVAL):
            try:
                state = self.client.poll(
                    position=self.safe_time(),
                    paused=self._is_paused(),
                    file=self._playing_file())
                failures = 0
                self._last_error = ''
                self._apply(state)
                self._write_status(state)
            except RelayError as e:
                failures += 1
                self._last_error = str(e)
                if failures == 5:
                    common.log(f"lost relay: {e}", xbmc.LOGERROR)
                    common.notify('Party connection lost')
                self._write_status(None)
        common.log("engine stopped")

    def _expected_position(self, state):
        pos = float(state['position'])
        if not state['paused']:
            elapsed = self.client.server_now() - float(state['set_at'])
            pos += max(0.0, elapsed) * float(state.get('speed') or 1.0)
        return pos

    def _apply(self, state):
        cfg = _settings()
        item = state.get('item')
        local_file = self._playing_file()

        # Nothing shared: if we follow the party and it stopped, stop too.
        if not item:
            # A fresh party item is a fresh chance for items that failed.
            self._failed_keys.clear()
            if cfg['follow_item'] and local_file \
                    and state.get('set_by') \
                    and state['set_by'] != self.client.member_id:
                self.suppress.arm('stop')
                self.player.stop()
            return

        own_change = state.get('set_by') == self.client.member_id
        key = _item_key(item)

        # An auto-open whose grace lapsed with nothing playing failed
        # silently (no error callback ever fired): give up on that item.
        if self._opening_until and time.time() >= self._opening_until \
                and not local_file:
            self._opening_until = 0.0
            if self._opened_key:
                self._failed_keys.add(self._opened_key)
            common.notify("Can't play the party item on this device")

        # Same item? Resolved URLs differ per device for plugin streams,
        # so match on the shared key as well as the literal file.
        same = bool(local_file) and \
            (item['file'] == local_file or key == self._local_key)

        if not same:
            if not cfg['follow_item'] or own_change:
                return
            if time.time() < self._opening_until:
                return  # still waiting for a previous open to come up
            if key in self._failed_keys:
                return  # it won't play here; user was told once already
            url = _playable_url(item)
            if not url:
                # e.g. a host-local proxy URL with no plugin path shared
                self._failed_keys.add(key)
                common.log(f"party item not playable here: {item['file']}",
                           xbmc.LOGWARNING)
                common.notify("Party item can't be played on this device")
                return
            common.log(f"following party item: {url}")
            common.notify(f"Playing: {item.get('label') or 'party item'}")
            self.suppress.arm('open')
            self._opened_key = key
            self._opening_until = time.time() + OPEN_GRACE
            self.player.play(url)
            return

        # Pause state.
        local_paused = self._is_paused()
        if bool(state['paused']) != local_paused and not own_change:
            self._set_paused(bool(state['paused']))
            local_paused = bool(state['paused'])

        # Drift correction (both while playing and while paused).
        expected = self._expected_position(state)
        current = self.safe_time()
        drift = current - expected
        if abs(drift) > cfg['drift_threshold'] \
                and time.time() - self._last_correction > CORRECTION_COOLDOWN:
            common.log(f"drift {drift:+.1f}s — correcting to {expected:.1f}")
            self._last_correction = time.time()
            self._seek(expected)

    # -- status for the UI -------------------------------------------------

    def _write_status(self, state):
        status = {
            'active': True,
            'connected': not self._last_error,
            'error': self._last_error,
            'room': self.client.room,
            'relay': self.client.base,
            'member_id': self.client.member_id,
            'updated': time.time(),
        }
        if state:
            status['members'] = state.get('members') or []
            status['item'] = state.get('item')
            status['paused'] = state.get('paused')
        try:
            common.save_json(common.status_file(), status)
        except Exception:
            pass
