# Apple TV for Kodi (experimental)

Sign in with your Apple ID and browse **Apple TV+ Originals** and your
**iTunes movie library** in Kodi, with playback through **InputStream Adaptive**
using **Widevine** DRM.

> ⚠️ **Encrypted video does not play yet.** Sign-in, browsing, search and the
> whole DRM pipeline work, and **audio plays**, but the Widevine CDM refuses to
> decrypt Apple's video. Read *Current status* before installing. Everything
> here is reconstructed from real `tv.apple.com` browser captures; Apple
> documents none of it and can change it at any time.

## Current status

| Piece | Status |
|-------|--------|
| Apple ID sign-in + two-factor (trusted device or SMS) | ✅ Matches the real web flow (authorize → SRP with *no-username-in-x* + `X-Apple-HC` hashcash → 2FA → trust) |
| Apple-ID login → `media-user-token` (store login) | ✅ `POST auth.tv.apple.com/auth/v1/web` mints it automatically after sign-in |
| Browse Apple TV+ Originals, search | ✅ Real shelf titles, posters, full item lists |
| Show → episode browsing | ✅ Shows open to their episode list with S/E metadata |
| Playback resolve (`/uts/v3/movies/{id}`, `/episodes/{id}`) | ✅ Returns the `hlsUrl` for the entitled feature (not the trailer) |
| Widevine licence exchange (`fpsRequest`) | ✅ Local proxy wraps the challenge in Apple's JSON envelope; the returned key id always matches the requested one |
| Key id delivery (`KEYID` + `tenc` patching) | ✅ Apple omits both; the proxy recovers the key id from the PSSH and supplies it |
| **Audio decryption and playback** | ✅ Works |
| **Video decryption** | ❌ CDM returns `kNoKey`, or ISA finds no decrypter at the first encrypted chapter |

### Why video does not play

Apple encrypts video with **cbcs pattern encryption (1:9)** and audio without a
pattern (`0:0`). On this setup the CDM decrypts the unpatterned audio and
refuses the patterned video in both modes: InputStream Adaptive's test
decryption fails, so the stream is flagged `SSD_SECURE_PATH` and decoded inside
the CDM, which then reports `kNoKey` for a key it demonstrably holds.

Ruled out with evidence, so nobody repeats the work:

- **Not a key mismatch.** The requested key id equals the key id in the returned
  licence, on every request across several captures.
- **Not a missing key id.** Apple sends no `KEYID` attribute and an all-zero
  `tenc` default_KID; both are now filled in from the PSSH, and the failure is
  unchanged.
- **Not resolution or quality tier.** An SD variant fails exactly like a 1080p
  one.
- **Not the crypto mode.** ISA maps `METHOD=SAMPLE-AES` to `AES_CBC` itself.
- **Not the secure-decoder setting.** `NOSECUREDECODER` clears
  `SSD_SECURE_DECODER`, not `SSD_SECURE_PATH`, so it cannot apply here.

One unexplained detail worth pursuing upstream: ISA logs
`ToCdmVideoCodecProfile: Unknown codec profile 0` every time it opens Apple
video through the CDM, i.e. the CDM's video decoder is initialised with no
codec profile. See `docs/inputstream-adaptive-issue.md` for a written-up report.

Note that no other Kodi DRM addon appears to hit this: Amazon, Disney+, Max,
Paramount and Crunchyroll all stream **DASH with `cenc`**, while Apple offers
**only HLS with `cbcs`**.

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

Even once video decrypts, two hard limits remain and neither can be coded away:

1. **Standard definition at best.** Kodi ships the **software Widevine L3** CDM.
   Apple (like most services) restricts L3 to SD — its own web player selects
   roughly 360p. Real HD/4K needs the hardware **Widevine L1** DRM that only
   exists on Apple's devices and certified TVs.
2. **Apple gives no public API.** Sign-in, two-factor auth, the catalogue and
   the playback/licence endpoints are all reverse-engineered from Apple's web
   app. Apple changes these deliberately to break unofficial clients, so parts
   of this addon **will need updating over time**, and some may need adjusting
   before they work at all against your account/region.

If you want full-quality Apple TV+ on your TV, use a real Apple TV app on
supported hardware.

## Features

- Apple ID sign-in with two-factor authentication (SRP-6a web flow)
- Browse Apple TV+ Originals (shelves of shows and movies)
- Show → episode browsing with season/episode metadata
- Search the Apple TV catalogue
- Widevine licence exchange wired through InputStream Adaptive
  (audio decrypts; see *Current status* for the video limitation)

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
4. Browse **Apple TV+ Originals** or **Search**, open a show for its episodes,
   and press play.

## Settings

- **Storefront ID** — `143441` is the United States. Change for other regions.
- **Locale** — e.g. `en-US`, `en-GB`.
- **OAuth widget key override** — advanced. The key that identifies the Apple TV
  web client to Apple's sign-in service. Leave blank to use the built-in
  default; set it only if sign-in stops working and you have captured a current
  key from a browser session at `https://tv.apple.com`.
- **Disable InputStream Adaptive secure decoder** — advanced, off by default.
  Sets ISA's global `NOSECUREDECODER`, which affects every DRM addon on the
  system. It does not fix the video issue above; left in only for testing.
- **Maximum video height** — advanced, `0` (no limit) by default. Drops variants
  above the given height from the master playlist. Renditions belonging only to
  removed variants are dropped with them.
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
- **Audio plays but the picture is black or frozen** — this is the known video
  limitation described in *Current status*, not a configuration mistake. The log
  shows `kNoKey` or `Decrypter for the stream not found`.
- **Playback stops a few seconds in** — Apple leaves the opening chapters
  unencrypted and starts encryption a few chapters in, which is where the video
  problem surfaces.

## Legal

Not affiliated with or endorsed by Apple Inc. Apple, Apple TV, Apple TV+ and
iTunes are trademarks of Apple Inc. Use only with your own account and content
that you are entitled to access. This addon does not circumvent DRM — playback
relies on the standard Widevine CDM decrypting content for an authenticated,
licensed session, exactly as a browser would.

## License

GPL-3.0-or-later
