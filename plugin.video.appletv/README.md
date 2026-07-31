# Apple TV for Kodi (experimental)

Sign in with your Apple ID and browse **Apple TV+ Originals** and your
**iTunes movie library** in Kodi, with playback through **InputStream Adaptive**
using **Widevine** DRM.

> ⚠️ **Experimental, and requires Kodi 22 with InputStream Adaptive 22.**
> Playback works, in standard definition only — see *Current status* for why.
> Everything here is reconstructed from real `tv.apple.com` browser captures;
> Apple documents none of it and can change it at any time.

## Current status

| Piece | Status |
|-------|--------|
| Apple ID sign-in + two-factor (trusted device or SMS) | ✅ Matches the real web flow (authorize → SRP with *no-username-in-x* + `X-Apple-HC` hashcash → 2FA → trust) |
| Apple-ID login → `media-user-token` (store login) | ✅ `POST auth.tv.apple.com/auth/v1/web` mints it automatically after sign-in |
| Browse Apple TV+, MLS and Formula 1 tabs, search | ✅ Real shelf titles, every shelf on a tab, every item in a shelf |
| Artwork | ✅ Tall posters and wide stills each requested at their own aspect ratio, so neither is cropped |
| Trailers and bonus content | ✅ Context menus on movies and shows; taken from the title's own detail response |
| Sports clips (highlights, interviews, key plays) | ✅ Played from the stream the shelf lists inline — Apple gives these no detail endpoint |
| Show → episode browsing | ✅ Shows open to their episode list with S/E metadata |
| Playback resolve (`/uts/v3/movies/{id}`, `/episodes/{id}`) | ✅ Returns the `hlsUrl` for the entitled feature (not the trailer) |
| Widevine licence exchange (`fpsRequest`) | ✅ Local proxy wraps the challenge in Apple's JSON envelope; the returned key id always matches the requested one |
| Key id delivery (`KEYID` + `tenc` patching) | ✅ Apple omits both; the proxy recovers the key id from the PSSH and supplies it |
| **Audio decryption and playback** | ✅ Works |
| **Video decryption and playback** | ✅ Works on Kodi 22 + ISA 22, standard definition only |

### Requirements and limits for playback

The addon requires **Kodi 22 with InputStream Adaptive 22 or newer**. Playback
relies on the `inputstream.adaptive.drm` property, which the 21.x series does
not have, so Kodi 21 is not supported.

Three constraints decide which stream is played, all enforced automatically by
the manifest proxy:

1. **Standard definition only.** Apple keys every quality tier separately and
   the higher tiers demand output protection a software (L3) Widevine CDM
   cannot provide: the CDM reports those keys as output restricted and the DRM
   session fails. Only the lowest tier's key is usable, hence the default
   360-pixel height cap. Apple's own web player picks the same tier for the
   same reason.
2. **H.264 only.** Encrypted video is decoded inside the CDM, whose decoder
   does not handle HEVC (`ToCdmVideoCodec: Unknown video codec 5`). Apple
   publishes H.264 at every tier, so non-H.264 variants are skipped.
3. **No Dolby Vision or HDR.** Kodi decodes Dolby Vision Profile 5 as plain
   HEVC, which renders with badly shifted colours, so those variants are
   skipped too.

Raising the height cap on hardware with stronger output protection is
possible, but on a typical desktop the CDM will refuse the key.

### What the addon has to do that Apple does not

- Apple sends no `KEYID` on `#EXT-X-KEY` and an all-zero `tenc` default_KID.
  Both are filled in from the key's PSSH, otherwise InputStream Adaptive asks
  the CDM to decrypt with a key that was never licensed.
- Apple's licence server wants the challenge wrapped in a JSON envelope with
  the matching key's `uri`, `adamId` and `svcId`; a local proxy does that
  translation, matching each challenge to its key by key id.
- Apple leaves the opening chapters unencrypted and starts encryption several
  chapters in, so the DRM session is pre-initialised via `pre_init_data`.

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

Two hard limits remain and neither can be coded away:

1. **Standard definition only.** Kodi ships the **software Widevine L3** CDM,
   and Apple only issues a usable key for its lowest tier at that level — its
   own web player selects roughly 360p for the same reason. HD/4K needs the
   hardware **Widevine L1** DRM found on Apple's devices and certified TVs.
2. **Apple gives no public API.** Sign-in, two-factor auth, the catalogue and
   the playback/licence endpoints are all reverse-engineered from Apple's web
   app. Apple changes these deliberately to break unofficial clients, so parts
   of this addon **will need updating over time**, and some may need adjusting
   before they work at all against your account/region.

If you want full-quality Apple TV+ on your TV, use a real Apple TV app on
supported hardware.

## Features

- Apple ID sign-in with two-factor authentication (SRP-6a web flow)
- Browse the Apple TV+, MLS and Formula 1 tabs (all of each tab's shelves)
- Browse categories (Kids & Family, Sci-Fi, Comedies, ...) and MLS club pages as folders
- Show → episode browsing with season/episode metadata
- Search the Apple TV catalogue
- **Play trailer** and **Bonus content** context-menu entries on movies and shows
- Widevine playback through InputStream Adaptive (standard definition)

## Requirements

- **Kodi 22 or later with InputStream Adaptive 22 or later.** This is enforced
  in `addon.xml`. Playback depends on the `inputstream.adaptive.drm` property,
  which does not exist in the 21.x series (only `drm_legacy` was added there,
  in 21.5.0), and on Kodi 21 the video never decrypts.
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
4. Browse **Apple TV+ Originals** or **Search**, open a show for its episodes,
   and press play.

## Settings

- **Storefront ID** — `143441` is the United States. Change for other regions.
- **Locale** — e.g. `en-US`, `en-GB`.
- **OAuth widget key override** — advanced. The key that identifies the Apple TV
  web client to Apple's sign-in service. Leave blank to use the built-in
  default; set it only if sign-in stops working and you have captured a current
  key from a browser session at `https://tv.apple.com`.
- **Maximum video height** — advanced, `360` by default. Higher tiers use keys
  the CDM reports as output restricted, so raising this generally stops
  playback; `0` removes the limit.
- **Standard dynamic range only** — advanced, on by default. Skips Dolby Vision
  and HDR variants, which Kodi renders with shifted colours.
- **H.264 only** — advanced, on by default. The CDM's decoder cannot decode
  HEVC, so those variants are skipped.
- **media-user-token / Manifest URL override** — advanced debug inputs. Paste
  values captured from `tv.apple.com` to exercise playback without signing in.

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
- **"Playback could not be resolved"** — you may not be signed in, or the title
  is not in your subscription/region. The addon logs whether it obtained a
  `media-user-token` and an `hlsUrl`.
- **Audio plays but the picture is black, or playback stops part way in** —
  almost always the quality tier. Apple leaves the opening chapters
  unencrypted, so trouble starts at the first encrypted chapter. Check the log
  for `status 3` (the CDM refusing an output-restricted key: lower *Maximum
  video height*) or `Unknown video codec 5` (an HEVC variant: enable *H.264
  only*).
- **Nothing plays and the log has no `drm property` line** — you are on ISA 21
  or older, which lacks the DRM property this addon needs.

## Legal

Not affiliated with or endorsed by Apple Inc. Apple, Apple TV, Apple TV+ and
iTunes are trademarks of Apple Inc. Use only with your own account and content
that you are entitled to access. This addon does not circumvent DRM — playback
relies on the standard Widevine CDM decrypting content for an authenticated,
licensed session, exactly as a browser would.

## License

GPL-3.0-or-later
