"""
Settings dialogs for Suwayomi addon
"""

import xbmcgui
import xbmcaddon

from .helpers import (
    get_setting, get_setting_bool, get_setting_int,
    get_manga_reading_settings, set_manga_reading_settings,
    load_manga_settings, save_manga_settings, mark_manga_configured,
    show_notification, show_error
)

ADDON = xbmcaddon.Addon()


def show_manga_settings_dialog(manga_id, manga_title=""):
    """Show dialog to configure reading settings for a specific manga"""
    
    manga_settings = get_manga_reading_settings(manga_id)
    
    zoom_setting = get_setting('zoom_mode') or 'Fit Width'
    zoom_options = ['Fit Width', 'Fit Height', 'Fit Screen', 'Original']
    padding_options = [0, 5, 10, 15, 20, 25]
    two_page_start_options = ['single_first', 'paired_first']
    two_page_start_labels = ['Single First (1, 2-3, 4-5...)', 'Paired First (1-2, 3-4...)']
    
    direction = manga_settings.get('direction', get_setting('reading_direction') or 'Left to Right')
    mode = manga_settings.get('mode', get_setting('reading_mode') or 'Paged')
    zoom = zoom_options[manga_settings.get('zoom_mode', zoom_options.index(zoom_setting) if zoom_setting in zoom_options else 0)]
    padding = manga_settings.get('padding_percent', get_setting_int('padding_percent') or 0)
    two_page = manga_settings.get('two_page_mode', get_setting_bool('two_page_mode'))
    two_page_start = manga_settings.get('two_page_start', 'single_first')
    auto_play = manga_settings.get('auto_play', get_setting_bool('auto_play'))
    speed = manga_settings.get('speed', get_setting_int('slideshow_speed') or 5)
    
    dialog = xbmcgui.Dialog()
    
    while True:
        using_custom = manga_settings.get('_configured', False)
        status = "[COLOR green]Custom[/COLOR]" if using_custom else "[COLOR gray]Using Defaults[/COLOR]"
        two_page_start_label = 'Single First' if two_page_start == 'single_first' else 'Paired First'
        
        options = [
            f"Status: {status}",
            f"Direction: [COLOR yellow]{direction}[/COLOR]",
            f"Mode: [COLOR yellow]{mode}[/COLOR]",
            f"Zoom: [COLOR yellow]{zoom}[/COLOR]",
            f"Padding: [COLOR yellow]{padding}%[/COLOR] (split both sides)",
            f"Two-Page Mode: [COLOR yellow]{'On' if two_page else 'Off'}[/COLOR] (Paged only)",
            f"Two-Page Start: [COLOR yellow]{two_page_start_label}[/COLOR]",
            f"Auto-advance: [COLOR yellow]{'On' if auto_play else 'Off'}[/COLOR] (fallback)",
            f"Speed: [COLOR yellow]{speed} sec[/COLOR]",
            "[B]Save Settings[/B]",
            "Reset to Defaults",
            "Cancel"
        ]
        
        title = f"Settings: {manga_title}" if manga_title else "Manga Reading Settings"
        selected = dialog.select(title, options)
        
        if selected == -1 or selected == 11:
            return
        elif selected == 1:
            directions = ["Left to Right", "Right to Left"]
            current_idx = 0 if direction == "Left to Right" else 1
            new_dir = dialog.select("Reading Direction", directions, preselect=current_idx)
            if new_dir >= 0:
                direction = directions[new_dir]
        elif selected == 2:
            modes = ["Paged", "Webtoon"]
            current_idx = 0 if mode == "Paged" else 1
            new_mode = dialog.select("Reading Mode", modes, preselect=current_idx)
            if new_mode >= 0:
                mode = modes[new_mode]
        elif selected == 3:
            current_idx = zoom_options.index(zoom) if zoom in zoom_options else 0
            new_zoom = dialog.select("Zoom Mode", zoom_options, preselect=current_idx)
            if new_zoom >= 0:
                zoom = zoom_options[new_zoom]
        elif selected == 4:
            padding_labels = [f"{p}% ({p//2}% each side)" for p in padding_options]
            current_idx = padding_options.index(padding) if padding in padding_options else 0
            new_padding = dialog.select("Side Padding (total)", padding_labels, preselect=current_idx)
            if new_padding >= 0:
                padding = padding_options[new_padding]
        elif selected == 5:
            two_page = not two_page
        elif selected == 6:
            current_idx = two_page_start_options.index(two_page_start) if two_page_start in two_page_start_options else 0
            new_start = dialog.select("Two-Page Start Mode", two_page_start_labels, preselect=current_idx)
            if new_start >= 0:
                two_page_start = two_page_start_options[new_start]
        elif selected == 7:
            auto_play = not auto_play
        elif selected == 8:
            speeds = ["1 second", "2 seconds", "3 seconds", "5 seconds", "10 seconds", "15 seconds", "30 seconds"]
            speed_values = [1, 2, 3, 5, 10, 15, 30]
            current_idx = speed_values.index(speed) if speed in speed_values else 3
            new_speed = dialog.select("Auto-advance Speed", speeds, preselect=current_idx)
            if new_speed >= 0:
                speed = speed_values[new_speed]
        elif selected == 9:
            settings = {
                'direction': direction,
                'mode': mode,
                'zoom_mode': zoom_options.index(zoom),
                'padding_percent': padding,
                'two_page_mode': two_page,
                'two_page_start': two_page_start,
                'auto_play': auto_play,
                'speed': speed,
                '_configured': True
            }
            if set_manga_reading_settings(manga_id, settings):
                show_notification("Settings saved!")
                return
            else:
                show_error("Failed to save settings")
        elif selected == 10:
            all_settings = load_manga_settings()
            if str(manga_id) in all_settings:
                del all_settings[str(manga_id)]
                save_manga_settings(all_settings)
                show_notification("Reset to defaults")
                direction = get_setting('reading_direction') or 'Left to Right'
                mode = get_setting('reading_mode') or 'Paged'
                zoom = zoom_setting
                padding = get_setting_int('padding_percent') or 0
                two_page = get_setting_bool('two_page_mode')
                two_page_start = 'single_first'
                auto_play = get_setting_bool('auto_play')
                speed = get_setting_int('slideshow_speed') or 5
                manga_settings = {}


def show_first_time_settings_dialog(manga_id, manga_title=""):
    """Show simplified first-time settings dialog for a new manga"""
    
    zoom_setting = get_setting('zoom_mode') or 'Fit Width'
    zoom_options = ['Fit Width', 'Fit Height', 'Fit Screen', 'Original']
    zoom_index = zoom_options.index(zoom_setting) if zoom_setting in zoom_options else 0
    padding_options = [0, 5, 10, 15, 20, 25]
    
    direction = get_setting('reading_direction') or 'Left to Right'
    mode = get_setting('reading_mode') or 'Paged'
    zoom = zoom_options[zoom_index]
    padding = get_setting_int('padding_percent') or 0
    two_page = get_setting_bool('two_page_mode')
    two_page_start = 'single_first'
    auto_play = get_setting_bool('auto_play')
    speed = get_setting_int('slideshow_speed') or 5
    
    dialog = xbmcgui.Dialog()
    
    while True:
        two_page_start_label = 'Single First' if two_page_start == 'single_first' else 'Paired First'
        
        options = [
            f"[B]Start Reading (Use Defaults)[/B]",
            f"Direction: [COLOR yellow]{direction}[/COLOR]",
            f"Mode: [COLOR yellow]{mode}[/COLOR]",
            f"Zoom: [COLOR yellow]{zoom}[/COLOR]",
            f"Padding: [COLOR yellow]{padding}%[/COLOR]",
            f"Two-Page: [COLOR yellow]{'On' if two_page else 'Off'}[/COLOR] | Start: {two_page_start_label}",
            "[B]Save & Start Reading[/B]",
            "Cancel"
        ]
        
        title = f"First Time Setup: {manga_title}" if manga_title else "Reading Settings"
        selected = dialog.select(title, options)
        
        if selected == -1 or selected == 7:
            return None
        
        if selected == 0:
            mark_manga_configured(manga_id)
            return {
                'direction': direction,
                'mode': mode,
                'zoom_mode': zoom_index,
                'padding_percent': padding,
                'two_page_mode': two_page,
                'two_page_start': two_page_start,
                'auto_play': auto_play,
                'speed': speed
            }
        elif selected == 1:
            directions = ["Left to Right", "Right to Left"]
            current_idx = 0 if direction == "Left to Right" else 1
            new_dir = dialog.select("Reading Direction", directions, preselect=current_idx)
            if new_dir >= 0:
                direction = directions[new_dir]
        elif selected == 2:
            modes = ["Paged", "Webtoon"]
            current_idx = 0 if mode == "Paged" else 1
            new_mode = dialog.select("Reading Mode", modes, preselect=current_idx)
            if new_mode >= 0:
                mode = modes[new_mode]
        elif selected == 3:
            current_idx = zoom_options.index(zoom) if zoom in zoom_options else 0
            new_zoom = dialog.select("Zoom Mode", zoom_options, preselect=current_idx)
            if new_zoom >= 0:
                zoom = zoom_options[new_zoom]
        elif selected == 4:
            padding_labels = [f"{p}% ({p//2}% each side)" for p in padding_options]
            current_idx = padding_options.index(padding) if padding in padding_options else 0
            new_padding = dialog.select("Side Padding (total)", padding_labels, preselect=current_idx)
            if new_padding >= 0:
                padding = padding_options[new_padding]
        elif selected == 5:
            two_page_options = [
                "Two-Page: Off",
                "Two-Page: On, Single First (1, 2-3, 4-5...)",
                "Two-Page: On, Paired First (1-2, 3-4...)"
            ]
            current_idx = 0 if not two_page else (1 if two_page_start == 'single_first' else 2)
            new_two = dialog.select("Two-Page Mode", two_page_options, preselect=current_idx)
            if new_two == 0:
                two_page = False
            elif new_two == 1:
                two_page = True
                two_page_start = 'single_first'
            elif new_two == 2:
                two_page = True
                two_page_start = 'paired_first'
        elif selected == 6:
            settings = {
                'direction': direction,
                'mode': mode,
                'zoom_mode': zoom_options.index(zoom),
                'padding_percent': padding,
                'two_page_mode': two_page,
                'two_page_start': two_page_start,
                'auto_play': auto_play,
                'speed': speed,
                '_configured': True
            }
            set_manga_reading_settings(manga_id, settings)
            return settings
