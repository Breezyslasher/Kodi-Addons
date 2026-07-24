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
from client import PROTOCOL_VERSION, RelayClient, RelayError


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


ITEM_PROPS = ['file', 'uniqueid', 'title', 'year', 'showtitle', 'season',
              'episode', 'artist', 'album', 'musicbrainztrackid']


def _identity(info):
    """Library-identity fields from a JSON-RPC item description, so other
    devices can match their own copy of the same content."""
    out = {}
    ids = {k: v for k, v in (info.get('uniqueid') or {}).items() if v}
    if ids:
        out['ids'] = ids
    itype = info.get('type') or ''
    if itype == 'episode' and info.get('showtitle'):
        out['type'] = 'episode'
        out['show'] = info['showtitle']
        out['season'] = info.get('season')
        out['episode'] = info.get('episode')
    elif itype == 'movie' and info.get('title'):
        out['type'] = 'movie'
        out['title'] = info['title']
        if info.get('year'):
            out['year'] = info['year']
    elif info.get('title') and (
            itype == 'song'
            # media-server direct plays sometimes report type 'unknown';
            # an artist alongside a title marks it as music anyway
            or (itype in ('', 'unknown') and info.get('artist'))):
        out['type'] = 'song'
        out['title'] = info['title']
        if info.get('artist'):
            out['artist'] = info['artist']  # Kodi gives a list
        if info.get('album'):
            out['album'] = info['album']
        if info.get('musicbrainztrackid'):
            out['ids'] = dict(out.get('ids') or {},
                              mbtrack=info['musicbrainztrackid'])
    return out


def _now_playing_item(player):
    """Describe the playing item in a way other devices can act on.

    player.getPlayingFile() returns the *resolved* stream — for addon
    content that is a tokenized (often device-bound) URL, frequently
    behind a proxy on 127.0.0.1, which no other device can open. The
    JSON-RPC item still holds the original plugin:// path and the
    library identity (title/artist/ids), so we share those: guests
    prefer resolving per-device. When playing from a queue, the queue
    and position are shared too, entries with their own identity.
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
                            'properties': ITEM_PROPS})
        if result is None:  # some Kodi versions reject extended props
            result = _json_rpc('Player.GetItem',
                               {'playerid': player_id,
                                'properties': ['file']})
        info = (result or {}).get('item') or {}
        jfile = info.get('file') or ''
        if jfile.startswith('plugin://'):
            item['plugin'] = jfile
        item.update(_identity(info))
        # a real title beats Kodi's filename fallback in labels/toasts
        if item.get('title'):
            item['label'] = item['title']
        elif not item['label'] and info.get('label'):
            item['label'] = info['label']
        # Share the queue when playing from a playlist/album, so members
        # can line up the same items and transition locally in sync.
        props = _json_rpc('Player.GetProperties',
                          {'playerid': player_id,
                           'properties': ['playlistid', 'position',
                                          'repeat', 'shuffled']}) or {}
        playlist_id = props.get('playlistid')
        playlist_pos = props.get('position')
        # repeat/shuffle ride along so followers can mirror the mode
        item['repeat'] = props.get('repeat') or 'off'
        if props.get('shuffled'):
            item['shuffled'] = True
        if isinstance(playlist_id, int) and playlist_id >= 0 \
                and isinstance(playlist_pos, int) and playlist_pos >= 0:
            listing = _json_rpc('Playlist.GetItems',
                                {'playlistid': playlist_id,
                                 'properties': ITEM_PROPS})
            if listing is None:
                listing = _json_rpc('Playlist.GetItems',
                                    {'playlistid': playlist_id,
                                     'properties': ['file']})
            entries = []
            for e in (listing or {}).get('items') or []:
                entry = {'file': e.get('file') or '',
                         'label': e.get('title') or e.get('label') or ''}
                entry.update(_identity(e))
                entries.append(entry)
            if 1 < len(entries) <= 100:
                item['playlist'] = entries
                item['playlist_pos'] = playlist_pos
                with_id = sum(1 for e in entries if e.get('type'))
                common.log(f"sharing queue: {len(entries)} entries "
                           f"@ {playlist_pos}, {with_id} with identity")
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
    """This device's own copy of the shared item: (open_ref, local_file),
    or (None, '').

    Matches by library identity (uniqueid like imdb/tvdb/tmdb,
    show/season/episode, title+year for movies, artist/album/title and
    MusicBrainz id for songs). The ref is a Player.Open/Playlist.Add
    item like {'songid': 1340} — opening by library id makes Kodi show
    full metadata instead of a bare file URL.
    """
    if item.get('type') == 'song':
        return _find_song(item)

    if item.get('type') == 'episode' and item.get('show'):
        season, ep = item.get('season'), item.get('episode')
        if season in (None, '') or ep in (None, ''):
            return None, ''
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
                    return {'episodeid': episode['episodeid']}, \
                        episode.get('file') or ''
        return None, ''

    ids = item.get('ids') or {}
    title = item.get('title') or ''
    year = item.get('year')
    if not ids and not (title and year):
        return None, ''
    params = {'properties': ['file', 'uniqueid', 'year']}
    if title:
        params['filter'] = {'field': 'title', 'operator': 'is',
                            'value': title}
    movies = (_json_rpc('VideoLibrary.GetMovies', params)
              or {}).get('movies') or []
    for movie in movies:
        uid = movie.get('uniqueid') or {}
        if any(v and uid.get(k) == v for k, v in ids.items()):
            return {'movieid': movie['movieid']}, movie.get('file') or ''
    if title and year:
        for movie in movies:
            if int(movie.get('year') or 0) == int(year):
                return {'movieid': movie['movieid']}, \
                    movie.get('file') or ''
    return None, ''


def _find_song(item):
    """This device's music-library copy: ({'songid': N}, file) or
    (None, ''). Filters by title (+ first artist, + album when known);
    a shared MusicBrainz id picks the exact recording. Retries without
    the album so compilations still match."""
    title = item.get('title') or ''
    if not title:
        return None, ''
    artists = item.get('artist') or []
    album = item.get('album') or ''
    mb_track = (item.get('ids') or {}).get('mbtrack') or ''
    conditions = [{'field': 'title', 'operator': 'is', 'value': title}]
    if artists:
        conditions.append({'field': 'artist', 'operator': 'is',
                           'value': str(artists[0])})
    for with_album in ([True, False] if album else [False]):
        parts = list(conditions)
        if with_album:
            parts.append({'field': 'album', 'operator': 'is',
                          'value': album})
        params = {'properties': ['file', 'musicbrainztrackid'],
                  'filter': {'and': parts} if len(parts) > 1 else parts[0]}
        songs = (_json_rpc('AudioLibrary.GetSongs', params)
                 or {}).get('songs') or []
        if mb_track:
            for song in songs:
                if song.get('musicbrainztrackid') == mb_track:
                    return {'songid': song['songid']}, \
                        song.get('file') or ''
        if songs:
            return {'songid': songs[0]['songid']}, \
                songs[0].get('file') or ''
    return None, ''


def _local_entry_ref(entry):
    """(open_ref, local_key) this device can queue for a shared playlist
    entry, or (None, ''). Preference: plugin path (resolves per-device),
    own library copy by id (full metadata on screen), plain shared path.
    Host-specific http(s) streams with no matchable identity can't be
    queued."""
    file = entry.get('file') or ''
    if file.startswith('plugin://'):
        return {'file': file}, file
    if entry.get('type'):
        ref, local_file = _find_in_library(entry)
        if ref:
            return ref, (local_file or file)
    if file and not file.startswith(('http://', 'https://')):
        return {'file': file}, file
    return None, ''


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
        if self.engine.is_natural_advance(_item_key(item)):
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

    def _end_playback(self, announce):
        # A suppressed stop is one the engine itself caused — including
        # the teardown of the old item when an open replaces playback.
        # It must not be misread as the new open failing.
        if self.engine.suppress.consume('stop'):
            self.engine.note_stopped()
            return
        # A stop while our own auto-open is still coming up means the open
        # failed locally — that must not stop the party for everyone else.
        failed_open = self.engine.note_open_failed()
        self.engine.note_stopped()
        if failed_open or not announce:
            return
        self._push('stop')

    def onPlayBackStopped(self):
        # A deliberate stop by any member stops the party.
        self._end_playback(announce=True)

    def onPlayBackEnded(self):
        # Natural end is single-writer, like queue advances: only the
        # member who announced the item declares it over. Followers reach
        # the end at nearly the same moment, and their 'stop' would race
        # the driver's next-track announcement at every queue boundary.
        self._end_playback(announce=self.engine.drives_current_item())

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
        self._open_fallbacks = []  # untried open refs for the current item
        self._i_opened_current = False  # we announced the current item
        self._party_keys = set()   # keys belonging to the shared queue
        self._queue_map = {}       # party entry key -> our local key
        self._queue_index = {}     # party entry key -> queue position
        self._queue_order = []     # party entry keys in shared order
        self._queue_playlistid = 0
        self._queue_jump_pending = ''
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
        if self.client.relay_protocol < PROTOCOL_VERSION:
            common.log(f"relay speaks protocol {self.client.relay_protocol},"
                       f" addon speaks {PROTOCOL_VERSION}", xbmc.LOGWARNING)
            common.notify('Relay is older than the addon — '
                          'update it for all features')
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

    def drives_current_item(self):
        """True if this device announced the current party item and so
        speaks for its lifecycle (advances, natural end)."""
        return self._i_opened_current

    def is_natural_advance(self, key):
        """True when this AV-start is just our queued copy of the party
        playlist moving to the next track. Every member's queue advances
        near-simultaneously; only the member who opened the playlist
        announces the change, or each advance would echo as a fresh
        'open' from every device at once."""
        return (not self._i_opened_current
                and bool(self._party_keys)
                and key in self._party_keys)

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
        self._open_fallbacks = []  # something is playing; no retry needed
        if was_auto_open and self._opened_key:
            self._local_key = self._opened_key
        else:
            self._local_key = _item_key(item) if item else ''
        return was_auto_open

    def note_open_failed(self):
        """Called on stop/error. Returns True if an auto-open was in
        flight (i.e. the stop is fallout from a failed open, not a user
        action). Tries the next open candidate — e.g. a plugin id that
        only exists on the host's media server fails, but this device's
        own library copy is next in line — before giving up and marking
        the item so we don't retry it forever."""
        if not (self._opening_until and time.time() < self._opening_until):
            return False
        if self._open_fallbacks:
            next_ref = self._open_fallbacks.pop(0)
            common.log(f"open failed, trying fallback: {next_ref}",
                       xbmc.LOGWARNING)
            self.suppress.arm('open')
            self._opening_until = time.time() + OPEN_GRACE
            _json_rpc('Player.Open', {'item': next_ref})
            return True
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
            key_now = _item_key(item)
            if key_now != self._last_party_key:
                # whoever announced this item also announces natural
                # queue advances and the natural end for it
                self._i_opened_current = \
                    state.get('set_by') == self.client.member_id
                self._party_keys = {
                    e.get('file') or ''
                    for e in item.get('playlist') or []} - {''}
                self._party_keys |= set(self._queue_map.values())
            self._last_party_key = key_now

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
            self._party_keys = set()
            self._queue_map = {}
            self._queue_index = {}
            self._queue_order = []
            self._queue_jump_pending = ''
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
        # silently (no error callback ever fired): try the next open
        # candidate, or give up on the item when none remain.
        if self._opening_until and time.time() >= self._opening_until \
                and not local_file:
            if self._open_fallbacks:
                next_ref = self._open_fallbacks.pop(0)
                common.log(f"open timed out, trying fallback: {next_ref}",
                           xbmc.LOGWARNING)
                self.suppress.arm('open')
                self._opening_until = time.time() + OPEN_GRACE
                _json_rpc('Player.Open', {'item': next_ref})
                return
            self._opening_until = 0.0
            if self._opened_key:
                self._failed_keys.add(self._opened_key)
            common.notify("Can't play the party item on this device")

        # Same item? Resolved URLs differ per device for plugin streams
        # and library copies, so match on the shared key, and through the
        # queue's party-key -> local-key mapping.
        same = bool(local_file) and \
            (item['file'] == local_file or key == self._local_key
             or (key in self._queue_map
                 and self._queue_map[key] == self._local_key))
        if same:
            self._queue_jump_pending = ''

        if not same:
            if not cfg['follow_item'] or own_change:
                return
            if time.time() < self._opening_until:
                return  # still waiting for a previous open to come up
            if key in self._failed_keys:
                return  # it won't play here; user was told once already

            # The shared queue order changed (e.g. the host toggled
            # shuffle): rebuild instead of jumping, so our local queue
            # follows the new order from here on.
            shared_order = [e.get('file') or ''
                            for e in item.get('playlist') or []]
            order_changed = bool(shared_order) and self._queue_order \
                and shared_order != self._queue_order

            # Our local queue already contains this item: jump to its
            # slot instead of rebuilding. Waiting one poll first gives
            # our own natural advance a moment to line up by itself.
            if key in self._queue_index and local_file \
                    and not order_changed:
                if self._queue_jump_pending != key:
                    self._queue_jump_pending = key
                    return
                self._queue_jump_pending = ''
                self.suppress.arm('open')
                self.suppress.arm('stop')
                self._opened_key = key
                self._opening_until = time.time() + OPEN_GRACE
                _json_rpc('Player.Open',
                          {'item': {'playlistid': self._queue_playlistid,
                                    'position': self._queue_index[key]}})
                return

            # A shared queue whose entries all map to something playable
            # here: line it up locally (by library id where matched, so
            # Kodi shows full metadata) and start at the party position.
            # Transitions then happen natively on every device.
            entries = item.get('playlist') or []
            playlist_pos = item.get('playlist_pos')
            refs = None
            if len(entries) > 1 and isinstance(playlist_pos, int) \
                    and 0 <= playlist_pos < len(entries):
                refs = []
                for i, entry in enumerate(entries):
                    ref, local_key = _local_entry_ref(entry)
                    if not ref:
                        common.log(f"queue entry {i} not usable here: "
                                   f"{entry.get('file')}", xbmc.LOGWARNING)
                        refs = None
                        break
                    refs.append((entry.get('file') or '', ref, local_key))
            if refs:
                playlist_id = 0 if item.get('type') == 'song' else 1
                common.log(f"following party queue "
                           f"({len(refs)} items @ {playlist_pos})")
                common.notify(f"Playing: {item.get('label') or 'party item'}")
                self.suppress.arm('open')
                if local_file:
                    # replacing running playback fires a stop for the
                    # old item — expected, not a failed open
                    self.suppress.arm('stop')
                self._opened_key = key
                self._open_fallbacks = []
                self._opening_until = time.time() + OPEN_GRACE
                self._queue_playlistid = playlist_id
                self._queue_map = {ek: lk for ek, _, lk in refs}
                self._queue_index = {ek: i
                                     for i, (ek, _, _) in enumerate(refs)}
                self._queue_order = [ek for ek, _, _ in refs]
                self._party_keys |= set(self._queue_map.values())
                _json_rpc('Playlist.Clear', {'playlistid': playlist_id})
                _json_rpc('Playlist.Add',
                          {'playlistid': playlist_id,
                           'item': [ref for _, ref, _ in refs]})
                _json_rpc('Player.Open',
                          {'item': {'playlistid': playlist_id,
                                    'position': playlist_pos}})
                return

            # Single item — candidates in preference order: plugin path
            # (device resolves its own stream), own library copy by id
            # (full metadata on screen), the shared plain path. A failed
            # candidate falls through to the next.
            candidates = []
            if item.get('plugin'):
                candidates.append({'file': item['plugin']})
            lib_ref, _lib_file = _find_in_library(item)
            if lib_ref:
                candidates.append(lib_ref)
            plain = _playable_url(item)
            if plain and plain != item.get('plugin'):
                candidates.append({'file': plain})
            if not candidates:
                self._failed_keys.add(key)
                common.log(f"party item not playable here: {item['file']}",
                           xbmc.LOGWARNING)
                common.notify("Party item can't be played on this device")
                return
            common.log(f"following party item: {candidates[0]}")
            common.notify(f"Playing: {item.get('label') or 'party item'}")
            self.suppress.arm('open')
            if local_file:
                self.suppress.arm('stop')
            self._opened_key = key
            self._open_fallbacks = candidates[1:]
            self._opening_until = time.time() + OPEN_GRACE
            self._queue_map = {}
            self._queue_index = {}
            _json_rpc('Player.Open', {'item': candidates[0]})
            return

        # Pause state.
        local_paused = self._is_paused()
        if bool(state['paused']) != local_paused and not own_change:
            self._set_paused(bool(state['paused']))
            local_paused = bool(state['paused'])

        # Repeat mode follows the party (the item's announcer owns it);
        # shuffle stays OFF while following a shared queue — the
        # announcements are the order of truth, and two independent
        # shuffles can never agree.
        if 'repeat' in item and not self._i_opened_current \
                and cfg['follow_item']:
            player_id = _active_player_id()
            if player_id is not None:
                modes = _json_rpc('Player.GetProperties',
                                  {'playerid': player_id,
                                   'properties': ['repeat',
                                                  'shuffled']}) or {}
                party_repeat = item.get('repeat') or 'off'
                if (modes.get('repeat') or 'off') != party_repeat:
                    common.log(f"matching party repeat: {party_repeat}")
                    _json_rpc('Player.SetRepeat',
                              {'playerid': player_id,
                               'repeat': party_repeat})
                if modes.get('shuffled') and self._queue_map:
                    _json_rpc('Player.SetShuffle',
                              {'playerid': player_id, 'shuffle': False})

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
