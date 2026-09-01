# Friendly TV for Kodi

Watch your own Friendly TV subscription in Kodi: the live channel lineup, the
programme guide, and the on-demand catalogue, played through InputStream
Adaptive with Widevine DRM.

Not affiliated with or endorsed by Frndly TV. It speaks the same private
endpoints as the web player at `watch.frndlytv.com`; use it with your own
subscription.

## Requirements

- Kodi 20 (Nexus) or newer, with **InputStream Adaptive 20.3.0+**
- A working **Widevine CDM**. `script.module.inputstreamhelper` installs one on
  demand and is listed as an optional dependency; on Android the system CDM is
  used instead.
- A Friendly TV subscription.

## Setting it up

1. Install the addon.
2. Open its settings and enter the email address and password you sign in to
   Friendly TV with, or open the addon and choose **Sign in** to be prompted.
3. That is all. There is no pairing step and no browser round trip.

## What is in the menu

| Entry | What it lists |
|-------|---------------|
| **Live TV** | every channel, labelled `Channel - what is on now` |
| **TV Guide** | channels as folders, each with a day of schedule |
| **Search** | channel names and what is on in the next 12 hours |
| **Home**, **Movies**, **TV**, **My Stuff** | the service's own pages, as it arranges them |

In the guide, only the programme currently on the air can be selected, and
selecting it joins that channel live. The rest of the schedule is information:
Friendly TV offers no catch-up route from a guide entry, so the addon does not
pretend to have one.

### Recording

Any airing in the guide or in search results has **Record...** and **Stop or
delete recording...** on its context menu. Those open the choices the service
itself offers — record this episode, record the whole series, stop, or delete
a series recording — and the confirmation shown is the service's own wording.
Recordings appear under **My Stuff**.

### About Search

This searches **the channel lineup and the guide**, not Friendly TV's full
on-demand catalogue. The service runs catalogue search on a separate API
(`/search/api/v3/`) whose request shape has never been captured — the web
player loads that screen as a lazy chunk, and no capture has opened it — so
implementing it would mean guessing, and a guessed endpoint is worse than an
honest smaller one. What is here searches real data and every result works.

See `docs/frndlytv-protocol.md` for what a capture would need to contain to
turn this into the real thing.

## IPTV Manager

With [IPTV Manager](https://github.com/add-ons/service.iptv.manager) installed,
the lineup and a day of guide can be published into Kodi's own **TV** section
as a PVR source. Turn it on under the addon's *Integration* settings.

## How playback works

Friendly TV's licence server takes a **raw Widevine challenge** and answers
with **raw licence bytes**, with the entitlement carried in a token already
baked into the licence url. That is exactly what InputStream Adaptive speaks,
so ISA posts straight to it.

**There is no licence proxy in this addon** — nothing translates a challenge,
mints a token, or listens on localhost. If you have seen the Apple TV+ or
YouTube TV addons in this repository, that machinery exists because those
services wrap the challenge in a JSON envelope. This one does not, and the
simplest thing that works is the right amount of code.

Streams are AVC up to 720p with AAC audio, and they play on a software (L3)
Widevine CDM — there is no HDCP tier gating to work around.

## Concurrent streams

Friendly TV counts concurrent streams and only frees a slot when a client posts
its poll key back. The addon runs a small background service whose only job is
to do that when playback stops, including when Kodi is closed mid-stream.
Without it an account locks itself out of its own subscription after a few
plays, with "too many devices" and no device actually watching.

## Troubleshooting

Turn on Kodi's debug logging (*Settings → System → Logging*) and look for lines
tagged `[plugin.video.frndlytv]`. The addon logs its platform and ISA version
once per run, which is the first thing worth knowing.

**A stream loads but never decrypts.** The DASH manifests carry no `cenc:pssh`
and no `default_KID`, so ISA has to recover the key id from the init segment's
`tenc` box. That path works, but it is the least-travelled part of this addon.
See `docs/frndlytv-protocol.md` for what a fix would look like.

**"Your subscription does not include this channel."** That is the service's own
answer (`streamStatus.hasAccess`), not a guess by the addon — some channels are
add-on packages.

**Sign-in fails with a message from the service.** Friendly TV returns refusals
as HTTP 200 with the reason in the body; whatever it said is shown verbatim.

## What is not implemented

- **Catalogue search**, for the reason above. The lineup-and-guide search
  stands in for it.
- **Series browse down to a specific episode** — the service's own TV pages are
  listed as it arranges them, but the chain from a season to a playable episode
  has not been captured end to end.

## Documentation

`docs/frndlytv-protocol.md` records the whole protocol as captured, including
what is *not* known and where the gaps are.

## Licence

GPL-3.0-or-later.
