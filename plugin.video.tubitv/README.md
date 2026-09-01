# TubiTV for Kodi

Browse and watch [Tubi](https://tubitv.com/) — free, ad-supported films and TV
shows — from inside Kodi.

This is a fork of [Lunatixz's](https://github.com/Lunatixz) `plugin.video.tubitv`,
kept here so the sign-in can be maintained as Tubi changes its API.

## Features

- Browse the Tubi categories, with paging through long ones
- Home, which is Tubi's own screen, Continue Watching and My List rows included
  — see [Home and the category list](#home-and-the-category-list)
- Related titles and trailers on every film and series
- Add to and remove from My List, and like or dislike a title
- Kids mode, and Tubi's own English, Spanish or French metadata
- Tubi's linear channels as Live TV, optionally in the Kodi TV guide
- Search the Tubi catalogue
- Series listed by season, with episode numbers, cast, plot and artwork
- Add a film or a whole show to the Kodi library as `.strm` files
- Optional Tubi account sign-in
- PseudoTV Live recommendation service

## Which endpoints this uses

Tubi retired the `/oz` endpoints the addon used to browse with. Everything now
goes through the API the Tubi site itself uses, and every call authenticates
with a bearer token — the signed-in user token when there is one, the anonymous
device token otherwise:

| Purpose | Endpoint |
|---|---|
| Category list | `tensor-cdn…/api/v1/browse_list` |
| Category contents, paged | `tensor-cdn…/api/v7/containers/{id}` |
| Search | `search…/api/v3/search` |
| Seasons of a series | `content-cdn…/api/v3/series/{id}/episodes` |
| Title, metadata and streams | `content-cdn…/api/v3/content` |
| Linear channel line-up | `tensor-cdn…/api/v2/epg` |
| Live programme guide | `epg-cdn…/content/epg/programming` |
| Personalised home rows | `tensor-cdn…/api/v8/homescreen` |
| My List | `user-queue…/api/v2/queues` |
| Related titles | `autopilot-cdn…/api/v3/related` |
| Resume reporting | `lishi…/api/v2/view_history` |
| Saving and unsaving a title | `user-queue…/api/v2/queues` |
| Part-watched titles, and forgetting one | `lishi…/api/v2/view_history` |
| Liking and disliking | `account…/user/preferences/rate` |

## Home and the category list

The addon's root is `browse_list`, Tubi's full category list, and Home is
`homescreen`, the rows Tubi's own home screen shows. They overlap heavily but
they are not the same list, and neither contains the other. Counting distinct
row titles per capture, English, kids mode off:

| Capture | Categories | Home rows | Home rows not in the category list |
|---|---|---|---|
| session A | 110 | 40 | 6 |
| session B | 130 | 47 | 4 |
| session C | 130 | 70 | 17 |

What Home has that the category list does not is the personal and the
time-sensitive: Continue Watching, My List, Watch Next, Recommended, and rows
Tubi assembles for the moment. The category list is the whole catalogue, in
Tubi's own order, and it is the longer of the two.

Home is fetched the way the site fetches it — the first seven rows, then a
second call from the cursor it hands back asking for every row after them.
Only the first call was being made, which is why Home used to show six or
seven rows against the root's hundred and more, and why it looked like a
short duplicate of the root rather than a different list.

## Playback and DRM

Tubi encrypts some titles and not others, so the addon handles both. It asks
for `hlsv6`, `hlsv3` and the Widevine stream, then prefers whichever clear
stream is on offer, choosing the highest resolution and H.264 over H.265 since
more devices can decode it.

`hlsv3` is in that list because some titles have no clear `hlsv6` at all —
their only unencrypted rendition is an `hlsv3` 480p sitting beside encrypted
720p ones. Tubi's own web player takes exactly that stream on those titles and
never requests a licence. Of the 404 non-live titles seen across the captures,
59 carry DRM, and 26 of those are of this shape; without `hlsv3` the only thing
left to pick for them was a 720p rendition no software CDM can play.

When only an encrypted stream is available, playback goes through
`inputstream.adaptive` with `com.widevine.alpha` and the title's own license
server — which needs a working Widevine CDM on the device. Clear titles play
without any of that. The anonymous device token is enough for encrypted
playback too: a guest token with `is_guest: true` and no `user_id` was seen
carrying a Widevine licence through to a decoded picture.

Across every capture taken, 70 titles carry DRM, and the HDCP grade tracks the
resolution rather than the title — without a single exception in 810
renditions:

| height | `hdcp_version` | renditions |
|---|---|---|
| 576 | `hdcp_disabled` | 303 |
| 720 | `hdcp_v1` | 726 |
| 1080 | `hdcp_v1` | 84 |

A software CDM — which is all a desktop, Flatpak or LibreELEC Kodi has —
cannot play the protected kind. Measured on Rush Hour with Widevine CDM
4.10.3050.0, which reports Widevine level 3, the 576p rendition plays and the
720p one does not. Kodi 22 with inputstream.adaptive 22.3.20 names the reason:

```
CurlFile::Open - https://license.adrise.tv/challenge?…&drm_token=…"hdcp":"v1"…
OnKeyStatusChangeNotify: KID b9a568f6…, status OUTPUT_NOT_ALLOWED
License update successful
KID b9a568f6… not found in DRM license
InitializeSession: Failed to initialize a DRM session for the stream
OpenStream - Unsupported stream 1001. Stream disabled.
```

The licence request succeeds. The key it returns is marked
`OUTPUT_NOT_ALLOWED`, so no usable key reaches the decoder. Kodi 21 with ISA
21.5.22 does the same in less obvious words — key status 3, `Single decrypt
failed, secure path only`, then `kNoKey` on every frame until the stream ends.
So the addon prefers the HDCP-free rendition by default.

Tubi issues both renditions of a title against the same licence session — the
same `external_id` — and the two tokens differ only in their output policy:
`hdcp: v1` with `analog_out: cgms_required` for 720p, against `hdcp: disabled`
with `analog_out: enabled` for 576p. `wv_security_level` and
`pr_security_level` are identical on both, so the HDCP grade is the only thing
separating the rendition that plays from the one that does not.

inputstream.adaptive's own **Ignore HDCP status** makes no difference: it
governs whether ISA filters renditions by HDCP itself, not whether the licence
server will issue a usable key.

Turn on **Allow HDCP protected streams** in the Playback settings if your device
has hardware DRM (many Android TV boxes and Shields do) and you would rather
have the higher quality rendition. It is a 720p-against-576p choice on the
titles that have no clear rendition, and changes nothing anywhere else, since a
clear stream is preferred before HDCP is considered at all.

The licence request is handed to inputstream.adaptive through `drm_legacy` on
version 21 and later, and through the older `license_type`/`license_key`
properties on builds that predate it — those are deprecated upstream but remain
the only ones Kodi 19 and 20 understand.

## Live TV and IPTV Manager

Tubi carries around 150 linear channels — 177 when first captured, 147 as
delivered to IPTV Simple most recently. They show up under **Live TV** in the
addon, and they are all unencrypted plain HLS, so they need no CDM.

The addon also implements the
[IPTV Manager](https://github.com/add-ons/service.iptv.manager/wiki/Integration)
integration, which puts those channels into Kodi's own TV section with a
programme guide roughly two days deep. Install IPTV Manager and it will pick
this addon up on its own — the **IPTV Manager** setting is on by default and can
be switched off if you would rather keep the channels inside the addon.

The channel line-up is served as JSON-STREAMS and the guide as JSON-EPG, both
written back over the callback socket IPTV Manager supplies.

The callback socket is opened before the document is built. IPTV Manager gives
an addon ten seconds to call back and then waits as long as the connection
stays open, and building the guide takes one request per batch of channels —
comfortably more than ten seconds — so connecting first is what keeps the guide
from arriving empty.

## Adding to the Kodi library

**Add Movie To Library** and **Add Show To Library** write `.strm` files — one
per film, one per episode — that play back through this addon.

They go under this addon's own profile directory:

```
userdata/addon_data/plugin.video.tubitv/movies/<Title>/<Title>.strm
userdata/addon_data/plugin.video.tubitv/shows/<Series>/S01E01  <Title>.strm
```

The path is logged each time, at debug level, so a debug log says where a title went.

Kodi does not show them until that folder is added as a video source —
Settings → Media → Library → Videos → Add videos, pointed at `movies` as
Movies and `shows` as TV shows. Until then the export looks like it did
nothing: the scan it kicks off finds no source covering the folder and
finishes in a millisecond.

t1mlib writes these under `script.module.t1mlib`'s profile instead — a folder
belonging to a dependency, which nobody would think to add as a source. This
addon overrides that so its own exports are somewhere that makes sense.

## Resume

With **Tell Tubi where you stopped watching** on and an account signed in, the
addon reports your position when playback stops, so Continue Watching here and
in Tubi's own apps agree. A background service does the reporting: by the time
playback stops the plugin process that resolved the stream is long gone, so it
leaves what is playing in a window property for the service to pick up.

A film reports as itself. An episode reports as itself too, with its series
named alongside as a `parent_id` — a string, where the content id is a number.
Trailers report nothing.

The same history is read back to place resume points, so a film or episode
started on another device carries on where it left off here. Removing a title
from Continue Watching keys on the history entry's own id rather than the
title's, so the history is read first — the same shape as unsaving from My
List.

## Language

Tubi localises its category names and descriptions from the `Accept-Language`
it is asked with; nothing else about a request changes. The **Language** setting
picks between the three it has been seen serving — `en-US`, `es-MX` and `fr-CA`.

## A note on ids

A series is identified by its content id with a zero in front of it — `03321`
for series `3321` — wherever ratings, related titles or a search's contents map
are concerned. Films use their id unchanged.

## Installation

In Kodi: Settings → Add-ons → Install from repository → Breezyslasher
Repository → Video add-ons → TubiTV.

The one dependency, **t1m Library Routines** (`script.module.t1mlib`), comes
from the official Kodi add-on repository, so Kodi pulls it in by itself.

## Configuration

Signing in is **optional** — Tubi is free to browse without an account, and the
anonymous device token is accepted for browsing and playback alike. An account
is only needed for the personal features, and for titles Tubi gates behind a
sign-in.

- **User Email** / **User Password**: your Tubi account credentials
- **Sign out**: tells Tubi the device is signing out, the same three calls the
  website makes, then forgets the tokens. If Tubi cannot be reached it still
  clears them, since a sign-out has to leave the addon signed out either way.

  Getting this to stick took three goes, because the addon signs in on every
  single plugin invocation. Clearing the cached tokens was not enough — with
  the email and password still stored, the next browse signed straight back in
  and wrote a fresh session over the top, which is why `session.json` looked
  as though it never cleared. Emptying those two settings was not enough
  either: Sign out is an action button *inside the settings dialog*, and that
  dialog keeps its own copy of every field and writes it back when it closes,
  so the credentials reappeared and the addon signed in again 760ms later.

  What holds is a fingerprint of the credentials signed out of, recorded in
  `session.json` where the settings dialog cannot reach it. While it matches
  what is in settings, the addon stays anonymous. Changing either field
  changes the fingerprint and lifts the block by itself, so signing back in is
  just a matter of re-entering the password.

## How signing in works

Tubi retired the old `POST /oz/auth/login/` form post, which is what broke the
addon: the previous code crashed on startup the moment that call stopped
returning a session cookie, so nothing in the addon worked at all.

The addon now performs the same handshake as the Tubi web client:

1. `POST /device/anonymous/signing_key` — sends a PKCE style challenge,
   `base64url(sha256(verifier))`, and gets back a signing key
2. `POST /device/anonymous/token` — the query string is signed
   `TUBI-HMAC-SHA256` (an AWS SigV4 lookalike) with that key, and returns an
   anonymous device token
3. `POST /api/v2/user/login` — the actual sign-in, authorised by the anonymous
   device token from step 2
4. `POST https://tubitv.com/oz/user` — hands the tokens to the web frontend,
   which replies with the `connect.sid` cookie the content calls authenticate
   against

Tokens are cached in `session.json` in the addon profile directory and reused
until they expire, so a browse or a playback no longer repeats the handshake.
Only a digest of the credentials is stored, to notice when the settings change.

A failed sign-in is not fatal any more. The addon reports it once, then carries
on anonymously, and waits 15 minutes before retrying so a wrong password does
not hammer the login endpoint.

## Credits

- Original addon by [Lunatixz](https://github.com/Lunatixz)
- `script.module.t1mlib` by [t1m](https://github.com/learningit), from the
  official Kodi add-on repository

## License

GPL-2.0-or-later — see [LICENSE.txt](LICENSE.txt)
