# Starz for Kodi *(in development)*

An unofficial Starz client for Kodi, playing through InputStream Adaptive with
Widevine DRM.

**Status: early test builds.** Sign-in, profiles, browsing (Home, Series,
Movies, Browse, Continue Watching, series seasons and episodes), search, My
List and playback are built from captures of the Starz web player. A small
background service reports where you stopped watching back to Starz. See
[docs/recon.md](docs/recon.md). Not yet published in the Breezyslasher
repository.

## Signing in

Settings → Account → Sign in offers two methods:

- **Email and password.** Kodi links itself the way starz.com/activate does,
  which needs no captcha. If Starz refuses, the error it returns is shown.
- **Activation code.** Kodi shows a 4-character code; enter it at
  starz.com/activate on a phone or computer.

## Resolution: what to expect

- **Desktop (Windows, macOS, Linux) and LibreELEC:** standard definition.
  Kodi uses the software Widevine **L3** CDM, and streaming services reserve HD
  for hardware-protected playback. This is a DRM limit, not a bug in the
  add-on.
- Starz limits an account to **6 active devices**; Kodi counts as one. At the
  limit, playback shows Starz's "Maximum Devices Reached" message: log out
  old devices at starz.com/settings/managedevices.
- **Android with hardware Widevine L1** (most phones, Nvidia Shield, certified
  Android TV boxes): HD may be available. Not yet confirmed for Starz.

## Requirements

- Kodi 21 or later
- InputStream Adaptive and a Widevine CDM (`script.module.inputstreamhelper`
  will offer to install the CDM)
- A Starz subscription

## Disclaimer

This add-on is not affiliated with or endorsed by Starz. Use it only with your
own subscription. Third-party clients may be against the service's terms of
use, so any risk to your account is yours.
