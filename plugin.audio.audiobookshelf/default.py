"""
Audiobookshelf Kodi Client v1.0.5
Stream audiobooks and podcasts from your Audiobookshelf server
- Comprehensive bidirectional progress sync
- Startup sync pulls/pushes progress to server
- Background sync keeps local and server in sync
- Network reconnection automatically syncs
- Works for both streaming and downloaded content
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
from playback_monitor import (PlaybackMonitor, get_best_resume_position, 
                              sync_all_to_server, get_local_progress, save_local_progress)
from sync_manager import (get_sync_manager, startup_sync, on_network_reconnect, 
                          mark_offline, stop_background_sync)
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


TOKEN_CACHE_DURATION = 600


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


def get_finished_threshold():
    """Get the finished threshold from settings (0=90%, 1=95%, 2=last 30s, 3=last 60s)"""
    idx = get_setting_int('mark_podcast_finished_threshold', 1)
    if idx == 0:
        return 0.90
    elif idx == 1:
        return 0.95
    elif idx == 2:
        return 0.97  # ~last 30s of typical podcast
    else:
        return 0.95  # default


def get_sync_interval(is_podcast=False):
    setting_id = 'podcast_sync_interval' if is_podcast else 'audiobook_sync_interval'
    idx = get_setting_int(setting_id, 1)
    intervals = [10, 15, 30, 60]
    return intervals[idx] if idx < len(intervals) else 15


def check_download_path():
    if not get_setting_bool('enable_downloads'):
        return False
    path = get_setting('download_path')
    if not path or path.strip() == '':
        xbmcgui.Dialog().ok('Download Path Required', 
                           'Please set a download folder in settings.')
        ADDON.openSettings()
        path = get_setting('download_path')
        if not path or path.strip() == '':
            return False
    return True


def has_downloads():
    downloads = download_manager.get_all_downloads()
    return len(downloads) > 0


def get_library_service():
    """Get authenticated library service with caching"""
    if not is_network_available():
        if get_setting_bool('enable_downloads') and has_downloads():
            mark_offline()  # Mark that we're going offline
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
    
    token_cache = load_token_cache()
    current_time = time.time()
    
    if (token_cache.get('token') and token_cache.get('url') == url and 
        token_cache.get('expires', 0) > current_time):
        xbmc.log("Using cached token", xbmc.LOGINFO)
        lib_service = AudioBookShelfLibraryService(url, token_cache['token'])
        
        # Run startup sync with the library service
        try:
            sync_mgr = get_sync_manager()
            sync_mgr.set_library_service(lib_service)
            # Start background sync if not already running
            sync_mgr.start_background_sync()
        except Exception as e:
            xbmc.log(f"Sync manager setup error: {e}", xbmc.LOGDEBUG)
        
        return lib_service, url, token_cache['token'], False
    
    try:
        if auth_method == 1:
            api_key = get_setting('api_key')
            if not api_key:
                xbmcgui.Dialog().ok('API Key Required', 'Please enter your API key')
                ADDON.openSettings()
                return None, None, None, False
            token = api_key
        else:
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
        
        new_cache = {'token': token, 'url': url, 'expires': current_time + TOKEN_CACHE_DURATION}
        save_token_cache(new_cache)
        
        lib_service = AudioBookShelfLibraryService(url, token)
        
        # Perform startup sync - uploads pending local progress, downloads newer server progress
        if get_setting_bool('offline_sync_on_connect', True):
            try:
                uploaded, downloaded = startup_sync(lib_service)
                total = uploaded + downloaded
                if total > 0:
                    xbmcgui.Dialog().notification('Synced', f'{total} positions synced', 
                                                 xbmcgui.NOTIFICATION_INFO, 2000)
            except Exception as e:
                xbmc.log(f"Startup sync error: {e}", xbmc.LOGERROR)
        
        return lib_service, url, token, False
        
    except Exception as e:
        error_msg = str(e)
        xbmc.log(f"Auth failed: {error_msg}", xbmc.LOGERROR)
        
        if '429' in error_msg:
            token_cache = load_token_cache()
            if token_cache.get('token') and token_cache.get('url') == url:
                xbmc.log("Rate limited, using cached token", xbmc.LOGWARNING)
                xbmcgui.Dialog().notification('Rate Limited', 'Using cached session', xbmcgui.NOTIFICATION_WARNING)
                lib_service = AudioBookShelfLibraryService(url, token_cache['token'])
                return lib_service, url, token_cache['token'], False
        
        if get_setting_bool('enable_downloads') and has_downloads():
            if xbmcgui.Dialog().yesno('Connection Failed', 
                                      f'{error_msg[:80]}\n\nUse offline mode?'):
                mark_offline()  # Mark that we're going offline
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


def ask_resume(current_time, duration):
    if current_time < 10:
        return False
    return xbmcgui.Dialog().yesno('Resume', f'Resume from {format_time(current_time)}?',
                                  nolabel='Start Over', yeslabel='Resume')


def count_finished_episodes(library_service, item_id, episodes):
    """Count actually finished episodes (not in-progress)"""
    finished_threshold = get_finished_threshold()
    finished = 0
    
    for ep in episodes:
        ep_id = ep.get('id')
        if not ep_id:
            continue
        
        # Check local progress first
        local = get_local_progress(item_id, ep_id)
        if local and local.get('is_finished'):
            finished += 1
            continue
        
        # Check server progress
        if library_service:
            try:
                progress = library_service.get_media_progress(item_id, ep_id)
                if progress:
                    if progress.get('isFinished'):
                        finished += 1
                    elif progress.get('progress', 0) >= finished_threshold:
                        finished += 1
            except:
                pass
    
    return finished


def list_libraries():
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
    
    # Ensure sync manager is set up with library service
    try:
        sync_mgr = get_sync_manager()
        sync_mgr.set_library_service(library_service)
    except Exception as e:
        xbmc.log(f"Sync manager setup error in list_libraries: {e}", xbmc.LOGDEBUG)
    
    try:
        data = library_service.get_all_libraries()
        libraries = data.get('libraries', [])
        
        book_libs = [l for l in libraries if l.get('mediaType') == 'book']
        podcast_libs = [l for l in libraries if l.get('mediaType') == 'podcast']
        
        if not book_libs and len(podcast_libs) == 1:
            list_library_items(podcast_libs[0]['id'], is_podcast=True)
            return
        elif not podcast_libs and len(book_libs) == 1:
            list_library_items(book_libs[0]['id'], is_podcast=False)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'albums')
        
        if get_setting_bool('enable_downloads') and has_downloads():
            list_item = xbmcgui.ListItem(label='[Downloaded Items]')
            list_item.setArt({'icon': 'DefaultFolder.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='downloads'), list_item, isFolder=True)
        
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
    xbmcplugin.setContent(ADDON_HANDLE, 'albums')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        if is_podcast:
            list_item = xbmcgui.ListItem(label='[Search & Add Podcasts]')
            list_item.setArt({'icon': 'DefaultAddSource.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(action='search'), list_item, isFolder=True)
        
        items = library_service.get_library_items(library_id)
        view_filter = get_setting_int('view_filter', 0)
        show_markers = get_setting_bool('show_progress_markers', True)
        finished_threshold = get_finished_threshold()
        
        for item in items.get('results', []):
            media = item.get('media', {})
            metadata = media.get('metadata', {})
            item_id = item['id']
            media_type = item.get('mediaType', 'book')
            
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
            
            if view_filter == 1 and is_finished:
                continue
            elif view_filter == 2 and not is_downloaded:
                continue
            
            cover_url = f"{url}/api/items/{item_id}/cover?token={token}"
            local_cover = download_cover(cover_url, item_id)
            
            title = metadata.get('title', 'Unknown')
            author = metadata.get('authorName', '')
            duration = media.get('duration', 0)
            
            prefix = ''
            if show_markers:
                if is_downloaded:
                    prefix = '[DL] '
                elif media_type == 'podcast':
                    # Count actual finished episodes
                    num_eps = media.get('numEpisodes', 0)
                    if num_eps > 0:
                        # Get episodes and count finished
                        episodes = media.get('episodes', [])
                        if not episodes:
                            # Need to fetch episodes
                            try:
                                full_item = library_service.get_library_item_by_id(item_id, expanded=1)
                                episodes = full_item.get('media', {}).get('episodes', [])
                            except:
                                episodes = []
                        
                        if episodes:
                            finished_count = count_finished_episodes(library_service, item_id, episodes)
                            prefix = f'[{finished_count}/{num_eps}] '
                        else:
                            # Fallback to estimate
                            watched = int((progress_pct / 100) * num_eps)
                            prefix = f'[{watched}/{num_eps}] '
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
        finished_threshold = get_finished_threshold()
        
        # Find New Episodes option
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
            
            is_finished = False
            progress_pct = 0
            
            # Check local first
            local = get_local_progress(item_id, episode_id)
            if local:
                is_finished = local.get('is_finished', False)
                progress_pct = local.get('progress', 0) * 100
            
            # Then check server
            if not is_finished:
                try:
                    progress = library_service.get_media_progress(item_id, episode_id)
                    if progress:
                        server_finished = progress.get('isFinished', False)
                        server_pct = progress.get('progress', 0) * 100
                        if server_finished or server_pct >= finished_threshold * 100:
                            is_finished = True
                        elif server_pct > progress_pct:
                            progress_pct = server_pct
                except:
                    pass
            
            is_downloaded = download_manager.is_downloaded(item_id, episode_id)
            
            if view_filter == 1 and is_finished:
                continue
            elif view_filter == 2 and not is_downloaded:
                continue
            
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
    """Find new episodes by comparing RSS feed to server"""
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
        
        server_guids = {ep.get('guid') for ep in server_episodes if ep.get('guid')}
        server_titles = {(ep.get('title') or '').lower().strip() for ep in server_episodes}
        
        # Episodes on server without audio file
        need_download = [ep for ep in server_episodes 
                        if not ep.get('audioFile') or 
                        (isinstance(ep.get('audioFile'), dict) and not ep['audioFile'].get('ino'))]
        
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
        
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(rss.content)
            channel = root.find('channel')
        except:
            progress.close()
            xbmcgui.Dialog().notification('Parse Error', 'Invalid RSS', xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        itunes_ns = '{http://www.itunes.com/dtds/podcast-1.0.dtd}'
        new_episodes = []
        
        for item_el in channel.findall('item') if channel is not None else []:
            title_el = item_el.find('title')
            title = (title_el.text or 'Unknown').strip() if title_el is not None else 'Unknown'
            
            guid_el = item_el.find('guid')
            guid = (guid_el.text or title).strip() if guid_el is not None else title
            
            if guid in server_guids or title.lower().strip() in server_titles:
                continue
            
            enclosure = item_el.find('enclosure')
            if enclosure is None:
                continue
            audio_url = enclosure.get('url', '')
            audio_type = enclosure.get('type', 'audio/mpeg')
            audio_length = enclosure.get('length', '0')
            
            if not audio_url:
                continue
            
            # Get additional metadata
            desc_el = item_el.find('description')
            description = (desc_el.text or '')[:500] if desc_el is not None else ''
            
            pubdate_el = item_el.find('pubDate')
            pubdate = pubdate_el.text if pubdate_el is not None else ''
            
            duration_el = item_el.find(f'{itunes_ns}duration')
            duration = duration_el.text if duration_el is not None else ''
            
            season_el = item_el.find(f'{itunes_ns}season')
            season = season_el.text if season_el is not None else ''
            
            episode_el = item_el.find(f'{itunes_ns}episode')
            episode_num = episode_el.text if episode_el is not None else ''
            
            new_episodes.append({
                'title': title,
                'guid': guid,
                'audioUrl': audio_url,
                'audioType': audio_type,
                'audioLength': audio_length,
                'description': description,
                'pubDate': pubdate,
                'duration': duration,
                'season': season,
                'episode': episode_num
            })
        
        progress.close()
        
        xbmc.log(f"Found {len(need_download)} need download, {len(new_episodes)} new from RSS", xbmc.LOGINFO)
        
        # Store new episodes in addon data for batch add
        if new_episodes:
            _store_new_episodes(item_id, new_episodes)
        
        if not need_download and not new_episodes:
            xbmcgui.Dialog().notification('Up to Date', 'No new episodes', xbmcgui.NOTIFICATION_INFO)
            xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'episodes')
        
        # Add All button
        total = len(need_download) + len(new_episodes)
        if total > 0:
            list_item = xbmcgui.ListItem(label=f'[Add All {total} Episodes]')
            list_item.setArt({'icon': 'DefaultAddSource.png'})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='batch_add_episodes', item_id=item_id),
                                       list_item, isFolder=False)
        
        # Episodes needing download (already on server, just need audio)
        for ep in need_download:
            list_item = xbmcgui.ListItem(label=f'[Need DL] {ep.get("title", "Unknown")}')
            list_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='download_to_server', item_id=item_id, episode_id=ep.get('id')),
                                       list_item, isFolder=False)
        
        # New from RSS (not on server yet) - can be added directly
        for i, ep in enumerate(new_episodes[:50]):
            list_item = xbmcgui.ListItem(label=f'[NEW] {ep["title"]}')
            list_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, 
                                       build_url(action='add_new_episode', item_id=item_id, episode_index=str(i)),
                                       list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Find episodes error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def _get_episodes_cache_file():
    """Get path to episodes cache file"""
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    return os.path.join(profile, 'new_episodes_cache.json')


def _store_new_episodes(item_id, episodes):
    """Store new episodes for later batch add"""
    import json
    cache = {}
    cache_file = _get_episodes_cache_file()
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache = json.load(f)
    except:
        pass
    
    cache[item_id] = episodes
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)


def _get_stored_episodes(item_id):
    """Get stored episodes for item"""
    import json
    cache_file = _get_episodes_cache_file()
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache = json.load(f)
                return cache.get(item_id, [])
    except:
        pass
    return []


def add_new_episode(item_id, episode_index):
    """Add a single new episode from RSS to server"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        episodes = _get_stored_episodes(item_id)
        idx = int(episode_index)
        
        if idx >= len(episodes):
            xbmcgui.Dialog().notification('Error', 'Episode not found', xbmcgui.NOTIFICATION_ERROR)
            return
        
        ep = episodes[idx]
        xbmcgui.Dialog().notification('Adding...', ep['title'][:30], xbmcgui.NOTIFICATION_INFO, 2000)
        
        # Create episode directly using API
        episode_data = {
            'title': ep['title'],
            'guid': ep['guid'],
            'enclosure': {
                'url': ep['audioUrl'],
                'type': ep.get('audioType', 'audio/mpeg'),
                'length': ep.get('audioLength', '0')
            }
        }
        
        if ep.get('description'):
            episode_data['description'] = ep['description']
        if ep.get('pubDate'):
            episode_data['pubDate'] = ep['pubDate']
        if ep.get('season'):
            episode_data['season'] = ep['season']
        if ep.get('episode'):
            episode_data['episode'] = ep['episode']
        
        xbmc.log(f"Creating episode: {ep['title']}", xbmc.LOGINFO)
        
        # Try to create episode
        create_url = f"{url}/api/podcasts/{item_id}/episode"
        response = requests.post(create_url, headers=library_service.headers, 
                                json={'episodeData': episode_data}, timeout=30)
        
        xbmc.log(f"Create response: {response.status_code}", xbmc.LOGINFO)
        
        if response.status_code in [200, 201]:
            # Episode created, now trigger download
            try:
                result = response.json()
                new_ep_id = result.get('id')
                if new_ep_id:
                    dl_url = f"{url}/api/podcasts/{item_id}/download-episodes"
                    dl_response = requests.post(dl_url, headers=library_service.headers, 
                                               json=[new_ep_id], timeout=30)
                    xbmc.log(f"Download queue response: {dl_response.status_code}", xbmc.LOGINFO)
            except:
                pass
            
            xbmcgui.Dialog().notification('Added', ep['title'][:30], xbmcgui.NOTIFICATION_INFO)
            xbmc.executebuiltin('Container.Refresh')
        else:
            xbmc.log(f"Create failed: {response.text[:200]}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Failed', f'Error {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
            
    except Exception as e:
        xbmc.log(f"Add episode error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def batch_add_episodes(item_id):
    """Add all new episodes from RSS"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        # Get stored new episodes
        new_episodes = _get_stored_episodes(item_id)
        
        # Get episodes needing download from server
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        server_episodes = item.get('media', {}).get('episodes', [])
        need_download = [ep for ep in server_episodes 
                        if not ep.get('audioFile') or 
                        (isinstance(ep.get('audioFile'), dict) and not ep['audioFile'].get('ino'))]
        
        total = len(new_episodes) + len(need_download)
        if total == 0:
            xbmcgui.Dialog().notification('Up to Date', 'No new episodes to add', xbmcgui.NOTIFICATION_INFO)
            return
        
        progress = xbmcgui.DialogProgress()
        progress.create('Adding Episodes', f'Adding {total} episodes...')
        
        added = 0
        queued = 0
        
        # Add new episodes from RSS
        for i, ep in enumerate(new_episodes):
            if progress.iscanceled():
                break
            
            pct = int((i / max(len(new_episodes), 1)) * 50)
            progress.update(pct, f'Adding: {ep["title"][:40]}')
            
            episode_data = {
                'title': ep['title'],
                'guid': ep['guid'],
                'enclosure': {
                    'url': ep['audioUrl'],
                    'type': ep.get('audioType', 'audio/mpeg'),
                    'length': ep.get('audioLength', '0')
                }
            }
            
            if ep.get('description'):
                episode_data['description'] = ep['description']
            if ep.get('pubDate'):
                episode_data['pubDate'] = ep['pubDate']
            
            try:
                create_url = f"{url}/api/podcasts/{item_id}/episode"
                response = requests.post(create_url, headers=library_service.headers, 
                                        json={'episodeData': episode_data}, timeout=30)
                
                if response.status_code in [200, 201]:
                    added += 1
                    # Queue download
                    try:
                        result = response.json()
                        new_ep_id = result.get('id')
                        if new_ep_id:
                            need_download.append({'id': new_ep_id})
                    except:
                        pass
                else:
                    xbmc.log(f"Failed to create {ep['title']}: {response.status_code}", xbmc.LOGWARNING)
            except Exception as e:
                xbmc.log(f"Create error: {str(e)}", xbmc.LOGERROR)
        
        # Queue all downloads
        if need_download:
            progress.update(75, f'Queueing {len(need_download)} downloads...')
            
            ids = [ep['id'] for ep in need_download if ep.get('id')]
            if ids:
                try:
                    dl_url = f"{url}/api/podcasts/{item_id}/download-episodes"
                    dl_response = requests.post(dl_url, headers=library_service.headers, 
                                               json=ids[:20], timeout=30)
                    if dl_response.status_code == 200:
                        queued = min(len(ids), 20)
                except Exception as e:
                    xbmc.log(f"Download queue error: {str(e)}", xbmc.LOGERROR)
        
        progress.close()
        
        msg = f'{added} added, {queued} queued' if added > 0 else f'{queued} queued for download'
        xbmcgui.Dialog().notification('Done', msg, xbmcgui.NOTIFICATION_INFO)
        xbmc.executebuiltin('Container.Refresh')
        
    except Exception as e:
        try:
            progress.close()
        except:
            pass
        xbmc.log(f"Batch add error: {str(e)}", xbmc.LOGERROR)
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


def list_parts(item_id):
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
        
        # Get best resume position (uses unified progress)
        current_time, is_finished, _ = get_best_resume_position(library_service, item_id)
        
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
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        import requests
        
        progress = xbmcgui.DialogProgress()
        progress.create('Adding Podcast', 'Getting metadata...')
        
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
        
        progress.update(60, 'Adding to library...')
        
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
    
    xbmc.log(f"[PLAY] play_audio: title={title}, duration={duration}, start={start_position}, is_podcast={is_podcast}", xbmc.LOGINFO)
    
    # Don't let duration be 0 - try to get from server progress if we have none
    if duration == 0 and library_service:
        try:
            progress = library_service.get_media_progress(item_id, episode_id)
            if progress and progress.get('duration', 0) > 0:
                duration = progress['duration']
                xbmc.log(f"[PLAY] Got duration from server progress: {duration}", xbmc.LOGINFO)
        except:
            pass
    
    list_item = xbmcgui.ListItem(path=play_url)
    set_music_info(list_item, title=title, duration=duration)
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    sync_enabled = get_setting_bool('sync_podcast_progress' if is_podcast else 'sync_audiobook_progress', True)
    sync_on_stop = get_setting_bool('podcast_sync_on_stop' if is_podcast else 'audiobook_sync_on_stop', True)
    sync_interval = get_sync_interval(is_podcast)
    finished_threshold = get_finished_threshold()
    
    _active_monitor = PlaybackMonitor(
        library_service, item_id, duration if duration > 0 else 1,
        episode_id=episode_id,
        sync_enabled=sync_enabled, sync_on_stop=sync_on_stop, 
        sync_interval=sync_interval, finished_threshold=finished_threshold
    )
    _active_monitor.start_monitoring_async(start_position)


def play_at_position(item_id, file_ino, seek_time):
    library_service, url, token, offline = get_library_service()
    
    # Check if downloaded
    if download_manager.is_downloaded(item_id):
        play_offline_item(item_id, seek_position=seek_time)
        return
    
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        media = item.get('media', {})
        
        # Try multiple places for duration
        duration = media.get('duration', 0)
        if duration == 0:
            # Sum duration from audio files
            audio_files = media.get('audioFiles', [])
            duration = sum(f.get('duration', 0) for f in audio_files)
        if duration == 0:
            # Try tracks
            tracks = media.get('tracks', [])
            duration = sum(t.get('duration', 0) for t in tracks)
        
        title = media.get('metadata', {}).get('title', 'Unknown')
        
        xbmc.log(f"[PLAY] play_at_position: item={item_id}, duration={duration}, seek={seek_time}", xbmc.LOGINFO)
        
        play_url = f"{url}/api/items/{item_id}/file/{file_ino}?token={token}"
        play_audio(play_url, title, duration, library_service, item_id, start_position=seek_time)
        
    except Exception as e:
        xbmc.log(f"[PLAY] Error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_item(item_id):
    library_service, url, token, offline = get_library_service()
    
    if download_manager.is_downloaded(item_id):
        play_offline_item(item_id)
        return
    
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        media = item.get('media', {})
        
        # Try multiple places for duration
        duration = media.get('duration', 0)
        if duration == 0:
            audio_files = media.get('audioFiles', [])
            duration = sum(f.get('duration', 0) for f in audio_files)
        if duration == 0:
            tracks = media.get('tracks', [])
            duration = sum(t.get('duration', 0) for t in tracks)
        
        title = media.get('metadata', {}).get('title', 'Unknown')
        
        xbmc.log(f"[PLAY] play_item: item={item_id}, duration={duration}", xbmc.LOGINFO)
        
        # Use unified progress
        current_time, is_finished, server_duration = get_best_resume_position(library_service, item_id)
        
        # Use server duration if we don't have one
        if duration == 0 and server_duration > 0:
            duration = server_duration
            xbmc.log(f"[PLAY] Using server duration: {duration}", xbmc.LOGINFO)
        
        start_position = current_time if current_time > 10 and not is_finished and ask_resume(current_time, duration) else 0
        
        play_url = library_service.get_file_url(item_id)
        play_audio(play_url, title, duration, library_service, item_id, start_position=start_position)
        
    except Exception as e:
        xbmc.log(f"[PLAY] Error: {str(e)}", xbmc.LOGERROR)
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
        
        # Use unified progress
        current_time, is_finished, _ = get_best_resume_position(library_service, item_id, episode_id)
        
        start_position = current_time if current_time > 10 and not is_finished and ask_resume(current_time, duration) else 0
        
        play_url = library_service.get_file_url(item_id, episode_id=episode_id)
        play_audio(play_url, title, duration, library_service, item_id, 
                  episode_id=episode_id, start_position=start_position, is_podcast=True)
        
    except Exception as e:
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def play_offline_item(item_id, episode_id=None, seek_position=None):
    """Play downloaded item using unified progress - syncs with server when online"""
    download_info = download_manager.get_download_info(item_id, episode_id)
    if not download_info:
        xbmcgui.Dialog().notification('Error', 'Not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    duration = download_info.get('duration', 0)
    
    # Try to get library service for server sync (even for downloaded items)
    library_service = None
    try:
        lib_svc, _, _, offline = get_library_service()
        if not offline and lib_svc:
            library_service = lib_svc
            xbmc.log("[OFFLINE] Got library service for sync", xbmc.LOGINFO)
            
            # We were offline, now online - trigger reconnection sync
            sync_mgr = get_sync_manager()
            if sync_mgr._sync_state.get('was_offline', False):
                xbmc.log("[OFFLINE] Was offline, triggering reconnection sync", xbmc.LOGINFO)
                on_network_reconnect(library_service)
        else:
            xbmc.log("[OFFLINE] No library service (offline mode)", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[OFFLINE] Could not get library service: {str(e)}", xbmc.LOGDEBUG)
    
    # Get best resume position (unified - uses same position as streaming)
    if seek_position is not None:
        start_pos = seek_position
    else:
        start_pos, is_finished, server_duration = get_best_resume_position(library_service, item_id, episode_id)
        if server_duration > duration:
            duration = server_duration
        
        if start_pos > 10 and not is_finished:
            if not ask_resume(start_pos, duration):
                start_pos = 0
    
    file_path = download_info.get('file_path')
    if download_info.get('is_multifile'):
        file_path, start_pos = download_manager.get_file_for_position(item_id, start_pos)
    
    if not file_path or not os.path.exists(file_path):
        xbmcgui.Dialog().notification('Error', 'File not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    list_item = xbmcgui.ListItem(path=file_path)
    set_music_info(list_item, title=download_info['title'], duration=duration)
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    # Use PlaybackMonitor with library_service if available (for server sync)
    global _active_monitor
    is_podcast = episode_id is not None
    finished_threshold = get_finished_threshold()
    
    xbmc.log(f"[OFFLINE] Starting monitor with library_service={'YES' if library_service else 'NO'}", xbmc.LOGINFO)
    
    _active_monitor = PlaybackMonitor(
        library_service,  # Pass library service for server sync
        item_id, 
        duration if duration > 0 else 1,
        episode_id=episode_id,
        sync_enabled=True,  # Always enable sync
        sync_on_stop=True,
        sync_interval=get_sync_interval(is_podcast),
        finished_threshold=finished_threshold
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
    elif action == 'add_new_episode':
        add_new_episode(params['item_id'], params['episode_index'])
    elif action == 'batch_add_episodes':
        batch_add_episodes(params['item_id'])
    elif action == 'download_to_server':
        download_episode_to_server(params['item_id'], params['episode_id'])
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
