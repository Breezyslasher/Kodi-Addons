# Kodi Addons Repository

Welcome to my personal Kodi addon repository! This collection includes various addons I've developed to enhance the Kodi media center experience.

## 📦 Available Addons


### 🎧 [Audiobookshelf](plugin.audio.audiobookshelf/)
Stream audiobooks and podcasts from your Audiobookshelf server.

**Recent updates:**
- API key login — use an Audiobookshelf API key instead of username/password (overrides it when set)
- Each library is now listed separately instead of merged into single Audiobooks/Podcasts folders (toggle "Group libraries by type" to restore the old view)

See the [addon README](plugin.audio.audiobookshelf/README.md) for the full feature list.

**Requirements:** Kodi 19+, Audiobookshelf server 2.26+

---

### ⬇️ [PlexKodiConnect Download](context.plexkodiconnect.download/)
Context-menu addon for downloading Plex media (movies, shows, music) for offline viewing, including bulk and smart-unwatched downloads.

See the [addon README](context.plexkodiconnect.download/README.md) for details.

**Requirements:** Kodi 19+, PlexKodiConnect addon

---

### 📱 [scrcpy Launcher](script.scrcpy-launcher/)
Stream your Android device screen — or the Samsung DeX desktop — to Kodi using scrcpy (USB or Wi-Fi, with LibreELEC/OSMC/Flatpak handling).

**Recent updates:**
- Samsung DeX mirroring with auto-detected display id (no root, no miraclecast)
- Virtual Desktop mode (`--new-display`) for phones without an active DeX session
- Bundled scrcpy updated to v4.0 on x86_64 (SDL3, self-contained binary)

See the [addon README](script.scrcpy-launcher/README.md) for details.

**Requirements:** Kodi 19+, Android device with USB debugging enabled

---

### 🎬 [Watch Party](service.watchparty/)
Synchronized playback across Kodi devices — a SyncLounge-style watch party, native to Kodi. Host a party on one device (or a standalone relay server), friends join with a room code, and play/pause/seek stay in sync everywhere with automatic drift correction.

**Highlights:**
- Works on the couch (embedded relay in Kodi) and over the internet (standalone relay on a VPS/Pi/Docker — nobody port-forwards)
- Addon streams (YouTube, Disney+, …) follow by `plugin://` path, so every device resolves its own stream with its own account
- Prebuilt multi-arch Docker image: `ghcr.io/breezyslasher/kodi-watchparty-relay` with a live `/status` dashboard of rooms and members
- Saved relay address + room code — rejoining the usual party is just OK, OK

See the [addon README](service.watchparty/README.md) for setup and details.

**Requirements:** Kodi 19+ on all devices; for remote parties a reachable relay (Docker one-liner)

---

### 🔗 [Webhook Runner](script.webhook.runner/)
Fire Home Assistant (or any) webhooks from Kodi — either by remote-button press or automatically on Kodi events.

**Recent updates:**
- Expanded event triggers to 40+ Kodi events (playback, playlist, screensaver/DPMS, system power, library, input, volume) with per-event webhook mapping
- Button mapping via Keymap Editor integration (long-press and raw button codes)
- Optional default URL prefix so adding new webhooks is one-field paste

See the [addon README](script.webhook.runner/README.md) for details.

**Requirements:** Kodi 21+, Home Assistant instance (or any HTTP webhook target)

---

## 🚀 Installation

### Method 1: Repository Installation (Recommended)

1. In Kodi: Settings → Add-ons → Install from repository
2. Add my repository URL: `https://raw.githubusercontent.com/Breezyslasher/Kodi-Addons/main/zips/repository.breezyslasher/repository.breezyslasher-2026.7.24.zip`
3. Install "Breezyslasher Repository"
4. Browse and install addons from the repository

### Method 2: Manual Installation

1. Download the desired addon ZIP file from the [Zips](https://github.com/Breezyslasher/Kodi-Addons/tree/main/zips) page
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded ZIP file

## 📋 System Requirements

- **Kodi Version**: 19 (Matrix) or later
- **Python**: 3.0.0 or later
- **Network Connection**: Required for server-based addons
- **Storage Space**: For offline download features

## 🛠️ Development & Building

This repository uses automated workflows to build and distribute addons:

- **GitHub Actions**: Automatically builds addon ZIP files
- **Repository Generator**: `generate_repo.py` creates the repository metadata
- **Version Management**: Each addon maintains its own versioning

### Repository Structure
```
Kodi-Addons/
├── plugin.audio.audiobookshelf/     # Audiobookshelf addon
├── context.plexkodiconnect.download/ # Plex download addon  
├── script.scrcpy-launcher/          # scrcpy launcher addon
├── script.webhook.runner/           # Webhook runner addon
├── service.watchparty/              # Watch Party sync addon
├── repository.breezyslasher/        # Repository metadata
├── zips/                           # Generated ZIP files
├── .github/workflows/              # CI/CD workflows
└── generate_repo.py               # Repository generator
```

## 🤝 Contributing

While these are personal projects, I welcome feedback and suggestions:

- **Bug Reports**: Please open an issue with details about the problem
- **Feature Requests**: Open an issue describing the desired feature
- **Questions**: Use the issue tracker for any questions

## 📄 License

Each addon has its own license:
- **Audiobookshelf**: GPL-3.0-or-later
- **PlexKodiConnect Download**: MIT
- **scrcpy Launcher**: MIT
- **Watch Party**: MIT
- **Webhook Runner**: MIT

## 🔗 Links

- **Repository**: https://github.com/Breezyslasher/Kodi-Addons
- **Issues**: https://github.com/Breezyslasher/Kodi-Addons/issues

## ⭐ Support

If you find these addons useful, consider giving the repository a star! For support:

1. Check the individual addon documentation
2. Search existing issues for solutions
3. Create a new issue with detailed information about your problem

---

**Note**: These addons are provided as-is with no warranty. Use at your own risk and ensure you have proper backups of your Kodi setup.
