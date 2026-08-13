# -*- coding: utf-8 -*-
#
# Advanced Kodi Launcher scraping engine for the Heroic Games Launcher.
#
# Scrapes metadata and artwork from the library data the Heroic Games
# Launcher already keeps on disk (store cache). Fully offline.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.

# --- Python standard library ---
from __future__ import unicode_literals
from __future__ import division

import logging
import re

# --- AKL packages ---
from akl import constants, settings
from akl.utils import io
from akl.scrapers import Scraper
from akl.api import ROMObj

# Local modules
from resources.lib import heroic


# ------------------------------------------------------------------------------------------------
# Heroic library scraper (metadata and assets).
# Uses the artwork URLs and descriptions from Heroic's own library cache.
# ------------------------------------------------------------------------------------------------
class HeroicScraper(Scraper):
    # --- Class variables ------------------------------------------------------------------------
    supported_metadata_list = [
        constants.META_TITLE_ID,
        constants.META_YEAR_ID,
        constants.META_GENRE_ID,
        constants.META_DEVELOPER_ID,
        constants.META_RATING_ID,
        constants.META_PLOT_ID,
        constants.META_TAGS_ID
    ]
    supported_asset_list = [
        constants.ASSET_BOXFRONT_ID,
        constants.ASSET_POSTER_ID,
        constants.ASSET_FANART_ID,
        constants.ASSET_BANNER_ID,
        constants.ASSET_SNAP_ID,
        constants.ASSET_ICON_ID,
        constants.ASSET_CLEARLOGO_ID
    ]

    # --- Constructor ----------------------------------------------------------------------------
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.games_by_app_name = {}
        self.config_dir = None
        self.extra_cache = {}

        cache_dir = settings.getSettingAsFilePath('scraper_cache_dir')
        super(HeroicScraper, self).__init__(cache_dir)

    # --- Base class abstract methods ------------------------------------------------------------
    def get_name(self):
        return 'Heroic Library scraper'

    def get_filename(self):
        return 'heroic'

    def supports_disk_cache(self):
        return False

    def supports_search_string(self):
        return False

    def supports_metadata_ID(self, metadata_ID):
        return metadata_ID in HeroicScraper.supported_metadata_list

    def supports_metadata(self):
        return True

    def supports_asset_ID(self, asset_ID):
        return asset_ID in HeroicScraper.supported_asset_list

    def supports_assets(self):
        return True

    def check_before_scraping(self, status_dic):
        # Try to preload the library. Never disable the scraper here: the ROMs
        # passed to get_candidates() may carry their own Heroic config path in
        # the scanned data (stored by the Heroic library scanner).
        self._load_library()
        if not self.games_by_app_name:
            self.logger.warning('No Heroic library data found yet.')

    def get_candidates(self, search_term, rom: ROMObj, platform, status_dic):
        if self.scraper_disabled:
            self.logger.debug('Scraper disabled. Returning empty data for candidates.')
            return None

        self._load_library(rom)

        app_name = rom.get_scanned_data_element('app_name') or rom.get_identifier()
        self.logger.debug('search_term    "{}"'.format(search_term))
        self.logger.debug('app_name       "{}"'.format(app_name))

        game = self.games_by_app_name.get(app_name)
        if game is None:
            game = self._search_by_title(search_term or rom.get_name())
        if game is None:
            self.logger.debug('No Heroic library entry found')
            return []

        candidate = self._new_candidate_dic()
        candidate['id'] = game.get('app_name')
        candidate['display_name'] = heroic.get_title(game)
        candidate['order'] = 1
        return [candidate]

    def get_metadata(self, status_dic):
        if self.scraper_disabled:
            self.logger.debug('Scraper disabled. Returning empty data.')
            return self._new_gamedata_dic()

        game = self.games_by_app_name.get(self.candidate['id']) if self.candidate else None
        gamedata = self._new_gamedata_dic()
        if game is None:
            return gamedata

        extra = self._get_extra(game)

        library_plot = self._clean_HTML_from_text(heroic.get_plot(game))
        extra_plot = self._clean_HTML_from_text(extra['plot'])

        gamedata['title'] = heroic.get_title(game)
        gamedata['developer'] = heroic.get_developer(game) or extra['developer']
        # Prefer the longer of the two descriptions.
        gamedata['plot'] = extra_plot if len(extra_plot) > len(library_plot) else library_plot
        gamedata['year'] = extra['year']
        gamedata['genre'] = ', '.join(extra['genres'])
        if extra['rating'] is not None:
            gamedata['rating'] = extra['rating']
        gamedata['tags'] = [heroic.get_store_tag(game)]
        return gamedata

    def get_assets(self, asset_info_id: str, status_dic):
        if self.scraper_disabled:
            self.logger.debug('Scraper disabled. Returning empty data.')
            return []

        game = self.games_by_app_name.get(self.candidate['id']) if self.candidate else None
        if game is None:
            return []

        extra = self._get_extra(game)
        title = heroic.get_title(game)

        if asset_info_id == constants.ASSET_SNAP_ID:
            assets_list = []
            for index, url in enumerate(extra['screenshots']):
                if not self._is_downloadable(url):
                    continue
                asset_data = self._new_assetdata_dic()
                asset_data['asset_ID'] = asset_info_id
                asset_data['display_name'] = '{} screenshot #{}'.format(title, index + 1)
                asset_data['url_thumb'] = url
                asset_data['url'] = url
                assets_list.append(asset_data)
            return assets_list

        # Library art first, enrichment art (Epic key images, Amazon urls) as fallback.
        art = dict(extra['art'])
        art.update(heroic.get_art(game))
        url = art.get(asset_info_id)
        if not url:
            return []
        if not self._is_downloadable(url):
            # Sideloaded games may reference local file:// art that AKL's
            # downloader cannot fetch. Skip those instead of erroring.
            self.logger.debug('Skipping non-downloadable art URL: {}'.format(url))
            return []

        asset_data = self._new_assetdata_dic()
        asset_data['asset_ID'] = asset_info_id
        asset_data['display_name'] = '{} ({})'.format(title, asset_info_id)
        asset_data['url_thumb'] = url
        asset_data['url'] = url
        return [asset_data]

    def _is_downloadable(self, url: str) -> bool:
        return isinstance(url, str) and url.startswith(('http://', 'https://'))

    def _get_extra(self, game: dict) -> dict:
        app_name = game.get('app_name')
        if app_name not in self.extra_cache:
            self.extra_cache[app_name] = heroic.get_extra_game_data(self.config_dir, game)
        return self.extra_cache[app_name]

    def resolve_asset_URL(self, selected_asset, status_dic):
        url = selected_asset['url']
        return url, url

    def resolve_asset_URL_extension(self, selected_asset, image_url, status_dic):
        ext = io.get_URL_extension(image_url)
        return ext if ext else 'jpg'

    # --- Internal methods -----------------------------------------------------------------------
    def _load_library(self, rom: ROMObj = None):
        config_dir = None
        if rom is not None:
            config_dir = rom.get_scanned_data_element('heroic_config')
        if not config_dir:
            config_dir = settings.getSetting('heroic_config_dir')
        if not config_dir:
            config_dir = heroic.detect_config_dir()

        if not config_dir or config_dir == self.config_dir:
            return

        self.config_dir = config_dir
        self.games_by_app_name = {}
        self.extra_cache = {}
        for game in heroic.load_games(config_dir):
            app_name = game.get('app_name')
            if app_name:
                self.games_by_app_name[app_name] = game
        self.logger.debug('Loaded {} Heroic games for scraping'.format(len(self.games_by_app_name)))

    def _search_by_title(self, search_term: str):
        if not search_term:
            return None
        term = search_term.lower().strip()
        for game in self.games_by_app_name.values():
            if heroic.get_title(game).lower() == term:
                return game
        for game in self.games_by_app_name.values():
            if term in heroic.get_title(game).lower():
                return game
        return None

    def _clean_HTML_from_text(self, txt):
        if not txt:
            return ''
        cleaned = re.sub('<[^<]+?>', '', txt)
        return cleaned
