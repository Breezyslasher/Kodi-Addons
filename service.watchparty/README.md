# Watch Party

Synchronized playback across Kodi devices — a [SyncLounge](https://github.com/synclounge/synclounge)-style
watch party, but native to Kodi. One device hosts a party, others join with a
room code, and play / pause / seek are mirrored to everyone in real time with
automatic drift correction.

No external server, no cloud service: the hosting Kodi runs a tiny embedded
relay that all members (including the host itself) talk to over HTTP. For
remote friends, the same relay also ships as a
[standalone server](#remote-parties-standalone-relay) you can run on a VPS,
Raspberry Pi or in Docker, so nobody has to port-forward anything.

## How it works

```
                 ┌────────────────────────┐
                 │  Host Kodi             │
                 │  ┌──────────────────┐  │   poll / commands (HTTP, ~1s)
   Guest Kodi ───┼──▶  relay server    ◀──┼─── Guest Kodi
                 │  └───────▲──────────┘  │
                 │          │ localhost   │
                 │     sync engine        │
                 └────────────────────────┘
```

- Every member runs a **sync engine**: local player events (play, pause,
  seek, stop, opening a file) are pushed to the relay, and the shared state
  is polled about once a second and applied locally.
- The shared position is anchored to **server time**; each client measures
  its clock offset against the relay (round-trip midpoint, EMA-smoothed),
  so members agree on "where the party is" even with network latency.
- If a member's position drifts past the threshold (default 3 s), the
  engine seeks them back into sync — corrections are rate-limited so
  playback isn't constantly jumping.
- **Addon content follows by `plugin://` path**, not by stream URL: each
  device resolves the stream with its own copy of the addon (own account,
  own session), the same way SyncLounge lets every Plex client fetch its
  own stream. Resolved playback URLs are never opened on other devices.
- **Library items match by identity**: the party shares IMDb/TVDb/TMDb ids
  (and show/season/episode or title+year), so a guest whose own library has
  the same movie or episode — different NAS, different path — plays their
  local copy automatically. No shared source needed. Music matches too:
  by artist/album/title, with MusicBrainz ids picking the exact recording.
  Matched items open by library id, so Kodi shows full metadata instead
  of a bare stream URL, and a failed open (e.g. an id from someone
  else's media server) falls through to the next way of playing it.
- **Playlists follow too**: when the party plays from a queue (an album,
  a season, a mixed playlist), the whole queue is shared — each entry
  with its own identity — and lined up on every member, so transitions
  happen locally and stay smooth. Only the member who started the queue
  announces advances and its natural end; everyone else's player just
  moves with it.
- **Repeat and shuffle**: the announcer's repeat mode (off/one/all) is
  mirrored to everyone, so repeat-one loops and repeat-all wraps stay in
  sync. Shuffle is the announcer's privilege — followers stay unshuffled
  and play the announced order. Toggles propagate instantly (not at the
  next track): followers reorder their queue in place to the announcer's
  new order — the playing track keeps playing and stays correctly marked,
  because the queue is permuted with swaps rather than rebuilt.
- **Buffer hold**: while any member watching the item is buffering, the
  party auto-pauses, and resumes when they catch up. A deliberate
  pause/play always overrides the hold.
- Commands you triggered yourself are never re-applied to you (echo
  suppression), so pausing locally doesn't bounce back as a second pause.
- All relay traffic runs off Kodi's UI and player threads, so a slow or
  distant relay never stalls the interface.

## Usage

The main menu offers three ways into a party (plus Settings):

- **Host a party on this device** — for the couch/LAN case. This Kodi
  runs the relay; it shows your `ip:port` and a generated 4-letter room
  code for friends to join with.
- **Start a party on a relay server** — for remote parties. Point it at
  your standalone/Docker relay (`ip:port` or `https://...`), pick a room
  code to share, and the addon opens the room and joins it. The relay is
  pinged first so a wrong address fails loudly, not silently.
- **Join an existing party** — enter the address and room code someone
  shared with you. Address and code are remembered and pre-filled next
  time, so rejoining is just OK, OK.

Then just play something — everyone follows. Any member can pause, seek or
resume for the whole party (this can be turned off per device, see below).
Members joining or leaving show as notifications, and *Party status* shows
who's in the room, what's playing, and each member's position.

## Remote parties (standalone relay)

For friends outside your LAN, run the relay on a neutral, always-reachable
machine instead of inside Kodi. Everyone — including you — joins it as a
guest, and all connections go *outward*, so no port forwarding, VPN or
router changes on anyone's side.

**The standalone relay lives in
[Breezyslasher/WatchParty](https://github.com/Breezyslasher/WatchParty)
(`watchparty-relay/`)**, with how to run it (Python, or the Docker image
`ghcr.io/breezyslasher/watchparty-relay`, multi-arch), its modes,
persistence, the dashboard and the **remote** page (`/remote`: run a room
from any browser, nothing to install). It moved there from this folder;
the old image `ghcr.io/breezyslasher/kodi-watchparty-relay` is no longer
updated — switch to the new name and keep your `/data` volume.

The relay this addon hosts inside Kodi (*Host a party on this device*) is
the same code: `resources/lib/relay.py` and the remote page in
`resources/remote/` are copies made by WatchParty's `tools/vendor-kodi.sh`
(see `resources/remote/VENDORED.txt`) — change them there, not here. It
serves the same dashboard (`http://kodi-ip:8765/status`) and remote
(`http://kodi-ip:8765/remote`) for its room. The addon warns ("Relay is
older than the addon") when it joins a relay that doesn't speak its
protocol version.

Content still needs to be resolvable per device — a streaming addon
everyone has, a shared media server, or each member's own copy.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Device name | system name | How you appear in the member list |
| Host port | 8765 | Port the embedded relay listens on when hosting |
| Saved host address | — | Pre-filled in the join dialog; updated automatically after each join |
| Saved room code | — | Pre-filled in the join dialog; updated automatically after each join |
| Follow party item | on | Automatically open whatever the party plays |
| Send my play/pause/seek | on | Off = follow-only mode (a "viewer" that can't drive the party) |
| Lock control to me | off | Host mode: when this device starts an item, only it can play/pause/seek/stop until it stops — other members' controls are rejected |
| Drift threshold | 3 s | How far out of sync before a corrective seek |

## Notes & limitations

- **Content must be resolvable on every device.** Addon streams (YouTube,
  Disney+, debrid addons, …) work when every member has the same addon
  installed and signed in — each device resolves its own stream from the
  shared `plugin://` path. Library items on a network share (NFS/SMB) work
  when the share is reachable from every device (a VPN covers remote
  guests). A file that only exists locally on the host
  (`/home/me/movie.mkv`) opens on guests only if their own library has
  the same movie/episode (matched by id — see above); otherwise turn off
  *Follow party item* there and start the same content manually —
  pause/seek sync still applies once playback has begun.
- **Watch Party's Android app and browser extension** can share a room with
  Kodi. They share streaming-service links (Netflix, Disney+, …), which Kodi
  doesn't open; titles they start from their *Browse* also carry the title's
  identity in the same fields Kodi uses (`type`, `title`, `year`, `ids`, or
  `show`, `season`, `episode`), so Kodi plays its **own library copy** of
  that movie or episode, matched as above (tested in `tests/test_engine.py`,
  `AppItemTest`). The other way, they look a Kodi title up on JustWatch and
  open it on a streaming service. A library file and a streaming copy can
  start a few seconds apart (studio logos, recaps); sync then keeps that
  offset. A pasted link carries no identity and is skipped here.
- **Their links in Kodi's streaming addons:** when there's no library copy,
  a shared link plays in an installed addon that has the title
  (`addon_links.py`, formats read from each addon's source): Netflix
  (CastagnaIT's, movies — an episode needs ids a link doesn't have),
  YouTube, SlyGuy's Disney+, Hulu and Paramount+, SlyGuy's HBO Max and
  Discovery+ (only links copied while playing, which carry the edit id),
  Sandmann79's Prime Video, and the Apple TV and Tubi addons in this repo
  (Tubi: movies and episodes; episode progress reports correctly from
  TubiTV 2026.11.15). Fawesome links (`fawesome.tv/movies|tv-shows/<id>/…`)
  play in the Fawesome addon (branch `plugin.video.fawesome`), else from
  the stream in the title's page (`fawesome.py`, no addon needed;
  2026.11.21.1, untested in Kodi). The apps do the reverse: a title playing in one of these
  addons opens in the service's app or website there (`AddonLinks.kt`,
  `links.js`). Untested with the real addons.
- Stopping playback stops the party's shared item for everyone (like taking
  the disc out). Natural end-of-file does the same.
- When the last member leaves the room (or is pruned), the item goes with
  them: whoever joins next starts with nothing playing instead of being sent
  into the old title. A relay restart keeps it (saved rooms load with no
  members, and members rejoin into them).
- Members are pruned after 15 s without contact, but devices rejoin
  automatically after a network blip (with a "Rejoined party" toast) —
  no manual re-joining needed.
- Traffic is plain HTTP on your LAN; the room code is the access token.
  For anything crossing the internet, prefer the standalone relay behind
  TLS (reverse proxy / tunnel) over exposing a raw port.
- With the embedded relay, the party lives in the hosting Kodi — if the
  host quits, the party ends. A standalone relay keeps the room alive
  independently of any member.

## Testing

Two checklists ship as issue templates, so each pass is recorded and
linkable: *Smoke Test — Watch Party* (47 checks, ~20 min, run it after
every change) and *Testing Checklist — Watch Party* (434 checks, for
releases). [`TESTING.md`](TESTING.md) is the companion: rig, suggested
order, where bugs are most likely, and what is a known limit rather than
a bug. Problems go in the *Watch Party — bug report* template.

The automated suite (engine decision logic, addon links, and the built-in
relay's copy; the relay's own tests are in WatchParty's `watchparty-relay/`)
runs on every PR:

```
python3 -m unittest discover service.watchparty/tests
```

## Requirements

- Kodi 19 (Matrix) or later on all devices
- Network path from every member to the relay — the hosting Kodi's port
  (default 8765) on a LAN, or the standalone relay's address for remote
  parties
