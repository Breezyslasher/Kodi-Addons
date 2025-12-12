# PlexKodiConnect Download

A Kodi context menu addon that allows you to download movies, TV shows, and music from PlexKodiConnect to your device for offline viewing and listening.

## Features

### 📥 Download Options
- **Individual Items**: Download single movies, episodes, or songs
- **Bulk Downloads**: Download entire seasons, albums, artists, or TV shows
- **Smart Downloads**: Download unwatched episodes with customizable limits
- **Metadata Support**: Automatically downloads .nfo files and artwork

### 🎬 Media Support
- **Movies**: Download individual movies with metadata
- **TV Shows**: Download episodes, seasons, or entire series
- **Music**: Download songs, albums, or complete artist discographies

### 🗂️ Organization
- **Folder Structure**: Organize downloads by media type (Movies/TV Shows/Music)
- **TV Show Organization**: Separate folders for shows and seasons
- **Music Organization**: Artist/Album folder structure
- **Artwork**: Download posters and cover art with media

### ⚙️ Smart Features
- **Auto-play**: Option to play content immediately after download


## Installation

1. Download the `context.plexkodiconnect.download-1.0.0.zip` file
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded ZIP file
4. The addon will add a "Download from Plex" option to context menus

## Usage

### Basic Downloading
1. Right-click on any Plex media item in Kodi
2. Select "Download from Plex" from the context menu
3. Choose your download preference (single item, season, album, etc.)
4. The download will begin and be saved to your configured location

### TV Show Downloads
- **From Episode Page**: Download episode, entire season, or full show
- **From Season Page**: Download season or entire show
- **Unwatched Options**: Download 1, 5, 10, or all unwatched episodes

### Music Downloads
- **From Song Page**: Download song, entire album, or full artist
- **From Album Page**: Download album or entire artist

## Configuration

### General Settings
- **Download Location**: Where to save downloaded files (default: `special://home/plexdownloads/`)
- **Organize by Type**: Create separate folders for Movies/TV Shows/Music
- **TV Show Organization**: Create Show/Season folder structure
- **Music Organization**: Create Artist/Album folder structure
- **Metadata Files**: Generate .nfo files for Kodi library integration
- **Download Artwork**: Include posters and cover art with downloads
- **Auto-play**: Ask to play content immediately after download


### TV Show Menu Options
- Toggle visibility of "Download entire show" options
- Configure "Download entire season" options
- Enable/disable unwatched episode download options
- Set custom unwatched episode limits (1, 5, 10 episodes)

### Music Menu Options
- Show/hide "Download entire artist" options
- Configure "Download entire album" options

### Expert / Danger Zone
⚠️ **Use with caution** - These actions cannot be undone:
- Delete ALL downloaded movies
- Delete ALL downloaded TV shows  
- Delete ALL downloaded music

## Requirements

- Kodi 19 (Matrix) or later
- PlexKodiConnect addon
- Active Plex server connection
- Sufficient storage space for downloads

## File Structure

Downloads are organized as follows (when organization is enabled):

```
plexdownloads/
├── Movies/
│   └── Movie Title (Year)/
│       ├── Movie Title (Year).mp4
│       ├── Movie Title (Year).nfo
│       └── poster.jpg
├── TV Shows/
│   └── Show Title/
│       ├── Season 01/
│       │   ├── S01E01 Episode Title.mp4
│       │   ├── S01E01 Episode Title.nfo
│       │   └── ...
│       └── Season 02/
└── Music/
    └── Artist Name/
        └── Album Name/
            ├── 01 Song Title.mp3
            ├── 01 Song Title.nfo
            ├── cover.jpg
            └── ...
```

## Troubleshooting

### Download Fails
- Check your Plex server connection
- Verify the item is available on your Plex server
- Ensure you have sufficient storage space
- Check download permissions for the target folder

### Context Menu Not Appearing
- Ensure PlexKodiConnect is properly installed and configured
- Verify you're right-clicking on Plex content (not local Kodi library)
- Restart Kodi after installing the addon

### Playback Issues
- Check that the downloaded file is not corrupted
- Verify the file format is supported by Kodi
- Try playing the file directly from the filesystem

## Version

**Version**: 1.0.0  
**License**: MIT

## Support

For issues and feature requests, please check the Kodi forum thread or GitHub repository for this addon.
