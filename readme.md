# Kodi Addons Repository

Welcome to my personal Kodi addon repository! This collection includes various addons I've developed to enhance the Kodi media center experience.

## 📦 Available Addons

### 🎧 [Audiobookshelf](plugin.audio.audiobookshelf/)
Stream audiobooks and podcasts from your Audiobookshelf server directly in Kodi.

**Features:**
- Stream audiobooks (M4B, MP3, multi-file) with chapter navigation
- Browse and stream podcast episodes
- Progress sync with server (bidirectional)
- Download for offline playback
- Resume playback support
- Search and add new podcasts from iTunes
- Find new episodes from RSS feeds
- API Key or Username/Password authentication

**Requirements:** Kodi 19+, Audiobookshelf server 2.0+

---

### ⬇️ [PlexKodiConnect Download](context.plexkodiconnect.download/)
Context menu addon for downloading Plex media for offline viewing.

**Features:**
- Download movies, TV shows, and music from PlexKodiConnect
- Bulk downloads (seasons, albums, artists, shows)
- Smart downloads (unwatched episodes with limits)
- Organized folder structure with metadata
- Auto-play after download option
- Hide/restore Plex versions

**Requirements:** Kodi 19+, PlexKodiConnect addon

---

### 🔗 [Webhook Runner](script.webhook.runner/)
Execute up to 10 Home Assistant webhooks from Kodi.

**Features:**
- Configure up to 10 webhook URLs and names
- Launch from remote buttons or GUI interface
- Perfect for Home Assistant automation
- Simple configuration through addon settings

**Requirements:** Kodi 19+, Home Assistant instance

---

## 🚀 Installation

### Method 1: Repository Installation (Recommended)

1. In Kodi: Settings → Add-ons → Install from repository
2. Add my repository URL: `https://raw.githubusercontent.com/Breezyslasher/Kodi-Addons/main/zips/repository.breezyslasher/repository.breezyslasher-1.0.9.zip`
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
├── script.webhook.runner/           # Webhook runner addon
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
