# Apple TV for Kodi (experimental)

Sign in with your Apple ID and browse **Apple TV+ Originals** and your
**iTunes movie library** in Kodi, with playback through **InputStream Adaptive**
using **Widevine** DRM.

> ⚠️ **Experimental.** Read the *Reality check* and *Current status* sections
> before installing. This addon depends entirely on Apple's private,
> undocumented services and on Kodi's Widevine support. It is a working
> foundation to test and iterate on, not a guaranteed plug-and-play experience.

## Current status

Reconstructed from a real `tv.apple.com` browser capture:

| Piece | Status |
|-------|--------|
| Apple ID sign-in + two-factor (device or SMS) | ✅ Matches the real web flow (authorize → SRP with *no-username-in-x* + hashcash → 2FA → trust) |
| Browse Apple TV+ Originals / search | ✅ Real shelf titles, posters, and full item lists |
| Playback prepare (manifest `t=`, `media-user-token`) | ✅ Scraped from the server-rendered title page (`hlsUrl`/`userToken`) |
| Widevine licence exchange (`fpsRequest`) | ✅ Local proxy wraps the challenge in Apple's JSON envelope |
| HLS + Widevine playback via InputStream Adaptive | ✅ Wired with manifest/segment auth headers, service certificate, licence proxy |
| Apple-ID login → `tv.apple.com` media session hand-off | ⏳ The store-login step is still being reproduced; if the title page has no `hlsUrl`, sign-in didn't carry through — paste your `media-user-token` (and a manifest URL) in Advanced settings to test end-to-end meanwhile |

Sign-in and browsing are expected to work. Playback works automatically **if**
the Apple-ID login carries through to a `tv.apple.com` media session; that
hand-off is the last piece under construction, with a manual paste fallback.

## Reality check — please read

Apple protects Apple TV+ and iTunes video with DRM, and **which** DRM depends
on the client:

| Client | DRM used | Works in Kodi? |
|--------|----------|----------------|
| iPhone / iPad / Mac / Apple TV / Safari | **FairPlay** | ❌ Kodi cannot decrypt FairPlay |
| Android, Android TV, Chrome/web | **Widevine** | ✅ Kodi decrypts Widevine via InputStream Adaptive |

This addon works by **mimicking the Apple TV web/Android client**, so Apple
serves **Widevine** streams that Kodi *can* decrypt — the same technique the
Netflix and Disney+ Kodi addons use.

Two hard limits remain, and neither can be coded away:

1. **Standard definition only.** Kodi ships the **software Widevine L3** CDM.
   Apple (like most services) restricts L3 to SD — expect roughly 480–540p.
   Real HD/4K needs the hardware **Widevine L1** DRM that only exists on
   Apple's own devices and certified TVs.
2. **Apple gives no public API.** Sign-in, two-factor auth, the catalogue and
   the playback/licence endpoints are all reverse-engineered from Apple's web
   app. Apple changes these deliberately to break unofficial clients, so parts
   of this addon **will need updating over time**, and some may need adjusting
   before they work at all against your account/region.

If you want full-quality Apple TV+ on your TV, use a real Apple TV app on
supported hardware. This addon exists for the "browse and watch in Kodi"
use case, accepting the SD/reliability trade-offs above.

## Features

- Apple ID sign-in with two-factor authentication (SRP-6a web flow)
- Browse Apple TV+ Originals (shelves of shows and movies)
- Browse Movies genres
- Browse your iTunes purchased/rented movie library (when signed in)
- Search the Apple TV catalogue
- Widevine playback wired through InputStream Adaptive

## Requirements

- **Kodi 19 (Matrix) or later**
- **InputStream Adaptive** (bundled with most Kodi builds)
- **Widevine CDM** — the addon uses *InputStream Helper* to install it
  automatically on supported platforms (x86/ARM Linux, Android, Windows).
  Widevine is **not** available on some platforms (e.g. iOS, and Apple silicon
  without the Android runtime).
- An **Apple ID** with an active Apple TV+ subscription and/or iTunes purchases

## Installation

Install from the Breezyslasher repository, or from zip:
`plugin.video.appletv-<version>.zip`.

## First run

1. Open the addon → **Sign in with Apple ID**.
2. Enter your Apple ID email and password.
3. If prompted, enter the **six-digit code** that appears on your trusted Apple
   devices.
4. Browse **Apple TV+ Originals**, **Movies**, **My iTunes Library**, or
   **Search**, and press play.

## Settings

- **Storefront ID** — `143441` is the United States. Change for other regions.
- **Locale** — e.g. `en-US`, `en-GB`.
- **OAuth widget key override** — advanced. The key that identifies the Apple TV
  web client to Apple's sign-in service. Leave blank to use the built-in
  default; set it only if sign-in stops working and you have captured a current
  key from a browser session at `https://tv.apple.com`.

## Troubleshooting & helping it improve

Because Apple's endpoints are private, the most useful thing when something
fails is a Kodi debug log. Enable **Settings → System → Logging → Enable debug
logging**, reproduce the problem, and look for `[plugin.video.appletv]` lines —
the addon logs the raw shapes of Apple's responses when it can't map them, which
is exactly what's needed to correct the catalogue or playback parsing.

Common cases:

- **Sign-in fails immediately** — Apple may have rotated the OAuth widget key,
  or changed the SRP flow. Capture a current key (see settings) or open an issue
  with the logged status codes.
- **"Playback could not be resolved"** — the title may be FairPlay-only for your
  session, region-locked, or the playables response shape changed. The raw
  response is logged.
- **Black screen / licence error** — usually a Widevine CDM issue; let
  InputStream Helper install/repair it, and confirm your platform supports
  Widevine.

## Legal

Not affiliated with or endorsed by Apple Inc. Apple, Apple TV, Apple TV+ and
iTunes are trademarks of Apple Inc. Use only with your own account and content
that you are entitled to access. This addon does not circumvent DRM — playback
relies on the standard Widevine CDM decrypting content for an authenticated,
licensed session, exactly as a browser would.

## License

GPL-3.0-or-later
