# Starz for Kodi *(in development)*

An unofficial Starz client for Kodi, playing through InputStream Adaptive with
Widevine DRM.

**Status: skeleton.** The add-on installs and shows a menu, but sign-in,
browsing and playback are not built yet. They are being built from captures of
the Starz web player; see [docs/recon.md](docs/recon.md). This add-on is not
yet published in the Breezyslasher repository.

## Resolution: what to expect

- **Desktop (Windows, macOS, Linux) and LibreELEC:** standard definition.
  Kodi uses the software Widevine **L3** CDM, and streaming services reserve HD
  for hardware-protected playback. This is a DRM limit, not a bug in the
  add-on.
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
