# USB Camera Viewer for Kodi

View USB cameras, HDMI/composite capture cards, and V4L2 video devices directly in Kodi.

## Features

- **Auto-detection** of all connected video devices
- **USB Webcams** - View any USB camera or webcam
- **HDMI Capture Cards** - Elgato, AVerMedia, Magewell, generic USB capture cards
- **Composite/S-Video Capture** - EasyCap and similar capture devices
- **Game Capture** - View PS Vita, Nintendo Switch, PlayStation, Xbox via capture card
- **Low Latency Mode** - Optimized for real-time gaming and monitoring
- **Multiple Inputs** - Switch between HDMI/composite inputs on multi-input cards
- **Configurable Resolution** - From 320x240 to 1920x1080
- **External Player** - Option to use ffplay for maximum compatibility

## Supported Platforms

- **LibreELEC** (recommended)
- Any Linux-based Kodi installation with V4L2 support

## Requirements

- Kodi 19 (Matrix) or later
- Linux with V4L2 support (built into LibreELEC)
- USB video device (camera or capture card)
- FFmpeg (included in LibreELEC)

## Installation

### From Repository (Recommended)
1. Add the Breezyslasher repository to Kodi
2. Navigate to Add-ons > Install from repository
3. Find "USB Camera Viewer" under Video add-ons
4. Install

### Manual Installation
1. Download the latest ZIP from releases
2. In Kodi: Add-ons > Install from zip file
3. Select the downloaded ZIP file

## Usage

1. Connect your USB camera or capture card
2. Open the addon from Video Add-ons
3. Select your device from the list
4. Video will start playing

### For PS Vita / Game Consoles

To view your PS Vita or game console in Kodi:

1. **Get a capture card** - You need an HDMI or composite USB capture card
2. **Connect your console** - Connect the console's video output to the capture card
3. **Connect to Kodi device** - Plug the capture card into a USB port
4. **Open the addon** - Your capture card should appear in the device list

Popular capture cards:
- **HDMI**: Elgato Cam Link, AVerMedia Live Gamer, generic USB HDMI capture
- **Composite**: EasyCap, USBTV-based devices

## Settings

### Video Settings
- **Resolution** - Output resolution (1920x1080, 1280x720, etc.)
- **Framerate** - Capture framerate (15-60 fps)
- **Pixel Format** - Auto, MJPEG, YUYV, NV12, H.264

### Playback
- **Low Latency Mode** - Minimize delay for gaming (recommended: ON)
- **External Player** - Use ffplay instead of Kodi player

### Capture Card
- **Default Input** - Default input for multi-input cards
- **Video Standard** - NTSC/PAL/SECAM for composite input

## Troubleshooting

### No devices detected
- Ensure device is connected via USB
- Try a different USB port
- Reboot LibreELEC after connecting new devices
- Check device recognition: `ls -la /dev/video*`

### Video won't play
- Try lowering resolution in settings
- Use "Play with External Player" option
- Install `inputstream.ffmpegdirect` for better compatibility

### Low framerate or stuttering
- Enable Low Latency Mode
- Lower the resolution
- Try MJPEG pixel format

### Capture card shows wrong input
- Use context menu to select specific input
- Set default input in addon settings

## Technical Details

This addon uses V4L2 (Video4Linux2) to access video devices, which is the standard Linux video capture API. Video is captured using FFmpeg and can be played either:

1. Through Kodi's built-in player (via pipe)
2. Using `inputstream.ffmpegdirect` (if installed)
3. Using external `ffplay` (fallback)

## License

GPL-3.0

## Support

For issues and feature requests, please visit:
https://github.com/Breezyslasher/Kodi-Addons/issues
