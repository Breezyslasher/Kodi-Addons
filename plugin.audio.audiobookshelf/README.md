# Audiobookshelf for Kodi

Stream audiobooks and podcasts from your [Audiobookshelf](https://www.audiobookshelf.org/) server directly in Kodi.

## Features

### Audiobooks
- Stream single-file (M4B) and multi-file audiobooks
- Chapter navigation with resume support
- Progress sync with server
- Download for offline playback

### Podcasts
- Browse and stream podcast episodes
- Search and add new podcasts from iTunes
- Find new episodes from RSS feeds
- Download episodes to server
- Download locally for offline playback
- Episode progress tracking

### General
- **Authentication**: Username/Password or API Key
- **Progress Sync**: Automatic sync with configurable intervals
- **Offline Mode**: Download content for offline playback
- **View Modes**: List or Grid view with cover art
- **Resume Playback**: Pick up where you left off
- **Progress Markers**: See completion status at a glance

## Installation

1. Download the latest release ZIP file
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded ZIP file
4. Go to Add-ons → Music add-ons → Audiobookshelf
5. Configure your server settings

## Configuration

### Server Settings
- **Server IP Address**: Your Audiobookshelf server IP
- **Port**: Default is 13378
- **Authentication Method**: Choose Username/Password or API Key
- **Username/Password**: Your Audiobookshelf credentials
- **API Key**: Generate from Audiobookshelf settings → Users → Your user → API Key

### Display Settings
- **View Mode**: List or Grid (covers)
- **Show titles in grid**: Toggle title display in grid view
- **Show Items**: All, Hide Finished, or Downloaded Only
- **Show progress markers**: Toggle [X%], [Done], etc.

### Download Settings
- **Enable Downloads**: Turn on local downloads
- **Download Folder**: Where to save downloads

### Sync Settings
- Configure sync intervals and behavior for audiobooks and podcasts separately
- Choose how to resolve conflicts between local and server progress

## Usage

### Playing Content
1. Navigate to your audiobook or podcast
2. For multi-file books: select chapter or use Resume
3. Progress automatically syncs to server

### Finding New Podcast Episodes
1. Open a podcast
2. Select "[Find New Episodes]"
3. Click "[Refresh Podcast from RSS]" to check for new episodes
4. Episodes marked [NEW] are from RSS but not on server
5. Episodes marked [Need DL] are on server but not downloaded
6. Click an episode to add/download to server

### Downloading for Offline
1. Enable downloads in settings
2. Set a download folder
3. Use context menu → Download on any item
4. Access downloads from "[Downloaded Items]"

## Requirements

- Kodi 19 (Matrix) or later
- Audiobookshelf server 2.0+
- Network connection (or downloaded content for offline)

## Troubleshooting

### "Too Many Requests" Error
The addon caches authentication tokens for 5 minutes. If you see this error, wait a moment and try again.

### Episodes Not Downloading to Server
Make sure the episode exists in Audiobookshelf's database first. Use "[Refresh Podcast from RSS]" to update the episode list.

### Playback Issues
- Check your server is accessible from Kodi's device
- Verify your credentials are correct
- For podcasts, ensure the episode has audio downloaded on the server

## License

GPL-3.0-or-later

## Credits

Based on the original audiobookshelf_simpleclient by platzregen.
