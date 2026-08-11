---
name: Smoke Test — Watch Party
about: Quick per-build pass — run this after every change, before shipping
title: "[Smoke] Watch Party — <version>"
labels: testing, service.watchparty
assignees: ''
---

| Mark | Meaning |
|---|---|
| ✓ | Working |
| ✗ | Broken — open a bug report and link it here |
| ⚠ | Works with caveats — note them inline |
| `- [ ]` | Untested |

**~20 minutes, two devices.** Everything here has broken at least once.
For a full sweep use *Testing Checklist — Watch Party* instead.

- Addon version (A / B):
- Relay: embedded / standalone / Docker / behind TLS · protocol:
- Content used for the sync block:

---

## 1. It starts (2 min)

- [ ] Both devices show the addon and its menu opens
- [ ] A hosts or starts a party; B joins with the address + code
- [ ] Both appear in *Party status* on both devices
- [ ] Relay's address in a browser shows the dashboard
- [ ] No Python exceptions in `kodi.log` on either device

## 2. Core sync (5 min)

- [ ] A plays something → B follows and opens the same content
- [ ] Pause on A → B pauses
- [ ] Resume on A → B resumes
- [ ] Pause on B → A pauses (unless control is locked)
- [ ] Seek on A → B lands at the same spot
- [ ] Stop on A → B stops
- [ ] Item plays to its natural end → no restart, no toast, no stop loop
- [ ] Kodi's UI stays responsive throughout (especially on a remote relay)

## 3. Queue behaviour (5 min)

Play an album or a season, not a single item.

- [ ] B lines up the same queue (`following party queue (N items @ P)`)
- [ ] A track ends naturally → both advance to the same next item
- [ ] Advance is smooth on B — no stop/restart stutter
- [ ] A skips a track → B follows to the same track
- [ ] B's ▶ marker is on the item actually playing
- [ ] Titles/artists are real, not filenames or URLs
- [ ] A toggles shuffle → B keeps playing, reorders, next track matches A
- [ ] A sets repeat one → both loop the same item
- [ ] Over ~10 track changes, B never plays a different item than A

## 4. Recovery (3 min)

- [ ] Drop B's network ~30s → "Rejoined party" and back in sync by itself
- [ ] Content missing on B → one toast, A keeps playing, no retry storm
- [ ] A member leaving is noticed by the others
- [ ] Restart the relay (if standalone with persistence) → party resumes

## 5. Dashboard (3 min)

- [ ] Now playing, elapsed and progress match reality
- [ ] Member rows show sensible positions and drift
- [ ] Artwork shows, or a clean placeholder — never a broken image
- [ ] Room codes are masked
- [ ] No `X-Plex-Token` / `api_key` anywhere in `/status.json`
- [ ] Readable at phone width

## 6. Regression list (2 min — mostly observed during the above)

Each line is a bug that shipped once.

- [ ] Guest's failed playback does not stop playback on the host
- [ ] A failed item is not retried in a loop
- [ ] Addon streams share the plugin path, not the host's resolved URL
- [ ] Guest's auto-open is not re-announced back to the party
- [ ] Host does not restart playback when the guest starts playing
- [ ] Pressing play with a remote relay does not freeze Kodi
- [ ] Party stop only stops devices playing the party's item
- [ ] Buffering guest does not permanently pause the party
- [ ] Follower's natural track end does not stop the party
- [ ] Shuffle does not leave the guest's marker on the wrong row
- [ ] Guest never jumps to a stale queue position (wrong song)
- [ ] Rewritten stream tokens do not cause spurious re-opens
- [ ] Music shows real title/artist, not `file.m4a`
- [ ] Relay root path serves the dashboard, not JSON

## 7. Automated

- [ ] `python3 -m unittest discover service.watchparty/tests` passes

---

**Verdict:** ship / fix first

**Notes:**
