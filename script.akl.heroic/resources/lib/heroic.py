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
        art['poster'] = game['art_square']
    if game.get('art_cover'):
        art['fanart'] = game['art_cover']
    if game.get('art_icon'):
        art['icon'] = game['art_icon']
    if game.get('art_logo'):
        art['clearlogo'] = game['art_logo']
    return art


# ------------------------------------------------------------------------------------------------
# Extra game data enrichment.
# Beyond the library files, Heroic and the store clients it embeds keep richer
# caches on disk: legendary's per-game Epic metadata (descriptions, key art),
# nile's raw Amazon library (details, genres, screenshots) and the PCGamingWiki
# cache (review scores). All reads are best-effort and fully offline.
# ------------------------------------------------------------------------------------------------
STORE_TAGS = {
    'legendary': 'epic',
    'gog': 'gog',
    'nile': 'amazon',
    'sideload': 'sideload'
}


def _load_json(filepath: str):
    text = _read_text(filepath)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception as ex:
        logger.debug('Failed to parse %s: %s', filepath, ex)
        return None


def _legendary_metadata_dirs(config_dir: str) -> list:
    """Locations of legendary's Epic metadata cache, newest layout first:
    Heroic's own legendary config dir, then the legacy shared one."""
    config_dir = os.path.expanduser(config_dir)
    return [
        os.path.join(config_dir, 'legendaryConfig', 'legendary', 'metadata'),
        os.path.join(os.path.dirname(config_dir), 'legendary', 'metadata'),
    ]


def _get_epic_metadata(config_dir: str, app_name: str) -> dict:
    for metadata_dir in _legendary_metadata_dirs(config_dir):
        data = _load_json(os.path.join(metadata_dir, app_name + '.json'))
        if isinstance(data, dict):
            return data.get('metadata') or {}
    return {}


def _get_amazon_details(config_dir: str, app_name: str) -> dict:
    library = _load_json(os.path.join(os.path.expanduser(config_dir),
                                      'nile_config', 'nile', 'library.json'))
    if not isinstance(library, list):
        return {}
    for item in library:
        if not isinstance(item, dict):
            continue
        product = item.get('product') or {}
        if item.get('id') == app_name or product.get('id') == app_name:
            detail = product.get('productDetail') or {}
            return detail.get('details') or detail or {}
    return {}


def _get_wiki_scores(config_dir: str, title: str) -> dict:
    wiki = _load_json(os.path.join(os.path.expanduser(config_dir),
                                   'store_cache', 'wikigameinfo.json'))
    if not isinstance(wiki, dict):
        return {}
    entry = wiki.get(title)
    return entry if isinstance(entry, dict) else {}


def _year_from(date_str) -> str:
    if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
        return date_str[:4]
    return ''


def get_extra_game_data(config_dir: str, game: dict) -> dict:
    """Best-effort enrichment for a single game from the other caches Heroic
    keeps on disk. Returns plot/developer/year/genres/rating plus additional
    art URLs and screenshots; every field may be empty."""
    extra = {'plot': '', 'developer': '', 'year': '',
             'genres': [], 'rating': None, 'art': {}, 'screenshots': []}
    if not config_dir:
        return extra

    runner = get_runner(game)
    app_name = game.get('app_name')

    if runner == 'legendary' and app_name:
        md = _get_epic_metadata(config_dir, app_name)
        extra['plot'] = md.get('description') or ''
        custom = md.get('customAttributes') or {}
        dev = md.get('developer')
        if not dev:
            dev_attr = custom.get('DeveloperName') or {}
            dev = dev_attr.get('value') if isinstance(dev_attr, dict) else None
        extra['developer'] = dev or ''
        release_attr = custom.get('ReleaseDate') or {}
        if isinstance(release_attr, dict):
            extra['year'] = _year_from(release_attr.get('value'))
        for image in md.get('keyImages') or []:
            if not isinstance(image, dict):
                continue
            image_type = image.get('type')
            url = image.get('url')
            if not url:
                continue
            if image_type in ('DieselGameBoxTall', 'OfferImageTall'):
                extra['art'].setdefault('poster', url)
                extra['art'].setdefault('boxfront', url)
            elif image_type in ('DieselGameBoxWide', 'OfferImageWide', 'DieselStoreFrontWide'):
                extra['art'].setdefault('banner', url)
                extra['art'].setdefault('fanart', url)
            elif image_type == 'DieselGameBoxLogo':
                extra['art'].setdefault('clearlogo', url)
            elif image_type == 'Screenshot':
                extra['screenshots'].append(url)

    elif runner == 'nile' and app_name:
        details = _get_amazon_details(config_dir, app_name)
        extra['plot'] = details.get('description') or details.get('shortDescription') or ''
        extra['developer'] = details.get('developer') or details.get('publisher') or ''
        extra['year'] = _year_from(details.get('releaseDate'))
        genres = details.get('genres')
        if isinstance(genres, list):
            extra['genres'] = [str(g) for g in genres if g]
        screenshots = details.get('screenshots')
        if isinstance(screenshots, list):
            extra['screenshots'] = [s for s in screenshots if isinstance(s, str)]
        if details.get('backgroundUrl2') or details.get('backgroundUrl1'):
            extra['art'].setdefault('fanart', details.get('backgroundUrl2') or details.get('backgroundUrl1'))
        if details.get('logoUrl'):
            extra['art'].setdefault('clearlogo', details['logoUrl'])
        if details.get('iconUrl'):
            extra['art'].setdefault('icon', details['iconUrl'])

    # GOG (and any runner): the library entry's own 'extra' block may carry
    # genres and a release date once Heroic has cached the game's store page.
    game_extra = game.get('extra') or {}
    if not extra['genres'] and isinstance(game_extra.get('genres'), list):
        extra['genres'] = [str(g) for g in game_extra['genres'] if g]
    if not extra['year']:
        extra['year'] = _year_from(game_extra.get('releaseDate'))

    # Review score from Heroic's PCGamingWiki cache (0-10 scale for Kodi).
    scores = _get_wiki_scores(config_dir, get_title(game))
    for source_key in ('metacritic', 'opencritic', 'igdb'):
        source = scores.get(source_key)
        if isinstance(source, dict) and source.get('score'):
            try:
                extra['rating'] = round(float(source['score']) / 10, 1)
                break
            except (TypeError, ValueError):
                pass

    return extra


def get_store_tag(game: dict) -> str:
    return STORE_TAGS.get(get_runner(game), get_runner(game))
