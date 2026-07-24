# Watch Party

Synchronized playback across Kodi devices — a [SyncLounge](https://github.com/synclounge/synclounge)-style
watch party, but native to Kodi. One device hosts a party, others join with a
room code, and play / pause / seek are mirrored to everyone in real time with
automatic drift correction.

No external server, no cloud service: the hosting Kodi runs a tiny embedded
relay that all members (including the host itself) talk to over HTTP.

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
`ip:port` and the room code.

Then just play something — everyone follows. Any member can pause, seek or
resume for the whole party (this can be turned off per device, see below).
*Party status* shows who's in the room, what's playing, and each member's
position.

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
- Traffic is plain HTTP on your LAN (or a VPN/tunnel for remote friends);
  the room code is the access token. Don't expose the port directly to the
  internet.
- The relay lives in the hosting Kodi — if the host quits, the party ends.

## Requirements

- Kodi 19 (Matrix) or later on all devices
- Network connectivity between guests and the host's port (default 8765)
