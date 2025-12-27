# scrcpy Launcher for Kodi

Stream your Android device screen to Kodi using scrcpy. Supports USB and WiFi connections on LibreELEC, OSMC, and desktop Linux with Flatpak Kodi.

https://github.com/user-attachments/assets/5d711656-cce1-4eb5-8446-dbc04ccc1aab

## Features

- **USB Streaming** - Connect via USB cable for lowest latency
- **WiFi Streaming** - Connect via ADB over WiFi
- **LibreELEC/OSMC Support** - Automatically stops/restarts Kodi for fullscreen streaming
- **Flatpak Support** - Works with Flatpak Kodi on desktop Linux
- **Audio Forwarding** - Stream device audio (Android 11+)
- **Configurable Video** - FPS, bitrate, codec, resolution settings
- **Keyboard Shortcuts** - Configurable quit key and device controls

## Requirements

- Android device with USB debugging enabled
- USB cable (for USB mode) or WiFi network (for WiFi mode)
- LibreELEC, OSMC, or Linux with Kodi

## Installation

1. Download the latest zip file
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded zip

## Usage

### USB Streaming

1. Enable USB debugging on your Android device
2. Connect device via USB cable
3. Launch the addon
4. Select **Stream USB Device**

### WiFi Streaming

1. Enable USB debugging on your Android device
2. Connect via USB first and run `adb tcpip 5555` OR enable Wireless debugging (Android 11+)
3. Go to addon **Settings → WiFi Connection**
4. Enter your device's IP address
5. Launch the addon
6. Select **Stream WiFi Device**

## Exiting scrcpy

- **Keyboard**: Press `MOD + Q` (MOD is configurable in settings)
- **Unplug USB**: Device disconnection automatically exits
- **Close window**: Alt+F4 or click close button

## Keyboard Shortcuts

Default modifier key is Left Alt. Can be changed in Settings → Controls.

| Shortcut | Action |
|----------|--------|
| MOD + Q | Quit scrcpy |
| MOD + F | Toggle fullscreen |
| MOD + G | Resize to 1:1 pixel ratio |
| MOD + H | Home button |
| MOD + B | Back button |
| MOD + S | App switch |
| MOD + P | Power button |
| MOD + O | Turn device screen off |
| MOD + N | Expand notifications |
| MOD + Shift + N | Collapse notifications |

## Settings

### WiFi Connection
- **IP Address** - Your Android device's IP
- **Port** - ADB port (default: 5555)

### Video
- **Render Driver** - Auto, OpenGLES2, OpenGL, or Software
- **Max FPS** - Limit framerate (0 = unlimited)
- **Max Size** - Limit resolution (0 = original)
- **Bitrate** - Video bitrate (e.g. 8M, 16M)
- **Video Codec** - H264, H265, or AV1
- **Crop** - Crop area (width:height:x:y)
- **Fullscreen** - Start in fullscreen mode

### Audio
- **Enable Audio** - Forward device audio (Android 11+)
- **Audio Codec** - Opus, AAC, FLAC, or Raw

### Device
- **Stay Awake** - Keep device awake while streaming
- **Turn Screen Off** - Turn off device screen while streaming

### Controls
- **Quit Key Modifier** - Key modifier for shortcuts (LAlt, LSuper, RAlt, RSuper)

### Advanced
- **Extra Arguments** - Additional scrcpy command line arguments

## Troubleshooting

### Check the log file (LibreELEC)
```bash
cat /storage/.kodi/temp/scrcpy.log
```

### Device not detected
- Ensure USB debugging is enabled
- Try a different USB cable
- Accept the USB debugging prompt on your device

### WiFi connection fails
- Ensure device and Kodi are on same network
- Check IP address is correct
- Try `adb tcpip 5555` via USB first

### No audio
- Audio requires Android 11 or higher
- Check audio is enabled in settings

## Supported Architectures

- x86_64 (Intel/AMD 64-bit)
- aarch64 (ARM 64-bit)

## Credits

- [scrcpy](https://github.com/Genymobile/scrcpy) by Genymobile
- [ADB](https://developer.android.com/studio/command-line/adb) by Google

## License

MIT License
