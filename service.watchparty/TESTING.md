# Watch Party — test checklist

A full manual pass over every feature. Cases have stable ids (`WP-nnn`)
so a bug report can point at one — the issue form asks for it.

**Legend:** ✅ works · ⚠️ works with caveats (note them) · ❌ broken · ➖ not
applicable / not tested.

Automated tests cover the relay protocol and the engine's decision logic
(`python3 -m unittest discover service.watchparty/tests`, run by CI on
every PR). This checklist covers what only real devices can prove:
playback, timing and the UI.

---

## How to run this as a bug hunt

You do not have to go top to bottom. Bugs are not evenly spread — this
order front-loads the places they have actually turned up.

**Pass 1 — smoke (15 min).** WP-030, WP-070→WP-076, WP-081, WP-150.
If any of these fail, stop and report; everything else builds on them.

**Pass 2 — the risky half (60–90 min).** §10 queues/repeat/shuffle, §8
content types you actually use, §9 host controls, §11 resilience. This is
where the last five bugs of this project came from.

**Pass 3 — the rest (45 min).** §1–3 UI and settings, §5–6 relay and
remote, §12 dashboard, §13 housekeeping.

**Pass 4 — soak.** WP-174: leave a long queue running for an evening and
check sync in the morning. Slow leaks (drift accumulation, queue state
rot, memory) only show up here.

### Where I would look first

Honest assessment of what is least proven, roughly most to least likely
to still bite:

1. **Repeat-one propagation (WP-128).** Kodi may loop a track without a
   fresh AV-start; if it does, the announcer never announces the loop and
   followers can drift. Least verified of the repeat modes.
2. **Shuffle order fidelity (WP-126/127/135).** Followers reorder their
   queue to match the announcer, which assumes Kodi reports the shuffled
   order via `Playlist.GetItems`. If it reports the pre-shuffle order on
   your build, followers reorder to the wrong sequence.
3. **Mixed-content queues (WP-134).** A queue holding both music and
   video picks one playlist id; a mixed queue is untested territory.
4. **Cross-server Plex fallback (WP-097).** Works by letting the first
   open fail — timing-dependent, and the failure path is the least
   travelled code in the follow logic.
5. **Reboot / update mid-party (WP-006, WP-142).** Session file survives,
   but what the engine does on a cold start into a live party is not
   verified.
6. **Three or more members (WP-147).** Everything was built and tested
   with two; buffer hold and lock with 3+ members are logically sound but
   unproven.
7. **Long queues (WP-133).** The 100-entry cap and the per-entry library
   matching cost are untested at scale — watch for a stall when a big
   album is shared or reordered.

### Known limits — not bugs, do not chase

- **Live TV / PVR (WP-100)** is unsupported: channel ids are per-device
  and the position anchor is meaningless for a broadcast. Expect noise;
  only report it if it breaks the *other* device.
- **Playback speed is not synced.** Fast-forward on one device is not
  mirrored; drift correction snaps it back afterwards.
- **Plex artwork shows the placeholder (WP-163)** by design — those URLs
  carry a token that must not go on an unauthenticated page.
- **Followers' queue windows may differ visually** from the announcer's
  while a queue is active, as long as the playing item and the next item
  match.
- **Kodi labels the addon a "Program add-on"** — that is what the
  launchable-script extension point does; the service still runs.

### Results log

| Date | Build (addon / relay) | Passes run | Failures (case ids) |
|---|---|---|---|
|  |  |  |  |

---

## 0. Rig

You need at least two Kodi devices. Several cases need three, and the
remote cases need a relay reachable from outside your LAN.

| | |
|---|---|
| **A** | "host" device — starts most parties |
| **B** | "guest" device |
| **C** | third device, for lock/quorum cases (optional but recommended) |
| **R** | standalone relay (Docker/VPS/Pi) for the remote cases |

Before starting, record: addon version on each device (Settings →
Add-ons → Watch Party), Kodi version/platform per device, relay image
tag or `relay_standalone.py` commit, and whether the relay is embedded,
LAN standalone, or remote behind TLS.

Keep `kodi.log` open on both A and B — every line the addon writes is
prefixed `[Watch Party]`.

---

## 1. Install & service lifecycle

- [ ] **WP-001** Addon installs from the repository without an error toast.
- [ ] **WP-002** After install, no party is active and Kodi behaves
      normally (no stray pauses/seeks during ordinary playback).
- [ ] **WP-003** `kodi.log` shows `[Watch Party] service starting` once at
      Kodi startup, and `service stopped` on quit.
- [ ] **WP-004** Idle (no party): playing/pausing local media triggers no
      relay traffic and no `[Watch Party]` push lines.
- [ ] **WP-005** Disabling the addon stops the service; re-enabling starts
      it without a Kodi restart.
- [ ] **WP-006** Updating the addon while a party is active: the party
      resumes (or cleanly reports disconnection) rather than wedging Kodi.

## 2. Main menu & session UI

- [ ] **WP-010** Idle menu shows exactly: *Host a party on this device*,
      *Start a party on a relay server*, *Join an existing party*,
      *Settings*.
- [ ] **WP-011** In-party menu shows: *Party status*, *Leave party*,
      *Settings*.
- [ ] **WP-012** *Host a party on this device* shows this device's
      `ip:port` and a 4-character room code; the code avoids
      easily-confused characters (no O/0, I/1).
- [ ] **WP-013** *Start a party on a relay server* pre-fills the saved
      address, accepts `ip`, `ip:port` and `https://host[/path]`, and
      lets you choose the room code.
- [ ] **WP-014** Starting on an unreachable relay prompts
      "Relay not reachable … keep trying anyway?" rather than failing
      silently. Answering *No* leaves no party active.
- [ ] **WP-015** *Join an existing party* pre-fills saved address + code;
      after a successful join both are saved (check Settings).
- [ ] **WP-016** *Party status* lists every member, what's playing, and
      each member's position; it updates when reopened.
- [ ] **WP-017** *Leave party* stops sync immediately; the device stops
      appearing in other members' status within ~15s.
- [ ] **WP-018** Room codes are case-insensitive (`movie` joins `MOVIE`).

## 3. Settings

- [ ] **WP-020** *Device name* blank → system name shown to others; set →
      that name shown.
- [ ] **WP-021** *Host port* change is used by the next hosted party.
- [ ] **WP-022** *Saved host address* / *Saved room code* pre-fill the
      join dialogs and can be edited/cleared by hand.
- [ ] **WP-023** *Follow party item* OFF → this device never auto-opens
      the party's item, but still follows pause/seek once you start the
      same content yourself.
- [ ] **WP-024** *Send my play/pause/seek* OFF → this device follows but
      its own play/pause/seek never move the party ("viewer" mode).
- [ ] **WP-025** *Lock control to me* ON → see §9.
- [ ] **WP-026** *Drift threshold* change takes effect (e.g. 1s makes
      corrections more frequent, 10s far rarer).

## 4. Embedded relay (LAN party)

- [ ] **WP-030** A hosts; B joins with A's `ip:port` + code → both appear
      in *Party status* on both devices.
- [ ] **WP-031** Wrong room code → clear "wrong room code" message, not a
      generic failure.
- [ ] **WP-032** Wrong/unreachable address → clear failure message; no
      half-joined state.
- [ ] **WP-033** Host port already in use → "Cannot host on port …"
      notification, no crash.
- [ ] **WP-034** Host leaves the party → guests report the party ended /
      connection lost rather than hanging.
- [ ] **WP-035** `http://<A-ip>:8765/` in a browser shows the dashboard.

## 5. Standalone relay (LAN & remote)

- [ ] **WP-040** `python3 relay_standalone.py` starts and logs the mode
      and dashboard hint.
- [ ] **WP-041** `docker compose up -d` starts; `docker ps` shows the
      container healthy (healthcheck hits `/ping`).
- [ ] **WP-042** Prebuilt image pulls and runs
      (`ghcr.io/breezyslasher/kodi-watchparty-relay:latest`).
- [ ] **WP-043** Open mode: any 3–12 char alphanumeric code creates a room
      on first join; two different codes are fully isolated (different
      items, members, playback).
- [ ] **WP-044** Open mode: an invalid code (2 chars, spaces, symbols) is
      rejected with "room code must be 3-12 letters or digits" **shown on
      the Kodi device**.
- [ ] **WP-045** Fixed mode (`WATCHPARTY_ROOMS=A,B`): listed codes work;
      any other is rejected with "fixed room codes", and the valid codes
      are **not** disclosed in the error.
- [ ] **WP-046** Empty rooms disappear after ~5 minutes (open mode).
- [ ] **WP-047** Persistence: with a party mid-item, restart the relay
      (`docker compose restart`) → members auto-rejoin and the item and
      position are preserved.
- [ ] **WP-048** Persistence across re-creation: `docker compose down &&
      up -d` (volume kept) → same result as WP-047.
- [ ] **WP-049** No state file configured → relay starts clean after a
      restart (no crash, party simply gone).
- [ ] **WP-050** Relay restart while **nothing** is playing → no phantom
      item, no spurious playback on any device.

## 6. Remote access

- [ ] **WP-060** Join over `https://` through a reverse proxy/tunnel
      (cloudflared, nginx, Caddy) succeeds.
- [ ] **WP-061** A relay behind CDN bot protection still admits the addon
      (User-Agent fix). If blocked, the device shows "HTTP 403 from
      proxy/CDN, not the relay" — *that message means proxy config, not
      the addon*.
- [ ] **WP-062** Relay on a different network entirely (VPS): both
      devices connect outward; no port forwarding on either LAN.
- [ ] **WP-063** Latency: with a remote relay, pause on A appears on B in
      roughly ≤2s.
- [ ] **WP-064** Protocol handshake: run an **older** relay against the
      current addon → "Relay is older than the addon" notification on
      join.
- [ ] **WP-065** Matching versions → no such warning.

## 7. Core playback sync

Run this block once per content type in §8.

- [ ] **WP-070** A plays an item → B follows and opens the same content.
- [ ] **WP-071** Pause on A → B pauses within ~1–2s. Resume likewise.
- [ ] **WP-072** Pause on **B** → A pauses (any member can drive, unless
      locked).
- [ ] **WP-073** Seek forward on A → B lands within a couple of seconds
      of the same spot.
- [ ] **WP-074** Seek backward → same.
- [ ] **WP-075** Stop on A → B stops.
- [ ] **WP-076** Item reaches its natural end on both → no stray restart,
      no "can't play" toast, no party-wide stop loop.
- [ ] **WP-077** Late joiner: C joins mid-item → opens the item and lands
      at the party's *current* position, not from 0:00.
- [ ] **WP-078** Drift correction: on B, seek ~10s away and leave it →
      B is pulled back within a few seconds; log shows `drift … correcting`.
- [ ] **WP-079** Corrections are rate-limited: no rapid seek-loop /
      stutter (watch for repeated `correcting` lines <4s apart).
- [ ] **WP-080** Pausing locally does not bounce back as a second pause
      (echo suppression) — no visible double-pause flicker.
- [ ] **WP-081** **No UI freeze**: with a *remote* relay, pressing play,
      pause and seek keeps Kodi's interface responsive at all times.

## 8. Content types

Repeat §7 for each; note which ones your party actually uses.

- [ ] **WP-090** Local library file on a share both devices can reach
      (SMB/NFS).
- [ ] **WP-091** Library item where each device has its **own copy** at a
      **different path** → follows via IMDb/TMDb id match (movie).
- [ ] **WP-092** Same, TV episode → matched by show/season/episode.
- [ ] **WP-093** Same, music track → matched by artist/album/title.
- [ ] **WP-094** Addon stream both devices have (YouTube) → each device
      resolves its own stream; playback starts on both.
- [ ] **WP-095** DRM/subscription addon both devices are signed into
      (Disney+ etc. via SlyGuy/PKC) → same.
- [ ] **WP-096** Plex via PKC, **same** server → follows by plugin path.
- [ ] **WP-097** Plex via PKC, **different** servers, same title in both
      libraries → first attempt fails, then falls through to the local
      library copy and plays. (Expect a few seconds' delay.)
- [ ] **WP-098** Content genuinely absent on B → one "Can't play the party
      item on this device" toast, **A keeps playing**, no retry storm.
- [ ] **WP-099** After WP-098, A plays something else → B follows normally
      (the failed item is not held against the next one).
- [ ] **WP-100** Live TV / PVR channel — *known unsupported*; record what
      actually happens (expect drift-correction noise; must not break A).

## 9. Host controls

- [ ] **WP-110** *Lock control to me* ON on A; A opens an item → B's
      pause/seek are rejected; B sees the "controls are locked" reason.
- [ ] **WP-111** While locked, A's own controls still drive everyone.
- [ ] **WP-112** A stops the item → lock releases; B can drive again.
- [ ] **WP-113** A leaves the party while locked → lock clears (B is not
      stuck locked out forever).
- [ ] **WP-114** Buffer hold: force B to buffer (throttle its network mid
      playback) → the whole party pauses, and resumes automatically when
      B recovers.
- [ ] **WP-115** During a buffer hold, a deliberate pause by A stays
      paused after B recovers (manual action wins).
- [ ] **WP-116** B buffering **something else** (not the party item) does
      not pause the party.

## 10. Queues, repeat & shuffle

- [ ] **WP-120** A plays an album/season/playlist → B lines up the same
      queue; log shows `following party queue (N items @ P)`.
- [ ] **WP-121** Track/episode ends naturally → **both** advance to the
      same next item, with no stop/restart stutter and no toast.
- [ ] **WP-122** A skips to the next track → B follows to the same track.
- [ ] **WP-123** A skips **backward** / jumps several tracks → B follows.
- [ ] **WP-124** B's playlist window shows the ▶ marker on the song that
      is actually playing, and the queue order matches A's.
- [ ] **WP-125** Metadata: B shows real title/artist/album (not
      `file.m4a` or a URL) in now-playing and in *Party status*.
- [ ] **WP-126** A toggles **shuffle** mid-track → B keeps playing the
      current track uninterrupted, reorders its queue, and the **next**
      track matches A's. Log: `queue order changed — reordering local
      queue`.
- [ ] **WP-127** After a shuffle, B's ▶ marker is still on the playing
      song (not an adjacent row).
- [ ] **WP-128** A sets **repeat one** → both loop the same track.
- [ ] **WP-129** A sets **repeat all** and lets the queue wrap → both wrap
      to the same first track with no gap/stop on B.
- [ ] **WP-130** A turns repeat **off** → both stop repeating.
- [ ] **WP-131** Repeat/shuffle changes propagate within ~2s, **without
      waiting for a track change**, and without the position jumping.
- [ ] **WP-132** B toggling shuffle locally does not reorder the party;
      B is reconciled back.
- [ ] **WP-133** A long queue (50+ items) still syncs; note any lag when
      the queue is built or reordered.
- [ ] **WP-134** A queue containing an item B **can't** play → B falls
      back to per-item following instead of refusing the whole queue.
- [ ] **WP-135** **Wrong-song check** (the regression this fix targets):
      over 10+ track changes, including manual skips and a shuffle
      toggle, B never plays a different song from A.

## 11. Resilience

- [ ] **WP-140** Kill B's Wi-Fi for ~30s mid-party, restore → B shows
      "Rejoined party" and resumes in sync without manual re-joining.
- [ ] **WP-141** Kill the *relay* for ~30s (remote case) → devices report
      "Party connection lost", then recover when it returns.
- [ ] **WP-142** Reboot B entirely mid-party → after Kodi restarts it is
      *not* silently in a party (expected: session persists — record what
      actually happens).
- [ ] **WP-143** Member join/leave notifications appear on the other
      devices ("… joined the party" / "… left the party").
- [ ] **WP-144** Joining a party that already has members produces no
      burst of join toasts (first poll is a silent baseline).
- [ ] **WP-145** Two members with the **same device name** → both listed,
      nothing crashes.
- [ ] **WP-146** Non-ASCII device name (accents, emoji) → renders
      correctly in *Party status* and on the dashboard.
- [ ] **WP-147** Three+ devices in one party: all stay in sync; pause from
      any of them reaches all others.

## 12. Dashboard (`/` on the relay)

- [ ] **WP-150** The relay's bare address (`http://ip:8765`) shows the
      dashboard, not JSON. `/status` shows the same page.
- [ ] **WP-151** Header: ONLINE pill, uptime, protocol `v3`, room count,
      poll RTT, updating clock.
- [ ] **WP-152** Now playing: title, metadata line, state pill
      (▶ PLAYING / ❙❙ PAUSED), elapsed/duration.
- [ ] **WP-153** Progress advances smoothly between polls (no 2s steps)
      and matches the actual playback position.
- [ ] **WP-154** Sync timeline: one marker per member; markers past the
      drift threshold turn amber with a signed drift value.
- [ ] **WP-155** Member rows: correct name, position, drift; `HOST ·
      LOCKED`, `BUFFERING`, `NO POLL Ns` badges appear in the right
      circumstances.
- [ ] **WP-156** Relay health rail: commands/min, corrective seeks,
      members, state seq all move plausibly.
- [ ] **WP-157** A second room appears under OTHER ROOMS (and is not
      duplicated in the main column).
- [ ] **WP-158** Empty state ("No active rooms") when no party is running.
- [ ] **WP-159** Stop the relay with the page open → UNREACHABLE state,
      last snapshot kept on screen dimmed, sweeping bar; recovers by
      itself when the relay returns.
- [ ] **WP-160** Buffer hold shows the amber banner naming the member and
      how long the hold has lasted.
- [ ] **WP-161** Room codes are masked (`B···K`) by default.
- [ ] **WP-162** `WATCHPARTY_SHOW_CODES=1` reveals full codes.
- [ ] **WP-163** Artwork: appears for content whose art is a plain
      http(s) image; **placeholder** for Plex/token-bearing art (by
      design) and for local-only paths — never a broken image icon.
- [ ] **WP-164** Phone width (360px): everything legible, no horizontal
      scrolling, stats wrap to two columns.
- [ ] **WP-165** The dashboard is read-only — nothing on it can control
      playback.
- [ ] **WP-166** `/status.json` returns valid JSON with `protocol`,
      `uptime`, and per-room `now`/`members`.
- [ ] **WP-167** No member ids appear anywhere in `/status.json` or the
      page (names only).

## 13. Housekeeping

- [ ] **WP-170** Automated suite passes:
      `python3 -m unittest discover service.watchparty/tests`.
- [ ] **WP-171** The addon zip built by CI contains no `tests/`,
      `Dockerfile` or `docker-compose.yml`.
- [ ] **WP-172** Repo install/update path works: bump lands on devices via
      the repository without manual zip installs.
- [ ] **WP-173** Idle CPU on a Raspberry Pi with no party: negligible.
- [ ] **WP-174** An overnight party left running is still in sync (or has
      failed loudly) the next morning.

---

## Reporting

One issue per problem, using the **Watch Party bug report** template.
Always include the case id (e.g. `WP-126`), both devices' addon versions,
the relay flavour, and the `[Watch Party]` log lines from **both** the
device that misbehaved and the one that was driving.
