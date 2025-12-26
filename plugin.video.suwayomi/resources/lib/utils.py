"""
Utility functions for Suwayomi Kodi Addon
"""
import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import xbmcvfs
import sys
import os
from urllib.parse import urlencode, parse_qsl


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDON_DATA_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))


def get_setting(key):
    """Get addon setting value"""
    try:
        return ADDON.getSetting(key)
    except Exception:
        return ""


def get_setting_bool(key):
    """Get addon setting as boolean"""
    try:
        value = ADDON.getSetting(key)
        return value.lower() == 'true'
    except Exception:
        return False


def get_setting_int(key):
    """Get addon setting as integer"""
    try:
        value = ADDON.getSetting(key)
        return int(value) if value else 0
    except Exception:
        return 0


def set_setting(key, value):
    """Set addon setting value"""
    ADDON.setSetting(key, str(value))


def get_localized_string(string_id):
    """Get localized string"""
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGDEBUG):
    """Log message to Kodi log"""
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def log_error(message):
    """Log error message"""
    log(message, xbmc.LOGERROR)


def log_info(message):
    """Log info message"""
    log(message, xbmc.LOGINFO)


def show_notification(message, heading=None, icon=xbmcgui.NOTIFICATION_INFO, time=5000):
    """Show notification to user"""
    if heading is None:
        heading = ADDON_NAME
    xbmcgui.Dialog().notification(heading, message, icon, time)


def show_error(message, heading=None):
    """Show error notification"""
    show_notification(message, heading, xbmcgui.NOTIFICATION_ERROR)


def show_ok_dialog(heading, message):
    """Show OK dialog"""
    xbmcgui.Dialog().ok(heading, message)


def show_yesno_dialog(heading, message):
    """Show Yes/No dialog"""
    return xbmcgui.Dialog().yesno(heading, message)


def get_keyboard_input(heading="", default="", hidden=False):
    """Get keyboard input from user"""
    kb = xbmc.Keyboard(default, heading, hidden)
    kb.doModal()
    if kb.isConfirmed():
        return kb.getText()
    return None


def build_url(base_url, **kwargs):
    """Build plugin URL with parameters"""
    return f"{base_url}?{urlencode(kwargs)}"


def parse_url(url):
    """Parse plugin URL parameters"""
    return dict(parse_qsl(url.lstrip('?')))


def create_list_item(label, label2="", icon="", thumb="", fanart="", plot="", 
                     is_folder=False, is_playable=False, **kwargs):
    """Create Kodi list item"""
    li = xbmcgui.ListItem(label=label, label2=label2)
    
    # Set art
    art = {}
    if icon:
        art['icon'] = icon
    if thumb:
        art['thumb'] = thumb
        art['poster'] = thumb
    if fanart:
        art['fanart'] = fanart
    if art:
        li.setArt(art)
    
    # Set info
    info_tag = li.getVideoInfoTag()
    if plot:
        info_tag.setPlot(plot)
    
    # Set additional properties
    for key, value in kwargs.items():
        if key == 'title':
            info_tag.setTitle(value)
        elif key == 'genre':
            info_tag.setGenres(value if isinstance(value, list) else [value])
        elif key == 'year':
            info_tag.setYear(value)
        elif key == 'studio':
            info_tag.setStudios([value] if isinstance(value, str) else value)
    
    # Set playability
    if is_playable:
        li.setProperty('IsPlayable', 'true')
    
    return li


def add_directory_item(handle, url, listitem, is_folder=True, total_items=0):
    """Add directory item to Kodi listing"""
    xbmcplugin.addDirectoryItem(handle, url, listitem, isFolder=is_folder, 
                                 totalItems=total_items)


def end_directory(handle, sort_method=None, content_type=None, cache_to_disc=True):
    """End directory listing"""
    if sort_method is not None:
        xbmcplugin.addSortMethod(handle, sort_method)
    if content_type:
        xbmcplugin.setContent(handle, content_type)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=cache_to_disc)


def format_date(timestamp):
    """Format timestamp to readable date"""
    if not timestamp:
        return ""
    try:
        from datetime import datetime
        # Handle milliseconds timestamp
        if timestamp > 1e12:
            timestamp = timestamp / 1000
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def format_chapter_name(chapter):
    """Format chapter name for display"""
    number = chapter.get('chapterNumber', 0) or 0
    name = chapter.get('name', '') or ''
    scanlator = chapter.get('scanlator', '')
    
    # Clean up the name
    name = name.strip()
    
    # Check if name already contains chapter info
    name_lower = name.lower()
    has_chapter_prefix = name_lower.startswith('chapter') or name_lower.startswith('ch.')
    has_chapter_number = str(int(number)) in name if number > 0 else False
    
    if name and (has_chapter_prefix or has_chapter_number):
        # Name already has chapter info, use it directly
        result = name
    elif number > 0:
        if name:
            result = f"Chapter {number}: {name}"
        else:
            result = f"Chapter {number}"
    elif name:
        result = name
    else:
        result = "Chapter"
    
    if scanlator:
        result += f" [{scanlator}]"
    
    return result


def format_manga_status(status):
    """Format manga status for display"""
    status_map = {
        'ONGOING': 'Ongoing',
        'COMPLETED': 'Completed',
        'LICENSED': 'Licensed',
        'PUBLISHING_FINISHED': 'Publishing Finished',
        'CANCELLED': 'Cancelled',
        'ON_HIATUS': 'On Hiatus',
        'UNKNOWN': 'Unknown'
    }
    return status_map.get(status, status or 'Unknown')


def truncate_text(text, max_length=200):
    """Truncate text to maximum length"""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


class ProgressDialog:
    """Context manager for progress dialog"""
    
    def __init__(self, heading, message=""):
        self.dialog = xbmcgui.DialogProgress()
        self.heading = heading
        self.message = message
    
    def __enter__(self):
        self.dialog.create(self.heading, self.message)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dialog.close()
        return False
    
    def update(self, percent, message=None):
        if message:
            self.dialog.update(percent, message)
        else:
            self.dialog.update(percent)
    
    def is_cancelled(self):
        return self.dialog.iscanceled()


class ImageSlideshow:
    """Handle image slideshow for manga reading"""
    
    def __init__(self, images, start_index=0, auto_slideshow=False, slideshow_delay=5):
        self.images = images
        self.current_index = start_index
        self.auto_slideshow = auto_slideshow
        self.slideshow_delay = slideshow_delay
    
    def show(self):
        """Show slideshow"""
        if not self.images:
            show_error("No images to display")
            return
        
        # Use Kodi's built-in slideshow
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        playlist.clear()
        
        for i, image_url in enumerate(self.images):
            li = xbmcgui.ListItem(path=image_url)
            playlist.add(image_url, li)
        
        # Start playback
        xbmc.Player().play(playlist)


def ensure_data_directory():
    """Ensure addon data directory exists"""
    if not xbmcvfs.exists(ADDON_DATA_PATH):
        xbmcvfs.mkdirs(ADDON_DATA_PATH)
    return ADDON_DATA_PATH


def cache_path(filename):
    """Get path for cache file"""
    ensure_data_directory()
    return os.path.join(ADDON_DATA_PATH, filename)


def save_json_cache(filename, data):
    """Save data to JSON cache file"""
    import json
    filepath = cache_path(filename)
    try:
        with xbmcvfs.File(filepath, 'w') as f:
            f.write(json.dumps(data))
        return True
    except Exception as e:
        log_error(f"Failed to save cache: {e}")
        return False


def load_json_cache(filename):
    """Load data from JSON cache file"""
    import json
    filepath = cache_path(filename)
    try:
        if xbmcvfs.exists(filepath):
            with xbmcvfs.File(filepath, 'r') as f:
                return json.loads(f.read())
    except Exception as e:
        log_error(f"Failed to load cache: {e}")
    return None


def select_from_list(heading, options):
    """Show selection dialog"""
    return xbmcgui.Dialog().select(heading, options)


def context_menu(options):
    """Show context menu and return selected index"""
    return xbmcgui.Dialog().contextmenu(options)


def get_resolution():
    """Get current screen resolution"""
    return xbmcgui.getScreenWidth(), xbmcgui.getScreenHeight()
