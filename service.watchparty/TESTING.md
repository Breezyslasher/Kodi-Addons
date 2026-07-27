# Watch Party — testing

**The checklist itself is an issue template.** Open a new issue with
*Testing Checklist — Watch Party* and tick items off there, so each test
pass is recorded, dated and linkable. It covers every feature: 434 checks
across installation, menus, settings, both relay flavours, persistence,
remote access, sync, content types, queues, repeat/shuffle, control lock,
buffer hold, resilience, the dashboard, performance, and a regression
list of every bug fixed so far.

This page is the companion: how to run a pass, and what to watch.

## Automated tests

Relay protocol and engine decision logic, no Kodi required — run by CI on
every PR touching the addon:

```
python3 -m unittest discover service.watchparty/tests
```

Everything else needs real devices, which is what the checklist is for.

## Rig

| | |
|---|---|
| **A** | "host" device — starts most parties |
| **B** | "guest" device |
| **C** | third device, for lock and quorum cases (optional) |
| **R** | standalone relay (Docker/VPS/Pi) for the remote cases |

Keep `kodi.log` open on A and B; addon lines are prefixed
`[Watch Party]`. Record both devices' addon versions, Kodi versions and
platforms, and the relay flavour before you start.

## Suggested order

Bugs are not evenly spread. This front-loads where they have actually
turned up.

1. **Smoke (15 min)** — join a party, the Core Playback Sync section, and
   the dashboard loading. If these fail, stop; everything else builds on
   them.
2. **The risky half (60–90 min)** — Queues, Repeat & Shuffle, the Content
   Types you actually use, Control Lock, Buffer Hold, Resilience. Every
   late bug in this project came from here.
3. **The rest (45 min)** — installation, menus, settings, relay modes,
   remote access, the dashboard sections, performance.
4. **Soak** — leave a long queue running for an evening and check sync in
   the morning. Drift accumulation and state rot only show up here.

Run the **Regression Tests** section on every build; it is the cheapest
insurance in the document.

## Where to look first

Least-proven behaviours, roughly most to least likely to still bite:

1. **Repeat one.** Kodi may loop a track without a fresh AV-start; if it
   does, the announcer never announces the loop and followers can drift.
   Least verified of the repeat modes.
2. **Shuffle order fidelity.** Followers reorder to match the announcer,
   which assumes Kodi reports the *shuffled* order from
   `Playlist.GetItems`. If your build reports the pre-shuffle order,
   followers will reorder to the wrong sequence.
3. **Mixed music + video queues.** One playlist id is chosen for the
   queue; a genuinely mixed queue is untested territory.
4. **Cross-server Plex fallback.** It works by letting the first open
   fail — timing-dependent, and the least travelled path in the follow
   logic.
5. **Cold start into a live party** (Kodi restart or addon update while a
   party runs). The session file survives; what the engine does on that
   first poll is unverified.
6. **Three or more members.** Everything was built and tested with two.
   Buffer hold and control lock with 3+ are logically sound but unproven.
7. **Long queues.** The 100-entry cap and per-entry library matching are
   untested at scale — watch for a stall when a big album is shared or
   reordered.

## Known limits — not bugs, don't chase

- **Live TV / PVR is unsupported.** Channel ids are per-device and a
  broadcast has no meaningful position anchor. Expect noise; only report
  it if it disturbs the *other* device.
- **Playback speed is not synced.** Fast-forward on one device is not
  mirrored; drift correction snaps it back afterwards.
- **Plex artwork shows the placeholder** by design — those URLs carry a
  token, and the dashboard is unauthenticated.
- **A follower's queue window may look different** from the announcer's
  while a queue is active. Only the playing item and the next item have
  to match.
- **Kodi labels this a "Program add-on".** That is what the launchable
  script extension point does; the background service still runs.

## Reporting

One issue per problem, using the *Watch Party — bug report* template.
Quote the checklist line, both devices' versions, the relay flavour, and
the `[Watch Party]` log lines from **both** the device that misbehaved
and the one that was driving. Redact tokens — stream and artwork URLs
often carry one.
