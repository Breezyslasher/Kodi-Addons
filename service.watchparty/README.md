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
- Commands you triggered yourself are never re-applied to you (echo
  suppression), so pausing locally doesn't bounce back as a second pause.

## Usage

**Host:** Add-ons → Watch Party → *Start a party*. Kodi shows your address
and a 4-letter room code.

**Guests:** Add-ons → Watch Party → *Join a party*, enter the host's
`ip:port` (or a relay URL like `https://party.example.com`) and the room
code. Both are remembered and pre-filled next time.

Then just play something — everyone follows. Any member can pause, seek or
resume for the whole party (this can be turned off per device, see below).
*Party status* shows who's in the room, what's playing, and each member's
position.

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
docker run -d -p 8765:8765 --restart unless-stopped ghcr.io/breezyslasher/watchparty-relay:latest
```

Or build it yourself from this addon's folder:

```
docker compose up -d
# or
docker build -t watchparty-relay . && docker run -d -p 8765:8765 --restart unless-stopped watchparty-relay
```

- **Open mode** (default): any room code creates a room on first join —
  SyncLounge-style. Rooms empty for 5 minutes are pruned.
- **Fixed mode** (`--room CODE`, repeatable, or `WATCHPARTY_ROOMS=A,B`):
  only the listed rooms exist; everything else is rejected.

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
| Follow party item | on | Automatically open whatever the party plays |
| Send my play/pause/seek | on | Off = follow-only mode (a "viewer" that can't drive the party) |
| Drift threshold | 3 s | How far out of sync before a corrective seek |

## Notes & limitations

- **File paths must be resolvable on every device.** Library items on a
  network share (NFS/SMB), HTTP streams, and addon `plugin://` URLs that all
  devices have installed work great. A file that only exists locally on the
  host (`/home/me/movie.mkv`) won't open on guests — turn off *Follow party
  item* on guests and start the same content manually; pause/seek sync still
  applies once file playback has begun.
- Stopping playback stops the party's shared item for everyone (like taking
  the disc out). Natural end-of-file does the same.
- Members are pruned after 15 s without contact; guests reconnect by
  re-joining.
- Traffic is plain HTTP on your LAN; the room code is the access token.
  For anything crossing the internet, prefer the standalone relay behind
  TLS (reverse proxy / tunnel) over exposing a raw port.
- With the embedded relay, the party lives in the hosting Kodi — if the
  host quits, the party ends. A standalone relay keeps the room alive
  independently of any member.

## Requirements

- Kodi 19 (Matrix) or later on all devices
- Network connectivity between guests and the host's port (default 8765)
