# -*- coding: utf-8 -*-
#
# Advanced Kodi Launcher: Heroic Games Launcher launcher implementation
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
import collections

# --- AKL packages ---
from akl.utils import kodi, io
from akl.launchers import LauncherABC, ExecutorFactoryABC, ExecutionSettings

# Local modules
from resources.lib import heroic

logger = logging.getLogger(__name__)

# Heroic protocol URI. $runner$ and $app_name$ get substituted by AKL with the
# values stored in the ROM's scanned data by the Heroic library scanner.
LAUNCH_URI_ARG = 'heroic://launch/$runner$/$app_name$'

FLATPAK_APP_ID = 'com.heroicgameslauncher.hgl'

MODE_FLATPAK = 'FLATPAK'
MODE_NATIVE = 'NATIVE'
MODE_WINDOWS = 'WINDOWS'
MODE_MACOS = 'MACOS'
MODE_CUSTOM = 'CUSTOM'


# -------------------------------------------------------------------------------------------------
# Launcher that starts games through a local Heroic Games Launcher installation.
# -------------------------------------------------------------------------------------------------
class HeroicLauncher(LauncherABC):

    def __init__(self,
                 launcher_id: str,
                 rom_id: str,
                 webservice_host: str,
                 webservice_port: int,
                 executorFactory: ExecutorFactoryABC = None,
                 execution_settings: ExecutionSettings = None):
        self.logger = logging.getLogger(__name__)
        super(HeroicLauncher, self).__init__(launcher_id, rom_id, webservice_host, webservice_port,
                                             executorFactory, execution_settings)

    # --------------------------------------------------------------------------------------------
    # Core methods
    # --------------------------------------------------------------------------------------------
    def get_name(self) -> str:
        return 'Heroic Launcher'

    def get_launcher_addon_id(self) -> str:
        addon_id = kodi.get_addon_id()
        return addon_id

    # --------------------------------------------------------------------------------------------
    # Launcher build wizard methods
    # --------------------------------------------------------------------------------------------
    #
    # Creates a new launcher using a wizard of dialogs. Called by parent build() method.
    #
    def _builder_get_wizard(self, wizard):
        wizard = kodi.WizardDialog_DictionarySelection(
            wizard, 'mode', 'How is Heroic installed?', self._get_mode_options)
        wizard = kodi.WizardDialog_FileBrowse(
            wizard, 'application', 'Select the Heroic executable', 1,
            self._get_appbrowser_filter, shares='programs',
            conditionalFunction=self._builder_wants_custom_app)
        return wizard

    def _get_mode_options(self, item_key, properties):
        options = collections.OrderedDict()
        if io.is_windows():
            options[MODE_WINDOWS] = 'Standard Windows installation'
        elif io.is_osx():
            options[MODE_MACOS] = 'Standard macOS installation (Heroic.app)'
        else:
            options[MODE_FLATPAK] = 'Flatpak installation'
            options[MODE_NATIVE] = 'Native installation (heroic in PATH)'
        options[MODE_CUSTOM] = 'Browse for the Heroic executable'
        return options

    def _get_appbrowser_filter(self, item_key, properties):
        return '.exe|.bat|.cmd|.lnk' if io.is_windows() else ''

    def _builder_wants_custom_app(self, item_key, properties) -> bool:
        return properties.get('mode') == MODE_CUSTOM

    def _build_post_wizard_hook(self):
        if io.is_windows():
            default_mode = MODE_WINDOWS
        elif io.is_osx():
            default_mode = MODE_MACOS
        else:
            default_mode = MODE_FLATPAK
        mode = self.launcher_settings.get('mode', default_mode)
        if mode == MODE_FLATPAK:
            self.launcher_settings['application'] = 'flatpak'
            self.launcher_settings['args'] = 'run {} --no-gui --no-splash "{}"'.format(
                FLATPAK_APP_ID, LAUNCH_URI_ARG)
        elif mode == MODE_NATIVE:
            self.launcher_settings['application'] = 'heroic'
            self.launcher_settings['args'] = '--no-gui --no-splash "{}"'.format(LAUNCH_URI_ARG)
        elif mode == MODE_WINDOWS:
            self.launcher_settings['application'] = heroic.detect_windows_executable()
            self.launcher_settings['args'] = '--no-gui --no-splash "{}"'.format(LAUNCH_URI_ARG)
        elif mode == MODE_MACOS:
            self.launcher_settings['application'] = heroic.detect_macos_executable()
            self.launcher_settings['args'] = '--no-gui --no-splash "{}"'.format(LAUNCH_URI_ARG)
        else:
            # Custom executable selected through the file browser.
            self.launcher_settings['args'] = '--no-gui --no-splash "{}"'.format(LAUNCH_URI_ARG)

        # When Kodi itself runs inside a Flatpak sandbox (tv.kodi.Kodi) the
        # command must be executed on the host through the flatpak-spawn portal:
        # neither the flatpak binary nor a host Heroic install exist in-sandbox.
        if heroic.kodi_in_flatpak():
            host_app = self.launcher_settings['application']
            self.launcher_settings['args'] = '--host {} {}'.format(
                host_app, self.launcher_settings['args'])
            self.launcher_settings['application'] = 'flatpak-spawn'

        self.launcher_settings['secname'] = 'Heroic'
        self.non_blocking = True
        return True

    def _builder_get_edit_options(self):
        options = super(HeroicLauncher, self)._builder_get_edit_options()
        options[self._change_application] = 'Change application ({})'.format(
            self.launcher_settings.get('application', ''))
        options[self._change_launcher_arguments] = "Modify Arguments: '{}'".format(
            self.launcher_settings.get('args', ''))
        return options

    def _change_application(self):
        current_application = self.launcher_settings.get('application', '')
        selected_application = kodi.browse(1, 'Select the Heroic executable', 'files',
                                           '', False, False, current_application)
        if selected_application is None or selected_application == current_application:
            return
        self.launcher_settings['application'] = selected_application

    def _change_launcher_arguments(self):
        args = self.launcher_settings.get('args', '')
        args = kodi.dialog_keyboard('Edit application arguments', text=args)
        if args is None:
            return
        self.launcher_settings['args'] = args

    # ---------------------------------------------------------------------------------------------
    # Execution methods
    # ---------------------------------------------------------------------------------------------
    def get_application(self) -> str:
        application = self.launcher_settings.get('application')
        if not application:
            logger.error('HeroicLauncher::get_application() No application defined')
            kodi.notify_warn('No Heroic application configured for this launcher.')
            return None
        return application
