# Heroic Games Launcher plugin for AKL (`script.akl.heroic`)

A plugin for [Advanced Kodi Launcher (AKL)](https://github.com/chrisism/plugin.program.akl)
that brings your [Heroic Games Launcher](https://heroicgameslauncher.com/) library
(Epic Games Store, GOG, Amazon Games and sideloaded games) into Kodi on Linux, Windows and macOS.

Everything is read straight from the files Heroic already keeps on disk — no
accounts, credentials or API keys are needed.

## Features

- **Scanner**: scans Heroic's local store cache (`store_cache/*_library.json` and
  `sideload_apps/library.json`) and adds your games as ROMs in AKL. Works with
  the Flatpak and native (deb/rpm/AppImage) installations on Linux, the standard
  Windows install (`%APPDATA%\heroic`) and the macOS app
  (`~/Library/Application Support/heroic`), all with auto-detection.
  Optionally limits the scan to installed games. Uninstalled or removed games
  are cleaned up as dead ROMs on rescan.
- **Launcher**: launches games through Heroic using its `heroic://launch/<runner>/<app_name>`
  URL protocol, via `flatpak run com.heroicgameslauncher.hgl`, a native `heroic`
  binary, `Heroic.exe` on Windows (auto-detected in `%LOCALAPPDATA%\Programs\heroic`
  or Program Files), `Heroic.app` on macOS, or a custom executable you pick.
- **Scraper**: fills in title, developer, plot and artwork (poster/boxfront,
  fanart, icon, clearlogo) from the metadata and art URLs in Heroic's own
  library cache.

## Requirements

- Linux, Windows or macOS with Kodi 19 (Matrix) or newer
- [Advanced Kodi Launcher](https://github.com/chrisism/plugin.program.akl) with `script.module.akl` 1.2.0+
- Heroic Games Launcher installed (Flatpak/native on Linux, the Windows
  installer, or Heroic.app on macOS) and logged in at least once so its
  library cache exists

## Usage

1. Install this addon (available from the [Breezyslasher repository](https://github.com/Breezyslasher/Kodi-Addons)).
2. In AKL, create or edit a collection/source and pick **Heroic Games Library** as the scanner.
   Choose your Heroic installation type (auto-detect works for most setups) and scan.
3. Assign **Heroic Games Launcher** as the launcher and pick how Heroic is installed
   (Flatpak / native / Windows / macOS / custom executable).
4. Optionally run the **Heroic Library Data** scraper to pull in artwork and descriptions.

## Notes

- Games launch through Heroic itself, so Proton/Wine settings, cloud saves and
  playtime tracking keep working exactly as they do when launching from Heroic.
- `tools/export_heroic_library.py` is a small standalone script (no Kodi needed)
  that dumps your Heroic library to JSON — handy for debugging what the scanner sees.

## License

GNU General Public License version 2. Based on the AKL plugin architecture by
[chrisism](https://github.com/chrisism) (script.akl.defaults / script.akl.steam).
