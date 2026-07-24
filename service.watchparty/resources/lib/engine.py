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
import collections
import queue
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
        'host_control': a.getSettingBool('host_control'),
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
        # Extended properties fail for some item types; fall back to the
        # minimal query rather than losing the plugin path.
        result = _json_rpc('Player.GetItem',
                           {'playerid': player_id,
                            'properties': ['file', 'uniqueid', 'title',
                                           'year', 'showtitle', 'season',
                                           'episode']})
        if result is None:
            result = _json_rpc('Player.GetItem',
                               {'playerid': player_id,
                                'properties': ['file']})
        info = (result or {}).get('item') or {}
        jfile = info.get('file') or ''
        if jfile.startswith('plugin://'):
            item['plugin'] = jfile
        # Library identity, so guests can match their own copy of the
        # same movie/episode even when file paths differ.
        ids = {k: v for k, v in (info.get('uniqueid') or {}).items() if v}
        if ids:
            item['ids'] = ids
        itype = info.get('type') or ''
        if itype == 'episode' and info.get('showtitle'):
            item['type'] = 'episode'
            item['show'] = info['showtitle']
            item['season'] = info.get('season')
            item['episode'] = info.get('episode')
        elif itype == 'movie' and info.get('title'):
            item['type'] = 'movie'
            item['title'] = info['title']
            if info.get('year'):
                item['year'] = info['year']
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


def _find_in_library(item):
    """This device's own copy of the shared item, or ''.

    Matches by library identity (uniqueid like imdb/tvdb/tmdb, or
    show/season/episode, or title+year) so a guest whose library has the
    same movie under a different path can follow without a shared source.
    """
    if item.get('type') == 'episode' and item.get('show'):
        season, ep = item.get('season'), item.get('episode')
        if season in (None, '') or ep in (None, ''):
            return ''
        shows = (_json_rpc('VideoLibrary.GetTVShows',
                           {'filter': {'field': 'title', 'operator': 'is',
                                       'value': str(item['show'])}})
                 or {}).get('tvshows') or []
        for show in shows:
            episodes = (_json_rpc('VideoLibrary.GetEpisodes',
                                  {'tvshowid': show['tvshowid'],
                                   'season': int(season),
                                   'properties': ['file', 'episode']})
                        or {}).get('episodes') or []
            for episode in episodes:
                if int(episode.get('episode') or -1) == int(ep):
                    return episode.get('file') or ''
        return ''

    ids = item.get('ids') or {}
    title = item.get('title') or ''
    year = item.get('year')
    if not ids and not (title and year):
        return ''
    params = {'properties': ['file', 'uniqueid', 'year']}
    if title:
        params['filter'] = {'field': 'title', 'operator': 'is',
                            'value': title}
    movies = (_json_rpc('VideoLibrary.GetMovies', params)
              or {}).get('movies') or []
    for movie in movies:
        uid = movie.get('uniqueid') or {}
        if any(v and uid.get(k) == v for k, v in ids.items()):
            return movie.get('file') or ''
    if title and year:
        for movie in movies:
            if int(movie.get('year') or 0) == int(year):
                return movie.get('file') or ''
    return ''


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

    def onPlayBackStarted(self):
        # Fires the moment an open is requested, long before the stream
        # is ready. Marks a local open in flight so the poll loop doesn't
        # fight the still-buffering player (stop/reopen/pause/seek).
        self.engine.note_local_open()

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

    def _handle_stop(self):
        suppressed = self.engine.suppress.consume('stop')
        # A stop while our own auto-open is still coming up means the open
        # failed locally — that must not stop the party for everyone else.
        failed_open = self.engine.note_open_failed()
        self.engine.note_stopped()
        if suppressed or failed_open:
            return
        self._push('stop')

    def onPlayBackStopped(self):
        self._handle_stop()

    def onPlayBackEnded(self):
        self._handle_stop()

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
        self._last_party_key = ''  # key of the party's last shared item
        self._local_starting_until = 0.0  # local open still buffering
        self._failed_keys = set()  # items that would not play here
        self._member_names = None  # roster baseline for join/leave toasts
        self._last_error = ''
        self.connected = False
        # Player callbacks run on Kodi's announce thread — network I/O
        # there stalls the whole UI. Commands are queued and sent by a
        # dedicated worker instead.
        self._cmd_q = queue.Queue()
        self._push_thread = threading.Thread(
            target=self._push_worker, name='watchparty-push', daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        state = self.client.join(common.device_name())
        self.connected = True
        common.log(f"joined party as {self.client.member_id}")
        self._write_status(state)
        self._thread.start()
        self._push_thread.start()

    def stop(self):
        self._stop_event.set()
        self._cmd_q.put(None)
        self._thread.join(timeout=8)
        self._push_thread.join(timeout=8)
        self.client.leave()
        self.connected = False
        try:
            common.save_json(common.status_file(), {'active': False})
        except Exception:
            pass

    # -- auto-open bookkeeping ---------------------------------------------

    def note_local_open(self):
        """An open was requested locally (any source — user or engine);
        the stream isn't ready yet. The poll loop backs off until AV
        starts so it can't kill or hijack a buffering stream."""
        self._local_starting_until = time.time() + OPEN_GRACE

    def note_stopped(self):
        """Local playback fully stopped — nothing is playing here now."""
        self._local_key = ''
        self._local_starting_until = 0.0

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
        self._local_starting_until = 0.0
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
        """Queue a command for the push worker. Called from Kodi's player
        callback thread, so it must never block on the network."""
        settings = _settings()
        if not settings['allow_control'] or not self.connected:
            return
        # Claim sole control of the party when opening, if configured.
        lock = cmd == 'open' and settings['host_control']
        self._cmd_q.put((cmd, position, item, lock))

    def _push_worker(self):
        while True:
            entry = self._cmd_q.get()
            if entry is None or self._stop_event.is_set():
                break
            cmd, position, item, lock = entry
            try:
                self.client.command(cmd, position=position, item=item,
                                    lock=lock)
                common.log(f"pushed {cmd} @ {position:.1f}", xbmc.LOGDEBUG)
            except RelayError as e:
                self._last_error = str(e)
                common.log(f"push {cmd} failed: {e}", xbmc.LOGERROR)

    # -- pull --------------------------------------------------------------

    def _run(self):
        failures = 0
        while not self._stop_event.wait(POLL_INTERVAL):
            try:
                local_file = self._playing_file()
                state = self.client.poll(
                    position=self.safe_time(),
                    paused=self._is_paused(),
                    file=local_file,
                    caching=bool(local_file) and bool(
                        xbmc.getCondVisibility('Player.Caching')),
                    on_item=bool(self._local_key) and
                    self._local_key == self._last_party_key)
                failures = 0
                self._last_error = ''
                self._notify_member_changes(state)
                self._apply(state)
                self._write_status(state)
            except RelayError as e:
                # Pruned after a network blip: the relay forgot us, but
                # the party may still be going — rejoin instead of
                # failing forever.
                if 'not a member' in str(e):
                    try:
                        self.client.join(common.device_name())
                        common.log('rejoined party after being pruned')
                        common.notify('Rejoined party')
                        failures = 0
                        self._last_error = ''
                        continue
                    except RelayError as rejoin_error:
                        e = rejoin_error
                failures += 1
                self._last_error = str(e)
                if failures == 5:
                    common.log(f"lost relay: {e}", xbmc.LOGERROR)
                    common.notify('Party connection lost')
                self._write_status(None)
        common.log("engine stopped")

    def _notify_member_changes(self, state):
        """Toast when someone joins or leaves the party."""
        names = sorted(m.get('name') or 'Kodi'
                       for m in state.get('members') or [])
        if self._member_names is None:  # first poll is the baseline
            self._member_names = names
            return
        if names == self._member_names:
            return
        old = collections.Counter(self._member_names)
        new = collections.Counter(names)
        self._member_names = names
        for name in (new - old).elements():
            common.notify(f"{name} joined the party")
        for name in (old - new).elements():
            common.notify(f"{name} left the party")

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

        if item:
            self._last_party_key = _item_key(item)

        # A local open is still coming up (the user just pressed play, or
        # our auto-open is resolving): leave the player alone — stopping,
        # reopening, pause-matching or seeking a buffering stream can wedge
        # it. Everything sorts itself out once AV starts.
        if time.time() < self._local_starting_until:
            return

        # Nothing shared: if we follow the party and it stopped, stop too —
        # but only if we're actually playing the party's item, never
        # something this device started on its own.
        if not item:
            self._failed_keys.clear()  # a fresh item is a fresh chance
            self._opening_until = 0.0  # pending auto-open is moot now
            if cfg['follow_item'] and local_file \
                    and state.get('set_by') \
                    and state['set_by'] != self.client.member_id \
                    and self._local_key \
                    and self._local_key == self._last_party_key:
                self.suppress.arm('stop')
                self.player.stop()
                self.note_stopped()
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
            # Prefer, in order: plugin path (device resolves its own
            # stream), this device's own library copy, the shared path.
            url = item.get('plugin') or _find_in_library(item) \
                or _playable_url(item)
            if not url:
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
