# Kodi Addons Repository

Welcome to my personal Kodi addon repository! This collection includes various addons I've developed to enhance the Kodi media center experience.

## Available Addons


### [Audiobookshelf](plugin.audio.audiobookshelf/)
Stream audiobooks and podcasts from your Audiobookshelf server.

**Recent updates:**
- API key login — use an Audiobookshelf API key instead of username/password (overrides it when set)
- Each library is now listed separately instead of merged into single Audiobooks/Podcasts folders (toggle "Group libraries by type" to restore the old view)

See the [addon README](plugin.audio.audiobookshelf/README.md) for the full feature list.

**Requirements:** Kodi 19+, Audiobookshelf server 2.26+

---

### [Apple TV](plugin.video.appletv/) *(experimental)*
Sign in with your Apple ID and browse Apple TV+ Originals, the MLS and Formula 1 tabs, and Search in Kodi, with playback through InputStream Adaptive using Widevine DRM.

**How it works:** Apple TV+ uses FairPlay DRM on Apple devices (which Kodi can't decrypt) but Widevine on its web client (which Kodi can). This addon mimics the Apple TV web client to receive Widevine streams.

**Resolution:** On a **Widevine-L1 Android device running Kodi 22** (most phones, Nvidia Shield, certified Android TV boxes) Apple's licence server grants the **HD and 4K tiers**: the addon detects hardware (L1) Widevine and plays **1080p automatically**, with HEVC tiers beyond that available by raising *Maximum video height* and turning off *H.264 only* (verified up to ~3K rendering on device). On desktop, playback tops out at **~540p** — Kodi ships the software Widevine **L3** CDM, and Apple flags the keys for tiers above ~540p as output-restricted (which an L3 CDM can't use), so the 540p tier is the ceiling there (verified: 540p plays, 720p is output-restricted). Live events (Formula 1, MLS) play in HD everywhere, since Apple licenses only that tier for them.

**Known limits:** Apple's private, undocumented endpoints may need updates whenever Apple changes them. See the [addon README](plugin.video.appletv/README.md) for the full reality check.

**Requirements:** an Apple ID, a Widevine CDM, and **Kodi 21+ on desktop** or **Kodi 22+ on Android** — Kodi 21's Android InputStream Adaptive can never license Apple's separately-keyed audio track (hardcoded single DRM session, fixed in ISA 22), so Android needs Kodi 22

---

### [Heroic Games Launcher for AKL](script.akl.heroic/)
Bring your Heroic Games Launcher library (Epic Games Store, GOG, Amazon Games and sideloaded games) into Kodi on Linux, Windows and macOS through [Advanced Kodi Launcher](https://github.com/chrisism/plugin.program.akl).

**Highlights:**
- Scans Heroic's local library cache directly — no accounts, credentials or API keys needed
- Launches games through Heroic itself (`heroic://launch/...`), so Proton/Wine settings, cloud saves and playtime tracking keep working
- Works with Flatpak and native Heroic installs on Linux, the standard Windows install, and Heroic.app on macOS, with auto-detection
- Scrapes titles, descriptions, developers and artwork from the data Heroic already keeps on disk

See the [addon README](script.akl.heroic/README.md) for details.

**Requirements:** Kodi 19+ on Linux, Windows or macOS, Advanced Kodi Launcher (`script.module.akl` 1.2.0+), Heroic Games Launcher

---

### [PlexKodiConnect Download](context.plexkodiconnect.download/)
Context-menu addon for downloading Plex media (movies, shows, music) for offline viewing, including bulk and smart-unwatched downloads.

See the [addon README](context.plexkodiconnect.download/README.md) for details.

**Requirements:** Kodi 19+, PlexKodiConnect addon

---

### [scrcpy Launcher](script.scrcpy-launcher/)
Stream your Android device screen — or the Samsung DeX desktop — to Kodi using scrcpy (USB or Wi-Fi, with LibreELEC/OSMC/Flatpak handling).

**Recent updates:**
- Samsung DeX mirroring with auto-detected display id (no root, no miraclecast)
- Virtual Desktop mode (`--new-display`) for phones without an active DeX session
- Bundled scrcpy updated to v4.0 on x86_64 (SDL3, self-contained binary)

See the [addon README](script.scrcpy-launcher/README.md) for details.

**Requirements:** Kodi 19+, Android device with USB debugging enabled

---

### [Watch Party](service.watchparty/)
Synchronized playback across Kodi devices — a SyncLounge-style watch party, native to Kodi. Host a party on one device (or a standalone relay server), friends join with a room code, and play/pause/seek stay in sync everywhere with automatic drift correction.

**Highlights:**
- Works on the couch (embedded relay in Kodi) and over the internet (standalone relay on a VPS/Pi/Docker — nobody port-forwards)
- Addon streams (YouTube, Disney+, …) follow by `plugin://` path, so every device resolves its own stream with its own account
- Prebuilt multi-arch Docker image: `ghcr.io/breezyslasher/kodi-watchparty-relay` with a live `/status` dashboard of rooms and members
- Saved relay address + room code — rejoining the usual party is just OK, OK

See the [addon README](service.watchparty/README.md) for setup and details.

**Requirements:** Kodi 19+ on all devices; for remote parties a reachable relay (Docker one-liner)

---

### [Webhook Runner](script.webhook.runner/)
Fire Home Assistant (or any) webhooks from Kodi — either by remote-button press or automatically on Kodi events.

**Recent updates:**
- Expanded event triggers to 40+ Kodi events (playback, playlist, screensaver/DPMS, system power, library, input, volume) with per-event webhook mapping
- Button mapping via Keymap Editor integration (long-press and raw button codes)
- Optional default URL prefix so adding new webhooks is one-field paste

See the [addon README](script.webhook.runner/README.md) for details.

**Requirements:** Kodi 21+, Home Assistant instance (or any HTTP webhook target)

---

### [YouTube TV](plugin.video.youtubetv/) *(experimental)*
Your YouTube TV subscription in Kodi: the live channel list, the 7-day guide, Home, your Library, Networks, YouTube TV's own categories, search, and recording a series from the context menu.

**How it works:** Signing in used to mean exporting a cookie jar from a browser, and on cookies YouTube TV does hand out a `dashManifestUrl` — the DASH path worked and is what most of the protocol notes were written against. A **device-code token is never offered one**: eight request shapes were tried, five sending less than the browser and three sending more, and all eight came back `dash=False`. So retiring the cookie jar for a one-off Google API project meant there was no manifest left to fetch, and the addon now speaks Google's own **SABR** protocol and bridges the result back to InputStream Adaptive as DASH over localhost. Beside it runs a licence proxy for YouTube's JSON-wrapped, rotating-key Widevine exchange, which ISA cannot speak on its own. The `n` parameter every media URL carries is solved by a JavaScript interpreter written in Python, so no box needs node or deno installed.

**Confirmed working:** Kodi 21.3 and 22.0 on Linux x86-64, and Kodi 21.2 on Android ARM64 — live channels and films, with metadata, artwork and 1080p Widevine playback. Should also work on Windows, macOS, Linux ARM (LibreELEC, Raspberry Pi) and other Android devices, which meet the same requirements. **Cannot work on iOS, tvOS or Xbox**, which have no Widevine CDM.

**Known limits:** private, undocumented endpoints that may change without notice. The guide's live preview mosaic is the one feature of the official app not implemented. Sign-in needs your own Google API project — the addon ships none, and reuses the YouTube addon's if you have it set up.

See the [addon README](plugin.video.youtubetv/README.md) for the full picture, and [the protocol notes](plugin.video.youtubetv/docs/youtube-tv-protocol.md) for how it was worked out.

**Requirements:** a paid YouTube TV subscription, Kodi 21+, InputStream Adaptive 21.5.22+ with a working Widevine CDM, and the addon's service enabled

---

## Installation

### Method 1: Repository Installation (Recommended)

1. In Kodi: Settings → Add-ons → Install from repository
2. Add my repository URL: `https://raw.githubusercontent.com/Breezyslasher/Kodi-Addons/main/zips/repository.breezyslasher/repository.breezyslasher-2026.8.12.zip`
3. Install "Breezyslasher Repository"
4. Browse and install addons from the repository

### Method 2: Manual Installation

1. Download the desired addon ZIP file from the [Zips](https://github.com/Breezyslasher/Kodi-Addons/tree/main/zips) page
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded ZIP file

## System Requirements

- **Kodi Version**: 19 (Matrix) or later
- **Python**: 3.0.0 or later
- **Network Connection**: Required for server-based addons
- **Storage Space**: For offline download features

## Development & Building

This repository uses automated workflows to build and distribute addons:

- **GitHub Actions**: `.github/workflows/generate-repo.yml` runs on every push to
  `main`. It builds each addon's ZIP, copies the declared artwork next to it so
  Kodi's add-on browser has an icon, regenerates `zips/addons.xml` and its MD5,
  and leaves out `docs`, `tools`, `tests` and `__pycache__` — an addon's notes
  belong in the repository, not in every install
- **Local builds**: `python3 tools/build_addon_zip.py [addon]` does the same
  thing on a workstation. It also refuses to include `baked_*.py`, which is
  where a personal build keeps its own credentials, unless `--personal` says so
- **Repository Generator**: `generate_repo.py` regenerates `zips/addons.xml`
  alone, as a manual fallback when the workflow has not run
- **Version Management**: Each addon maintains its own versioning

### Repository Structure
```
Kodi-Addons/
├── context.plexkodiconnect.download/ # Plex download addon
├── plugin.audio.audiobookshelf/     # Audiobookshelf addon
├── plugin.video.appletv/            # Apple TV+ addon
├── plugin.video.youtubetv/          # YouTube TV addon
├── script.akl.heroic/               # Heroic Games Launcher AKL plugin
├── script.scrcpy-launcher/          # scrcpy launcher addon
├── script.webhook.runner/           # Webhook runner addon
├── service.watchparty/              # Watch Party sync addon
├── repository.breezyslasher/        # Repository metadata
├── tools/                          # Build and check scripts
├── zips/                           # Generated ZIP files
├── .github/workflows/              # CI/CD workflows
└── generate_repo.py                # Repository generator (manual fallback)
```

## Contributing

While these are personal projects, I welcome feedback and suggestions:

- **Bug Reports**: Please open an issue with details about the problem
- **Feature Requests**: Open an issue describing the desired feature
- **Questions**: Use the issue tracker for any questions

## License

Each addon has its own license:
- **Apple TV**: GPL-3.0-or-later
- **Audiobookshelf**: GPL-3.0-or-later
- **Heroic Games Launcher for AKL**: GPL-2.0
- **PlexKodiConnect Download**: MIT
- **scrcpy Launcher**: MIT
- **Watch Party**: MIT
- **Webhook Runner**: MIT
- **YouTube TV**: GPL-3.0-or-later

## Links

- **Repository**: https://github.com/Breezyslasher/Kodi-Addons
- **Issues**: https://github.com/Breezyslasher/Kodi-Addons/issues

## Support

If you find these addons useful, consider giving the repository a star! For support:

1. Check the individual addon documentation
2. Search existing issues for solutions
3. Create a new issue with detailed information about your problem

---

**Note**: These addons are provided as-is with no warranty. Use at your own risk and ensure you have proper backups of your Kodi setup.
