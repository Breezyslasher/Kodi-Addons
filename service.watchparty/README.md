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
  local copy automatically. No shared source needed.
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
router changes on anyone's side:

```
python3 relay_standalone.py                  # open mode on :8765
python3 relay_standalone.py --room MOVIE     # invite-only: fixed room(s)
```

Or with Docker — a prebuilt multi-arch image (amd64 / arm64 / armv7, so
Raspberry Pi works) is published to GHCR by CI:

```
docker run -d -p 8765:8765 --restart unless-stopped ghcr.io/breezyslasher/kodi-watchparty-relay:latest
```

Or build it yourself from this addon's folder:

```
docker compose up -d
# or
docker build -t kodi-watchparty-relay . && docker run -d -p 8765:8765 --restart unless-stopped kodi-watchparty-relay
```

- **Open mode** (default): any room code creates a room on first join —
  SyncLounge-style. Rooms empty for 5 minutes are pruned.
- **Fixed mode** (`--room CODE`, repeatable, or `WATCHPARTY_ROOMS=A,B`):
  only the listed rooms exist; everything else is rejected.
- **Persistence**: with `--state-file PATH` (or `WATCHPARTY_STATE`), room
  playback state survives relay restarts — after an update or reboot,
  guests auto-rejoin and the party continues where it was. The Docker
  image has this on by default (`/data/state.json`; the compose file
  mounts a volume so it also survives container re-creation). Members
  aren't persisted; their devices rejoin automatically within seconds.
- **Version handshake**: the addon warns ("Relay is older than the
  addon") when it joins a relay that doesn't speak its protocol version,
  so a stale container is a visible message instead of silently missing
  features.
- **Dashboard**: open `/status` in a browser for a live view of active
  rooms, connected members and playback positions (auto-refreshes).
  Room codes are masked there by default since they double as the
  access tokens — set `WATCHPARTY_SHOW_CODES=1` to show them in full
  on a private deployment. `/status.json` serves the same data as JSON.
  The embedded in-Kodi relay serves the same dashboard for its room
  (`http://kodi-ip:8765/status`) — handy on a phone while hosting.

The relay is pure Python standard library (`relay_standalone.py` +
`resources/lib/relay.py`) — no pip installs. Point it behind a reverse
proxy or a tunnel (cloudflared, ngrok) for TLS, then guests join with the
`https://` URL. Content still needs to be resolvable per device — a
streaming addon everyone has, a shared media server, or each member's own
copy.

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
- Stopping playback stops the party's shared item for everyone (like taking
  the disc out). Natural end-of-file does the same.
- Members are pruned after 15 s without contact, but devices rejoin
  automatically after a network blip (with a "Rejoined party" toast) —
  no manual re-joining needed.
- Traffic is plain HTTP on your LAN; the room code is the access token.
  For anything crossing the internet, prefer the standalone relay behind
  TLS (reverse proxy / tunnel) over exposing a raw port.
- With the embedded relay, the party lives in the hosting Kodi — if the
  host quits, the party ends. A standalone relay keeps the room alive
  independently of any member.

## Requirements

- Kodi 19 (Matrix) or later on all devices
- Network path from every member to the relay — the hosting Kodi's port
  (default 8765) on a LAN, or the standalone relay's address for remote
  parties
