# Friendly TV for Kodi

Watch your own Friendly TV subscription in Kodi: the live channel lineup, the
programme guide, and the on-demand catalogue, played through InputStream
Adaptive with Widevine DRM.

Not affiliated with or endorsed by Frndly TV. It speaks the same private
endpoints as the web player at `watch.frndlytv.com`; use it with your own
subscription.

Verified playing on **Kodi 21.3** (ISA 21.5.22) and **Kodi 22**.

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
| **Search** | Friendly TV's catalogue of shows and films |
| **Home** | the rows Friendly TV puts on its own home screen |
| **Movies**, **TV**, **My Stuff** | the service's own pages, as it arranges them |

In the guide, only the programme currently on the air plays, and playing it
joins that channel live. Friendly TV offers no catch-up from a guide entry, so
selecting a later programme tells you what it is and when it is on rather than
pretending to play it.

Selecting the programme that is on now asks whether to **Play live** or
**Start over**. Those are two different paths to the same endpoint: the
channel's gives the live edge, the programme's own gives it from the
beginning.

Any film or show has **More like this** and **Add to / Remove from Favourites**
on its context menu — only the verb that applies is shown, because the cards say
which state they are in. Favourites are Friendly TV's *My Stuff*, so they show
up there and in its own apps.

A guide airing that belongs to a series also has **Go to show**, which opens
that show's page and its seasons — so you can go from "this is on at 8" to the
whole run of it.

Guide rows carry the synopsis, cast, artwork, episode number and certificate —
none of which is in the schedule itself; it comes from each airing's own
overlay, under the same *Fetch full descriptions* setting as listings.

### Recording

Any airing in the guide or in search results has **Record...** and **Stop or
delete recording...** on its context menu. Those open the choices the service
itself offers — record this episode, record the whole series, stop, or delete
a series recording — and the confirmation shown is the service's own wording.
Recordings appear under **My Stuff**.

### Search

Friendly TV's own catalogue search, with **Shows only / Movies only / Channels
only** filters at the top.

It searches more than titles. A person's name finds what they are in —
"Raymond Burr" returns *Perry Mason* and two of his films, none of which have
his name in the title. A genre word finds that genre — "action" returns
Gunsmoke, NCIS and Bonanza rather than titles containing the word. So the
**Browse by Genre** row on Home works, and any film or show has **Search the
cast...** on its context menu to find what else someone is in.

All results are listed at once rather than behind "Next page" folders. The
addon fetches the first page, learns the total, and pulls the rest together —
which matters, since a genre word can match several hundred titles.

### Films, shows and episodes

A **film** plays in one click. Its card points at a page rather than a stream,
but that page holds a single play button and nothing else, so the addon fetches
it and follows the button instead of making you open a folder to find one item.

A **show** stays a folder, because it genuinely has seasons under it. Shows,
films and episodes are each labelled as such for Kodi, so skins lay them out as
shows and films rather than as anonymous directories, and an episode carries its
own name with its season and episode numbers.

### Descriptions and cast

Friendly TV sends **no synopsis, cast or director with a listing** — across
every captured response those fields are empty on all but a handful of cards.
They exist only on each title's own page.

So that Kodi's **Information** dialog has something to show, the addon fetches
those pages for a listing's films and shows, on a small pool of threads. It is
one request per title, which is a real cost, so it is a setting: *Listings →
Fetch full descriptions and cast*, with a cap on how many titles per listing
(40 by default). Turn it off and listings open faster with titles and artwork
only.

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

Which ISA property carries the DRM depends on the ISA version, and the boundary
is **22.1.5**: newer builds take a JSON `drm` object, older ones the
`license_type`/`license_key` pair. Getting that wrong is not a warning but a
dead stream — ISA 21 ignores the JSON property silently and then refuses the
manifest with `InitializePeriod: Unhandled encrypted stream`. The addon picks by
the installed ISA version and logs which form it wrote.

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

**`Initializing stream with unknown KID!` in the log is normal here**, as is the
`ConvertKidStrToBytes: Cannot convert KID ""` beside it. Friendly TV's manifests
carry no `cenc:pssh` and no `default_KID`, so ISA opens the session without one;
the licence server returns the keys anyway, because the entitlement rides in the
licence url's token rather than in the challenge. Verified playing on Kodi 21
and Kodi 22. Not a fault to chase.

**"Your subscription does not include this channel."** That is the service's own
answer (`streamStatus.hasAccess`), not a guess by the addon — some channels are
add-on packages.

**Sign-in fails with a message from the service.** Friendly TV returns refusals
as HTTP 200 with the reason in the body; whatever it said is shown verbatim.

## What is not implemented

- **Series browse down to a specific episode** — the service's own TV pages are
  listed as it arranges them, but the chain from a season to a playable episode
  has not been captured end to end.

## Documentation

`docs/frndlytv-protocol.md` records the whole protocol as captured, including
what is *not* known and where the gaps are.

## Licence

GPL-3.0-or-later.
