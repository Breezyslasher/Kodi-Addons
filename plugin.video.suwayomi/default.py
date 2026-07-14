"""
Suwayomi Kodi Addon - Main Entry Point
A Kodi addon for reading manga from Suwayomi Server
"""
import sys
import os
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

# Add lib path to system path
ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')
sys.path.insert(0, os.path.join(ADDON_PATH, 'resources', 'lib'))

from suwayomi_api import SuwayomiAPI
from utils import (
    get_setting, get_setting_bool, get_setting_int,
    log, log_error, log_info, show_notification, show_error,
    build_url, parse_url, create_list_item, add_directory_item,
    end_directory, format_chapter_name, format_manga_status,
    format_date, truncate_text, get_keyboard_input,
    ProgressDialog, show_yesno_dialog, select_from_list
)


# Plugin handle and URL
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

# Manga settings file path
import json
import xbmcvfs

def get_manga_settings_path():
    """Get path to manga settings JSON file"""
    profile_path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not os.path.exists(profile_path):
        os.makedirs(profile_path)
    return os.path.join(profile_path, 'manga_settings.json')


def load_manga_settings():
    """Load all manga-specific settings from file"""
    settings_path = get_manga_settings_path()
    try:
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        log_error(f"Failed to load manga settings: {e}")
    return {}


def save_manga_settings(settings):
    """Save all manga-specific settings to file"""
    settings_path = get_manga_settings_path()
    try:
        with open(settings_path, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        log_error(f"Failed to save manga settings: {e}")
        return False


def get_manga_reading_settings(manga_id):
    """Get reading settings for a specific manga"""
    all_settings = load_manga_settings()
    return all_settings.get(str(manga_id), {})


def set_manga_reading_settings(manga_id, settings):
    """Save reading settings for a specific manga"""
    all_settings = load_manga_settings()
    all_settings[str(manga_id)] = settings
    return save_manga_settings(all_settings)


def is_manga_configured(manga_id):
    """Check if manga has been configured (first-time setup done)"""
    settings = get_manga_reading_settings(manga_id)
    return settings.get('_configured', False)


def mark_manga_configured(manga_id):
    """Mark manga as configured so dialog doesn't show again"""
    all_settings = load_manga_settings()
    if str(manga_id) not in all_settings:
        all_settings[str(manga_id)] = {}
    all_settings[str(manga_id)]['_configured'] = True
    save_manga_settings(all_settings)


def set_setting(setting_id, value):
    """Set an addon setting"""
    ADDON.setSetting(setting_id, str(value))


def get_thumbnail_url(api, manga):
    """Get proper thumbnail URL for a manga.
    
    Always use the server's thumbnail endpoint which handles caching.
    The thumbnailUrl field might contain expired external CDN URLs.
    """
    manga_id = manga.get('id')
    
    if manga_id:
        # Always use server's thumbnail endpoint - it handles caching and fetching
        return f"{api.server_url}/api/v1/manga/{manga_id}/thumbnail"
    
    # Fallback to thumbnailUrl from API only if no manga_id
    thumb = manga.get('thumbnailUrl', '')
    if thumb:
        if thumb.startswith('/'):
            return f"{api.server_url}{thumb}"
        elif thumb.startswith('http'):
            return thumb
    
    return ""


def get_cached_thumbnail(api, manga):
    """Download and cache thumbnail locally, return local path or URL.
    
    This downloads the cover from the server and caches it locally for faster loading.
    """
    import xbmcvfs
    from urllib.request import Request, urlopen
    import ssl
    
    manga_id = manga.get('id')
    if not manga_id:
        return ""
    
    # Get cache directory for covers
    profile_path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    covers_dir = os.path.join(profile_path, 'covers')
    
    if not os.path.exists(covers_dir):
        try:
            os.makedirs(covers_dir)
        except:
            pass
    
    # Check if already cached
    cache_file = os.path.join(covers_dir, f"{manga_id}.jpg")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1000:
        return cache_file
    
    # Use server's thumbnail endpoint - it handles caching and fetching from sources
    thumb_url = f"{api.server_url}/api/v1/manga/{manga_id}/thumbnail"
    
    # Create SSL context
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        req = Request(thumb_url)
        req.add_header('User-Agent', 'Kodi-Suwayomi/1.0')
        
        # Add auth header if needed
        if api.headers.get('Authorization'):
            req.add_header('Authorization', api.headers['Authorization'])
        
        with urlopen(req, timeout=10, context=ctx) as response:
            image_data = response.read()
            
            # Check if we got actual image data (not an error page)
            if len(image_data) > 1000:
                try:
                    with open(cache_file, 'wb') as f:
                        f.write(image_data)
                    return cache_file
                except:
                    pass
                    
    except Exception as e:
        log_error(f"Failed to download thumbnail for manga {manga_id}: {e}")
    
    # Return URL as fallback (Kodi can try to load it directly)
    return thumb_url


def show_manga_settings_dialog(manga_id, manga_title=""):
    """Show dialog to configure reading settings for a specific manga"""
    
    # Get current manga-specific settings or defaults
    manga_settings = get_manga_reading_settings(manga_id)
    
    # Get defaults from settings
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
        # Show if using custom or default
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
        
        if selected == -1 or selected == 11:  # Cancel
            return
        
        elif selected == 1:  # Direction
            directions = ["Left to Right", "Right to Left"]
            current_idx = 0 if direction == "Left to Right" else 1
            new_dir = dialog.select("Reading Direction", directions, preselect=current_idx)
            if new_dir >= 0:
                direction = directions[new_dir]
        
        elif selected == 2:  # Mode
            modes = ["Paged", "Webtoon"]
            current_idx = 0 if mode == "Paged" else 1
            new_mode = dialog.select("Reading Mode", modes, preselect=current_idx)
            if new_mode >= 0:
                mode = modes[new_mode]
        
        elif selected == 3:  # Zoom
            current_idx = zoom_options.index(zoom) if zoom in zoom_options else 0
            new_zoom = dialog.select("Zoom Mode", zoom_options, preselect=current_idx)
            if new_zoom >= 0:
                zoom = zoom_options[new_zoom]
        
        elif selected == 4:  # Padding
            padding_labels = [f"{p}% ({p//2}% each side)" for p in padding_options]
            current_idx = padding_options.index(padding) if padding in padding_options else 0
            new_padding = dialog.select("Side Padding (total)", padding_labels, preselect=current_idx)
            if new_padding >= 0:
                padding = padding_options[new_padding]
        
        elif selected == 5:  # Two-page mode
            two_page = not two_page
        
        elif selected == 6:  # Two-page start
            current_idx = two_page_start_options.index(two_page_start) if two_page_start in two_page_start_options else 0
            new_start = dialog.select("Two-Page Start Mode", two_page_start_labels, preselect=current_idx)
            if new_start >= 0:
                two_page_start = two_page_start_options[new_start]
        
        elif selected == 7:  # Auto-play
            auto_play = not auto_play
        
        elif selected == 8:  # Speed
            speeds = ["1 second", "2 seconds", "3 seconds", "5 seconds", "10 seconds", "15 seconds", "30 seconds"]
            speed_values = [1, 2, 3, 5, 10, 15, 30]
            current_idx = speed_values.index(speed) if speed in speed_values else 3
            new_speed = dialog.select("Auto-advance Speed", speeds, preselect=current_idx)
            if new_speed >= 0:
                speed = speed_values[new_speed]
        
        elif selected == 9:  # Save
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
        
        elif selected == 10:  # Reset
            all_settings = load_manga_settings()
            if str(manga_id) in all_settings:
                del all_settings[str(manga_id)]
                save_manga_settings(all_settings)
                show_notification("Reset to defaults")
                # Reset local vars to defaults
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
    
    # Get defaults from settings
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
        
        if selected == -1 or selected == 7:  # Cancel
            return None
        
        if selected == 0:  # Start with defaults, mark as configured
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
        
        elif selected == 1:  # Direction
            directions = ["Left to Right", "Right to Left"]
            current_idx = 0 if direction == "Left to Right" else 1
            new_dir = dialog.select("Reading Direction", directions, preselect=current_idx)
            if new_dir >= 0:
                direction = directions[new_dir]
        
        elif selected == 2:  # Mode
            modes = ["Paged", "Webtoon"]
            current_idx = 0 if mode == "Paged" else 1
            new_mode = dialog.select("Reading Mode", modes, preselect=current_idx)
            if new_mode >= 0:
                mode = modes[new_mode]
        
        elif selected == 3:  # Zoom
            current_idx = zoom_options.index(zoom) if zoom in zoom_options else 0
            new_zoom = dialog.select("Zoom Mode", zoom_options, preselect=current_idx)
            if new_zoom >= 0:
                zoom = zoom_options[new_zoom]
        
        elif selected == 4:  # Padding
            padding_labels = [f"{p}% ({p//2}% each side)" for p in padding_options]
            current_idx = padding_options.index(padding) if padding in padding_options else 0
            new_padding = dialog.select("Side Padding (total)", padding_labels, preselect=current_idx)
            if new_padding >= 0:
                padding = padding_options[new_padding]
        
        elif selected == 5:  # Two-page toggle and start
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
        
        elif selected == 6:  # Save & Start
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
            return settings


def show_reading_options_dialog(manga_id=None, chapter_name=""):
    """Show dialog to configure reading options before starting chapter"""
    
    # Get manga-specific settings if available, otherwise use defaults
    if manga_id:
        manga_settings = get_manga_reading_settings(manga_id)
    else:
        manga_settings = {}
    
    direction = manga_settings.get('direction', get_setting('reading_direction') or 'Left to Right')
    mode = manga_settings.get('mode', get_setting('reading_mode') or 'Paged')
    auto_play = manga_settings.get('auto_play', get_setting_bool('auto_play'))
    speed = manga_settings.get('speed', get_setting_int('slideshow_speed') or 5)
    
    while True:
        options = [
            f"[B]Start Reading[/B]",
            f"Direction: [COLOR yellow]{direction}[/COLOR]",
            f"Mode: [COLOR yellow]{mode}[/COLOR]",
            f"Auto-advance: [COLOR yellow]{'On' if auto_play else 'Off'}[/COLOR] (Paged only)",
            f"Speed: [COLOR yellow]{speed} sec[/COLOR]",
            "Cancel"
        ]
        
        dialog = xbmcgui.Dialog()
        title = f"Reading Options" + (f" - {chapter_name}" if chapter_name else "")
        selected = dialog.select(title, options)
        
        if selected == -1 or selected == 5:  # Cancel
            return None
        
        if selected == 0:  # Start Reading
            return {
                'direction': direction,
                'mode': mode,
                'auto_play': auto_play,
                'speed': speed
            }
        
        elif selected == 1:  # Direction
            directions = ["Left to Right", "Right to Left"]
            current_idx = 0 if direction == "Left to Right" else 1
            new_dir = dialog.select("Reading Direction", directions, preselect=current_idx)
            if new_dir >= 0:
                direction = directions[new_dir]
        
        elif selected == 2:  # Mode
            modes = ["Paged (Slideshow)", "Webtoon (Scrolling)"]
            current_idx = 0 if mode == "Paged" else 1
            new_mode = dialog.select("Reading Mode", modes, preselect=current_idx)
            if new_mode >= 0:
                mode = "Paged" if new_mode == 0 else "Webtoon"
        
        elif selected == 3:  # Auto-play
            auto_play = not auto_play
        
        elif selected == 4:  # Speed
            speeds = ["1 second", "2 seconds", "3 seconds", "5 seconds", "10 seconds", "15 seconds", "30 seconds"]
            speed_values = [1, 2, 3, 5, 10, 15, 30]
            current_idx = speed_values.index(speed) if speed in speed_values else 3
            new_speed = dialog.select("Auto-advance Speed", speeds, preselect=current_idx)
            if new_speed >= 0:
                speed = speed_values[new_speed]


def quick_reading_options():
    """Quick options accessible via context menu - opens addon settings"""
    ADDON.openSettings()


def get_api():
    """Get configured API instance"""
    server_url = get_setting('server_url')
    log_info(f"Server URL from settings: '{server_url}'")
    
    if not server_url:
        show_error("Please configure server URL in settings")
        return None
    
    # Clean up URL
    server_url = server_url.strip()
    if not server_url.startswith('http'):
        server_url = 'http://' + server_url
    
    log_info(f"Using server URL: {server_url}")
    
    username = None
    password = None
    if get_setting_bool('auth_enabled'):
        username = get_setting('username')
        password = get_setting('password')
    
    return SuwayomiAPI(server_url, username, password)


def show_history():
    """Show reading history from server"""
    api = get_api()
    if not api:
        return
    
    try:
        result = api.get_reading_history(offset=0, limit=50)
        chapters = result.get('chapters', {}).get('nodes', [])
        
        if not chapters:
            li = create_list_item("No reading history", plot="Start reading manga to build your history")
            add_directory_item(HANDLE, "", li, is_folder=False)
            end_directory(HANDLE)
            return
        
        for chapter in chapters:
            manga = chapter.get('manga', {})
            manga_id = manga.get('id')
            manga_title = manga.get('title', 'Unknown')
            chapter_id = chapter.get('id')
            chapter_name = chapter.get('name', '')
            chapter_num = chapter.get('chapterNumber', 0)
            last_page = chapter.get('lastPageRead', 0)
            is_read = chapter.get('isRead', False)
            thumbnail = manga.get('thumbnailUrl', '')
            
            # Get source name if available
            source = manga.get('source', {})
            source_name = source.get('displayName', '') if source else ''
            
            # Build thumbnail URL
            if thumbnail:
                if thumbnail.startswith('/'):
                    thumbnail = f"{api.server_url}{thumbnail}"
            else:
                thumbnail = get_thumbnail_url(api, manga)
            
            # Format label
            if chapter_num:
                label = f"{manga_title} - Ch.{chapter_num}"
            else:
                label = f"{manga_title} - {chapter_name}"
            
            if is_read:
                label += " [Read]"
            elif last_page > 0:
                label += f" [Page {last_page + 1}]"
            
            plot = f"Chapter: {chapter_name}" if chapter_name else f"Chapter {chapter_num}"
            if source_name:
                plot += f"\nSource: {source_name}"
            if last_page > 0:
                plot += f"\nProgress: Page {last_page + 1}"
            
            li = create_list_item(label, thumb=thumbnail, plot=plot)
            
            # Context menu
            context = [
                ('Continue Reading', f'RunPlugin({build_url(BASE_URL, action="read_chapter", manga_id=manga_id, chapter_id=chapter_id)})'),
                ('Go to Manga', f'Container.Update({build_url(BASE_URL, action="manga", manga_id=manga_id)})'),
            ]
            li.addContextMenuItems(context)
            
            # Clicking continues reading
            url = build_url(BASE_URL, action='read_chapter', manga_id=manga_id, chapter_id=chapter_id)
            add_directory_item(HANDLE, url, li, is_folder=False)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load history: {e}")
        show_error(f"Error loading history: {str(e)}")


def main_menu():
    """Show main menu"""
    items = [
        ('Library', 'library', 'DefaultMusicAlbums.png'),
        ('Categories', 'categories', 'DefaultMusicGenres.png'),
        ('Browse Sources', 'sources', 'DefaultMusicPlugins.png'),
        ('Recent Updates', 'updates', 'DefaultRecentlyAddedEpisodes.png'),
        ('History', 'history', 'DefaultYear.png'),
        ('Search', 'search', 'DefaultAddonsSearch.png'),
        ('Extensions', 'extensions', 'DefaultAddonRepository.png'),
        ('Downloads', 'downloads', 'DefaultFavourites.png'),
    ]
    
    for label, action, icon in items:
        url = build_url(BASE_URL, action=action)
        li = create_list_item(label, icon=icon, thumb=icon)
        add_directory_item(HANDLE, url, li, is_folder=True)
    
    end_directory(HANDLE)


def show_library(category_id=None, page=0):
    """Show library manga"""
    api = get_api()
    if not api:
        return
    
    items_per_page = get_setting_int('items_per_page') or 50
    offset = page * items_per_page
    
    try:
        result = api.get_library_manga(category_id=category_id, offset=offset, limit=items_per_page)
        mangas = result.get('mangas', {})
        nodes = mangas.get('nodes', [])
        total_count = mangas.get('totalCount', 0)
        page_info = mangas.get('pageInfo', {})
        
        if not nodes:
            show_notification("No manga in library")
            end_directory(HANDLE)
            return
        
        for manga in nodes:
            add_manga_item(manga)
        
        # Add pagination
        if page_info.get('hasNextPage'):
            url = build_url(BASE_URL, action='library', category_id=category_id or '', page=page + 1)
            li = create_list_item(f"Next Page ({page + 2})", icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        log_error(f"Failed to load library: {e}")
        show_error(f"Error: {str(e)}")
        end_directory(HANDLE)


def show_categories():
    """Show categories (hides empty ones)"""
    api = get_api()
    if not api:
        return
    
    try:
        result = api.get_categories()
        log_info(f"Categories result: {result}")
        categories = result.get('categories', {}).get('nodes', [])
        
        # Add "All" category - get total manga count
        library_result = api.get_library_manga(limit=1)
        total_manga = library_result.get('mangas', {}).get('totalCount', 0)
        
        url = build_url(BASE_URL, action='library')
        li = create_list_item(f"All Manga ({total_manga})", icon='DefaultMusicAlbums.png')
        add_directory_item(HANDLE, url, li, is_folder=True)
        
        for category in categories:
            cat_name = category.get('name', 'Unknown')
            cat_id = category.get('id')
            
            # Try to get manga count - different possible structures
            manga_count = None
            mangas_data = category.get('mangas')
            log_info(f"Category '{cat_name}': mangas_data={mangas_data}")
            
            if mangas_data:
                if isinstance(mangas_data, dict):
                    manga_count = mangas_data.get('totalCount')
                elif isinstance(mangas_data, list):
                    manga_count = len(mangas_data)
            
            log_info(f"Category '{cat_name}' (id={cat_id}): manga_count={manga_count}")
            
            # Skip empty categories if we definitely know they're empty
            if manga_count == 0:
                log_info(f"Skipping empty category: {cat_name}")
                continue
            
            # Build label with count if available
            if manga_count is not None and manga_count > 0:
                label = f"{cat_name} ({manga_count})"
            else:
                label = cat_name
            
            url = build_url(BASE_URL, action='category_manga', category_id=cat_id)
            li = create_list_item(label, icon='DefaultMusicGenres.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load categories: {e}")
        show_error(f"Error: {str(e)}")


def show_category_manga(category_id, page=0):
    """Show manga in a specific category"""
    api = get_api()
    if not api:
        return
    
    try:
        # Use a category-specific query without pagination (category.mangas doesn't support it)
        query = """
        query GetCategoryManga($categoryId: Int!) {
            category(id: $categoryId) {
                id
                name
                mangas {
                    nodes {
                        id
                        title
                        thumbnailUrl
                        author
                        artist
                        description
                        genre
                        status
                        inLibrary
                        unreadCount
                    }
                    totalCount
                }
            }
        }
        """
        result = api._execute_query(query, {'categoryId': category_id})
        
        category = result.get('category', {})
        mangas_data = category.get('mangas', {})
        nodes = mangas_data.get('nodes', [])
        
        if not nodes:
            show_notification("No manga in this category")
            end_directory(HANDLE)
            return
        
        for manga in nodes:
            add_manga_item(manga)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        # Try fallback without unreadCount
        try:
            query_fallback = """
            query GetCategoryManga($categoryId: Int!) {
                category(id: $categoryId) {
                    id
                    name
                    mangas {
                        nodes {
                            id
                            title
                            thumbnailUrl
                            author
                            artist
                            description
                            genre
                            status
                            inLibrary
                        }
                        totalCount
                    }
                }
            }
            """
            result = api._execute_query(query_fallback, {'categoryId': category_id})
            
            category = result.get('category', {})
            mangas_data = category.get('mangas', {})
            nodes = mangas_data.get('nodes', [])
            
            if not nodes:
                show_notification("No manga in this category")
                end_directory(HANDLE)
                return
            
            for manga in nodes:
                add_manga_item(manga)
            
            end_directory(HANDLE, content_type='movies')
            
        except Exception as e2:
            log_error(f"Failed to load category manga: {e2}")
            show_error(f"Error: {str(e2)}")


def get_language_filter():
    """Get list of allowed languages from settings"""
    lang_setting = get_setting('source_languages') or ''
    if not lang_setting.strip():
        return None  # No filter - show all
    languages = [l.strip().lower() for l in lang_setting.split(',') if l.strip()]
    return languages if languages else None


def get_hidden_sources():
    """Get list of hidden source names from settings"""
    hidden_setting = get_setting('hidden_sources') or ''
    if not hidden_setting.strip():
        return []
    return [s.strip().lower() for s in hidden_setting.split(',') if s.strip()]


def is_source_visible(source, lang_filter, hidden_sources):
    """Check if source should be visible based on filters"""
    source_name = (source.get('displayName') or source.get('name', '')).lower()
    source_lang = source.get('lang', '').lower()
    
    # Check if hidden
    for hidden in hidden_sources:
        if hidden in source_name:
            return False
    
    # Check language filter
    if lang_filter:
        if source_lang not in lang_filter:
            return False
    
    return True


def show_sources():
    """Show available sources"""
    api = get_api()
    if not api:
        return
    
    show_nsfw = get_setting_bool('show_nsfw')
    lang_filter = get_language_filter()
    hidden_sources = get_hidden_sources()
    
    try:
        result = api.get_sources()
        sources = result.get('sources', {}).get('nodes', [])
        
        # Group by language
        languages = {}
        for source in sources:
            if not show_nsfw and source.get('isNsfw'):
                continue
            
            # Apply language and hidden filters
            if not is_source_visible(source, lang_filter, hidden_sources):
                continue
            
            lang = source.get('lang', 'other')
            if lang not in languages:
                languages[lang] = []
            languages[lang].append(source)
        
        if not languages:
            show_notification("No sources match your language filter")
            end_directory(HANDLE)
            return
        
        # Sort languages
        for lang in sorted(languages.keys()):
            for source in sorted(languages[lang], key=lambda x: x.get('displayName', '')):
                label = f"[{lang.upper()}] {source.get('displayName', source.get('name', 'Unknown'))}"
                if source.get('isNsfw'):
                    label += " [NSFW]"
                
                # Use iconUrl from API response, or build URL as fallback
                icon_url = source.get('iconUrl', '')
                if icon_url:
                    if icon_url.startswith('/'):
                        icon_url = f"{api.server_url}{icon_url}"
                else:
                    icon_url = api.get_source_icon_url(source['id'])
                
                url = build_url(BASE_URL, action='source_menu', source_id=source['id'])
                li = create_list_item(label, thumb=icon_url, icon=icon_url)
                
                # Add context menu to hide source
                context_items = [
                    ('Hide from Search', 
                     f'RunPlugin({build_url(BASE_URL, action="hide_source", source_name=source.get("displayName", source.get("name", "")))})')
                ]
                li.addContextMenuItems(context_items)
                
                add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load sources: {e}")
        show_error(f"Error: {str(e)}")


def show_source_menu(source_id):
    """Show source menu (Popular, Latest, Search)"""
    api = get_api()
    if not api:
        return
    
    try:
        result = api.get_source(source_id)
        source = result.get('source', {})
        supports_latest = source.get('supportsLatest', False)
        
        items = [
            ('Popular', 'source_popular'),
            ('Search', 'source_search'),
        ]
        
        if supports_latest:
            items.insert(1, ('Latest', 'source_latest'))
        
        for label, action in items:
            url = build_url(BASE_URL, action=action, source_id=source_id)
            li = create_list_item(label, icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load source menu: {e}")
        show_error(f"Error: {str(e)}")


def show_source_popular(source_id, page=1):
    """Show popular manga from source"""
    api = get_api()
    if not api:
        return
    
    try:
        # Try GraphQL first, then REST API fallback
        try:
            result = api.get_source_popular(source_id, page)
            data = result.get('fetchSourceManga', {})
            mangas = data.get('mangas', [])
            has_next = data.get('hasNextPage', False)
        except Exception:
            # Fallback to REST API
            result = api.get_source_popular_rest(source_id, page)
            mangas = result.get('mangaList', [])
            has_next = result.get('hasNextPage', False)
        
        if not mangas:
            show_notification("No manga found")
            end_directory(HANDLE)
            return
        
        for manga in mangas:
            add_manga_item(manga, from_source=True)
        
        if has_next:
            url = build_url(BASE_URL, action='source_popular', source_id=source_id, page=page + 1)
            li = create_list_item(f"Next Page ({page + 1})", icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        log_error(f"Failed to load popular: {e}")
        show_error(f"Error: {str(e)}")
        end_directory(HANDLE)


def show_source_latest(source_id, page=1):
    """Show latest manga from source"""
    api = get_api()
    if not api:
        return
    
    try:
        # Try GraphQL first, then REST API fallback
        try:
            result = api.get_source_latest(source_id, page)
            data = result.get('fetchSourceManga', {})
            mangas = data.get('mangas', [])
            has_next = data.get('hasNextPage', False)
        except Exception:
            # Fallback to REST API
            result = api.get_source_latest_rest(source_id, page)
            mangas = result.get('mangaList', [])
            has_next = result.get('hasNextPage', False)
        
        if not mangas:
            show_notification("No manga found")
            end_directory(HANDLE)
            return
        
        for manga in mangas:
            add_manga_item(manga, from_source=True)
        
        if has_next:
            url = build_url(BASE_URL, action='source_latest', source_id=source_id, page=page + 1)
            li = create_list_item(f"Next Page ({page + 1})", icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        log_error(f"Failed to load latest: {e}")
        show_error(f"Error: {str(e)}")
        end_directory(HANDLE)


def search_source(source_id, search_term=None, page=1):
    """Search manga in source"""
    api = get_api()
    if not api:
        return
    
    if not search_term:
        search_term = get_keyboard_input("Search manga")
        if not search_term:
            # User cancelled - just end the directory
            end_directory(HANDLE)
            return
    
    try:
        # Try GraphQL first, then REST API fallback
        try:
            result = api.search_source(source_id, search_term, page)
            data = result.get('fetchSourceManga', {})
            mangas = data.get('mangas', [])
            has_next = data.get('hasNextPage', False)
        except Exception:
            # Fallback to REST API
            result = api.search_source_rest(source_id, search_term, page)
            mangas = result.get('mangaList', [])
            has_next = result.get('hasNextPage', False)
        
        if not mangas:
            show_notification("No results found")
            end_directory(HANDLE)
            return
        
        for manga in mangas:
            add_manga_item(manga, from_source=True)
        
        if has_next:
            url = build_url(BASE_URL, action='source_search', source_id=source_id, 
                          search_term=search_term, page=page + 1)
            li = create_list_item(f"Next Page ({page + 1})", icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        log_error(f"Failed to search: {e}")
        show_error(f"Error: {str(e)}")
        end_directory(HANDLE)


def global_search(search_term=None):
    """Global search across enabled sources"""
    api = get_api()
    if not api:
        return
    
    if not search_term:
        search_term = get_keyboard_input("Search all sources")
        if not search_term:
            end_directory(HANDLE)
            return
    
    try:
        dialog = xbmcgui.DialogProgress()
        dialog.create('Searching...', f'Searching for: {search_term}')
        
        # Get list of enabled sources
        sources_result = api.get_sources()
        sources = sources_result.get('sources', {}).get('nodes', [])
        
        # Apply filters
        show_nsfw = get_setting_bool('show_nsfw')
        lang_filter = get_language_filter()
        hidden_sources = get_hidden_sources()
        
        # Filter sources
        filtered_sources = []
        for source in sources:
            if not show_nsfw and source.get('isNsfw'):
                continue
            if not is_source_visible(source, lang_filter, hidden_sources):
                continue
            filtered_sources.append(source)
        
        if not filtered_sources:
            dialog.close()
            show_notification("No sources match your filters")
            end_directory(HANDLE)
            return
        
        all_mangas = []
        total_sources = len(filtered_sources)
        
        for i, source in enumerate(filtered_sources):
            if dialog.iscanceled():
                break
            
            source_id = source.get('id')
            source_name = source.get('displayName', source.get('name', 'Unknown'))
            
            dialog.update(int((i / total_sources) * 100), f'Searching {source_name}...')
            
            try:
                # Try REST API first (more reliable)
                result = api.search_source_rest(source_id, search_term, 1)
                mangas = result.get('mangaList', [])
                
                # Add source info to each manga
                for manga in mangas:
                    manga['_source_name'] = source_name
                    all_mangas.append(manga)
                    
            except Exception:
                try:
                    # Fallback to GraphQL
                    result = api.search_source(source_id, search_term, 1)
                    data = result.get('fetchSourceManga', {})
                    mangas = data.get('mangas', [])
                    
                    for manga in mangas:
                        manga['_source_name'] = source_name
                        all_mangas.append(manga)
                except:
                    continue  # Skip failed sources
        
        dialog.close()
        
        if not all_mangas:
            show_notification("No results found")
            end_directory(HANDLE)
            return
        
        for manga in all_mangas:
            source_name = manga.get('_source_name', '')
            prefix = f"[{source_name}] " if source_name else ""
            add_manga_item(manga, from_source=True, prefix=prefix)
        
        end_directory(HANDLE, content_type='movies')
        
    except Exception as e:
        log_error(f"Failed to search: {e}")
        show_error(f"Error: {str(e)}")
        end_directory(HANDLE)


def hide_source(source_name):
    """Add a source to the hidden sources list"""
    if not source_name:
        return
    
    hidden = get_setting('hidden_sources') or ''
    hidden_list = [s.strip() for s in hidden.split(',') if s.strip()]
    
    if source_name.lower() not in [h.lower() for h in hidden_list]:
        hidden_list.append(source_name)
        ADDON.setSetting('hidden_sources', ', '.join(hidden_list))
        show_notification(f"Hidden: {source_name}")
        xbmc.executebuiltin('Container.Refresh')


def manage_hidden_sources():
    """Show dialog to manage hidden sources"""
    hidden = get_setting('hidden_sources') or ''
    hidden_list = [s.strip() for s in hidden.split(',') if s.strip()]
    
    if not hidden_list:
        show_notification("No hidden sources")
        return
    
    dialog = xbmcgui.Dialog()
    
    while True:
        options = hidden_list + ["[Clear All]", "[Done]"]
        selected = dialog.select("Hidden Sources (select to unhide)", options)
        
        if selected == -1 or selected == len(options) - 1:  # Cancel or Done
            break
        elif selected == len(options) - 2:  # Clear All
            if dialog.yesno("Clear All", "Remove all hidden sources?"):
                ADDON.setSetting('hidden_sources', '')
                show_notification("All sources unhidden")
                break
        else:
            # Unhide selected source
            removed = hidden_list.pop(selected)
            ADDON.setSetting('hidden_sources', ', '.join(hidden_list))
            show_notification(f"Unhidden: {removed}")
            if not hidden_list:
                break
    
    xbmc.executebuiltin('Container.Refresh')


def show_manga(manga_id):
    """Show manga details and chapters"""
    api = get_api()
    if not api:
        return
    
    try:
        result = api.get_manga(manga_id)
        manga = result.get('manga', {})
        
        if not manga:
            show_error("Manga not found")
            return
        
        # Get chapters
        chapters_result = api.get_chapters(manga_id, limit=500)
        chapters = chapters_result.get('chapters', {}).get('nodes', [])
        
        # If no chapters, try to initialize/fetch the manga first
        if not chapters:
            log_info(f"No chapters found for manga {manga_id}, trying to fetch from source...")
            try:
                # Show a brief notification
                show_notification("Fetching chapters from source...", time=2000)
                # First fetch manga info, then fetch chapters
                api.refresh_manga(manga_id)
                api.fetch_chapters(manga_id)
                # Re-fetch chapters
                chapters_result = api.get_chapters(manga_id, limit=500)
                chapters = chapters_result.get('chapters', {}).get('nodes', [])
                log_info(f"After fetch: found {len(chapters)} chapters")
                if chapters:
                    show_notification(f"Found {len(chapters)} chapters", time=1500)
            except Exception as e:
                log_error(f"Failed to fetch chapters: {e}")
                show_notification("Could not fetch chapters from source", time=2000)
        
        # Count unread chapters
        unread_count = sum(1 for ch in chapters if not ch.get('isRead'))
        
        # Add manga info item (non-playable)
        info_label = f"[B]{manga.get('title', 'Unknown')}[/B]"
        if unread_count > 0:
            info_label += f" [COLOR orange]({unread_count} unread)[/COLOR]"
        
        # Get cached thumbnail
        thumb = get_cached_thumbnail(api, manga)
        
        plot = manga.get('description', '') or ''
        
        status = format_manga_status(manga.get('status'))
        author = manga.get('author', '') or ''
        artist = manga.get('artist', '') or ''
        genres = manga.get('genre', []) or []
        
        info_text = f"Status: {status}\n"
        if author:
            info_text += f"Author: {author}\n"
        if artist:
            info_text += f"Artist: {artist}\n"
        if genres:
            info_text += f"Genres: {', '.join(genres)}\n"
        info_text += f"\n{plot}"
        
        li = create_list_item(info_label, thumb=thumb, plot=info_text, fanart=thumb)
        
        # Add context menu for manga actions
        context_items = []
        # Reading settings for this manga
        manga_title = manga.get('title', '')
        context_items.append(('Reading Settings', 
            f'RunPlugin({build_url(BASE_URL, action="manga_settings", manga_id=manga_id, manga_title=manga_title)})'))
        
        if manga.get('inLibrary'):
            context_items.append(('Remove from Library', 
                f'RunPlugin({build_url(BASE_URL, action="remove_from_library", manga_id=manga_id)})'))
        else:
            context_items.append(('Add to Library', 
                f'RunPlugin({build_url(BASE_URL, action="add_to_library", manga_id=manga_id)})'))
        context_items.append(('Refresh Manga', 
            f'RunPlugin({build_url(BASE_URL, action="refresh_manga", manga_id=manga_id)})'))
        context_items.append(('Download All Chapters', 
            f'RunPlugin({build_url(BASE_URL, action="download_all", manga_id=manga_id)})'))
        context_items.append(('Mark All as Read', 
            f'RunPlugin({build_url(BASE_URL, action="mark_all_read", manga_id=manga_id)})'))
        li.addContextMenuItems(context_items)
        
        url = build_url(BASE_URL, action='manga', manga_id=manga_id)
        add_directory_item(HANDLE, url, li, is_folder=False)
        
        # Add chapters
        for chapter in chapters:
            add_chapter_item(chapter, manga, api)
        
        end_directory(HANDLE, content_type='episodes')
        
    except Exception as e:
        log_error(f"Failed to load manga: {e}")
        show_error(f"Error: {str(e)}")


def add_manga_item(manga, from_source=False, prefix=""):
    """Add manga item to listing"""
    api = get_api()
    if not api:
        return
    
    manga_id = manga.get('id')
    title = prefix + manga.get('title', 'Unknown')
    
    # Get unread count if available
    unread_count = manga.get('unreadCount', 0) or 0
    
    # Add indicators
    if manga.get('inLibrary'):
        if unread_count > 0:
            title += f" [COLOR orange]({unread_count})[/COLOR]"
        else:
            title += " [*]"
    
    # Check if manga has custom settings
    manga_settings = get_manga_reading_settings(manga_id)
    if manga_settings.get('_configured'):
        title += " [S]"  # Indicate custom settings
    
    # Get and cache thumbnail
    thumb = get_cached_thumbnail(api, manga)
    
    plot = manga.get('description', '') or ''
    author = manga.get('author', '')
    
    li = create_list_item(title, thumb=thumb, plot=truncate_text(plot), fanart=thumb)
    
    if author:
        li.setInfo('video', {'studio': author})
    
    # Add context menu
    context_items = []
    
    # Reading settings for this manga
    context_items.append(('Reading Settings', 
        f'RunPlugin({build_url(BASE_URL, action="manga_settings", manga_id=manga_id, manga_title=manga.get("title", ""))})'))
    
    if manga.get('inLibrary'):
        context_items.append(('Remove from Library', 
            f'RunPlugin({build_url(BASE_URL, action="remove_from_library", manga_id=manga_id)})'))
    else:
        context_items.append(('Add to Library', 
            f'RunPlugin({build_url(BASE_URL, action="add_to_library", manga_id=manga_id)})'))
    li.addContextMenuItems(context_items)
    
    url = build_url(BASE_URL, action='manga', manga_id=manga_id)
    add_directory_item(HANDLE, url, li, is_folder=True)


def add_chapter_item(chapter, manga, api):
    """Add chapter item to listing"""
    chapter_id = chapter.get('id')
    manga_id = manga.get('id')
    label = format_chapter_name(chapter)
    
    # Add status indicators
    if chapter.get('isDownloaded'):
        label += " [DL]"
    if chapter.get('isBookmarked'):
        label += " [BM]"
    if chapter.get('isRead'):
        label = f"[COLOR gray]{label}[/COLOR]"
    
    # Progress indicator
    page_count = chapter.get('pageCount', 0) or 0
    last_read = chapter.get('lastPageRead', 0) or 0
    if page_count > 0 and last_read > 0 and not chapter.get('isRead'):
        label += f" [{last_read}/{page_count}]"
    
    # Use cached thumbnail
    thumb = get_cached_thumbnail(api, manga)
    
    upload_date = format_date(chapter.get('uploadDate'))
    plot = f"Uploaded: {upload_date}" if upload_date else ""
    
    li = create_list_item(label, thumb=thumb, plot=plot)
    li.setProperty('IsPlayable', 'false')
    
    # Add context menu
    context_items = []
    # Reading options
    context_items.append(('Reading Settings', 
        f'RunPlugin({build_url(BASE_URL, action="manga_settings", manga_id=manga_id, manga_title=manga.get("title", ""))})'))
    # Auto slideshow option
    context_items.append(('Auto Slideshow', 
        f'RunPlugin({build_url(BASE_URL, action="read_chapter_slideshow", chapter_id=chapter_id)})'))
    
    if chapter.get('isRead'):
        context_items.append(('Mark as Unread', 
            f'RunPlugin({build_url(BASE_URL, action="mark_unread", chapter_id=chapter_id)})'))
    else:
        context_items.append(('Mark as Read', 
            f'RunPlugin({build_url(BASE_URL, action="mark_read", chapter_id=chapter_id)})'))
    
    if chapter.get('isDownloaded'):
        context_items.append(('Delete Download', 
            f'RunPlugin({build_url(BASE_URL, action="delete_download", chapter_id=chapter_id)})'))
    else:
        context_items.append(('Download Chapter', 
            f'RunPlugin({build_url(BASE_URL, action="download_chapter", chapter_id=chapter_id)})'))
    
    li.addContextMenuItems(context_items)
    
    # Click opens chapter for reading (first time shows settings, after that just reads)
    url = build_url(BASE_URL, action='chapter_pages', chapter_id=chapter_id, manga_id=manga_id)
    add_directory_item(HANDLE, url, li, is_folder=True)


def read_chapter_slideshow(chapter_id):
    """Read chapter as slideshow - downloads all images and plays automatically"""
    api = get_api()
    if not api:
        return
    
    try:
        # Fetch pages - this returns the actual page URLs
        log_info(f"Fetching pages for chapter {chapter_id}")
        pages_result = api.get_chapter_pages(chapter_id)
        
        # The pages array contains the actual URLs to the images
        pages = pages_result.get('fetchChapterPages', {}).get('pages', [])
        
        if not pages:
            show_error("No pages found")
            return
        
        log_info(f"Found {len(pages)} pages")
        log_info(f"First page URL: {pages[0] if pages else 'none'}")
        
        # Create cache directory
        import xbmcvfs
        import os
        
        # Get Kodi temp path
        kodi_temp = xbmcvfs.translatePath('special://temp/')
        cache_dir = os.path.join(kodi_temp, 'suwayomi_cache')
        
        # Create directory if needed
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        
        # Clear old cache files
        for f in os.listdir(cache_dir):
            try:
                os.remove(os.path.join(cache_dir, f))
            except:
                pass
        
        # Download images with progress
        from urllib.request import Request, urlopen
        import ssl
        
        local_files = []
        
        # Create SSL context
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        dialog = xbmcgui.DialogProgress()
        dialog.create('Loading Chapter', 'Downloading pages...')
        
        for i, page_url in enumerate(pages):
            if dialog.iscanceled():
                dialog.close()
                return
            
            percent = int((i / len(pages)) * 100)
            dialog.update(percent, f'Downloading page {i+1} of {len(pages)}')
            
            # Ensure page_url is a string
            page_url = str(page_url) if page_url else ''
            
            # Build full URL from the page_url returned by API
            if page_url.startswith('/'):
                full_url = f"{api.server_url}{page_url}"
            elif page_url.startswith('http'):
                full_url = page_url
            else:
                # Fallback to REST endpoint
                full_url = f"{api.server_url}/api/v1/manga/chapter/{chapter_id}/page/{i}"
            
            # Determine file extension from URL or default to jpg
            ext = '.jpg'
            if '.png' in str(page_url).lower():
                ext = '.png'
            elif '.webp' in str(page_url).lower():
                ext = '.webp'
            elif '.gif' in str(page_url).lower():
                ext = '.gif'
            
            local_path = os.path.join(cache_dir, f'page_{i:04d}{ext}')
            
            try:
                log_info(f"Downloading page {i+1} from: {full_url}")
                req = Request(full_url)
                req.add_header('User-Agent', 'Kodi-Suwayomi/1.0')
                
                # Add auth header if needed
                if api.headers.get('Authorization'):
                    req.add_header('Authorization', api.headers['Authorization'])
                
                with urlopen(req, timeout=30, context=ctx) as response:
                    image_data = response.read()
                    
                    # Check if we got actual image data (should be larger than 5KB typically)
                    if len(image_data) < 1000:
                        log_error(f"Page {i+1} data too small ({len(image_data)} bytes), might be an error")
                        log_info(f"Response content: {image_data[:500]}")
                    
                    # Write using Python's native file operations
                    with open(local_path, 'wb') as f:
                        f.write(image_data)
                    
                    local_files.append(local_path)
                    log_info(f"Downloaded page {i+1} ({len(image_data)} bytes)")
                    
            except Exception as e:
                log_error(f"Failed to download page {i}: {e}")
                # Continue anyway
        
        dialog.close()
        
        if not local_files:
            show_error("Failed to download any pages")
            return
        
        log_info(f"Starting slideshow from {cache_dir} with {len(local_files)} images")
        
        # Use SlideShow with the directory
        xbmc.executebuiltin(f'SlideShow({cache_dir},notrandom)')
        
        # Mark chapter as read
        try:
            api.update_chapter_progress(chapter_id, len(pages) - 1)
            api.mark_chapter_read(chapter_id, True)
            log_info("Marked chapter as read")
        except Exception as e:
            log_error(f"Failed to update progress: {e}")
        
    except Exception as e:
        log_error(f"Failed to read chapter: {e}")
        show_error(f"Error: {str(e)}")


def show_chapter_pages(chapter_id, manga_id=None, skip_options=False):
    """Download chapter pages and show in reader"""
    api = get_api()
    if not api:
        return
    
    try:
        # Get chapter info including lastPageRead
        chapter_info = api.get_chapter(chapter_id)
        chapter_data = chapter_info.get('chapter', {})
        last_page_read = chapter_data.get('lastPageRead', 0) or 0
        page_count = chapter_data.get('pageCount', 0) or 0
        is_read = chapter_data.get('isRead', False)
        
        # Get manga info
        manga_data = chapter_data.get('manga', {})
        if not manga_id:
            manga_id = manga_data.get('id')
        manga_title = manga_data.get('title', '')
        
        # Get defaults from settings
        zoom_setting = get_setting('zoom_mode') or 'Fit Width'
        zoom_map = {'Fit Width': 0, 'Fit Height': 1, 'Fit Screen': 2, 'Original': 3}
        zoom_mode = zoom_map.get(zoom_setting, 0)
        padding_percent = get_setting_int('padding_percent') or 0
        two_page_mode = get_setting_bool('two_page_mode')
        two_page_start_setting = get_setting('two_page_start') or 'Single First'
        two_page_start = 'single_first' if two_page_start_setting == 'Single First' else 'paired_first'
        
        # Only show settings dialog on FIRST time opening this manga
        reading_options = None
        
        if manga_id and not skip_options and not is_manga_configured(manga_id):
            # First time - show setup dialog
            reading_options = show_first_time_settings_dialog(manga_id, manga_title)
            if reading_options is None:  # User cancelled
                return
        else:
            # Already configured or skipped - use saved/default settings
            if manga_id:
                manga_settings = get_manga_reading_settings(manga_id)
            else:
                manga_settings = {}
            
            reading_options = {
                'direction': manga_settings.get('direction', get_setting('reading_direction') or 'Left to Right'),
                'mode': manga_settings.get('mode', get_setting('reading_mode') or 'Paged'),
                'auto_play': manga_settings.get('auto_play', get_setting_bool('auto_play')),
                'speed': manga_settings.get('speed', get_setting_int('slideshow_speed') or 5),
                'zoom_mode': manga_settings.get('zoom_mode', zoom_mode),
                'padding_percent': manga_settings.get('padding_percent', padding_percent),
                'two_page_mode': manga_settings.get('two_page_mode', two_page_mode),
                'two_page_start': manga_settings.get('two_page_start', two_page_start)
            }
        
        # Fetch pages - this returns the actual page URLs
        log_info(f"Fetching pages for chapter {chapter_id}")
        pages_result = api.get_chapter_pages(chapter_id)
        
        log_info(f"Pages result: {pages_result}")
        pages = pages_result.get('fetchChapterPages', {}).get('pages', [])
        
        if pages:
            log_info(f"First page URL: {pages[0]} (type: {type(pages[0]).__name__})")
        
        if not pages:
            show_notification("No pages found")
            return
        
        total_pages = len(pages)
        log_info(f"Found {total_pages} pages, last read: {last_page_read}")
        log_info(f"Reading options: {reading_options}")
        
        # Ask to resume if there's progress and not already read
        start_page = 0
        if last_page_read > 0 and last_page_read < total_pages - 1 and not is_read:
            dialog = xbmcgui.Dialog()
            resume = dialog.yesno(
                "Resume Reading?",
                f"Continue from page {last_page_read + 1} of {total_pages}?",
                yeslabel="Resume",
                nolabel="Start Over"
            )
            if resume:
                start_page = last_page_read
        
        # Apply reading direction
        if reading_options['direction'] == 'Right to Left':
            pages = list(reversed(pages))
            # Adjust start page for RTL
            start_page = total_pages - 1 - start_page
            log_info("Reversed page order for RTL reading")
        
        # Download all pages first
        import xbmcvfs
        import os
        from urllib.request import Request, urlopen
        import ssl
        
        # Get Kodi temp path
        kodi_temp = xbmcvfs.translatePath('special://temp/')
        cache_dir = os.path.join(kodi_temp, 'suwayomi_cache')
        
        # Create directory if needed
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        
        # Clear old cache files
        for f in os.listdir(cache_dir):
            try:
                os.remove(os.path.join(cache_dir, f))
            except:
                pass
        
        # Create SSL context
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        progress_dialog = xbmcgui.DialogProgress()
        progress_dialog.create('Loading Chapter', 'Downloading pages...')
        
        local_files = []
        
        for i, page_url in enumerate(pages):
            if progress_dialog.iscanceled():
                progress_dialog.close()
                return
            
            percent = int((i / len(pages)) * 100)
            progress_dialog.update(percent, f'Downloading page {i+1} of {len(pages)}')
            
            # Ensure page_url is a string
            page_url = str(page_url) if page_url else ''
            
            # Build full URL from the page_url returned by API
            if page_url.startswith('/'):
                full_url = f"{api.server_url}{page_url}"
            elif page_url.startswith('http'):
                full_url = page_url
            else:
                # Fallback to REST endpoint
                full_url = f"{api.server_url}/api/v1/manga/chapter/{chapter_id}/page/{i}"
            
            if i == 0:
                log_info(f"Page URL format: {full_url}")
            
            # Determine file extension
            ext = '.jpg'
            if '.png' in full_url.lower():
                ext = '.png'
            elif '.webp' in full_url.lower():
                ext = '.webp'
            elif '.gif' in full_url.lower():
                ext = '.gif'
            
            local_path = os.path.join(cache_dir, f'page_{i:04d}{ext}')
            
            try:
                req = Request(full_url)
                req.add_header('User-Agent', 'Kodi-Suwayomi/1.0')
                
                if api.headers.get('Authorization'):
                    req.add_header('Authorization', api.headers['Authorization'])
                
                with urlopen(req, timeout=30, context=ctx) as response:
                    image_data = response.read()
                    
                    with open(local_path, 'wb') as f:
                        f.write(image_data)
                    
                    local_files.append(local_path)
                    
            except Exception as e:
                log_error(f"Failed to download page {i}: {e}")
        
        progress_dialog.close()
        
        if not local_files:
            show_error("Failed to download any pages")
            return
        
        log_info(f"Downloaded {len(local_files)} pages, mode: {reading_options.get('mode', 'Paged')}")
        
        # Track final page for progress update
        final_page = [start_page]
        
        def on_viewer_close(page_index):
            final_page[0] = page_index
        
        # Get all reading options
        zoom_mode = reading_options.get('zoom_mode', 0)
        padding = reading_options.get('padding_percent', 0)
        is_webtoon = reading_options.get('mode', 'Paged') == 'Webtoon'
        two_page = reading_options.get('two_page_mode', False)
        two_page_start = reading_options.get('two_page_start', 'single_first')
        
        # Choose viewer based on mode
        try:
            from resources.lib.webtoon_viewer import show_webtoon_viewer
            final_page[0] = show_webtoon_viewer(
                local_files, start_page, 
                zoom_mode=zoom_mode, 
                padding_percent=padding,
                is_webtoon=is_webtoon,
                two_page_mode=two_page,
                two_page_start=two_page_start,
                on_close=on_viewer_close
            )
        except Exception as e:
            log_error(f"Viewer failed: {e}, falling back to slideshow")
            if start_page > 0:
                # Reorder files so slideshow starts from resume page
                reordered_dir = os.path.join(kodi_temp, 'suwayomi_reordered')
                if os.path.exists(reordered_dir):
                    for f in os.listdir(reordered_dir):
                        try:
                            os.remove(os.path.join(reordered_dir, f))
                        except:
                            pass
                else:
                    os.makedirs(reordered_dir)
                
                # Copy files in new order starting from start_page
                reordered_files = local_files[start_page:] + local_files[:start_page]
                import shutil
                for i, src_path in enumerate(reordered_files):
                    ext = os.path.splitext(src_path)[1]
                    dst_path = os.path.join(reordered_dir, f'page_{i:04d}{ext}')
                    shutil.copy2(src_path, dst_path)
                
                cache_dir = reordered_dir
            
            if reading_options.get('auto_play'):
                xbmc.executebuiltin(f'SlideShow({cache_dir},notrandom)')
            else:
                xbmc.executebuiltin(f'SlideShow({cache_dir},notrandom,pause)')
        
        # Update progress on server
        try:
            # Use the final page from viewer
            progress_page = final_page[0]
            api.update_chapter_progress(chapter_id, progress_page)
            
            # Mark as read if viewed most of the chapter
            if progress_page >= total_pages * 0.8:
                api.mark_chapter_read(chapter_id, True)
                log_info("Marked chapter as read")
            else:
                log_info(f"Updated progress to page {progress_page + 1}")
        except Exception as e:
            log_error(f"Failed to update progress: {e}")
        
    except Exception as e:
        log_error(f"Failed to load chapter: {e}")
        show_error(f"Error: {str(e)}")


def view_single_page(chapter_id, page_index, total, page_url=None):
    """View a single page - download and display"""
    api = get_api()
    if not api:
        return
    
    try:
        import xbmcvfs
        import os
        from urllib.request import Request, urlopen
        from urllib.parse import unquote
        import ssl
        
        # Use provided URL or fetch from API
        if page_url:
            full_url = unquote(page_url)
        else:
            # Need to fetch the pages to get the URL
            pages_result = api.get_chapter_pages(chapter_id)
            pages = pages_result.get('fetchChapterPages', {}).get('pages', [])
            if page_index < len(pages):
                page_url = pages[page_index]
                if page_url.startswith('/'):
                    full_url = f"{api.server_url}{page_url}"
                elif page_url.startswith('http'):
                    full_url = page_url
                else:
                    full_url = api.get_page_url(chapter_id, page_index)
            else:
                full_url = api.get_page_url(chapter_id, page_index)
        
        log_info(f"Viewing page {page_index + 1}/{total}: {full_url}")
        
        # Get Kodi temp path
        kodi_temp = xbmcvfs.translatePath('special://temp/')
        cache_dir = os.path.join(kodi_temp, 'suwayomi_cache')
        
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        
        # Determine extension
        ext = '.jpg'
        if '.png' in full_url.lower():
            ext = '.png'
        elif '.webp' in full_url.lower():
            ext = '.webp'
        
        local_path = os.path.join(cache_dir, f'single_page{ext}')
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = Request(full_url)
        req.add_header('User-Agent', 'Kodi-Suwayomi/1.0')
        if api.headers.get('Authorization'):
            req.add_header('Authorization', api.headers['Authorization'])
        
        dialog = xbmcgui.DialogProgress()
        dialog.create('Loading Page', f'Downloading page {page_index + 1}...')
        
        with urlopen(req, timeout=30, context=ctx) as response:
            image_data = response.read()
            with open(local_path, 'wb') as f:
                f.write(image_data)
        
        dialog.close()
        
        log_info(f"Downloaded page to {local_path} ({len(image_data)} bytes)")
        
        # Show the image
        xbmc.executebuiltin(f'ShowPicture({local_path})')
        
    except Exception as e:
        log_error(f"Failed to view page: {e}")
        show_error(f"Error: {str(e)}")


def view_page(page_url, page_num, total):
    """View a single page image - deprecated, use view_single_page"""
    log_info(f"Viewing page {page_num}/{total}: {page_url}")
    show_notification("Use 'View Pages (List)' for better compatibility")




def show_updates(page=0):
    """Show recent chapter updates"""
    api = get_api()
    if not api:
        return
    
    items_per_page = get_setting_int('items_per_page') or 50
    offset = page * items_per_page
    
    try:
        result = api.get_recent_updates(offset=offset, limit=items_per_page)
        chapters = result.get('chapters', {})
        nodes = chapters.get('nodes', [])
        page_info = chapters.get('pageInfo', {})
        
        if not nodes:
            show_notification("No recent updates")
            return
        
        for chapter in nodes:
            manga = chapter.get('manga') or {}
            manga_title = manga.get('title', 'Unknown')
            label = f"{manga_title} - {format_chapter_name(chapter)}"
            
            if chapter.get('isRead'):
                label = f"[COLOR gray]{label}[/COLOR]"
            
            manga_id = manga.get('id')
            
            # Use cached thumbnail
            thumb = get_cached_thumbnail(api, manga) if manga_id else ''
            
            upload_date = format_date(chapter.get('uploadDate'))
            
            li = create_list_item(label, thumb=thumb, plot=f"Updated: {upload_date}")
            
            # Context menu for auto slideshow
            context_items = [
                ('Auto Slideshow', 
                 f'RunPlugin({build_url(BASE_URL, action="read_chapter_slideshow", chapter_id=chapter["id"])})'),
                ('Reading Settings',
                 f'RunPlugin({build_url(BASE_URL, action="manga_settings", manga_id=manga_id, manga_title=manga_title)})'),
                ('Go to Manga',
                 f'Container.Update({build_url(BASE_URL, action="manga", manga_id=manga_id)})')
            ]
            li.addContextMenuItems(context_items)
            
            # Click loads chapter for manual reading
            url = build_url(BASE_URL, action='chapter_pages', chapter_id=chapter['id'], manga_id=manga_id)
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        if page_info.get('hasNextPage'):
            url = build_url(BASE_URL, action='updates', page=page + 1)
            li = create_list_item(f"Next Page ({page + 2})", icon='DefaultFolder.png')
            add_directory_item(HANDLE, url, li, is_folder=True)
        
        end_directory(HANDLE, content_type='episodes')
        
    except Exception as e:
        log_error(f"Failed to load updates: {e}")
        show_error(f"Error: {str(e)}")


def show_extensions():
    """Show extensions management"""
    api = get_api()
    if not api:
        return
    
    show_nsfw = get_setting_bool('show_nsfw')
    
    try:
        result = api.get_extensions()
        extensions = result.get('extensions', {}).get('nodes', [])
        
        # Separate installed and available
        installed = []
        available = []
        updates = []
        
        for ext in extensions:
            if not show_nsfw and ext.get('isNsfw'):
                continue
            if ext.get('isInstalled'):
                if ext.get('hasUpdate'):
                    updates.append(ext)
                else:
                    installed.append(ext)
            else:
                available.append(ext)
        
        # Add section headers and extensions
        if updates:
            li = create_list_item("[B]--- Updates Available ---[/B]")
            add_directory_item(HANDLE, "", li, is_folder=False)
            for ext in updates:
                add_extension_item(ext, api)
        
        if installed:
            li = create_list_item("[B]--- Installed ---[/B]")
            add_directory_item(HANDLE, "", li, is_folder=False)
            for ext in sorted(installed, key=lambda x: x.get('name', '')):
                add_extension_item(ext, api)
        
        if available:
            li = create_list_item("[B]--- Available ---[/B]")
            add_directory_item(HANDLE, "", li, is_folder=False)
            for ext in sorted(available, key=lambda x: x.get('name', '')):
                add_extension_item(ext, api)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load extensions: {e}")
        show_error(f"Error: {str(e)}")


def add_extension_item(ext, api):
    """Add extension item to listing"""
    pkg_name = ext.get('pkgName', '')
    apk_name = ext.get('apkName', '')  # Use apkName for icon URL
    label = f"[{ext.get('lang', '').upper()}] {ext.get('name', 'Unknown')}"
    
    if ext.get('isNsfw'):
        label += " [NSFW]"
    if ext.get('hasUpdate'):
        label += " [UPDATE]"
    if ext.get('isObsolete'):
        label += " [OBSOLETE]"
    
    # Use iconUrl from API response first, then build from apkName
    icon_url = ext.get('iconUrl', '')
    if icon_url:
        if icon_url.startswith('/'):
            icon_url = f"{api.server_url}{icon_url}"
    elif apk_name:
        icon_url = api.get_extension_icon_url(apk_name)
    
    li = create_list_item(label, thumb=icon_url, icon=icon_url, 
                         plot=f"Version: {ext.get('versionName', '')}\nPackage: {pkg_name}")
    
    # Context menu and click action based on status
    context_items = []
    url = ""
    
    if ext.get('isInstalled'):
        if ext.get('hasUpdate'):
            context_items.append(('Update', 
                f'RunPlugin({build_url(BASE_URL, action="update_extension", pkg_name=pkg_name)})'))
            # Clicking updates if update available
            url = build_url(BASE_URL, action='update_extension', pkg_name=pkg_name)
        context_items.append(('Uninstall', 
            f'RunPlugin({build_url(BASE_URL, action="uninstall_extension", pkg_name=pkg_name)})'))
    else:
        context_items.append(('Install', 
            f'RunPlugin({build_url(BASE_URL, action="install_extension", pkg_name=pkg_name)})'))
        # Clicking installs
        url = build_url(BASE_URL, action='install_extension', pkg_name=pkg_name)
    
    li.addContextMenuItems(context_items)
    add_directory_item(HANDLE, url, li, is_folder=False)


def show_downloads():
    """Show download queue"""
    api = get_api()
    if not api:
        return
    
    try:
        result = api.get_download_status()
        status = result.get('downloadStatus', {})
        state = status.get('state', 'STOPPED')
        queue = status.get('queue', [])
        
        # Add control items
        if state == 'STARTED':
            li = create_list_item("[B]Pause Downloads[/B]")
            url = build_url(BASE_URL, action='stop_downloader')
            add_directory_item(HANDLE, url, li, is_folder=False)
        else:
            li = create_list_item("[B]Start Downloads[/B]")
            url = build_url(BASE_URL, action='start_downloader')
            add_directory_item(HANDLE, url, li, is_folder=False)
        
        li = create_list_item("[B]Clear Queue[/B]")
        url = build_url(BASE_URL, action='clear_downloader')
        add_directory_item(HANDLE, url, li, is_folder=False)
        
        # Add queue items
        if queue:
            li = create_list_item(f"[B]--- Queue ({len(queue)} items) ---[/B]")
            add_directory_item(HANDLE, "", li, is_folder=False)
            
            for i, item in enumerate(queue):
                progress = item.get('progress', 0) or 0
                item_state = item.get('state', 'QUEUED')
                
                label = f"Item {i+1} [{item_state}]"
                if progress > 0:
                    label += f" ({progress}%)"
                
                li = create_list_item(label)
                add_directory_item(HANDLE, "", li, is_folder=False)
        else:
            li = create_list_item("Download queue is empty")
            add_directory_item(HANDLE, "", li, is_folder=False)
        
        end_directory(HANDLE)
        
    except Exception as e:
        log_error(f"Failed to load downloads: {e}")
        show_error(f"Error: {str(e)}")


# ==================== ACTIONS ====================

def add_to_library(manga_id):
    """Add manga to library with optional category selection"""
    api = get_api()
    if not api:
        return
    
    try:
        # Get available categories
        categories_result = api.get_categories()
        categories = categories_result.get('categories', {}).get('nodes', [])
        
        selected_category_ids = []
        
        if categories:
            # Build category list for selection
            category_names = [cat.get('name', f"Category {cat.get('id')}") for cat in categories]
            
            dialog = xbmcgui.Dialog()
            
            # Ask if user wants to select categories
            if dialog.yesno("Add to Library", "Do you want to select categories?", 
                           nolabel="Use Default", yeslabel="Select Categories"):
                # Show multi-select dialog
                selected = dialog.multiselect("Select Categories", category_names)
                
                if selected is not None and len(selected) > 0:
                    selected_category_ids = [categories[i].get('id') for i in selected]
        
        # Add to library
        api.add_manga_to_library(manga_id)
        
        # Set categories if selected
        if selected_category_ids:
            try:
                api.set_manga_categories(manga_id, selected_category_ids)
                cat_names = [categories[i].get('name') for i in range(len(categories)) if categories[i].get('id') in selected_category_ids]
                show_notification(f"Added to: {', '.join(cat_names)}")
            except Exception as e:
                log_error(f"Failed to set categories: {e}")
                show_notification("Added to library (category setting failed)")
        else:
            show_notification("Added to library")
        
        xbmc.executebuiltin('Container.Refresh')
        
    except Exception as e:
        show_error(f"Error: {str(e)}")


def remove_from_library(manga_id):
    """Remove manga from library"""
    api = get_api()
    if not api:
        return
    
    if not show_yesno_dialog("Confirm", "Remove from library?"):
        return
    
    try:
        api.remove_manga_from_library(manga_id)
        show_notification("Removed from library")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def refresh_manga(manga_id):
    """Refresh manga info"""
    api = get_api()
    if not api:
        return
    try:
        api.refresh_manga(manga_id)
        show_notification("Manga refreshed")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_chapter_read(chapter_id):
    """Mark chapter as read"""
    api = get_api()
    if not api:
        return
    try:
        api.mark_chapter_read(chapter_id, True)
        show_notification("Marked as read")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_chapter_unread(chapter_id):
    """Mark chapter as unread"""
    api = get_api()
    if not api:
        return
    try:
        api.mark_chapter_read(chapter_id, False)
        show_notification("Marked as unread")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def download_chapter(chapter_id):
    """Download chapter"""
    api = get_api()
    if not api:
        return
    try:
        api.enqueue_chapter_download(chapter_id)
        api.start_downloader()
        show_notification("Added to download queue")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def delete_chapter_download(chapter_id):
    """Delete downloaded chapter"""
    api = get_api()
    if not api:
        return
    
    if not show_yesno_dialog("Confirm", "Delete downloaded chapter?"):
        return
    
    try:
        api.delete_chapter_download(chapter_id)
        show_notification("Download deleted")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def download_all_chapters(manga_id):
    """Download all chapters for a manga"""
    api = get_api()
    if not api:
        return
    
    if not show_yesno_dialog("Confirm", "Download all chapters?"):
        return
    
    try:
        chapters_result = api.get_chapters(manga_id, limit=1000)
        chapters = chapters_result.get('chapters', {}).get('nodes', [])
        chapter_ids = [c['id'] for c in chapters if not c.get('isDownloaded')]
        
        if not chapter_ids:
            show_notification("All chapters already downloaded")
            return
        
        api.enqueue_chapters_download(chapter_ids)
        api.start_downloader()
        show_notification(f"Added {len(chapter_ids)} chapters to queue")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_all_chapters_read(manga_id):
    """Mark all chapters as read"""
    api = get_api()
    if not api:
        return
    
    try:
        chapters_result = api.get_chapters(manga_id, limit=1000)
        chapters = chapters_result.get('chapters', {}).get('nodes', [])
        chapter_ids = [c['id'] for c in chapters]
        
        api.mark_chapters_read(chapter_ids, True)
        show_notification("All chapters marked as read")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def install_extension(pkg_name):
    """Install extension"""
    api = get_api()
    if not api:
        return
    try:
        log_info(f"Installing extension: {pkg_name}")
        result = api.install_extension(pkg_name)
        log_info(f"Install result: {result}")
        show_notification("Extension installed successfully")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        log_error(f"Failed to install extension {pkg_name}: {e}")
        show_error(f"Install failed: {str(e)}")


def update_extension(pkg_name):
    """Update extension"""
    api = get_api()
    if not api:
        return
    try:
        log_info(f"Updating extension: {pkg_name}")
        result = api.update_extension(pkg_name)
        log_info(f"Update result: {result}")
        show_notification("Extension updated successfully")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        log_error(f"Failed to update extension {pkg_name}: {e}")
        show_error(f"Update failed: {str(e)}")


def uninstall_extension(pkg_name):
    """Uninstall extension"""
    api = get_api()
    if not api:
        return
    
    if not show_yesno_dialog("Confirm", "Uninstall extension?"):
        return
    
    try:
        log_info(f"Uninstalling extension: {pkg_name}")
        result = api.uninstall_extension(pkg_name)
        log_info(f"Uninstall result: {result}")
        show_notification("Extension uninstalled successfully")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        log_error(f"Failed to uninstall extension {pkg_name}: {e}")
        show_error(f"Uninstall failed: {str(e)}")


def start_downloader():
    """Start download queue"""
    api = get_api()
    if not api:
        return
    try:
        api.start_downloader()
        show_notification("Downloads started")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def stop_downloader():
    """Stop download queue"""
    api = get_api()
    if not api:
        return
    try:
        api.stop_downloader()
        show_notification("Downloads stopped")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def clear_downloader():
    """Clear download queue"""
    api = get_api()
    if not api:
        return
    
    if not show_yesno_dialog("Confirm", "Clear download queue?"):
        return
    
    try:
        api.clear_downloader()
        show_notification("Queue cleared")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


# ==================== ROUTER ====================

def router(params):
    """Route to appropriate function based on action"""
    action = params.get('action', '')
    
    if not action:
        main_menu()
    elif action == 'library':
        category_id = params.get('category_id')
        if category_id:
            category_id = int(category_id)
        page = int(params.get('page', 0))
        show_library(category_id, page)
    elif action == 'categories':
        show_categories()
    elif action == 'category_manga':
        category_id = int(params.get('category_id'))
        page = int(params.get('page', 0))
        show_category_manga(category_id, page)
    elif action == 'sources':
        show_sources()
    elif action == 'source_menu':
        show_source_menu(params['source_id'])
    elif action == 'source_popular':
        page = int(params.get('page', 1))
        show_source_popular(params['source_id'], page)
    elif action == 'source_latest':
        page = int(params.get('page', 1))
        show_source_latest(params['source_id'], page)
    elif action == 'source_search':
        page = int(params.get('page', 1))
        search_term = params.get('search_term')
        search_source(params['source_id'], search_term, page)
    elif action == 'search':
        search_term = params.get('search_term')
        global_search(search_term)
    elif action == 'manga':
        show_manga(int(params['manga_id']))
    elif action == 'read_chapter':
        # Legacy - redirect to chapter pages
        manga_id = params.get('manga_id')
        show_chapter_pages(int(params['chapter_id']), int(manga_id) if manga_id else None)
    elif action == 'read_chapter_slideshow':
        read_chapter_slideshow(int(params['chapter_id']))
    elif action == 'chapter_pages':
        manga_id = params.get('manga_id')
        show_chapter_pages(int(params['chapter_id']), int(manga_id) if manga_id else None)
    elif action == 'chapter_pages_quick':
        # Quick start - skip options dialog
        manga_id = params.get('manga_id')
        show_chapter_pages(int(params['chapter_id']), int(manga_id) if manga_id else None, skip_options=True)
    elif action == 'reading_options':
        # Open addon settings to reading section
        ADDON.openSettings()
    elif action == 'manga_settings':
        # Show manga-specific reading settings dialog
        manga_id = int(params['manga_id'])
        manga_title = params.get('manga_title', '')
        from urllib.parse import unquote
        if manga_title:
            manga_title = unquote(manga_title)
        show_manga_settings_dialog(manga_id, manga_title)
    elif action == 'view_single_page':
        from urllib.parse import unquote
        page_url = params.get('page_url')
        if page_url:
            page_url = unquote(page_url)
        view_single_page(int(params['chapter_id']), int(params['page_index']), int(params['total']), page_url)
    elif action == 'view_page':
        from urllib.parse import unquote
        page_url = unquote(params.get('page_url', ''))
        page_num = params.get('page_num', '1')
        total = params.get('total', '1')
        view_page(page_url, page_num, total)
    elif action == 'updates':
        page = int(params.get('page', 0))
        show_updates(page)
    elif action == 'extensions':
        show_extensions()
    elif action == 'downloads':
        show_downloads()
    # Actions (RunPlugin)
    elif action == 'add_to_library':
        add_to_library(int(params['manga_id']))
    elif action == 'remove_from_library':
        remove_from_library(int(params['manga_id']))
    elif action == 'refresh_manga':
        refresh_manga(int(params['manga_id']))
    elif action == 'mark_read':
        mark_chapter_read(int(params['chapter_id']))
    elif action == 'mark_unread':
        mark_chapter_unread(int(params['chapter_id']))
    elif action == 'download_chapter':
        download_chapter(int(params['chapter_id']))
    elif action == 'delete_download':
        delete_chapter_download(int(params['chapter_id']))
    elif action == 'download_all':
        download_all_chapters(int(params['manga_id']))
    elif action == 'mark_all_read':
        mark_all_chapters_read(int(params['manga_id']))
    elif action == 'install_extension':
        install_extension(params['pkg_name'])
    elif action == 'update_extension':
        update_extension(params['pkg_name'])
    elif action == 'uninstall_extension':
        uninstall_extension(params['pkg_name'])
    elif action == 'start_downloader':
        start_downloader()
    elif action == 'stop_downloader':
        stop_downloader()
    elif action == 'clear_downloader':
        clear_downloader()
    elif action == 'history':
        show_history()
    elif action == 'hide_source':
        from urllib.parse import unquote
        source_name = unquote(params.get('source_name', ''))
        hide_source(source_name)
    elif action == 'manage_hidden_sources':
        manage_hidden_sources()
    else:
        log_error(f"Unknown action: {action}")


if __name__ == '__main__':
    params = parse_url(sys.argv[2])
    log_info(f"Called with params: {params}")
    router(params)
