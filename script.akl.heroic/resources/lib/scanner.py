# -*- coding: utf-8 -*-
#
# Advanced Kodi Launcher: Heroic Games Launcher library scanner implementation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# --- Python standard library ---
from __future__ import unicode_literals
from __future__ import division

import logging
import typing
import collections

# --- AKL packages ---
from akl import report, api
from akl.utils import kodi, io

from akl.scanners import RomScannerStrategy, ROMCandidateABC

# Local modules
from resources.lib import heroic


class HeroicCandidate(ROMCandidateABC):

    def __init__(self, game: dict, config_dir: str):
        self.game = game
        self.config_dir = config_dir
        super(HeroicCandidate, self).__init__()

    def get_ROM(self) -> api.ROMObj:
        rom = api.ROMObj()
        rom.set_name(self.get_name())
        scanned_data = {
            'identifier': self.get_app_name(),
            'app_name': self.get_app_name(),
            'runner': heroic.get_runner(self.game),
            'heroic_title': self.get_name(),
            'install_path': heroic.get_install_path(self.game),
            'heroic_config': self.config_dir,
            'scanner': kodi.get_addon_id(),
            'scanner_version': kodi.get_addon_version()
        }
        rom.set_scanned_data(scanned_data)
        return rom

    def get_sort_value(self):
        return heroic.get_title(self.game)

    def get_app_name(self):
        return self.game.get('app_name')

    def get_name(self):
        return heroic.get_title(self.game)


class HeroicScanner(RomScannerStrategy):

    def __init__(self,
                 reports_dir: io.FileName,
                 source_id: str,
                 webservice_host: str,
                 webservice_port: int,
                 progress_dialog: kodi.ProgressDialog):
        self.logger = logging.getLogger(__name__)
        super(HeroicScanner, self).__init__(reports_dir, source_id,
                                            webservice_host, webservice_port,
                                            progress_dialog)

    # --------------------------------------------------------------------------------------------
    # Core methods
    # --------------------------------------------------------------------------------------------
    def get_name(self) -> str:
        return 'Heroic Library scanner'

    def get_scanner_addon_id(self) -> str:
        addon_id = kodi.get_addon_id()
        return addon_id

    def get_heroic_config_dir(self) -> str:
        path = self.scanner_settings.get('heroic_path')
        if path and path != 'AUTO':
            return path
        return heroic.detect_config_dir()

    def installed_only(self) -> bool:
        return self.scanner_settings.get('installed_only', True)

    # --------------------------------------------------------------------------------------------
    # Scanner configuration wizard methods
    # --------------------------------------------------------------------------------------------
    def _configure_get_wizard(self, wizard) -> kodi.WizardDialog:
        wizard = kodi.WizardDialog_DictionarySelection(
            wizard, 'heroic_path', 'Heroic installation type',
            self._get_config_dir_options)
        wizard = kodi.WizardDialog_FileBrowse(
            wizard, 'heroic_path', 'Select the Heroic config directory', 0, '',
            conditionalFunction=self._user_selected_custom_browsing)
        wizard = kodi.WizardDialog_YesNo(
            wizard, 'installed_only', 'Installed games only',
            'Only add games that are installed in Heroic?')
        return wizard

    def _get_config_dir_options(self, item_key, properties):
        options = collections.OrderedDict()
        options['AUTO'] = 'Auto-detect Heroic configuration'
        options[heroic.flatpak_config_dir()] = 'Flatpak installation'
        options[heroic.native_config_dir()] = 'Native installation (deb/rpm/AppImage)'
        options['BROWSE'] = 'Browse for the Heroic config directory ...'
        return options

    def _user_selected_custom_browsing(self, item_key, properties):
        return properties.get(item_key) == 'BROWSE'

    def _configure_post_wizard_hook(self):
        path = self.scanner_settings.get('heroic_path')
        self.scanner_settings['secname'] = 'Heroic' if path in (None, 'AUTO') else path
        return True

    def _configure_get_edit_options(self) -> dict:
        installed_str = 'ON' if self.installed_only() else 'OFF'

        options = collections.OrderedDict()
        options[self._change_heroic_path] = 'Change Heroic config directory ({})'.format(
            self.scanner_settings.get('heroic_path', 'AUTO'))
        options[self._change_installed_only] = "Installed games only (now {})".format(installed_str)
        return options

    def _change_heroic_path(self):
        dialog = kodi.OrdDictionaryDialog()
        selected = dialog.select('Heroic installation type', self._get_config_dir_options(None, None))
        if selected is None:
            return
        if selected == 'BROWSE':
            current = self.scanner_settings.get('heroic_path', '')
            selected = kodi.browse(0, 'Select the Heroic config directory', 'files',
                                   preselected_path=current)
            if not selected:
                return
        self.scanner_settings['heroic_path'] = selected

    def _change_installed_only(self):
        self.scanner_settings['installed_only'] = not self.installed_only()

    # ---------------------------------------------------------------------------------------------
    # Execution methods
    # ---------------------------------------------------------------------------------------------
    # ~~~ Scan for new games and put them in a list ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def _getCandidates(self, launcher_report: report.Reporter) -> typing.List[ROMCandidateABC]:
        self.progress_dialog.startProgress('Reading Heroic library ...')

        config_dir = self.get_heroic_config_dir()
        launcher_report.write('Reading Heroic config at {}'.format(config_dir))

        if not config_dir:
            self.logger.warning('No Heroic configuration directory found')
            kodi.notify_warn('Heroic installation not found')
            self.progress_dialog.endProgress()
            return []

        self.progress_dialog.updateProgress(30)
        games = heroic.load_games(config_dir)
        self.progress_dialog.updateProgress(80)

        if self.installed_only():
            games = [g for g in games if heroic.is_installed(g)]

        num_games = len(games)
        launcher_report.write('  Library scanner found {} games'.format(num_games))

        self.progress_dialog.endProgress()
        return [*(HeroicCandidate(g, config_dir) for g in games)]

    # --- Get dead entries -----------------------------------------------------------------
    def _getDeadRoms(self, candidates: typing.List[ROMCandidateABC],
                     roms: typing.List[api.ROMObj]) -> typing.List[api.ROMObj]:
        dead_roms = []
        num_roms = len(roms)
        if num_roms == 0:
            self.logger.info('Source is empty. No dead ROM check.')
            return dead_roms

        self.logger.info('Starting dead items scan')
        i = 0

        self.progress_dialog.startProgress('Checking for dead ROMs ...', num_roms)

        candidate_app_names = set(c.get_app_name() for c in candidates)
        for rom in reversed(roms):
            app_name = rom.get_scanned_data_element('app_name')
            self.logger.info('Searching {}'.format(app_name))
            self.progress_dialog.updateProgress(i)

            if app_name not in candidate_app_names:
                self.logger.info('Not found. Marking as dead: {} {}'.format(app_name, rom.get_name()))
                roms.remove(rom)
                dead_roms.append(rom)
            i += 1

        self.progress_dialog.endProgress()
        return dead_roms

    # ~~~ Now go processing item by item ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def _processFoundItems(self,
                           candidates: typing.List[ROMCandidateABC],
                           roms: typing.List[api.ROMObj],
                           launcher_report: report.Reporter) -> typing.List[api.ROMObj]:

        num_items = len(candidates)
        new_roms: typing.List[api.ROMObj] = []

        self.progress_dialog.startProgress('Scanning found items', num_items)
        self.logger.debug('============================== Processing Heroic games ==============================')
        launcher_report.write('Processing games ...')
        num_items_checked = 0

        appsAlreadyInSource = set(rom.get_scanned_data_element('app_name') for rom in roms)

        for candidate in sorted(candidates, key=lambda c: c.get_sort_value()):

            heroic_candidate: HeroicCandidate = candidate
            app_name = heroic_candidate.get_app_name()

            self.logger.debug('Searching {} with app_name {}'.format(heroic_candidate.get_name(), app_name))
            self.progress_dialog.updateProgress(num_items_checked, heroic_candidate.get_name())

            if app_name in appsAlreadyInSource:
                self.logger.debug('  {} already in source. Skipping'.format(app_name))
                num_items_checked += 1
                continue

            self.logger.debug('========== Processing Heroic game ==========')
            launcher_report.write('>>> title: {}'.format(heroic_candidate.get_name()))
            launcher_report.write('>>> app_name: {}'.format(app_name))

            self.logger.debug('Not found. Item {} is new'.format(heroic_candidate.get_name()))

            # ~~~~~ Process new ROM and add to the list ~~~~~
            new_rom = heroic_candidate.get_ROM()
            new_roms.append(new_rom)

            # ~~~ Check if user pressed the cancel button ~~~
            if self.progress_dialog.isCanceled():
                self.progress_dialog.endProgress()
                kodi.dialog_OK('Stopping ROM scanning. No changes have been made.')
                self.logger.info('User pressed Cancel button when scanning ROMs. ROM scanning stopped.')
                return None

            num_items_checked += 1

        self.progress_dialog.endProgress()
        return new_roms
