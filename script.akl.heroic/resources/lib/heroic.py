# -*- coding: utf-8 -*-
#
# Advanced Kodi Launcher: Heroic Games Launcher library access
#
# Reads game information straight from the files the Heroic Games Launcher
# keeps on disk. No network calls or credentials are involved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

from __future__ import unicode_literals
from __future__ import division

import os
import json
import logging
import subprocess

logger = logging.getLogger(__name__)

# Heroic config directories, in detection order.
# Linux
FLATPAK_CONFIG_DIR = '~/.var/app/com.heroicgameslauncher.hgl/config/heroic'
NATIVE_CONFIG_DIR = '~/.config/heroic'
# Windows (Heroic keeps its config in the roaming AppData folder)
WINDOWS_CONFIG_DIR = '%APPDATA%\\heroic'
# macOS
MACOS_CONFIG_DIR = '~/Library/Application Support/heroic'

# Default locations of the Heroic executable on Windows, in detection order.
# The NSIS installer defaults to a per-user install under local AppData;
# a per-machine install lands in Program Files.
WINDOWS_EXE_CANDIDATES = [
    '%LOCALAPPDATA%\\Programs\\heroic\\Heroic.exe',
    '%PROGRAMFILES%\\Heroic\\Heroic.exe',
]

# Default locations of the Heroic app bundle binary on macOS, in detection
# order (system-wide and per-user Applications folders).
MACOS_EXE_CANDIDATES = [
    '/Applications/Heroic.app/Contents/MacOS/Heroic',
    '~/Applications/Heroic.app/Contents/MacOS/Heroic',
]

# Runner ids used by Heroic in its library files.
RUNNER_NAMES = {
    'legendary': 'Epic Games Store',
    'gog': 'GOG',
    'nile': 'Amazon Games',
    'sideload': 'Sideloaded'
}


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


# ------------------------------------------------------------------------------------------------
# Flatpak sandbox support.
# When Kodi itself runs as a Flatpak (tv.kodi.Kodi) its sandbox blocks access to
# other applications' data (~/.var/app/...) and usually to ~/.config as well, so
# Heroic's files must be reached on the host through the flatpak-spawn portal.
# ------------------------------------------------------------------------------------------------
_HOST_CMD_TIMEOUT = 15


def kodi_in_flatpak() -> bool:
    """True when Kodi itself is running inside a Flatpak sandbox."""
    return os.path.exists('/.flatpak-info')


def _host_output(args: list):
    """Runs a command on the host through flatpak-spawn. Returns its stdout as
    text, or None when the command failed or the portal is unavailable."""
    try:
        result = subprocess.run(['flatpak-spawn', '--host'] + args,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=_HOST_CMD_TIMEOUT)
    except Exception as ex:
        logger.debug('flatpak-spawn --host %s failed: %s', args, ex)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace')


def _isdir(path: str) -> bool:
    if os.path.isdir(path):
        return True
    if kodi_in_flatpak():
        return _host_output(['test', '-d', path]) is not None
    return False


def _isfile(path: str) -> bool:
    if os.path.isfile(path):
        return True
    if kodi_in_flatpak():
        return _host_output(['test', '-f', path]) is not None
    return False


def _listdir(path: str) -> list:
    try:
        return os.listdir(path)
    except OSError:
        pass
    if kodi_in_flatpak():
        output = _host_output(['ls', '-1', path])
        if output is not None:
            return [line for line in output.splitlines() if line]
    return []


def _read_text(filepath: str):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError:
        pass
    if kodi_in_flatpak():
        return _host_output(['cat', filepath])
    return None


def flatpak_config_dir() -> str:
    return _expand(FLATPAK_CONFIG_DIR)


def native_config_dir() -> str:
    return _expand(NATIVE_CONFIG_DIR)


def windows_config_dir() -> str:
    return _expand(WINDOWS_CONFIG_DIR)


def macos_config_dir() -> str:
    return _expand(MACOS_CONFIG_DIR)


def config_dir_candidates() -> list:
    """Possible Heroic config directories for every supported platform."""
    if os.name == 'nt':
        return [windows_config_dir()]
    return [flatpak_config_dir(), native_config_dir(), macos_config_dir()]


def detect_config_dir() -> str:
    """Returns the first existing Heroic config directory or None."""
    for path in config_dir_candidates():
        if _isdir(path):
            return path
    return None


def detect_windows_executable() -> str:
    """Returns the Heroic executable on Windows: the first candidate that
    exists, or the default per-user install path when none is found yet."""
    for candidate in WINDOWS_EXE_CANDIDATES:
        expanded = _expand(candidate)
        if os.path.isfile(expanded):
            return expanded
    return _expand(WINDOWS_EXE_CANDIDATES[0])


def detect_macos_executable() -> str:
    """Returns the Heroic app bundle binary on macOS: the first candidate
    that exists, or the default /Applications path when none is found yet."""
    for candidate in MACOS_EXE_CANDIDATES:
        expanded = _expand(candidate)
        if os.path.isfile(expanded):
            return expanded
    return _expand(MACOS_EXE_CANDIDATES[0])


def get_library_files(config_dir: str) -> list:
    """All library JSON files under a Heroic config directory."""
    if not config_dir:
        return []
    config_dir = os.path.expanduser(config_dir)
    files = []
    store_cache = os.path.join(config_dir, 'store_cache')
    for fname in sorted(_listdir(store_cache)):
        if fname.endswith('_library.json'):
            files.append(os.path.join(store_cache, fname))
    sideload = os.path.join(config_dir, 'sideload_apps', 'library.json')
    if _isfile(sideload):
        files.append(sideload)
    return files


def _read_games_from_file(filepath: str) -> list:
    text = _read_text(filepath)
    if text is None:
        logger.warning('Failed to read Heroic library file %s', filepath)
        return []
    try:
        data = json.loads(text)
    except Exception as ex:
        logger.warning('Failed to parse Heroic library file %s: %s', filepath, ex)
        return []
    if not isinstance(data, dict):
        return []
    games = data.get('games') or data.get('library') or []
    return games if isinstance(games, list) else []


def load_games(config_dir: str) -> list:
    """
    Loads all games from a Heroic config directory.
    Returns a list of the raw game dictionaries from Heroic, deduplicated
    by (runner, app_name) and without DLC entries.
    """
    games = []
    seen = set()
    for filepath in get_library_files(config_dir):
        for game in _read_games_from_file(filepath):
            if not isinstance(game, dict):
                continue
            app_name = game.get('app_name')
            if not app_name:
                continue
            install = game.get('install') or {}
            if install.get('is_dlc'):
                continue
            key = (game.get('runner', ''), app_name)
            if key in seen:
                continue
            seen.add(key)
            games.append(game)
    logger.debug('Loaded %d games from Heroic config at %s', len(games), config_dir)
    return games


def find_game(config_dir: str, app_name: str) -> dict:
    """Finds a single game by its Heroic app_name. Returns None when absent."""
    if not app_name:
        return None
    for game in load_games(config_dir):
        if game.get('app_name') == app_name:
            return game
    return None


def get_title(game: dict) -> str:
    return game.get('title') or game.get('app_name') or 'Unknown'


def get_runner(game: dict) -> str:
    return game.get('runner') or 'sideload'


def is_installed(game: dict) -> bool:
    if game.get('is_installed'):
        return True
    install = game.get('install') or {}
    return bool(install.get('is_installed') or install.get('install_path') or install.get('executable'))


def get_install_path(game: dict) -> str:
    install = game.get('install') or {}
    return install.get('install_path') or install.get('path') or ''


def get_developer(game: dict) -> str:
    return game.get('developer') or ''


def get_plot(game: dict) -> str:
    extra = game.get('extra') or {}
    about = extra.get('about') or {}
    return (about.get('description')
            or about.get('shortDescription')
            or game.get('description')
            or '')


def get_art(game: dict) -> dict:
    """Maps Heroic artwork fields to AKL asset ids."""
    art = {}
    if game.get('art_square'):
        art['boxfront'] = game['art_square']
    if game.get('art_cover'):
        art['fanart'] = game['art_cover']
    if game.get('art_icon'):
        art['icon'] = game['art_icon']
    if game.get('art_logo'):
        art['clearlogo'] = game['art_logo']
    return art
