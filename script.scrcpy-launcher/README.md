# scrcpy Launcher for Kodi

Stream your Android device screen — or the **Samsung DeX desktop** — to Kodi using scrcpy. Supports USB and WiFi connections on LibreELEC, OSMC, and desktop Linux with Flatpak Kodi.

https://github.com/user-attachments/assets/5d711656-cce1-4eb5-8446-dbc04ccc1aab

## Features

- **USB Streaming** - Connect via USB cable for lowest latency
- **WiFi Streaming** - Connect via ADB over WiFi
- **Samsung DeX** - Mirror the DeX desktop (display id auto-detected)
- **Virtual Desktop** - Desktop-style windowing without a DeX session (`--new-display`, One UI 7 / Android 15+)
- **LibreELEC/OSMC Support** - Automatically stops/restarts Kodi for fullscreen streaming
- **Flatpak Support** - Works with Flatpak Kodi on desktop Linux
- **Audio Forwarding** - Stream device audio (Android 11+)
- **Configurable Video** - FPS, bitrate, codec, resolution settings
- **Keyboard Shortcuts** - Configurable quit key and device controls

## Samsung DeX

Samsung phones expose the DeX desktop as a second display (usually id 2)
whenever a DeX session is active. The addon connects adb, finds the DeX
display via `scrcpy --list-displays` (or a fixed id in Settings → Samsung
DeX), and mirrors it fullscreen with right-click forwarding.

Inspired by [TuxDex](https://github.com/semarainc/TuxDex), but with no
miraclecast, no root, and no network reconfiguration.

The DeX display only exists while DeX is running — start it via a DeX
dock/HDMI adapter or wireless DeX, or use **Virtual Desktop** mode which
needs no DeX session at all.

> **Note on DRM apps (Netflix, Disney+, etc.):** DRM apps mark their
> video surface as protected, so any screen capture — scrcpy included —
> shows a black/blank video area. This is enforced by Android/Widevine
> and cannot be fixed in scrcpy. It works on a real DeX dock because the
> HDMI link is HDCP-protected. 
## Requirements

- Android device with USB debugging enabled
- USB cable (for USB mode) or WiFi network (for WiFi mode)
- LibreELEC, OSMC, or Linux with Kodi

## Installation

1. Download the latest zip file
2. In Kodi: Settings → Add-ons → Install from zip file
3. Select the downloaded zip

## Usage

### Streaming the phone screen

1. Enable USB debugging on the device
2. *(WiFi only)* Run `adb tcpip 5555` over USB once, or enable Wireless
   debugging (Android 11+), then set the device IP in
   **Settings → WiFi Connection**
3. Launch the addon → **Stream Device**
4. If an IP is set you'll be asked USB or WiFi; otherwise it goes
   straight over USB

### Samsung DeX vs Virtual Desktop

Both put a desktop-style Android screen on Kodi, but they get there
differently:

| | Samsung DeX | Virtual Desktop |
|---|---|---|
| What it shows | Samsung's real DeX desktop (taskbar, windowed apps) | A scrcpy-created virtual display |
| Needs a DeX session running? | **Yes** (dock, HDMI adapter, or wireless DeX) | **No** — scrcpy makes the display |
| Works on non-Samsung phones? | No | Yes (Android 14+, best on 15 / One UI 7) |
| Use when | You already have DeX active and want the full Samsung UI | You have no dock / no DeX session, or a non-Samsung phone |

So you keep both: **DeX** mirrors a session you've already started;
**Virtual Desktop** conjures one when you can't. If you mostly use a dock,
DeX is your button; if you just want a desktop with nothing plugged in,
Virtual Desktop is the one that works.

**Samsung DeX:**
1. Start a DeX session on the phone (dock, HDMI adapter, or wireless DeX)
2. Launch the addon → **Samsung DeX**
3. Pick USB or WiFi (only asked if an IP is set in Settings)
4. The DeX display is auto-detected; set a fixed id in **Settings → Samsung DeX** if needed

**Virtual Desktop (no DeX session needed):**
1. Launch the addon → **Virtual Desktop**
2. A virtual display is created at the resolution from **Settings → Samsung DeX** (default 1920x1080)
3. On One UI 7 / Android 15+ this gives desktop-style windowing; older versions show an app launcher

**Detect Displays** in the menu lists every display id the phone reports —
handy for setting a fixed DeX display id.

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
