"""
Audiobookshelf Kodi Client v1.0.1
Stream audiobooks and podcasts from your Audiobookshelf server
"""
import os
import sys
import time
import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import xbmcvfs
from urllib.parse import urlencode, parse_qsl, quote, unquote
from library_service import AudioBookShelfLibraryService
from playback_monitor import PlaybackMonitor, get_resume_position
from download_manager import DownloadManager, is_network_available
try:
    from urllib.request import urlretrieve
except ImportError:
    from urllib import urlretrieve

ADDON = xbmcaddon.Addon()
ADDON_HANDLE = int(sys.argv[1])
ADDON_URL = sys.argv[0]

download_manager = DownloadManager(ADDON)
_active_monitor = None

# Global token cache file for persistence
def get_token_cache_file():
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not os.path.exists(profile):
        os.makedirs(profile)
    return os.path.join(profile, 'token_cache.json')

def load_token_cache():
    try:
        import json
        cache_file = get_token_cache_file()
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
    except:
        pass
    return {'token': None, 'url': None, 'expires': 0}

def save_token_cache(cache):
    try:
        import json
        cache_file = get_token_cache_file()
        with open(cache_file, 'w') as f:
            json.dump(cache, f)
    except:
        pass

TOKEN_CACHE_DURATION = 600  # 10 minutes


def build_url(**kwargs):
    return f'{ADDON_URL}?{urlencode(kwargs)}'


def get_setting(setting_id, default=''):
    try:
        val = ADDON.getSetting(setting_id)
        return val if val else default
    except:
        return default


def get_setting_bool(setting_id, default=False):
    val = get_setting(setting_id, 'true' if default else 'false')
    return val.lower() == 'true'


def get_setting_int(setting_id, default=0):
    try:
        return int(get_setting(setting_id, str(default)))
    except:
        return default


def get_sync_interval(is_podcast=False):
    setting_id = 'podcast_sync_interval' if is_podcast else 'audiobook_sync_interval'
    idx = get_setting_int(setting_id, 1)
    intervals = [10, 15, 30, 60]
    return intervals[idx] if idx < len(intervals) else 15


def check_download_path():
    """Check if download path is set, prompt user if not"""
    if not get_setting_bool('enable_downloads'):
        return False
    
    path = get_setting('download_path')
    if not path or path.strip() == '':
        xbmcgui.Dialog().ok('Download Path Required', 
                           'Please set a download folder in settings to enable downloads.')
        ADDON.openSettings()
        path = get_setting('download_path')
        if not path or path.strip() == '':
            return False
    return True


def has_downloads():
    """Check if user has any downloads"""
    downloads = download_manager.get_all_downloads()
    return len(downloads) > 0


def get_library_service():
    """Get authenticated library service with caching"""
    
    if not is_network_available():
        if get_setting_bool('enable_downloads') and has_downloads():
            return None, None, None, True
        xbmcgui.Dialog().ok('No Network', 'No network connection available')
        return None, None, None, False
    
    ip = get_setting('ipaddress')
    port = get_setting('port', '13378')
    auth_method = get_setting_int('auth_method', 0)
    
    if not ip:
        xbmcgui.Dialog().ok('Setup Required', 'Please configure server settings')
        ADDON.openSettings()
        return None, None, None, False
    
    url = f"http://{ip}:{port}"
    
    # Load cached token
    token_cache = load_token_cache()
    current_time = time.time()
    
    if (token_cache.get('token') and token_cache.get('url') == url and 
        token_cache.get('expires', 0) > current_time):
        xbmc.log("Using cached token", xbmc.LOGINFO)
        lib_service = AudioBookShelfLibraryService(url, token_cache['token'])
        return lib_service, url, token_cache['token'], False
    
    try:
        if auth_method == 1:  # API Key
            api_key = get_setting('api_key')
            if not api_key:
                xbmcgui.Dialog().ok('API Key Required', 'Please enter your API key in settings')
                ADDON.openSettings()
                return None, None, None, False
            token = api_key
        else:  # Username/Password
            username = get_setting('username')
            password = get_setting('password')
            if not username or not password:
                xbmcgui.Dialog().ok('Credentials Required', 'Please enter username and password')
                ADDON.openSettings()
                return None, None, None, False
            
            from login_service import AudioBookShelfService
            service = AudioBookShelfService(url)
            response = service.login(username, password)
            token = response.get('token')
            if not token:
                raise ValueError("No token received")
        
        # Save token to cache
        new_cache = {'token': token, 'url': url, 'expires': current_time + TOKEN_CACHE_DURATION}
        save_token_cache(new_cache)
        
        lib_service = AudioBookShelfLibraryService(url, token)
        
        # Sync offline progress
        if get_setting_bool('offline_sync_on_connect', True):
            try:
                synced = download_manager.sync_positions_to_server(lib_service)
                if synced > 0:
                    xbmcgui.Dialog().notification('Synced', f'{synced} positions synced', 
                                                 xbmcgui.NOTIFICATION_INFO, 2000)
            except:
                pass
        
        return lib_service, url, token, False
        
    except Exception as e:
        error_msg = str(e)
        xbmc.log(f"Auth failed: {error_msg}", xbmc.LOGERROR)
        
        # Try cached token if rate limited
        if '429' in error_msg:
            token_cache = load_token_cache()
            if token_cache.get('token') and token_cache.get('url') == url:
                xbmc.log("Rate limited, using cached token", xbmc.LOGWARNING)
                xbmcgui.Dialog().notification('Rate Limited', 'Using cached session', xbmcgui.NOTIFICATION_WARNING)
                lib_service = AudioBookShelfLibraryService(url, token_cache['token'])
                return lib_service, url, token_cache['token'], False
        
        # Only go offline if downloads enabled AND has downloads
        if get_setting_bool('enable_downloads') and has_downloads():
            if xbmcgui.Dialog().yesno('Connection Failed', 
                                      f'{error_msg[:80]}\n\nUse offline mode with downloads?'):
                return None, None, None, True
        
        xbmcgui.Dialog().ok('Connection Error', f'Failed to connect:\n{error_msg[:100]}')
        return None, None, None, False


def download_cover(url, item_id):
    try:
        profile_path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
        cache_dir = os.path.join(profile_path, 'covers')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        cache_file = os.path.join(cache_dir, f"{item_id}.jpg")
        if os.path.exists(cache_file):
            return cache_file
        urlretrieve(url, cache_file)
        return cache_file if os.path.exists(cache_file) else None
    except:
        return None


def set_music_info(list_item, title, artist='', duration=0, playcount=0, tracknumber=0):
    try:
        info_tag = list_item.getMusicInfoTag()
        info_tag.setTitle(title)
        if artist:
            info_tag.setArtist(artist)
        if duration > 0:
            info_tag.setDuration(int(duration))
        if playcount > 0:
            info_tag.setPlayCount(playcount)
        if tracknumber > 0:
            info_tag.setTrack(tracknumber)
    except AttributeError:
        list_item.setInfo('music', {'title': title, 'artist': artist, 'duration': int(duration),
                                    'playcount': playcount, 'tracknumber': tracknumber})


def find_file_for_position(audio_files, position):
    sorted_files = sorted(audio_files, key=lambda x: x.get('index', 0))
    cumulative = 0
    for f in sorted_files:
        file_duration = f.get('duration', 0)
        if position < cumulative + file_duration:
            return f, position - cumulative, cumulative
        cumulative += file_duration
    if sorted_files:
        return sorted_files[-1], 0, cumulative - sorted_files[-1].get('duration', 0)
    return None, 0, 0


def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def resolve_progress_conflict(server_time, local_time, duration, is_podcast=False):
    setting_id = 'podcast_conflict_resolution' if is_podcast else 'audiobook_conflict_resolution'
    resolution = get_setting_int(setting_id, 0)
    
    if resolution == 0:
        return server_time
    elif resolution == 1:
        return local_time
    elif resolution == 2:
        return max(server_time, local_time)
    else:
        options = [f'Server: {format_time(server_time)}', f'Local: {format_time(local_time)}',
                   f'Furthest: {format_time(max(server_time, local_time))}']
        choice = xbmcgui.Dialog().select('Resume Position', options)
        if choice == 0:
            return server_time
        elif choice == 1:
            return local_time
        elif choice == 2:
            return max(server_time, local_time)
        return server_time


def ask_resume(current_time, duration):
    if current_time < 10:
        return False
    return xbmcgui.Dialog().yesno('Resume', f'Resume from {format_time(current_time)}?',
                                  nolabel='Start Over', yeslabel='Resume')


def count_podcast_progress(library_service, item_id, episodes):
    """Count how many episodes are finished"""
    finished = 0
    started = 0
    for ep in episodes:
        try:
            progress = library_service.get_media_progress(item_id, ep.get('id'))
            if progress:
                if progress.get('isFinished'):
                    finished += 1
                elif progress.get('progress', 0) > 0:
                    started += 1
        except:
            pass
    return finished, started


def list_libraries():
    """List libraries with auto-navigation if only one type"""
    library_service, url, token, offline = get_library_service()
    
    if offline:
        xbmcplugin.setContent(ADDON_HANDLE, 'albums')
        list_item = xbmcgui.ListItem(label='[Downloaded Items]')
        list_item.setArt({'icon': 'DefaultFolder.png'})
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='downloads'), list_item, isFolder=True)
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        return
    
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        data = library_service.get_all_libraries()
        libraries = data.get('libraries', [])
        
        # Separate by type
        book_libs = [l for l in libraries if l.get('mediaType') == 'book']
        podcast_libs = [l for l in libraries if l.get('mediaType') == 'podcast']
        
        # Auto-navigate if only one type
        if not book_libs and len(podcast_libs) == 1:
            list_library_items(podcast_libs[0]['id'], is_podcast=True)
            return
        elif not podcast_libs and len(book_libs) == 1:
            list_library_items(book_libs[0]['id'], is_podcast=False)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'albums')
        
        # Downloads
        if get_setting_bool('enable_downloads') and has_downloads():
            list_item = xbmcgui.ListItem(label='[Downloaded Items]')
            list_item.setArt({'icon': 'DefaultFolder.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='downloads'), list_item, isFolder=True)
        
        # Libraries
        for library in libraries:
            list_item = xbmcgui.ListItem(label=library['name'])
            icon = 'DefaultMusicAlbums.png' if library.get('mediaType') == 'book' else 'DefaultMusicVideos.png'
            list_item.setArt({'icon': icon})
            is_podcast = library.get('mediaType') == 'podcast'
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='library', library_id=library['id'], 
                                                is_podcast='1' if is_podcast else '0'),
                                       list_item, isFolder=True)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_library_items(library_id, is_podcast=False):
    """List items in library"""
    xbmcplugin.setContent(ADDON_HANDLE, 'albums')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        # Add Search option for podcasts
        if is_podcast:
            list_item = xbmcgui.ListItem(label='[Search & Add Podcasts]')
            list_item.setArt({'icon': 'DefaultAddSource.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='search'), list_item, isFolder=True)
        
        items = library_service.get_library_items(library_id)
        view_filter = get_setting_int('view_filter', 0)
        show_markers = get_setting_bool('show_progress_markers', True)
        
        for item in items.get('results', []):
            media = item.get('media', {})
            metadata = media.get('metadata', {})
            item_id = item['id']
            media_type = item.get('mediaType', 'book')
            
            # Get progress
            is_finished = False
            progress_pct = 0
            try:
                progress = library_service.get_media_progress(item_id)
                if progress:
                    is_finished = progress.get('isFinished', False)
                    progress_pct = progress.get('progress', 0) * 100
            except:
                pass
            
            is_downloaded = download_manager.is_downloaded(item_id)
            
            # Apply filters
            if view_filter == 1:  # Hide Finished
                if is_finished:
                    continue
            elif view_filter == 2:  # Downloaded Only
                if not is_downloaded:
                    continue
            
            # Cover
            cover_url = f"{url}/api/items/{item_id}/cover?token={token}"
            local_cover = download_cover(cover_url, item_id)
            
            title = metadata.get('title', 'Unknown')
            author = metadata.get('authorName', '')
            duration = media.get('duration', 0)
            
            # Build prefix
            prefix = ''
            if show_markers:
                if is_downloaded:
                    prefix = '[DL] '
                elif media_type == 'podcast':
                    # For podcasts, count actual episodes
                    num_eps = media.get('numEpisodes', 0)
                    if num_eps > 0:
                        # If we have progress, estimate based on it
                        # But never show [Done] for podcasts unless truly all finished
                        if is_finished:
                            prefix = f'[{num_eps}/{num_eps}] '
                        elif progress_pct > 0:
                            # Rough estimate of watched episodes
                            watched = max(1, int((progress_pct / 100) * num_eps))
                            prefix = f'[{watched}/{num_eps}] '
                        # No prefix if no progress
                elif is_finished:
                    prefix = '[Done] '
                elif progress_pct > 0:
                    prefix = f'[{int(progress_pct)}%] '
            
            display_title = f'{prefix}{title}'
            
            list_item = xbmcgui.ListItem(label=display_title)
            if local_cover:
                list_item.setArt({'thumb': local_cover, 'poster': local_cover, 'fanart': local_cover})
            set_music_info(list_item, title=title, artist=author, duration=duration,
                          playcount=1 if is_finished else 0)
            
            # Context menu
            context_items = []
            if get_setting_bool('enable_downloads'):
                if is_downloaded:
                    context_items.append(('Delete Download', 
                                         f'RunPlugin({build_url(action="delete_download", item_id=item_id)})'))
                elif media_type == 'podcast':
                    context_items.append(('Download All Episodes', 
                                         f'RunPlugin({build_url(action="download_podcast", item_id=item_id)})'))
                else:
                    context_items.append(('Download', 
                                         f'RunPlugin({build_url(action="download", item_id=item_id, library_id=library_id)})'))
            if context_items:
                list_item.addContextMenuItems(context_items)
            
            # Navigation
            if media_type == 'podcast':
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                           build_url(action='episodes', item_id=item_id),
                                           list_item, isFolder=True)
            elif media.get('numAudioFiles', 1) > 1 or media.get('chapters'):
                xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                           build_url(action='parts', item_id=item_id),
                                           list_item, isFolder=True)
            else:
                list_item.setProperty('IsPlayable', 'true')
                xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                           build_url(action='play', item_id=item_id),
                                           list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_episodes(item_id, sort_by='date'):
    """List podcast episodes"""
    xbmcplugin.setContent(ADDON_HANDLE, 'episodes')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = item.get('media', {}).get('episodes', [])
        feed_url = item.get('media', {}).get('metadata', {}).get('feedUrl', '')
        
        view_filter = get_setting_int('view_filter', 0)
        show_markers = get_setting_bool('show_progress_markers', True)
        min_for_sort = get_setting_int('min_episodes_for_sort', 10)
        
        # Find New Episodes
        if feed_url:
            list_item = xbmcgui.ListItem(label='[Find New Episodes]')
            list_item.setArt({'icon': 'DefaultAddSource.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='find_episodes', item_id=item_id),
                                       list_item, isFolder=True)
        
        if not episodes:
            xbmcplugin.endOfDirectory(ADDON_HANDLE)
            return
        
        # Sort option
        if len(episodes) >= min_for_sort:
            sort_labels = {'date': 'Newest', 'date_old': 'Oldest', 'title': 'Title', 
                          'episode': 'Episode #', 'duration': 'Duration'}
            next_sort = {'date': 'date_old', 'date_old': 'title', 'title': 'episode',
                        'episode': 'duration', 'duration': 'date'}
            
            list_item = xbmcgui.ListItem(label=f'[Sort: {sort_labels.get(sort_by, "Newest")}]')
            list_item.setArt({'icon': 'DefaultPlaylist.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                       build_url(action='episodes', item_id=item_id, 
                                                sort_by=next_sort.get(sort_by, 'date')),
                                       list_item, isFolder=True)
        
        # Sort episodes
        if sort_by == 'date':
            episodes = sorted(episodes, key=lambda x: x.get('publishedAt') or 0, reverse=True)
        elif sort_by == 'date_old':
            episodes = sorted(episodes, key=lambda x: x.get('publishedAt') or 0)
        elif sort_by == 'title':
            episodes = sorted(episodes, key=lambda x: (x.get('title') or '').lower())
        elif sort_by == 'episode':
            episodes = sorted(episodes, key=lambda x: (x.get('season') or 0, x.get('episode') or 0))
        elif sort_by == 'duration':
            episodes = sorted(episodes, key=lambda x: x.get('duration') or 0, reverse=True)
        
        for episode in episodes:
            title = episode.get('title', 'Unknown')
            episode_id = episode.get('id')
            duration = episode.get('duration', 0)
            has_audio = episode.get('audioFile') is not None
            
            # Get progress
            is_finished = False
            progress_pct = 0
            try:
                progress = library_service.get_media_progress(item_id, episode_id)
                if progress:
                    is_finished = progress.get('isFinished', False)
                    progress_pct = progress.get('progress', 0) * 100
            except:
                pass
            
            is_downloaded = download_manager.is_downloaded(item_id, episode_id)
            
            # Apply filters
            if view_filter == 1:  # Hide Finished
                if is_finished:
                    continue
            elif view_filter == 2:  # Downloaded Only
                if not is_downloaded:
                    continue
            
            # Build prefix - show percentage for individual episodes
            prefix = ''
            if show_markers:
                if is_downloaded:
                    prefix = '[DL] '
                elif not has_audio:
                    prefix = '[+] '
                elif is_finished:
                    prefix = '[Done] '
                elif progress_pct > 0:
                    prefix = f'[{int(progress_pct)}%] '
            
            # Episode number
            season = episode.get('season')
            ep_num = episode.get('episode')
            ep_info = ''
            if season and ep_num:
                ep_info = f'S{season}E{ep_num} '
            elif ep_num:
                ep_info = f'E{ep_num} '
            
            list_item = xbmcgui.ListItem(label=f'{prefix}{ep_info}{title}')
            list_item.setProperty('IsPlayable', 'true' if has_audio or is_downloaded else 'false')
            set_music_info(list_item, title=title, duration=duration, playcount=1 if is_finished else 0)
            
            # Context menu
            context_items = []
            if not has_audio:
                context_items.append(('Download to Server', 
                                     f'RunPlugin({build_url(action="download_to_server", item_id=item_id, episode_id=episode_id)})'))
            if get_setting_bool('enable_downloads'):
                if is_downloaded:
                    context_items.append(('Delete Local', 
                                         f'RunPlugin({build_url(action="delete_download", item_id=item_id, episode_id=episode_id)})'))
                elif has_audio:
                    context_items.append(('Download Locally', 
                                         f'RunPlugin({build_url(action="download_episode", item_id=item_id, episode_id=episode_id)})'))
            if context_items:
                list_item.addContextMenuItems(context_items)
            
            if has_audio or is_downloaded:
                url_params = build_url(action='play_episode', item_id=item_id, episode_id=episode_id)
            else:
                url_params = build_url(action='download_to_server', item_id=item_id, episode_id=episode_id)
            
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def find_new_episodes(item_id):
    """Find new episodes from RSS feed"""
    library_service, url, token, offline = get_library_service()
    if not library_service or offline:
        xbmcgui.Dialog().notification('Error', 'Requires network', xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        import requests
        
        progress = xbmcgui.DialogProgress()
        progress.create('Finding Episodes', 'Getting podcast info...')
        
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        feed_url = item.get('media', {}).get('metadata', {}).get('feedUrl', '')
        server_episodes = item.get('media', {}).get('episodes', [])
        
        if not feed_url:
            progress.close()
            xbmcgui.Dialog().notification('No Feed', 'No RSS feed URL', xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        progress.update(20, 'Fetching RSS feed...')
        
        # Get server episode identifiers
        server_guids = {ep.get('guid') for ep in server_episodes if ep.get('guid')}
        server_titles = {(ep.get('title') or '').lower().strip() for ep in server_episodes}
        
        # Episodes on server without audio
        need_download = [ep for ep in server_episodes 
                        if not ep.get('audioFile') or 
                        (isinstance(ep.get('audioFile'), dict) and not ep['audioFile'].get('ino'))]
        
        # Fetch RSS
        try:
            rss = requests.get(feed_url, timeout=30, headers={'User-Agent': 'Kodi-Audiobookshelf/1.0'})
            rss.raise_for_status()
        except Exception as e:
            progress.close()
            xbmc.log(f"RSS fetch error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('RSS Error', 'Could not fetch feed', xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        progress.update(60, 'Parsing episodes...')
        
        # Parse RSS
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(rss.content)
            channel = root.find('channel')
        except:
            progress.close()
            xbmcgui.Dialog().notification('Parse Error', 'Invalid RSS', xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        itunes = '{http://www.itunes.com/dtds/podcast-1.0.dtd}'
        new_episodes = []
        
        for item_el in channel.findall('item') if channel is not None else []:
            title_el = item_el.find('title')
            title = (title_el.text or 'Unknown').strip() if title_el is not None else 'Unknown'
            
            guid_el = item_el.find('guid')
            guid = (guid_el.text or title).strip() if guid_el is not None else title
            
            if guid in server_guids or title.lower().strip() in server_titles:
                continue
            
            enclosure = item_el.find('enclosure')
            audio_url = enclosure.get('url', '') if enclosure is not None else ''
            if not audio_url:
                continue
            
            new_episodes.append({'title': title, 'guid': guid, 'audioUrl': audio_url})
        
        progress.close()
        
        xbmc.log(f"Found {len(need_download)} need download, {len(new_episodes)} new from RSS", xbmc.LOGINFO)
        
        if not need_download and not new_episodes:
            xbmcgui.Dialog().notification('Up to Date', 'No new episodes', xbmcgui.NOTIFICATION_INFO)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'episodes')
        
        # Refresh option - use RunPlugin so it doesn't fail as a folder
        list_item = xbmcgui.ListItem(label='[Refresh Podcast from RSS]')
        list_item.setArt({'icon': 'DefaultAddSource.png'})
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                   build_url(action='refresh_podcast', item_id=item_id),
                                   list_item, isFolder=False)
        
        # Batch download for ALL episodes (both types)
        total_downloadable = len(need_download) + len(new_episodes)
        if total_downloadable > 1:
            list_item = xbmcgui.ListItem(label=f'[Add All {total_downloadable} Episodes]')
            list_item.setArt({'icon': 'DefaultAddSource.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='batch_add_episodes', item_id=item_id),
                                       list_item, isFolder=False)
        
        # Episodes needing download (on server)
        for ep in need_download:
            list_item = xbmcgui.ListItem(label=f'[Need DL] {ep.get("title", "Unknown")}')
            list_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='download_to_server', item_id=item_id, episode_id=ep.get('id')),
                                       list_item, isFolder=False)
        
        # New from RSS
        for ep in new_episodes[:50]:
            list_item = xbmcgui.ListItem(label=f'[NEW] {ep["title"]}')
            list_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='add_rss_episode', item_id=item_id,
                                                episode_guid=quote(ep['guid']), episode_title=quote(ep['title'])),
                                       list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Find episodes error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def refresh_podcast(item_id):
    """Refresh podcast from RSS - tell server to check for new episodes"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        xbmcgui.Dialog().notification('Refreshing...', 'Checking RSS feed', xbmcgui.NOTIFICATION_INFO, 2000)
        
        # The API endpoint requires POST, not GET
        check_url = f"{url}/api/podcasts/{item_id}/check-new-episodes"
        xbmc.log(f"Calling (POST): {check_url}", xbmc.LOGINFO)
        
        # Try POST first (newer API)
        response = requests.post(check_url, headers=library_service.headers, timeout=60)
        xbmc.log(f"POST Response status: {response.status_code}", xbmc.LOGINFO)
        
        # If POST fails with 404, try GET (older API)
        if response.status_code == 404:
            xbmc.log("POST returned 404, trying GET", xbmc.LOGINFO)
            response = requests.get(check_url, headers=library_service.headers, timeout=60)
            xbmc.log(f"GET Response status: {response.status_code}", xbmc.LOGINFO)
        
        if response.status_code == 200:
            try:
                result = response.json()
                count = result.get('numEpisodesAdded', 0)
                xbmcgui.Dialog().notification('Refreshed', f'{count} new episodes found', xbmcgui.NOTIFICATION_INFO)
            except:
                xbmcgui.Dialog().notification('Refreshed', 'Check complete', xbmcgui.NOTIFICATION_INFO)
            xbmc.executebuiltin('Container.Refresh')
        elif response.status_code == 404:
            # The endpoint doesn't exist - this might be an older Audiobookshelf version
            # Try alternative approach: just fetch RSS directly
            xbmc.log("API endpoint not found, server may not support this feature", xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Not Supported', 'Server does not support RSS refresh', xbmcgui.NOTIFICATION_WARNING)
        else:
            xbmc.log(f"Response: {response.text[:200]}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Failed', f'Error {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
            
    except Exception as e:
        xbmc.log(f"Refresh error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def batch_add_episodes(item_id):
    """Add all new episodes from RSS and trigger downloads"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        progress = xbmcgui.DialogProgress()
        progress.create('Adding Episodes', 'Refreshing podcast from RSS...')
        
        # First refresh from RSS to add new episodes to server database (POST request)
        check_url = f"{url}/api/podcasts/{item_id}/check-new-episodes"
        xbmc.log(f"Batch add - calling POST: {check_url}", xbmc.LOGINFO)
        
        # Try POST first
        response = requests.post(check_url, headers=library_service.headers, timeout=120)
        
        # If 404, try GET
        if response.status_code == 404:
            xbmc.log("POST 404, trying GET", xbmc.LOGINFO)
            response = requests.get(check_url, headers=library_service.headers, timeout=120)
        
        added = 0
        if response.status_code == 200:
            try:
                result = response.json()
                added = result.get('numEpisodesAdded', 0)
                xbmc.log(f"Server added {added} episodes from RSS", xbmc.LOGINFO)
            except:
                pass
        else:
            xbmc.log(f"Check new episodes failed: {response.status_code}", xbmc.LOGWARNING)
        
        progress.update(50, 'Getting episode list...')
        
        # Get updated episode list
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = item.get('media', {}).get('episodes', [])
        
        # Find episodes without audio (need download on server)
        need_download = []
        for ep in episodes:
            audio_file = ep.get('audioFile')
            if audio_file is None:
                need_download.append(ep)
            elif isinstance(audio_file, dict) and not audio_file.get('ino'):
                need_download.append(ep)
        
        xbmc.log(f"Found {len(need_download)} episodes needing download", xbmc.LOGINFO)
        
        progress.update(70, f'Queueing {len(need_download)} downloads...')
        
        queued = 0
        if need_download:
            # Download episodes (limit to 20 at a time)
            ids_to_download = [ep['id'] for ep in need_download[:20] if ep.get('id')]
            
            if ids_to_download:
                dl_url = f"{url}/api/podcasts/{item_id}/download-episodes"
                xbmc.log(f"Downloading {len(ids_to_download)} episodes", xbmc.LOGINFO)
                dl_response = requests.post(dl_url, headers=library_service.headers, 
                                           json=ids_to_download, timeout=30)
                
                if dl_response.status_code == 200:
                    queued = len(ids_to_download)
                    xbmc.log(f"Queued {queued} episode downloads", xbmc.LOGINFO)
                else:
                    xbmc.log(f"Download queue failed: {dl_response.status_code}", xbmc.LOGERROR)
        
        progress.close()
        
        if queued > 0:
            xbmcgui.Dialog().notification('Done', f'{queued} episodes queued', xbmcgui.NOTIFICATION_INFO)
        elif added > 0:
            xbmcgui.Dialog().notification('Done', f'{added} new episodes added', xbmcgui.NOTIFICATION_INFO)
        else:
            xbmcgui.Dialog().notification('Up to Date', 'No new episodes to add', xbmcgui.NOTIFICATION_INFO)
        
        xbmc.executebuiltin('Container.Refresh')
        
    except Exception as e:
        try:
            progress.close()
        except:
            pass
        xbmc.log(f"Batch add error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def add_rss_episode(item_id, episode_guid, episode_title):
    """Add episode from RSS to server"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        guid = unquote(episode_guid)
        title = unquote(episode_title)
        
        xbmcgui.Dialog().notification('Adding...', title[:30], xbmcgui.NOTIFICATION_INFO, 2000)
        
        # Refresh podcast to add new episodes from RSS (use POST)
        check_url = f"{url}/api/podcasts/{item_id}/check-new-episodes"
        xbmc.log(f"Add RSS episode - calling POST: {check_url}", xbmc.LOGINFO)
        
        response = requests.post(check_url, headers=library_service.headers, timeout=60)
        
        # If 404, try GET
        if response.status_code == 404:
            xbmc.log("POST 404, trying GET", xbmc.LOGINFO)
            response = requests.get(check_url, headers=library_service.headers, timeout=60)
        
        xbmc.log(f"Refresh response: {response.status_code}", xbmc.LOGINFO)
        
        if response.status_code != 200:
            # Server doesn't support this - show message
            xbmcgui.Dialog().notification('Not Supported', 'Server RSS refresh not available', xbmcgui.NOTIFICATION_WARNING)
            return
        
        # Get updated episode list
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = item.get('media', {}).get('episodes', [])
        
        # Find the episode by guid or title
        target = None
        for ep in episodes:
            ep_guid = ep.get('guid', '')
            ep_title = (ep.get('title') or '').lower().strip()
            if ep_guid == guid or ep_title == title.lower().strip():
                target = ep
                break
        
        if target and target.get('id'):
            xbmc.log(f"Found episode: {target['id']}", xbmc.LOGINFO)
            
            # Check if already has audio
            if target.get('audioFile'):
                xbmcgui.Dialog().notification('Already Downloaded', 'Episode has audio', xbmcgui.NOTIFICATION_INFO)
                return
            
            # Trigger download on server
            dl_url = f"{url}/api/podcasts/{item_id}/download-episodes"
            dl_response = requests.post(dl_url, headers=library_service.headers, 
                                       json=[target['id']], timeout=30)
            
            xbmc.log(f"Download response: {dl_response.status_code}", xbmc.LOGINFO)
            
            if dl_response.status_code == 200:
                xbmcgui.Dialog().notification('Download Started', title[:30], xbmcgui.NOTIFICATION_INFO)
            else:
                xbmc.log(f"Download error: {dl_response.text[:200]}", xbmc.LOGERROR)
                xbmcgui.Dialog().notification('Added', 'Added but download failed', xbmcgui.NOTIFICATION_WARNING)
        else:
            xbmc.log(f"Episode not found. Looking for guid={guid[:30]} or title={title[:30]}", xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Not Found', 'Episode not found after refresh', xbmcgui.NOTIFICATION_WARNING)
        
        xbmc.executebuiltin('Container.Refresh')
        
    except Exception as e:
        xbmc.log(f"Add RSS episode error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_episode_to_server(item_id, episode_id):
    """Download episode on server"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        response = requests.post(f"{url}/api/podcasts/{item_id}/download-episodes",
                                headers=library_service.headers, json=[episode_id], timeout=30)
        
        if response.status_code == 200:
            xbmcgui.Dialog().notification('Download Started', 'Server downloading...', xbmcgui.NOTIFICATION_INFO)
        else:
            xbmcgui.Dialog().notification('Failed', f'Error {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_episodes_to_server(item_id, episode_ids):
    """Download multiple episodes on server"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        ids = episode_ids.split(',')
        response = requests.post(f"{url}/api/podcasts/{item_id}/download-episodes",
                                headers=library_service.headers, json=ids, timeout=30)
        
        if response.status_code == 200:
            xbmcgui.Dialog().notification('Downloads Started', f'{len(ids)} episodes', xbmcgui.NOTIFICATION_INFO)
        else:
            xbmcgui.Dialog().notification('Failed', f'Error {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def list_parts(item_id):
    """List chapters/parts for audiobook"""
    xbmcplugin.setContent(ADDON_HANDLE, 'songs')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        chapters = item.get('media', {}).get('chapters', [])
        audio_files = item.get('media', {}).get('audioFiles', [])
        total_duration = item.get('media', {}).get('duration', 0)
        
        progress = library_service.get_media_progress(item_id)
        current_time = progress.get('currentTime', 0) if progress else 0
        is_finished = progress.get('isFinished', False) if progress else False
        
        # Local progress
        local_pos = download_manager.get_local_resume_position(item_id)
        if local_pos and local_pos.get('current_time', 0) > current_time:
            current_time = local_pos['current_time']
        
        # Resume option
        if current_time > 10 and not is_finished:
            target_file, seek, _ = find_file_for_position(audio_files, current_time)
            if target_file:
                current_chapter = ""
                for ch in sorted(chapters, key=lambda x: x.get('start', 0)):
                    if ch.get('start', 0) <= current_time < ch.get('end', total_duration):
                        current_chapter = f" - {ch.get('title', '')[:20]}"
                        break
                
                list_item = xbmcgui.ListItem(label=f'[Resume: {format_time(current_time)}{current_chapter}]')
                list_item.setProperty('IsPlayable', 'true')
                xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                           build_url(action='play_at_position', item_id=item_id,
                                                    file_ino=target_file.get('ino'), seek_time=int(seek)),
                                           list_item, isFolder=False)
        
        # Chapters or files
        if chapters:
            for i, ch in enumerate(sorted(chapters, key=lambda x: x.get('start', 0))):
                title = ch.get('title', f'Chapter {i+1}')
                ch_start = ch.get('start', 0)
                ch_end = ch.get('end', total_duration)
                
                prefix = '> ' if ch_start <= current_time < ch_end else ''
                
                list_item = xbmcgui.ListItem(label=f'{prefix}{title}')
                list_item.setProperty('IsPlayable', 'true')
                set_music_info(list_item, title=title, duration=ch_end - ch_start, tracknumber=i+1)
                
                target_file, seek, _ = find_file_for_position(audio_files, ch_start)
                if target_file:
                    xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                               build_url(action='play_at_position', item_id=item_id,
                                                        file_ino=target_file.get('ino'), seek_time=int(seek)),
                                               list_item, isFolder=False)
        else:
            cumulative = 0
            for i, f in enumerate(sorted(audio_files, key=lambda x: x.get('index', 0))):
                title = f.get('metadata', {}).get('title', f'Part {i+1}')
                dur = f.get('duration', 0)
                
                prefix = '> ' if cumulative <= current_time < cumulative + dur else ''
                
                list_item = xbmcgui.ListItem(label=f'{prefix}{title}')
                list_item.setProperty('IsPlayable', 'true')
                set_music_info(list_item, title=title, duration=dur)
                
                xbmcplugin.addDirectoryItem(ADDON_HANDLE,
                                           build_url(action='play_at_position', item_id=item_id,
                                                    file_ino=f.get('ino'), seek_time=0),
                                           list_item, isFolder=False)
                cumulative += dur
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def search_podcasts():
    """Search iTunes for podcasts"""
    library_service, url, token, offline = get_library_service()
    if not library_service or offline:
        xbmcgui.Dialog().notification('Error', 'Requires network', xbmcgui.NOTIFICATION_ERROR)
        return
    
    keyboard = xbmc.Keyboard('', 'Search Podcasts')
    keyboard.doModal()
    if not keyboard.isConfirmed():
        return
    
    query = keyboard.getText()
    if not query:
        return
    
    try:
        import requests
        results = requests.get(f"https://itunes.apple.com/search?term={quote(query)}&media=podcast&limit=20",
                              timeout=10).json()
        
        if not results.get('results'):
            xbmcgui.Dialog().notification('No Results', 'No podcasts found', xbmcgui.NOTIFICATION_INFO)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'albums')
        
        for podcast in results['results']:
            name = podcast.get('collectionName', 'Unknown')
            artist = podcast.get('artistName', '')
            feed_url = podcast.get('feedUrl', '')
            artwork = podcast.get('artworkUrl600', '')
            
            if not feed_url:
                continue
            
            list_item = xbmcgui.ListItem(label=name)
            list_item.setArt({'thumb': artwork, 'poster': artwork})
            set_music_info(list_item, title=name, artist=artist)
            
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='add_podcast', feed_url=feed_url, name=name),
                                       list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def add_podcast_to_library(feed_url, name):
    """Add podcast to server"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        progress = xbmcgui.DialogProgress()
        progress.create('Adding Podcast', 'Getting metadata...')
        
        # Get metadata from RSS
        metadata = {'title': name, 'feedUrl': feed_url}
        try:
            rss = requests.get(feed_url, timeout=15, headers={'User-Agent': 'Kodi-Audiobookshelf/1.0'})
            import xml.etree.ElementTree as ET
            root = ET.fromstring(rss.content)
            channel = root.find('channel')
            if channel is not None:
                title_el = channel.find('title')
                if title_el is not None and title_el.text:
                    metadata['title'] = title_el.text
                
                itunes = '{http://www.itunes.com/dtds/podcast-1.0.dtd}'
                author_el = channel.find(f'{itunes}author')
                if author_el is not None and author_el.text:
                    metadata['author'] = author_el.text
                
                img_el = channel.find(f'{itunes}image')
                if img_el is not None:
                    metadata['imageUrl'] = img_el.get('href', '')
        except:
            pass
        
        progress.update(40, 'Finding library...')
        
        data = library_service.get_all_libraries()
        podcast_libs = [l for l in data.get('libraries', []) if l.get('mediaType') == 'podcast']
        
        if not podcast_libs:
            progress.close()
            xbmcgui.Dialog().notification('Error', 'No podcast library', xbmcgui.NOTIFICATION_ERROR)
            return
        
        library_id = podcast_libs[0]['id']
        if len(podcast_libs) > 1:
            progress.close()
            names = [l['name'] for l in podcast_libs]
            idx = xbmcgui.Dialog().select('Select Library', names)
            if idx < 0:
                return
            library_id = podcast_libs[idx]['id']
            progress.create('Adding Podcast', 'Adding...')
        
        progress.update(60)
        
        library = library_service.get_library(library_id)
        folders = library.get('folders', [])
        if not folders:
            progress.close()
            xbmcgui.Dialog().notification('Error', 'No folder', xbmcgui.NOTIFICATION_ERROR)
            return
        
        folder_id = folders[0].get('id')
        folder_path = folders[0].get('fullPath', '/podcasts')
        safe_name = "".join(c for c in metadata['title'] if c.isalnum() or c in ' -_').strip()[:50]
        
        payload = {
            'path': f"{folder_path}/{safe_name}",
            'folderId': folder_id,
            'libraryId': library_id,
            'media': {'metadata': metadata},
            'autoDownloadEpisodes': False
        }
        
        response = requests.post(f"{url}/api/podcasts", headers=library_service.headers, json=payload, timeout=30)
        progress.close()
        
        if response.status_code == 200:
            xbmcgui.Dialog().notification('Added', metadata['title'][:30], xbmcgui.NOTIFICATION_INFO)
        elif 'exist' in response.text.lower():
            xbmcgui.Dialog().notification('Exists', 'Already in library', xbmcgui.NOTIFICATION_INFO)
        else:
            xbmcgui.Dialog().notification('Failed', f'Error {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def list_downloads():
    """List downloaded items"""
    xbmcplugin.setContent(ADDON_HANDLE, 'songs')
    
    downloads = download_manager.get_all_downloads()
    
    if not downloads:
        xbmcgui.Dialog().notification('No Downloads', 'Nothing downloaded yet', xbmcgui.NOTIFICATION_INFO)
    
    for key, info in downloads.items():
        list_item = xbmcgui.ListItem(label=info['title'])
        list_item.setProperty('IsPlayable', 'true')
        
        if info.get('cover_path') and os.path.exists(info['cover_path']):
            list_item.setArt({'thumb': info['cover_path'], 'poster': info['cover_path']})
        
        set_music_info(list_item, title=info['title'], artist=info.get('author', ''), duration=info.get('duration', 0))
        
        list_item.addContextMenuItems([
            ('Delete', f'RunPlugin({build_url(action="delete_download", key=key)})')
        ])
        
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='play_offline', key=key), list_item, isFolder=False)
    
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


# === PLAYBACK FUNCTIONS ===

def play_audio(play_url, title, duration, library_service, item_id, episode_id=None, 
               start_position=0, is_podcast=False):
    global _active_monitor
    
    list_item = xbmcgui.ListItem(path=play_url)
    set_music_info(list_item, title=title, duration=duration)
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    sync_enabled = get_setting_bool('sync_podcast_progress' if is_podcast else 'sync_audiobook_progress', True)
    sync_on_stop = get_setting_bool('podcast_sync_on_stop' if is_podcast else 'audiobook_sync_on_stop', True)
    sync_interval = get_sync_interval(is_podcast)
    
    _active_monitor = PlaybackMonitor(
        library_service, item_id, duration if duration > 0 else 1,
        episode_id=episode_id, download_manager=download_manager,
        sync_enabled=sync_enabled, sync_on_stop=sync_on_stop, sync_interval=sync_interval
    )
    _active_monitor.start_monitoring_async(start_position)


def play_at_position(item_id, file_ino, seek_time):
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        duration = item.get('media', {}).get('duration', 0)
        title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
        
        play_url = f"{url}/api/items/{item_id}/file/{file_ino}?token={token}"
        play_audio(play_url, title, duration, library_service, item_id, start_position=seek_time)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_item(item_id):
    library_service, url, token, offline = get_library_service()
    
    if download_manager.is_downloaded(item_id):
        play_offline_item(item_id)
        return
    
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        duration = item.get('media', {}).get('duration', 0)
        title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
        
        current_time = get_resume_position(library_service, item_id, None)
        local_pos = download_manager.get_local_resume_position(item_id)
        if local_pos and local_pos.get('current_time', 0) > current_time:
            current_time = local_pos['current_time']
        
        start_position = current_time if current_time > 10 and ask_resume(current_time, duration) else 0
        
        play_url = library_service.get_file_url(item_id)
        play_audio(play_url, title, duration, library_service, item_id, start_position=start_position)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def play_episode(item_id, episode_id):
    library_service, url, token, offline = get_library_service()
    
    if download_manager.is_downloaded(item_id, episode_id):
        play_offline_item(item_id, episode_id)
        return
    
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episode = next((ep for ep in item.get('media', {}).get('episodes', []) 
                       if ep.get('id') == episode_id), None)
        
        if not episode:
            raise ValueError("Episode not found")
        
        if not episode.get('audioFile'):
            xbmcgui.Dialog().notification('Not Available', 'Not on server', xbmcgui.NOTIFICATION_WARNING)
            return
        
        title = episode.get('title', 'Unknown')
        duration = episode.get('duration', 0)
        
        current_time = get_resume_position(library_service, item_id, episode_id)
        start_position = current_time if current_time > 10 and ask_resume(current_time, duration) else 0
        
        play_url = library_service.get_file_url(item_id, episode_id=episode_id)
        play_audio(play_url, title, duration, library_service, item_id, 
                  episode_id=episode_id, start_position=start_position, is_podcast=True)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def play_offline_item(item_id, episode_id=None):
    download_info = download_manager.get_download_info(item_id, episode_id)
    if not download_info:
        xbmcgui.Dialog().notification('Error', 'Not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    local_pos = download_manager.get_local_resume_position(item_id, episode_id)
    duration = download_info.get('duration', 0)
    
    start_pos = 0
    if local_pos and local_pos.get('current_time', 0) > 10 and not local_pos.get('is_finished'):
        if ask_resume(local_pos['current_time'], duration):
            start_pos = local_pos['current_time']
    
    file_path = download_info.get('file_path')
    if download_info.get('is_multifile'):
        file_path, start_pos = download_manager.get_file_for_position(item_id, start_pos)
    
    if not file_path or not os.path.exists(file_path):
        xbmcgui.Dialog().notification('Error', 'File not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    list_item = xbmcgui.ListItem(path=file_path)
    set_music_info(list_item, title=download_info['title'], duration=duration)
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    global _active_monitor
    _active_monitor = PlaybackMonitor(
        None, item_id, duration if duration > 0 else 1,
        episode_id=episode_id, download_manager=download_manager,
        offline_mode=True, sync_enabled=get_setting_bool('offline_save_progress', True)
    )
    _active_monitor.start_monitoring_async(start_pos)


# === DOWNLOAD FUNCTIONS ===

def download_item(item_id, library_id):
    if not check_download_path():
        return
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        media = item.get('media', {})
        metadata = media.get('metadata', {})
        
        item_data = {
            'title': metadata.get('title', 'Unknown'),
            'duration': media.get('duration', 0),
            'author': metadata.get('authorName', ''),
            'cover_url': f"{url}/api/items/{item_id}/cover?token={token}",
            'audio_files': media.get('audioFiles', []),
            'chapters': media.get('chapters', [])
        }
        
        if len(item_data['audio_files']) > 1:
            download_manager.download_audiobook_complete(item_id, item_data, library_service)
        else:
            download_manager.download_item(item_id, item_data, library_service)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_podcast(item_id):
    """Download all podcast episodes locally"""
    if not check_download_path():
        return
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = [ep for ep in item.get('media', {}).get('episodes', []) if ep.get('audioFile')]
        
        if not episodes:
            xbmcgui.Dialog().notification('No Episodes', 'Nothing to download', xbmcgui.NOTIFICATION_WARNING)
            return
        
        if not xbmcgui.Dialog().yesno('Download Podcast', f'Download {len(episodes)} episodes?'):
            return
        
        progress = xbmcgui.DialogProgress()
        progress.create('Downloading Podcast')
        
        success = 0
        for i, ep in enumerate(episodes):
            if progress.iscanceled():
                break
            
            # Kodi 21+ only accepts 2 arguments for update()
            pct = int((i / len(episodes)) * 100)
            msg = f'{i+1}/{len(episodes)}: {ep.get("title", "")[:40]}'
            progress.update(pct, msg)
            
            try:
                ep_id = ep.get('id')
                item_data = {
                    'title': ep.get('title', 'Unknown'),
                    'duration': ep.get('duration', 0),
                    'cover_url': f"{url}/api/items/{item_id}/cover?token={token}"
                }
                # Use download_item with show_progress=False for silent download
                download_manager.download_item(item_id, item_data, library_service, episode_id=ep_id, show_progress=False)
                success += 1
            except Exception as e:
                xbmc.log(f"Episode download error: {str(e)}", xbmc.LOGERROR)
        
        progress.close()
        xbmcgui.Dialog().notification('Complete', f'{success} episodes downloaded', xbmcgui.NOTIFICATION_INFO)
        
    except Exception as e:
        xbmc.log(f"Download podcast error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_episode(item_id, episode_id):
    if not check_download_path():
        return
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episode = next((ep for ep in item.get('media', {}).get('episodes', []) 
                       if ep.get('id') == episode_id), None)
        
        if not episode:
            raise ValueError("Episode not found")
        
        item_data = {
            'title': episode.get('title', 'Unknown'),
            'duration': episode.get('duration', 0),
            'cover_url': f"{url}/api/items/{item_id}/cover?token={token}"
        }
        
        download_manager.download_item(item_id, item_data, library_service, episode_id=episode_id)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def delete_download(item_id=None, episode_id=None, key=None):
    if key:
        parts = key.split('_', 1)
        item_id = parts[0]
        episode_id = parts[1] if len(parts) > 1 else None
    
    if item_id:
        download_manager.delete_download(item_id, episode_id)
        xbmc.executebuiltin('Container.Refresh')


# === ROUTER ===

def router(paramstring):
    params = dict(parse_qsl(paramstring))
    action = params.get('action')
    
    if not params or not action:
        list_libraries()
    elif action == 'library':
        list_library_items(params['library_id'], params.get('is_podcast') == '1')
    elif action == 'episodes':
        list_episodes(params['item_id'], params.get('sort_by', 'date'))
    elif action == 'parts':
        list_parts(params['item_id'])
    elif action == 'play':
        play_item(params['item_id'])
    elif action == 'play_at_position':
        play_at_position(params['item_id'], params['file_ino'], int(params.get('seek_time', 0)))
    elif action == 'play_episode':
        play_episode(params['item_id'], params['episode_id'])
    elif action == 'downloads':
        list_downloads()
    elif action == 'play_offline':
        parts = params.get('key', '').split('_', 1)
        play_offline_item(parts[0], parts[1] if len(parts) > 1 else None)
    elif action == 'search':
        search_podcasts()
    elif action == 'add_podcast':
        add_podcast_to_library(params['feed_url'], params['name'])
    elif action == 'find_episodes':
        find_new_episodes(params['item_id'])
    elif action == 'refresh_podcast':
        refresh_podcast(params['item_id'])
    elif action == 'batch_add_episodes':
        batch_add_episodes(params['item_id'])
    elif action == 'add_rss_episode':
        add_rss_episode(params['item_id'], params.get('episode_guid', ''), params.get('episode_title', ''))
    elif action == 'download_to_server':
        download_episode_to_server(params['item_id'], params['episode_id'])
    elif action == 'download_episodes_to_server':
        download_episodes_to_server(params['item_id'], params['episode_ids'])
    elif action == 'download':
        download_item(params['item_id'], params.get('library_id'))
    elif action == 'download_podcast':
        download_podcast(params['item_id'])
    elif action == 'download_episode':
        download_episode(params['item_id'], params['episode_id'])
    elif action == 'delete_download':
        delete_download(item_id=params.get('item_id'), episode_id=params.get('episode_id'), key=params.get('key'))
    else:
        list_libraries()


if __name__ == '__main__':
    router(sys.argv[2][1:])
