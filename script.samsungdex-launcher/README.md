# Samsung DeX Launcher for Kodi

Stream the **Samsung DeX desktop** to your Kodi box (TV, HTPC, LibreELEC)
over USB or WiFi — turning Kodi into a wireless DeX monitor.

Inspired by [TuxDex](https://github.com/semarainc/TuxDex), rebuilt around
scrcpy 3.x with several improvements:

| | TuxDex | This addon |
|---|---|---|
| Wireless transport | Miracast via miraclecast | ADB over WiFi (or USB) |
| Root required | Yes (`pkexec`) | No |
| Network changes | Disables WiFi/NetworkManager | None |
| DeX display | Hardcoded `--display 2` | Auto-detected via `--list-displays` |
| Audio | ffplay RTP sidecar | Native scrcpy audio (Android 11+) |
| No DeX session? | Not supported | Virtual desktop via `--new-display` |
| Platform | Desktop Linux only | Desktop Linux, Flatpak Kodi, LibreELEC |

## How it works

Samsung phones expose the DeX desktop as a **second display** (usually id 2)
whenever a DeX session is active. scrcpy can mirror any display, so the addon:

1. Connects adb (USB, or `ip:port` over WiFi)
2. Runs `scrcpy --list-displays` and picks the non-phone display
   (or uses a fixed id from settings)
3. Launches `scrcpy --display-id=N --fullscreen --forward-all-clicks ...`

**Virtual Desktop mode** skips DeX entirely: `scrcpy --new-display=1920x1080`
creates a virtual display on the phone with desktop-style windowing
(One UI 7 / Android 15+, app launcher on older versions). Useful when no
DeX session is running and you don't have a dock.

## Requirements

- Samsung phone with DeX (S/Note/Fold series) — or any Android 15+ phone
  for Virtual Desktop mode
- USB debugging enabled; for WiFi, wireless debugging or `adb tcpip 5555`
- **scrcpy Launcher addon** (dependency — provides the adb/scrcpy binaries)
- Kodi 19+ on Linux (desktop, Flatpak, or LibreELEC)

## Starting a DeX session for mirroring

The DeX display only exists while DeX is running. Options:

- Phone connected to a DeX dock/HDMI adapter (mirror that session remotely)
- Wireless DeX to any nearby Miracast TV
- *(No session at all?)* → use **Virtual Desktop** mode instead
- *(Advanced)* AOSP desktop mode on the external display:
  `adb shell settings put global force_desktop_mode_on_external_displays 1`
  (reboot required; behavior varies by One UI version)

## Menu

- **Launch DeX (USB / WiFi)** — mirror the DeX display
- **Virtual Desktop (USB / WiFi)** — create + mirror a virtual desktop
- **Detect Displays** — list display ids the phone reports
- **Settings** — connection, display id, resolution, video/audio, controls

On LibreELEC, Kodi is stopped while streaming and restarted when scrcpy
exits (same mechanism as scrcpy Launcher). Log: `/storage/.kodi/temp/dex.log`.

## Known limitations

- WiFi requires adb-over-TCP to be enabled on the phone first
- Keyboard/mouse are forwarded by scrcpy; the experience is best with a
  USB/Bluetooth mouse + keyboard on the Kodi box
- Quit key defaults to scrcpy's (Mod+q); configurable in settings
