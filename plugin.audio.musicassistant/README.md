# Music Assistant Kodi Addon

Browse and control music from your Music Assistant 2.7+ server directly in Kodi.

## Features

- **Full Library Access**: Browse artists, albums, tracks, playlists, and radio stations
- **Podcasts & Audiobooks**: Access podcasts and audiobooks (new in MA 2.7)
- **Search**: Search across your entire music library
- **Favorites**: Add/remove items from your favorites
- **Recently Played**: Quick access to recently played content
- **Local Playback**: Play audio on your Kodi device using Sendspin
- **Remote Control**: Control any Music Assistant player
- **Queue Management**: View and control player queues
- **Secure Authentication**: Token-based authentication for MA 2.7+

## Requirements

- Music Assistant Server 2.7 or newer
- Kodi 19 (Matrix) or newer
- Network access to your Music Assistant server
- **For local playback**: sendspin-cli installed on your Kodi device

## Installation

1. Download the addon ZIP file
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded ZIP file

## Local Playback Setup (Sendspin)

To play music directly on your Kodi device (required for streaming services like Apple Music, Spotify, etc.):

### Step 1: Install sendspin-cli

On your Kodi device, open a terminal/SSH and run:

```bash
pip install sendspin
```

### Step 2: Enable Auto-Start (Recommended)

The addon can automatically start sendspin when you open it:

1. Go to addon **Settings** → **Playback Options**
2. Enable **"Auto-start Sendspin Player"** (enabled by default)
3. Optionally set a custom **"Sendspin Player Name"**

That's it! When you browse music and play a track, it will automatically:
- Start the sendspin player
- Connect to Music Assistant
- Play audio through your device

### Alternative: Manual Start

If you prefer to manage sendspin yourself:

```bash
# Interactive mode (with UI)
sendspin

# Headless mode (background)
sendspin --headless --name "Kodi Player"
```

## Configuration

### Connection Settings

1. Go to Add-on Settings
2. Enter your Music Assistant server URL (e.g., `http://192.168.1.100:8095`)
3. **Authentication** (required for MA 2.7+):
   - Enter your username and password - a token will be generated automatically
   - Or create a token in MA web interface and paste it in the "Auth Token" field

## Usage

### Main Menu

- **Artists**: Browse all artists in your library
- **Albums**: Browse all albums
- **Tracks**: Browse all tracks
- **Playlists**: Access your playlists
- **Radio Stations**: Browse internet radio stations
- **Podcasts**: Browse podcasts and episodes
- **Audiobooks**: Browse audiobooks
- **Search**: Search across all media types
- **Favorites**: Quick access to favorited items
- **Recently Played**: Recently played content
- **Queue**: View and control current playback queue
- **Players**: Browse and select MA players

### Context Menu Actions

- **Play**: Start playback immediately
- **Play Next**: Add to queue after current track
- **Add to Queue**: Add to end of queue
- **Add to Favorites** / **Remove from Favorites**: Manage favorites

## How It Works

This addon acts as a **remote control** for Music Assistant:
1. You browse your music library in Kodi
2. When you select a track, the addon sends a play command to Music Assistant
3. Music Assistant streams the audio to your selected player (sendspin on your device)
4. Audio plays through your Kodi device's speakers

This approach is **required** for streaming services like Apple Music, Spotify, Tidal, etc. because they use DRM - only Music Assistant can decrypt and stream the audio.

## Troubleshooting

### No players found
- Install and run sendspin-cli on your device
- Check that sendspin can find your MA server: `sendspin --list-servers`

### "Authentication Required" Error
- Check that username/password or token is correctly configured
- Verify the token hasn't expired

### "Cannot Connect" Error
- Check the server URL is correct
- Ensure Kodi can reach the server on your network
- Verify the MA server is running

## License

Apache License 2.0

## Links

- [Music Assistant](https://music-assistant.io)
- [Sendspin](https://www.sendspin-audio.com)
- [sendspin-cli](https://github.com/Sendspin/sendspin-cli)
