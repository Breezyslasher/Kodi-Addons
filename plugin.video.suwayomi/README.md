# Suwayomi Kodi Addon

A Kodi addon for reading manga from your [Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server) instance.

## Features

- **Library Management**: Browse and manage your manga library
- **Categories**: Organize manga by categories
- **Source Browser**: Browse installed sources (Popular, Latest, Search)
- **Global Search**: Search across all installed sources
- **Chapter Reader**: Read manga chapters with image slideshow
- **Reading Progress**: Track and sync reading progress
- **Downloads**: Queue and manage chapter downloads
- **Extensions**: Install, update, and manage extensions

## Requirements

- Kodi 19 (Matrix) or newer
- A running Suwayomi-Server instance
- `script.module.requests` addon (usually pre-installed)

## Installation

### Manual Installation

1. Download this repository as a ZIP file
2. In Kodi, go to **Settings** → **Add-ons** → **Install from zip file**
3. Navigate to and select the downloaded ZIP file
4. The addon will be installed

### From Repository (if available)

1. Add the repository containing this addon
2. Go to **Add-ons** → **Install from repository**
3. Find and install "Suwayomi"

## Configuration

After installation, configure the addon:

1. Go to **Add-ons** → **Video add-ons** → **Suwayomi**
2. Open addon settings (right-click → Settings or press 'c')
3. Configure:
   - **Server URL**: Your Suwayomi-Server URL (e.g., `http://192.168.1.100:4567`)
   - **Authentication**: Enable and enter credentials if your server requires authentication

## Usage

### Main Menu

- **Library**: Browse manga in your library
- **Categories**: Browse manga organized by categories
- **Browse Sources**: Access installed sources to discover new manga
- **Recent Updates**: View recently updated chapters
- **Search**: Global search across all sources
- **Extensions**: Manage source extensions
- **Downloads**: View and control download queue

### Reading Manga

1. Navigate to a manga and select it
2. Choose a chapter to read
3. The chapter will open as an image slideshow
4. Navigate using Kodi's built-in controls

### Context Menu Actions

Right-click (or press 'c') on items for additional actions:

**On Manga:**
- Add/Remove from Library
- Refresh Manga Info
- Download All Chapters
- Mark All as Read

**On Chapters:**
- Mark as Read/Unread
- Download/Delete Download

## API

This addon uses Suwayomi-Server's GraphQL API for all operations. The API provides:
- Full library management
- Source browsing and searching
- Chapter fetching and page rendering
- Download queue management
- Extension management

## Troubleshooting

### Cannot connect to server
- Verify your server URL is correct
- Ensure Suwayomi-Server is running
- Check if authentication is required
- Verify network connectivity between Kodi and the server

### Authentication errors
- Enable authentication in addon settings
- Verify username and password match server configuration

### No sources available
- Install extensions through the Extensions menu
- Refresh the sources list

### Images not loading
- The server may be fetching pages from the source
- Check server logs for errors
- Some sources may have rate limiting

## License

MIT License

## Credits

- [Suwayomi Project](https://github.com/Suwayomi) for the amazing manga server
- [Kodi](https://kodi.tv/) for the media center platform

## Support

For issues related to:
- **This addon**: Open an issue on this repository
- **Suwayomi-Server**: Visit [Suwayomi-Server issues](https://github.com/Suwayomi/Suwayomi-Server/issues)
- **Source extensions**: Check the respective extension repository
