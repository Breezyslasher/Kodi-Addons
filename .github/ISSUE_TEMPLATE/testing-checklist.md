---
name: Testing Checklist — Watch Party
about: Complete feature testing checklist for the Watch Party addon and relay
title: "[Testing] Watch Party — Complete Feature Checklist"
labels: testing, service.watchparty
assignees: ''
---

**How to mark:** a GitHub checkbox is only ticked or unticked, so tick it
to mean *"I ran this"* and put the result after the text.

| Written as | Meaning |
|---|---|
| `- [ ] Pause on A → B pauses` | not tested yet |
| `- [x] Pause on A → B pauses` | ran it, works |
| `- [x] Pause on A → B pauses ✗ #42` | ran it, broken — link the bug report |
| `- [x] Pause on A → B pauses ⚠ 3s late` | works, but note the caveat |

Anything left unticked at the end is untested, not passed.

**Build under test**

- Addon version (device A):
- Addon version (device B):
- Addon version (device C):
- Kodi version / platform per device:
- Relay: embedded / standalone / Docker / behind TLS
- Relay image tag or commit:
- Protocol version shown on the dashboard:

**Rig:** A = host, B = guest, C = third device (optional), R = standalone
relay. Keep `kodi.log` open on A and B — addon lines are prefixed
`[Watch Party]`.

---

## Installation & Updates

- [ ] Addon installs from the repository without errors
- [ ] Addon appears under Program add-ons
- [ ] Addon also appears under Services in My add-ons
- [ ] Icon renders in the add-on browser
- [ ] Addon description reads correctly and is not truncated
- [ ] Updating the addon over an older version preserves settings
- [ ] Updating preserves the saved host address and room code
- [ ] Repository update path delivers new versions without manual zips
- [ ] Installed zip contains no `tests/` folder
- [ ] Installed zip contains no `Dockerfile` or `docker-compose.yml`
- [ ] Uninstalling removes the addon without leaving Kodi in a bad state

## Service Lifecycle

- [ ] `service starting` appears in the log at Kodi startup
- [ ] `service stopped` appears in the log at Kodi shutdown
- [ ] Service runs without a party active and consumes no network
- [ ] Ordinary playback with no party active triggers no `[Watch Party]` pushes
- [ ] Ordinary playback with no party active is never paused/seeked by the addon
- [ ] Disabling the addon stops the service
- [ ] Re-enabling the addon starts the service without a Kodi restart
- [ ] Session survives a Kodi restart (or ends cleanly — record which)
- [ ] Updating the addon mid-party does not wedge Kodi
- [ ] No Python exceptions in the log during any of the above

## Main Menu

- [ ] Idle menu shows "Host a party on this device"
- [ ] Idle menu shows "Start a party on a relay server"
- [ ] Idle menu shows "Join an existing party"
- [ ] Idle menu shows "Settings"
- [ ] In-party menu shows "Party status"
- [ ] In-party menu shows "Leave party"
- [ ] In-party menu shows "Settings"
- [ ] Menu switches between idle and in-party sets correctly
- [ ] Cancelling the menu (Back) does nothing
- [ ] "Settings" opens the addon settings dialog

## Host a Party on This Device

- [ ] Party starts and the dialog shows this device's IP and port
- [ ] Dialog shows a 4-character room code
- [ ] Room code avoids confusable characters (no O/0, I/1)
- [ ] A new code is generated each time a party is started
- [ ] Notification confirms the party started
- [ ] Another device can join with the shown address and code
- [ ] Hosting uses the port set in Settings
- [ ] Port already in use → "Cannot host on port …" and no crash
- [ ] Starting a party while already in one is not possible (menu changes)

## Start a Party on a Relay Server

- [ ] Address prompt pre-fills the saved address
- [ ] Accepts a bare IP
- [ ] Accepts `ip:port`
- [ ] Accepts `https://host`
- [ ] Accepts `https://host/path` (reverse-proxy sub-path)
- [ ] Invalid port in the address → clear error, no party created
- [ ] Room code prompt pre-fills the saved code, or suggests a new one
- [ ] Relay is pinged before joining
- [ ] Unreachable relay → "Relay not reachable … keep trying anyway?" prompt
- [ ] Answering No leaves no party active
- [ ] Answering Yes retries in the background
- [ ] Success dialog shows the address and room code to share
- [ ] Address and code are saved to Settings afterwards
- [ ] Another device can join the room just created

## Join an Existing Party

- [ ] Address prompt pre-fills the saved address
- [ ] Room code prompt pre-fills the saved code
- [ ] Joining with correct details succeeds
- [ ] "Joining party…" notification appears
- [ ] Address and code are saved after a successful join
- [ ] Lower-case room code joins the same room as upper-case
- [ ] Whitespace around the code is tolerated
- [ ] Wrong room code → clear "wrong room code" message
- [ ] Invalid code format on an open relay → "must be 3-12 letters or digits"
- [ ] Unknown code on a fixed-room relay → "fixed room codes" message
- [ ] Fixed-room error does not disclose the valid codes
- [ ] Unreachable address → clear failure, no half-joined state
- [ ] Cancelling either prompt aborts cleanly
- [ ] Rejoining the usual party is two confirmations (OK, OK)

## Party Status

- [ ] Shows the room code
- [ ] Shows the relay address
- [ ] Shows connected state
- [ ] Shows every member by name
- [ ] Shows each member's position
- [ ] Shows each member's playing/paused state
- [ ] Shows what is currently playing
- [ ] Shows "Nothing playing yet" when idle
- [ ] Shows the last error when disconnected
- [ ] Reopening reflects changes since last opened
- [ ] Stale status (relay gone) is reported, not shown as live

## Leave Party

- [ ] Leaving stops sync immediately
- [ ] "Left the party" notification appears
- [ ] Local playback is not stopped by leaving
- [ ] The device disappears from other members' status within ~15s
- [ ] Menu returns to the idle set
- [ ] Rejoining after leaving works

## Settings

- [ ] Device name blank → system name is shown to other members
- [ ] Device name set → that name is shown to other members
- [ ] Non-ASCII device name renders correctly everywhere
- [ ] Host port change is used by the next hosted party
- [ ] Saved host address pre-fills the join dialogs
- [ ] Saved room code pre-fills the join dialogs
- [ ] Both saved values can be edited by hand
- [ ] Both saved values can be cleared
- [ ] "Follow party item" ON → auto-opens what the party plays
- [ ] "Follow party item" OFF → never auto-opens
- [ ] "Follow party item" OFF → still follows pause/seek on manually started content
- [ ] "Send my play/pause/seek" ON → local actions drive the party
- [ ] "Send my play/pause/seek" OFF → local actions do not drive the party
- [ ] "Send my play/pause/seek" OFF → this device still follows others
- [ ] "Lock control to me" OFF → anyone can control
- [ ] "Lock control to me" ON → see Control Lock section
- [ ] Drift threshold 1s → corrections are frequent
- [ ] Drift threshold 10s → corrections are rare
- [ ] Settings changes take effect without restarting Kodi

## Embedded Relay (hosting inside Kodi)

- [ ] Relay starts when a party is hosted
- [ ] Log shows the listening port and room code
- [ ] Host joins its own relay over localhost
- [ ] Guests can reach it on the LAN
- [ ] Relay stops when the party is left
- [ ] Relay stops when Kodi quits
- [ ] Host quitting → guests report the party ended / connection lost
- [ ] `http://<host-ip>:8765/` serves the dashboard
- [ ] `/ping` responds with JSON

## Standalone Relay — Startup

- [ ] `python3 relay_standalone.py` starts
- [ ] Startup line names the mode (open/fixed) and the dashboard
- [ ] `--port` changes the listening port
- [ ] `--bind` restricts the interface
- [ ] `WATCHPARTY_PORT` env var works
- [ ] `WATCHPARTY_BIND` env var works
- [ ] Invalid `--room` code is rejected at startup with a clear message
- [ ] Ctrl-C shuts down cleanly
- [ ] SIGTERM (`docker stop`, systemd) shuts down cleanly

## Standalone Relay — Open Mode

- [ ] Any valid code creates a room on first join
- [ ] Second device joining the same code lands in the same room
- [ ] Two different codes are isolated: separate items
- [ ] Two different codes are isolated: separate members
- [ ] Two different codes are isolated: separate playback state
- [ ] Lower-case code joins the same room as upper-case
- [ ] Codes shorter than 3 characters are rejected
- [ ] Codes longer than 12 characters are rejected
- [ ] Codes with spaces or symbols are rejected
- [ ] Rejection reason is shown on the Kodi device
- [ ] Empty rooms are pruned after ~5 minutes
- [ ] Pruning is logged

## Standalone Relay — Fixed Mode

- [ ] `--room CODE` restricts to that room
- [ ] Multiple `--room` flags work
- [ ] `WATCHPARTY_ROOMS=A,B` works
- [ ] Listed codes are joinable
- [ ] Unlisted codes are rejected
- [ ] Rejection message does not reveal the valid codes
- [ ] Fixed rooms are not pruned when empty

## Standalone Relay — Persistence

- [ ] `--state-file` creates the file
- [ ] `WATCHPARTY_STATE` env var works
- [ ] State is written when the party changes
- [ ] State is written on clean shutdown
- [ ] Restart mid-item → item is restored
- [ ] Restart mid-item → position is restored (advanced correctly)
- [ ] Restart mid-item → members auto-rejoin without user action
- [ ] Restart mid-item → playback continues on devices in sync
- [ ] Library ids survive the restart (guests still match their own copy)
- [ ] Queue survives the restart
- [ ] Control lock is *not* restored (stale member id)
- [ ] Buffer hold is *not* restored
- [ ] Restart while nothing is playing → no phantom item
- [ ] Corrupt/unreadable state file → relay still starts, logs a warning
- [ ] No state file configured → relay starts clean after restart

## Docker

- [ ] `docker compose up -d` builds and starts
- [ ] Prebuilt GHCR image pulls and runs
- [ ] Container reports healthy (healthcheck hits `/ping`)
- [ ] Port mapping change works
- [ ] `WATCHPARTY_ROOMS` env var honoured in the container
- [ ] `WATCHPARTY_SHOW_CODES` env var honoured in the container
- [ ] Named volume keeps state across `down` + `up -d`
- [ ] `restart: unless-stopped` brings it back after a host reboot
- [ ] Container runs as a non-root user
- [ ] Multi-arch: runs on the target device (amd64 / arm64 / armv7)
- [ ] Image rebuild after an addon update carries the new protocol

## Remote Access

- [ ] Join over `https://` through a reverse proxy succeeds
- [ ] Join through a cloudflared/ngrok tunnel succeeds
- [ ] Sub-path routing (`https://host/watchparty`) works if configured
- [ ] Relay on a VPS: both devices connect outward, no port forwarding
- [ ] Device behind CGNAT / mobile hotspot can join
- [ ] CDN bot protection does not block the addon
- [ ] If blocked, message reads "HTTP 403 from proxy/CDN, not the relay"
- [ ] Pause propagates in roughly ≤2s over the remote relay
- [ ] Seek propagates in roughly ≤2s over the remote relay
- [ ] Kodi's UI never stalls while using a remote relay
- [ ] Dashboard is reachable over the same tunnel

## Protocol Handshake

- [ ] `/ping` reports the protocol version
- [ ] Join reports the protocol version
- [ ] Older relay + newer addon → "Relay is older than the addon" warning
- [ ] Matching versions → no warning
- [ ] Warning does not prevent the party from working

## Core Playback Sync

- [ ] A plays → B follows and opens the same content
- [ ] Pause on A → B pauses within ~1–2s
- [ ] Resume on A → B resumes
- [ ] Pause on B → A pauses
- [ ] Resume on B → A resumes
- [ ] Seek forward on A → B lands at the same spot
- [ ] Seek backward on A → B lands at the same spot
- [ ] Seek on B → A follows
- [ ] Rapid repeated seeks settle without oscillation
- [ ] Stop on A → B stops
- [ ] Stop on B → A stops
- [ ] Natural end of item → no stray restart on either device
- [ ] Natural end of item → no "can't play" toast
- [ ] Natural end of item → no party-wide stop loop
- [ ] Late joiner opens the item at the party's current position, not 0:00
- [ ] Positions stay within a few seconds over 10+ minutes
- [ ] Pausing locally does not bounce back as a second pause
- [ ] Seeking locally does not bounce back as a second seek
- [ ] Starting new content on A replaces the party item everywhere

## Drift Correction

- [ ] Manual 10s desync on B is corrected within a few seconds
- [ ] Log shows `drift … correcting to …`
- [ ] Corrections are rate-limited (no repeated seeks < 4s apart)
- [ ] No audible stutter loop during correction
- [ ] Drift threshold setting changes correction sensitivity
- [ ] Correction works while paused (position matches on resume)
- [ ] Clock offset between devices does not cause constant corrections
- [ ] Corrections counter increases on the dashboard

## Following Items

- [ ] Addon content follows by `plugin://` path
- [ ] Each device resolves its own stream (own account/session)
- [ ] Resolved playback URLs are never opened on another device
- [ ] Shared network paths (SMB/NFS) are opened as-is
- [ ] Movie matched in the guest's own library by IMDb/TMDb id
- [ ] Episode matched by show/season/episode
- [ ] Song matched by artist/album/title
- [ ] Song matched exactly when MusicBrainz ids are present
- [ ] Matched items open by library id (full metadata, not a bare URL)
- [ ] Failed plugin open falls through to the library copy
- [ ] Failed library open falls through to the shared path
- [ ] Item playable nowhere → one toast, no retry storm
- [ ] Item playable nowhere → the driving device keeps playing
- [ ] After a failure, the *next* item still follows normally
- [ ] Following while already playing something else replaces it cleanly
- [ ] The old item's teardown is not mistaken for a failed open

## Content Types

- [ ] Local file on a shared drive (SMB)
- [ ] Local file on a shared drive (NFS)
- [ ] Library movie, same path on both devices
- [ ] Library movie, different path per device
- [ ] Library TV episode, different path per device
- [ ] Music track, different path per device
- [ ] YouTube addon
- [ ] Other free streaming addon
- [ ] Subscription/DRM addon (Disney+ etc.), both signed in
- [ ] Plex via PKC, same server
- [ ] Plex via PKC, different servers, same title in both libraries
- [ ] Jellyfin / Emby
- [ ] Direct HTTP(S) stream URL
- [ ] Music album queue
- [ ] TV season queue
- [ ] Mixed music + video queue
- [ ] Live TV / PVR (known unsupported — record behaviour, must not break A)

## Queues — Build & Follow

- [ ] A playing from a queue shares it (log: `sharing queue: N entries`)
- [ ] Log reports how many entries carried identity
- [ ] B lines up the same queue (log: `following party queue (N items @ P)`)
- [ ] B starts at the party's position, not the top of the queue
- [ ] Queue with unmappable entries falls back to per-item following
- [ ] Log names the first entry that could not be mapped
- [ ] Queues longer than 100 items are capped, not dropped
- [ ] Single-item "queues" are ignored
- [ ] Metadata is correct on B (title/artist/album, not a filename or URL)
- [ ] Artwork appears on B where the content has it

## Queues — Playback

- [ ] Track/episode ends → both devices advance to the same next item
- [ ] Advance is smooth (no stop/restart stutter on B)
- [ ] Advance produces no "Playing:" toast storm on B
- [ ] A skips to the next item → B follows to the same item
- [ ] A skips backward → B follows
- [ ] A jumps several items ahead → B follows
- [ ] B's playing marker (▶) is on the item actually playing
- [ ] B's queue order matches A's
- [ ] Over 10+ track changes, B never plays a different item than A
- [ ] Stopping the queue stops both devices
- [ ] Reaching the end of the queue behaves the same on both

## Repeat & Shuffle

- [ ] Repeat one on A → both devices loop the same item
- [ ] Repeat all on A → both wrap to the first item together
- [ ] Repeat all wrap is seamless on B (no stop/gap)
- [ ] Repeat off on A → both stop repeating
- [ ] Repeat change propagates within ~2s without waiting for a track change
- [ ] Repeat change does not move the playback position
- [ ] Shuffle on A reorders A's queue including the current item
- [ ] B keeps playing the current item uninterrupted through the shuffle
- [ ] B's queue is reordered to match A (log: `reordering local queue`)
- [ ] B's ▶ marker is still on the playing item after the shuffle
- [ ] The next item after a shuffle is the same on both devices
- [ ] Shuffle off on A propagates the restored order
- [ ] B toggling shuffle locally does not reorder the party
- [ ] B's local shuffle is reconciled back off
- [ ] Repeat/shuffle survive a track change
- [ ] Repeat/shuffle state is visible on the dashboard

## Control Lock

- [ ] Lock enabled on A; A opens an item → lock is taken
- [ ] B's pause is rejected
- [ ] B's seek is rejected
- [ ] B's stop is rejected
- [ ] B sees "controls are locked by the host"
- [ ] A's controls still drive everyone while locked
- [ ] A stopping the item releases the lock
- [ ] A leaving the party releases the lock
- [ ] Lock is shown on the dashboard (HOST · LOCKED badge)
- [ ] Lock is not restored after a relay restart
- [ ] With lock off, any member can drive again

## Buffer Hold

- [ ] B buffering the party item pauses the whole party
- [ ] Party resumes automatically when B recovers
- [ ] Dashboard shows the buffer-hold banner naming B
- [ ] Banner shows how long the hold has lasted
- [ ] B's member row shows the BUFFERING badge
- [ ] A deliberate pause during a hold stays paused after recovery
- [ ] A deliberate play during a hold overrides the hold
- [ ] B buffering *other* content does not pause the party
- [ ] A member that leaves while holding does not freeze the party
- [ ] Hold is not restored after a relay restart

## Resilience

- [ ] B's network dropped for ~30s → "Rejoined party" on recovery
- [ ] After rejoin, B is in sync without manual action
- [ ] Relay stopped for ~30s → "Party connection lost" on devices
- [ ] Devices recover by themselves when the relay returns
- [ ] Members are pruned after ~15s of silence
- [ ] Member join shows "… joined the party" on other devices
- [ ] Member leave shows "… left the party" on other devices
- [ ] Joining a populated room does not produce a burst of join toasts
- [ ] Two members with the same name both appear, nothing crashes
- [ ] Very long device name does not break the UI or dashboard
- [ ] Kodi restart on B mid-party behaves sensibly (record what happens)
- [ ] Rapid leave/join cycling does not corrupt state
- [ ] Relay reboot (host machine restart) recovers with persistence on

## Multiple Members & Rooms

- [ ] Three devices in one party all stay in sync
- [ ] Pause from any of the three reaches the other two
- [ ] Seek from any of the three reaches the other two
- [ ] A late third member joins mid-item at the right position
- [ ] One member leaving does not disturb the other two
- [ ] Buffer hold triggered by the third member works
- [ ] Two separate rooms on one relay do not interfere
- [ ] Dashboard shows both rooms correctly

## Dashboard — Access & Header

- [ ] Bare relay address serves the dashboard
- [ ] `/status` serves the same page
- [ ] Page loads with no console errors
- [ ] Header shows the addon icon (or a drawn mark on the slim image)
- [ ] ONLINE pill with pulsing dot when healthy
- [ ] Uptime counts up correctly
- [ ] Protocol version is correct
- [ ] Room count is correct
- [ ] Poll RTT shows a plausible value
- [ ] "updated" clock advances every ~2s

## Dashboard — Now Playing

- [ ] Title is correct
- [ ] Metadata line shows type/year/artist/album as applicable
- [ ] Queue position shown (e.g. "queue 3/18")
- [ ] Repeat/shuffle shown when active
- [ ] "controls locked to …" shown when locked
- [ ] PLAYING pill while playing
- [ ] PAUSED pill while paused
- [ ] Elapsed time matches actual playback
- [ ] Duration shown when known
- [ ] Progress advances smoothly between polls (no 2s stepping)
- [ ] Progress bar matches the elapsed figure
- [ ] Artwork shown for content with a plain http(s) image
- [ ] Placeholder shown for Plex/token-bearing art (by design)
- [ ] Placeholder shown for local-only art paths
- [ ] Unreachable art falls back to the placeholder, never a broken image
- [ ] Room code is masked (e.g. `B···K`)

## Dashboard — Sync Timeline

- [ ] Server anchor bar is drawn
- [ ] One marker per member on the item
- [ ] Markers move as playback advances
- [ ] In-sync markers are blue
- [ ] Markers past the drift threshold turn amber
- [ ] Signed drift is printed under out-of-sync markers
- [ ] Legend is present and accurate
- [ ] Seeks snap the markers rather than sliding oddly

## Dashboard — Members

- [ ] Member count is correct
- [ ] Each member's name is correct
- [ ] Avatar initials are sensible
- [ ] Position is correct per member
- [ ] Drift value is correct per member
- [ ] HOST · LOCKED badge on the locking member
- [ ] BUFFERING badge while a member caches
- [ ] NO POLL Ns badge on a silent member
- [ ] Stale members are dimmed
- [ ] Buffering row is tinted
- [ ] Members not on the party item are labelled as such

## Dashboard — Health & Other Rooms

- [ ] Poll round-trip bar chart renders
- [ ] Peak RTT caption is correct
- [ ] Commands / min moves when controls are used
- [ ] Corrective seeks count increases when corrections happen
- [ ] Members count matches reality
- [ ] State seq increases on each command
- [ ] Other rooms appear as cards
- [ ] Other room cards show state, item and member count
- [ ] The main room is not duplicated in the rail
- [ ] "No other active rooms" shown when there is only one

## Dashboard — States

- [ ] Empty state shown when no rooms are active
- [ ] Empty state copy is correct
- [ ] Stopping the relay switches to UNREACHABLE
- [ ] Last good snapshot stays on screen, dimmed
- [ ] "Last good snapshot … (Ns ago)" counts up
- [ ] Sweeping bar animates
- [ ] Recovery clears the state without a full page flash
- [ ] Buffer-hold state shows the amber banner and pill

## Dashboard — Responsive & Accessibility

- [ ] Desktop (≥900px) shows two columns
- [ ] 720–900px shows one column with the rail below
- [ ] <720px is edge-to-edge
- [ ] 360px wide: everything legible
- [ ] 360px wide: no horizontal scrolling
- [ ] 360px wide: header stats wrap to two columns
- [ ] Long titles and paths wrap rather than overflow
- [ ] Nothing requires hover to be usable
- [ ] `prefers-reduced-motion` disables the pulse and sweep

## Dashboard — Data & Security

- [ ] `/status.json` returns valid JSON
- [ ] `/status.json` includes protocol and uptime
- [ ] Per-room `now` object carries the item detail
- [ ] Per-member drift, caching, on_item, age are present
- [ ] Room codes are masked in `/status.json` too
- [ ] `WATCHPARTY_SHOW_CODES=1` reveals full codes
- [ ] No member ids appear anywhere in the page or JSON
- [ ] No media-server tokens appear anywhere in the page or JSON
- [ ] Dashboard is read-only — nothing controls playback
- [ ] `/ping` still returns JSON for health checks
- [ ] Unknown paths return a JSON 404

## Performance & Resources

- [ ] Idle CPU with no party is negligible (check on the weakest device)
- [ ] CPU during a party is acceptable on a Raspberry Pi
- [ ] Kodi UI stays responsive while a party is active
- [ ] Kodi UI stays responsive with a remote/slow relay
- [ ] Navigating menus during playback is not delayed
- [ ] A 50+ item queue builds without a noticeable stall
- [ ] A 50+ item queue reorders without a noticeable stall
- [ ] Memory does not grow visibly over a long party
- [ ] Log volume is reasonable (no per-second spam at default level)

## Regression Tests (previously fixed bugs)

- [ ] Guest's failed playback does not stop playback on the host
- [ ] A failed item is not retried in a loop
- [ ] Addon streams share the plugin path, not the host's resolved URL
- [ ] YouTube content plays on the guest
- [ ] Disney+/SlyGuy content plays on the guest
- [ ] Guest's auto-open is not re-announced back to the party
- [ ] Host does not restart playback when the guest starts playing
- [ ] Pressing play with a remote relay does not freeze Kodi
- [ ] Slow relay does not stall the Kodi interface
- [ ] Party stop only stops devices playing the party's item
- [ ] Buffering guest does not permanently pause the party
- [ ] Music shows real title/artist, not `file.m4a`
- [ ] Queue syncs for Plex music (https entries with library matches)
- [ ] Follower's natural track end does not stop the party
- [ ] Shuffle does not leave the guest's ▶ marker on the wrong row
- [ ] Guest never jumps to a stale queue position (wrong song)
- [ ] Rewritten stream tokens do not cause spurious re-opens
- [ ] Guest auto-rejoins after being pruned, without user action
- [ ] Relay root path serves the dashboard, not JSON
- [ ] `/status.json` never exposes member ids

## Notes / Observations

<!-- Anything odd, timings, device-specific behaviour, screenshots -->

## Summary

- Cases passed:
- Cases failed (list ids/lines):
- Blocking issues opened:
- Verdict: ship / fix first
