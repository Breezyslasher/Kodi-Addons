import os
import sys
import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import xbmcvfs
from urllib.parse import urlencode, parse_qsl
from login_service import AudioBookShelfService
from library_service import AudioBookShelfLibraryService
from playback_monitor import PlaybackMonitor, get_resume_position, ask_resume
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


def build_url(**kwargs):
    return f'{ADDON_URL}?{urlencode(kwargs)}'


def get_library_service():
    """Initialize library service with network check"""
    if not is_network_available() and ADDON.getSetting('enable_downloads') == 'true':
        xbmc.log("Offline mode - using downloads only", xbmc.LOGINFO)
        return None, None, None, True
    
    ip = ADDON.getSetting('ipaddress')
    port = ADDON.getSetting('port')
    username = ADDON.getSetting('username')
    password = ADDON.getSetting('password')
    
    if not all([ip, port, username, password]):
        xbmcgui.Dialog().ok('Setup Required', 'Configure addon settings')
        ADDON.openSettings()
        return None, None, None, False
    
    url = f"http://{ip}:{port}"
    
    try:
        service = AudioBookShelfService(url)
        response = service.login(username, password)
        token = response.get('token')
        if not token:
            raise ValueError("No token")
        
        lib_service = AudioBookShelfLibraryService(url, token)
        
        # Sync any unsynced resume positions
        try:
            synced = download_manager.sync_positions_to_server(lib_service)
            if synced > 0:
                xbmcgui.Dialog().notification('Synced', f'{synced} positions synced', xbmcgui.NOTIFICATION_INFO, 2000)
        except:
            pass
        
        return lib_service, url, token, False
    except Exception as e:
        xbmc.log(f"Login failed: {str(e)}", xbmc.LOGERROR)
        if ADDON.getSetting('enable_downloads') == 'true':
            xbmcgui.Dialog().notification('Offline Mode', 'Using downloaded content', xbmcgui.NOTIFICATION_INFO)
            return None, None, None, True
        xbmcgui.Dialog().ok('Error', 'Connection failed')
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
    """Set music info using InfoTagMusic"""
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
        list_item.setInfo('music', {
            'title': title, 'artist': artist, 'duration': int(duration),
            'playcount': playcount, 'tracknumber': tracknumber
        })


def find_file_for_position(audio_files, position):
    """
    Find which file contains the given position and calculate seek time within that file.
    Returns (file_info, seek_within_file, file_start_time)
    """
    sorted_files = sorted(audio_files, key=lambda x: x.get('index', 0))
    
    cumulative = 0
    for f in sorted_files:
        file_duration = f.get('duration', 0)
        file_end = cumulative + file_duration
        
        xbmc.log(f"File {f.get('index', 0)}: cumulative={cumulative}, duration={file_duration}, end={file_end}, position={position}", xbmc.LOGDEBUG)
        
        if position < file_end:
            seek_in_file = position - cumulative
            xbmc.log(f"Found file: index={f.get('index')}, seek_in_file={seek_in_file}", xbmc.LOGINFO)
            return f, seek_in_file, cumulative
        
        cumulative = file_end
    
    # Position beyond all files - return last file at position 0
    if sorted_files:
        last_file = sorted_files[-1]
        xbmc.log(f"Position beyond files, using last file", xbmc.LOGWARNING)
        return last_file, 0, cumulative - last_file.get('duration', 0)
    
    return None, 0, 0


def format_time(seconds):
    """Format seconds as human readable time"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def list_libraries():
    """List libraries with offline support"""
    xbmcplugin.setContent(ADDON_HANDLE, 'albums')
    
    library_service, url, token, offline = get_library_service()
    
    if offline:
        list_item = xbmcgui.ListItem(label='[Downloaded Items]')
        list_item.setArt({'icon': 'DefaultFolder.png'})
        url_params = build_url(action='downloads')
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        return
    
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        list_item = xbmcgui.ListItem(label='[Search Podcasts]')
        list_item.setArt({'icon': 'DefaultAddonLookAndFeel.png'})
        url_params = build_url(action='search')
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
        
        if ADDON.getSetting('enable_downloads') == 'true':
            list_item = xbmcgui.ListItem(label='[Downloaded Items]')
            list_item.setArt({'icon': 'DefaultFolder.png'})
            url_params = build_url(action='downloads')
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
        
        data = library_service.get_all_libraries()
        for library in data.get('libraries', []):
            list_item = xbmcgui.ListItem(label=library['name'])
            list_item.setArt({'icon': 'DefaultMusicAlbums.png'})
            url_params = build_url(action='library', library_id=library['id'])
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_downloads():
    """List downloaded items"""
    xbmcplugin.setContent(ADDON_HANDLE, 'songs')
    
    downloads = download_manager.get_all_downloads()
    
    for key, info in downloads.items():
        list_item = xbmcgui.ListItem(label=info['title'])
        list_item.setProperty('IsPlayable', 'true')
        
        if info.get('cover_path') and os.path.exists(info['cover_path']):
            list_item.setArt({'thumb': info['cover_path'], 'poster': info['cover_path']})
        
        set_music_info(list_item, title=info['title'], artist=info.get('author', ''),
                       duration=info.get('duration', 0))
        
        list_item.addContextMenuItems([
            ('Delete Download', f'RunPlugin({build_url(action="delete_download", key=key)})')
        ])
        
        url_params = build_url(action='play_offline', key=key)
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
    
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def fetch_podcast_metadata(feed_url):
    """Fetch podcast metadata from RSS feed"""
    import requests
    try:
        response = requests.get(feed_url, timeout=15)
        response.raise_for_status()
        
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)
        channel = root.find('channel')
        if channel is None:
            return {}
        
        metadata = {'title': '', 'author': '', 'description': '', 'imageUrl': '',
                    'language': '', 'genres': [], 'feedUrl': feed_url}
        
        title_el = channel.find('title')
        if title_el is not None and title_el.text:
            metadata['title'] = title_el.text
        
        # Try different author tags
        for tag in ['{http://www.itunes.com/dtds/podcast-1.0.dtd}author', 'author']:
            author_el = channel.find(tag)
            if author_el is not None and author_el.text:
                metadata['author'] = author_el.text
                break
        
        desc_el = channel.find('description')
        if desc_el is not None and desc_el.text:
            metadata['description'] = desc_el.text[:500]
        
        # Image
        image_el = channel.find('{http://www.itunes.com/dtds/podcast-1.0.dtd}image')
        if image_el is not None:
            metadata['imageUrl'] = image_el.get('href', '')
        else:
            image_el = channel.find('image')
            if image_el is not None:
                url_el = image_el.find('url')
                if url_el is not None and url_el.text:
                    metadata['imageUrl'] = url_el.text
        
        lang_el = channel.find('language')
        if lang_el is not None and lang_el.text:
            metadata['language'] = lang_el.text
        
        for cat in channel.findall('{http://www.itunes.com/dtds/podcast-1.0.dtd}category'):
            if cat.get('text'):
                metadata['genres'].append(cat.get('text'))
        
        return metadata
    except Exception as e:
        xbmc.log(f"Error fetching podcast metadata: {str(e)}", xbmc.LOGERROR)
        return {}


def search_podcasts():
    """Search iTunes for podcasts"""
    library_service, url, token, offline = get_library_service()
    if not library_service or offline:
        xbmcgui.Dialog().notification('Error', 'Search requires network', xbmcgui.NOTIFICATION_ERROR)
        return
    
    keyboard = xbmc.Keyboard('', 'Search Podcasts')
    keyboard.doModal()
    
    if not keyboard.isConfirmed():
        return
    
    query = keyboard.getText()
    if not query:
        return
    
    xbmcgui.Dialog().notification('Searching', f'Looking for: {query}', xbmcgui.NOTIFICATION_INFO)
    
    try:
        import requests
        search_url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&media=podcast&limit=20"
        response = requests.get(search_url, timeout=10)
        response.raise_for_status()
        results = response.json()
        
        podcasts = results.get('results', [])
        
        if not podcasts:
            xbmcgui.Dialog().notification('No Results', 'No podcasts found', xbmcgui.NOTIFICATION_INFO)
            return
        
        xbmcplugin.setContent(ADDON_HANDLE, 'albums')
        
        for podcast in podcasts:
            name = podcast.get('collectionName', 'Unknown Podcast')
            artist = podcast.get('artistName', '')
            feed_url = podcast.get('feedUrl', '')
            artwork = podcast.get('artworkUrl600', podcast.get('artworkUrl100', ''))
            
            if not feed_url:
                continue
            
            list_item = xbmcgui.ListItem(label=name)
            list_item.setArt({'thumb': artwork, 'poster': artwork})
            set_music_info(list_item, title=name, artist=artist)
            
            list_item.addContextMenuItems([
                ('Add to Library', f'RunPlugin({build_url(action="add_podcast", feed_url=feed_url, name=name)})')
            ])
            
            url_params = build_url(action='add_podcast', feed_url=feed_url, name=name)
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        
    except Exception as e:
        xbmc.log(f"Search error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Search Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def add_podcast_to_library(feed_url, name):
    """Add podcast to Audiobookshelf library with metadata"""
    library_service, url, token, offline = get_library_service()
    if not library_service or offline:
        xbmcgui.Dialog().notification('Error', 'Cannot add podcast offline', xbmcgui.NOTIFICATION_ERROR)
        return
    
    try:
        import requests
        
        progress = xbmcgui.DialogProgress()
        progress.create('Adding Podcast', 'Fetching metadata...')
        
        metadata = fetch_podcast_metadata(feed_url)
        
        progress.update(30, 'Finding library...')
        
        data = library_service.get_all_libraries()
        libraries = data.get('libraries', [])
        podcast_libs = [lib for lib in libraries if lib.get('mediaType') == 'podcast']
        
        if not podcast_libs:
            progress.close()
            xbmcgui.Dialog().notification('Error', 'No podcast library found', xbmcgui.NOTIFICATION_ERROR)
            return
        
        if len(podcast_libs) > 1:
            progress.close()
            lib_names = [lib['name'] for lib in podcast_libs]
            selected = xbmcgui.Dialog().select('Select Library', lib_names)
            if selected < 0:
                return
            library_id = podcast_libs[selected]['id']
            progress.create('Adding Podcast', 'Adding to library...')
        else:
            library_id = podcast_libs[0]['id']
        
        progress.update(50, 'Getting library info...')
        
        library = library_service.get_library(library_id)
        folders = library.get('folders', [])
        if not folders:
            progress.close()
            xbmcgui.Dialog().notification('Error', 'No folder in library', xbmcgui.NOTIFICATION_ERROR)
            return
        
        folder_id = folders[0].get('id')
        folder_path = folders[0].get('fullPath', '/podcasts')
        
        podcast_title = metadata.get('title') or name
        safe_name = "".join(c for c in podcast_title if c.isalnum() or c in ' -_').strip()[:50]
        podcast_path = f"{folder_path}/{safe_name}"
        
        progress.update(70, 'Adding podcast...')
        
        add_url = f"{url}/api/podcasts"
        headers = library_service.headers
        
        payload = {
            'path': podcast_path,
            'folderId': folder_id,
            'libraryId': library_id,
            'media': {
                'metadata': {
                    'title': podcast_title,
                    'author': metadata.get('author', ''),
                    'description': metadata.get('description', ''),
                    'feedUrl': feed_url,
                    'imageUrl': metadata.get('imageUrl', ''),
                    'language': metadata.get('language', ''),
                    'genres': metadata.get('genres', [])
                }
            },
            'autoDownloadEpisodes': False
        }
        
        response = requests.post(add_url, headers=headers, json=payload, timeout=30)
        progress.close()
        
        if response.status_code == 400:
            try:
                error_data = response.json()
                error_msg = error_data.get('error', response.text[:100])
            except:
                error_msg = response.text[:100]
            
            if 'exist' in error_msg.lower():
                xbmcgui.Dialog().notification('Already Exists', podcast_title, xbmcgui.NOTIFICATION_INFO)
            else:
                xbmcgui.Dialog().notification('Add Failed', error_msg[:50], xbmcgui.NOTIFICATION_ERROR)
            return
        
        response.raise_for_status()
        xbmcgui.Dialog().notification('Success', f'Added: {podcast_title}', xbmcgui.NOTIFICATION_INFO)
        
    except Exception as e:
        try:
            progress.close()
        except:
            pass
        xbmc.log(f"Error adding podcast: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Add Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_episode_to_server(item_id, episode_id):
    """Tell server to download a podcast episode"""
    library_service, url, token, offline = get_library_service()
    if not library_service or offline:
        xbmcgui.Dialog().notification('Error', 'Requires network', xbmcgui.NOTIFICATION_ERROR)
        return
    
    try:
        import requests
        
        # Get episode info for notification
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = item.get('media', {}).get('episodes', [])
        episode = next((ep for ep in episodes if ep.get('id') == episode_id), None)
        episode_title = episode.get('title', 'Episode') if episode else 'Episode'
        
        # Request server to download the episode
        download_url = f"{url}/api/podcasts/{item_id}/download-episodes"
        headers = library_service.headers
        payload = [episode_id]  # Array of episode IDs to download
        
        response = requests.post(download_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            xbmcgui.Dialog().notification('Download Started', episode_title, xbmcgui.NOTIFICATION_INFO)
            xbmc.log(f"Server downloading episode: {episode_title}", xbmc.LOGINFO)
        else:
            xbmc.log(f"Server download response: {response.status_code} - {response.text}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', f'Status: {response.status_code}', xbmcgui.NOTIFICATION_ERROR)
            
    except Exception as e:
        xbmc.log(f"Error requesting episode download: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def list_library_items(library_id):
    """List items with filter support"""
    xbmcplugin.setContent(ADDON_HANDLE, 'songs')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        items = library_service.get_library_items(library_id)
        view_filter = int(ADDON.getSetting('view_filter') or '0')
        sync_watched = ADDON.getSetting('sync_watched') == 'true'
        
        for item in items.get('results', []):
            media = item.get('media', {})
            metadata = media.get('metadata', {})
            item_id = item['id']
            media_type = item.get('mediaType', 'book')
            
            is_finished = False
            if sync_watched:
                try:
                    progress = library_service.get_media_progress(item_id)
                    is_finished = progress.get('isFinished', False) if progress else False
                except:
                    pass
            
            is_downloaded = download_manager.is_downloaded(item_id)
            
            if view_filter == 1 and is_finished:
                continue
            elif view_filter == 2 and not is_downloaded:
                continue
            
            cover_url = f"{url}/api/items/{item_id}/cover?token={token}"
            local_cover = download_cover(cover_url, item_id)
            if not local_cover:
                local_cover = os.path.join(ADDON.getAddonInfo('path'), 'resources', 'icon.png')
            
            title = metadata.get('title', 'Unknown')
            author = metadata.get('authorName', '')
            narrator = metadata.get('narratorName', '')
            duration = media.get('duration', 0)
            
            prefix = ''
            if is_downloaded:
                prefix = '[Downloaded] '
            elif is_finished and sync_watched:
                prefix = '[Watched] '
            
            list_item = xbmcgui.ListItem(label=f'{prefix}{title}')
            list_item.setArt({'thumb': local_cover, 'poster': local_cover, 'fanart': local_cover})
            set_music_info(list_item, title=title, artist=author or narrator, duration=duration,
                           playcount=1 if is_finished else 0)
            
            context_items = []
            if ADDON.getSetting('enable_downloads') == 'true':
                if is_downloaded:
                    context_items.append(('Delete Download', f'RunPlugin({build_url(action="delete_download", item_id=item_id)})'))
                else:
                    context_items.append(('Download', f'RunPlugin({build_url(action="download", item_id=item_id, library_id=library_id)})'))
            
            context_items.append(('Play from Beginning', f'RunPlugin({build_url(action="play_from_start", item_id=item_id)})'))
            list_item.addContextMenuItems(context_items)
            
            has_episodes = media_type == 'podcast' and media.get('numEpisodes', 0) > 0
            num_files = media.get('numAudioFiles', 1)
            
            if has_episodes:
                url_params = build_url(action='episodes', item_id=item_id)
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
            elif num_files > 1:
                url_params = build_url(action='parts', item_id=item_id)
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
            else:
                list_item.setProperty('IsPlayable', 'true')
                url_params = build_url(action='play', item_id=item_id)
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_episodes(item_id):
    """List podcast episodes with download to server option"""
    xbmcplugin.setContent(ADDON_HANDLE, 'episodes')
    
    library_service, url, token, offline = get_library_service()
    if not library_service:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1)
        episodes = item.get('media', {}).get('episodes', [])
        view_filter = int(ADDON.getSetting('view_filter') or '0')
        sync_watched = ADDON.getSetting('sync_watched') == 'true'
        
        def get_sort_key(ep):
            if ep.get('index') is not None:
                return (0, ep.get('index'))
            elif ep.get('episode') is not None:
                return (1, ep.get('episode'))
            elif ep.get('publishedAt'):
                return (2, ep.get('publishedAt'))
            return (3, ep.get('title', ''))
        
        episodes = sorted(episodes, key=get_sort_key, reverse=True)
        
        for episode in episodes:
            title = episode.get('title', 'Unknown Episode')
            episode_id = episode.get('id')
            duration = episode.get('duration', 0)
            
            # Check if episode has audio file (downloaded on server)
            has_audio = episode.get('audioFile') is not None
            
            is_finished = False
            if sync_watched:
                try:
                    progress = library_service.get_media_progress(item_id, episode_id)
                    is_finished = progress.get('isFinished', False) if progress else False
                except:
                    pass
            
            is_downloaded = download_manager.is_downloaded(item_id, episode_id)
            
            if view_filter == 1 and is_finished:
                continue
            elif view_filter == 2 and not is_downloaded:
                continue
            
            prefix = ''
            if is_downloaded:
                prefix = '[Downloaded] '
            elif not has_audio:
                prefix = '[Not on Server] '
            elif is_finished:
                prefix = '[Watched] '
            
            list_item = xbmcgui.ListItem(label=f'{prefix}{title}')
            list_item.setProperty('IsPlayable', 'true' if has_audio else 'false')
            set_music_info(list_item, title=title, duration=duration, playcount=1 if is_finished else 0)
            
            # Context menu
            context_items = []
            
            # Add "Download to Server" option for episodes not on server
            if not has_audio:
                context_items.append(('Download to Server', f'RunPlugin({build_url(action="download_to_server", item_id=item_id, episode_id=episode_id)})'))
            
            if ADDON.getSetting('enable_downloads') == 'true':
                if is_downloaded:
                    context_items.append(('Delete Local Download', f'RunPlugin({build_url(action="delete_download", item_id=item_id, episode_id=episode_id)})'))
                elif has_audio:
                    context_items.append(('Download Locally', f'RunPlugin({build_url(action="download_episode", item_id=item_id, episode_id=episode_id)})'))
            
            list_item.addContextMenuItems(context_items)
            
            if has_audio:
                url_params = build_url(action='play_episode', item_id=item_id, episode_id=episode_id)
            else:
                # Click to download to server
                url_params = build_url(action='download_to_server', item_id=item_id, episode_id=episode_id)
            
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_parts(item_id):
    """List chapters/parts with resume button"""
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
        
        # Get current progress
        progress = library_service.get_media_progress(item_id)
        current_time = progress.get('currentTime', 0) if progress else 0
        is_finished = progress.get('isFinished', False) if progress else False
        
        # Sort files for calculations
        sorted_files = sorted(audio_files, key=lambda x: x.get('index', 0))
        
        # Add Resume button if there's progress
        if current_time > 10 and not is_finished:
            # Calculate which file and position within file
            target_file, seek_in_file, file_start = find_file_for_position(audio_files, current_time)
            
            if target_file:
                file_ino = target_file.get('ino')
                time_str = format_time(current_time)
                seek_str = format_time(seek_in_file)
                
                # Find current chapter name
                current_chapter = "Unknown"
                if chapters:
                    for ch in chapters:
                        if ch.get('start', 0) <= current_time < ch.get('end', 0):
                            current_chapter = ch.get('title', 'Unknown')
                            break
                
                list_item = xbmcgui.ListItem(label=f'[▶ Resume: {time_str} - {current_chapter}]')
                list_item.setProperty('IsPlayable', 'true')
                set_music_info(list_item, title=f'Resume from {time_str}')
                
                # Pass file_ino and seek position directly
                url_params = build_url(action='play_at_position', item_id=item_id, 
                                       file_ino=file_ino, seek_time=int(seek_in_file))
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        # Play from Beginning
        list_item = xbmcgui.ListItem(label='[Play from Beginning]')
        list_item.setProperty('IsPlayable', 'true')
        set_music_info(list_item, title='Play from Beginning')
        
        if sorted_files:
            first_ino = sorted_files[0].get('ino')
            url_params = build_url(action='play_at_position', item_id=item_id, file_ino=first_ino, seek_time=0)
        else:
            url_params = build_url(action='play_from_start', item_id=item_id)
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        
        # List chapters
        if chapters:
            chapters = sorted(chapters, key=lambda x: x.get('start', 0))
            for i, chapter in enumerate(chapters):
                title = chapter.get('title', f'Chapter {i+1}')
                chapter_start = chapter.get('start', 0)
                chapter_end = chapter.get('end', 0)
                duration = chapter_end - chapter_start
                
                # Calculate file and seek position for this chapter
                target_file, seek_in_file, file_start = find_file_for_position(audio_files, chapter_start)
                
                # Mark current chapter
                display_title = title
                if current_time > 0 and chapter_start <= current_time < chapter_end:
                    display_title = f'▶ {title}'
                
                list_item = xbmcgui.ListItem(label=display_title)
                list_item.setProperty('IsPlayable', 'true')
                set_music_info(list_item, title=title, duration=duration, tracknumber=i+1)
                
                if target_file:
                    file_ino = target_file.get('ino')
                    url_params = build_url(action='play_at_position', item_id=item_id,
                                           file_ino=file_ino, seek_time=int(seek_in_file))
                else:
                    url_params = build_url(action='play_chapter', item_id=item_id, chapter_start=int(chapter_start))
                
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
        else:
            # No chapters, list audio files
            cumulative_time = 0
            for i, f in enumerate(sorted_files):
                title = f.get('metadata', {}).get('title', f'Part {i+1}')
                duration = f.get('duration', 0)
                
                display_title = title
                if current_time > 0 and cumulative_time <= current_time < cumulative_time + duration:
                    display_title = f'▶ {title}'
                
                list_item = xbmcgui.ListItem(label=display_title)
                list_item.setProperty('IsPlayable', 'true')
                set_music_info(list_item, title=title, duration=duration)
                url_params = build_url(action='play_at_position', item_id=item_id, 
                                       file_ino=f.get('ino'), seek_time=0)
                xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
                cumulative_time += duration
        
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def play_audio(play_url, title, duration, library_service, item_id, episode_id=None, start_position=0):
    """Universal playback function"""
    global _active_monitor
    
    list_item = xbmcgui.ListItem(path=play_url)
    set_music_info(list_item, title=title, duration=duration)
    
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    auto_delete = ADDON.getSetting('auto_delete') == 'true'
    _active_monitor = PlaybackMonitor(
        library_service, item_id, duration if duration > 0 else 1,
        episode_id=episode_id,
        auto_delete_enabled=auto_delete,
        download_manager=download_manager
    )
    _active_monitor.start_monitoring_async(start_position)


def play_at_position(item_id, file_ino, seek_time):
    """Play specific file at specific position - the main playback function"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        duration = item.get('media', {}).get('duration', 0)
        title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
        
        play_url = f"{url}/api/items/{item_id}/file/{file_ino}?token={token}"
        
        xbmc.log(f"Playing file {file_ino}, seeking to {seek_time}s", xbmc.LOGINFO)
        
        play_audio(play_url, title, duration, library_service, item_id, start_position=seek_time)
        
    except Exception as e:
        xbmc.log(f"Playback error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_item(item_id):
    """Play single-file item with resume"""
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
        
        resume_pos = get_resume_position(library_service, item_id, None)
        start_position = 0
        
        if resume_pos > 0:
            if ask_resume(resume_pos, duration):
                start_position = resume_pos
        
        play_url = library_service.get_file_url(item_id)
        play_audio(play_url, title, duration, library_service, item_id, start_position=start_position)
        
    except Exception as e:
        xbmc.log(f"Playback error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_from_start(item_id):
    """Play from beginning"""
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
        audio_files = item.get('media', {}).get('audioFiles', [])
        
        if audio_files:
            sorted_files = sorted(audio_files, key=lambda x: x.get('index', 0))
            ino = sorted_files[0].get('ino')
            play_url = f"{url}/api/items/{item_id}/file/{ino}?token={token}"
        else:
            play_url = library_service.get_file_url(item_id)
        
        play_audio(play_url, title, duration, library_service, item_id)
        
    except Exception as e:
        xbmc.log(f"Playback error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_chapter(item_id, chapter_start):
    """Play chapter - calculates correct file and seek position"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        audio_files = item.get('media', {}).get('audioFiles', [])
        duration = item.get('media', {}).get('duration', 0)
        title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
        
        target_file, seek_in_file, _ = find_file_for_position(audio_files, chapter_start)
        
        if not target_file:
            raise ValueError("No audio file found")
        
        ino = target_file.get('ino')
        play_url = f"{url}/api/items/{item_id}/file/{ino}?token={token}"
        
        xbmc.log(f"Playing chapter at {chapter_start}s: file={ino}, seek={seek_in_file}s", xbmc.LOGINFO)
        
        play_audio(play_url, title, duration, library_service, item_id, start_position=seek_in_file)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_episode(item_id, episode_id):
    """Play podcast episode"""
    library_service, url, token, offline = get_library_service()
    
    if download_manager.is_downloaded(item_id, episode_id):
        play_offline_item(item_id, episode_id)
        return
    
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1, episode=episode_id)
        episodes = item.get('media', {}).get('episodes', [])
        
        episode = next((ep for ep in episodes if ep.get('id') == episode_id), None)
        if not episode:
            raise ValueError("Episode not found")
        
        title = episode.get('title', 'Unknown')
        duration = episode.get('duration', 0)
        
        resume_pos = get_resume_position(library_service, item_id, episode_id)
        start_position = 0
        
        if resume_pos > 0:
            if ask_resume(resume_pos, duration):
                start_position = resume_pos
        
        play_url = library_service.get_file_url(item_id, episode_id=episode_id)
        play_audio(play_url, title, duration, library_service, item_id, 
                   episode_id=episode_id, start_position=start_position)
        
    except Exception as e:
        xbmc.log(f"Error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_offline_item(item_id, episode_id=None):
    """Play downloaded item"""
    download_info = download_manager.get_download_info(item_id, episode_id)
    if not download_info:
        xbmcgui.Dialog().notification('Error', 'Download not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    local_pos = download_manager.get_local_resume_position(item_id, episode_id)
    start_position = 0
    duration = download_info.get('duration', 0)
    
    if local_pos and local_pos.get('current_time', 0) > 10 and not local_pos.get('is_finished'):
        if ask_resume(local_pos['current_time'], duration):
            start_position = local_pos['current_time']
    
    if download_info.get('is_multifile'):
        file_path, seek_pos = download_manager.get_file_for_position(item_id, start_position)
        if not file_path:
            file_path = download_info['files'][0]['path']
            seek_pos = start_position
    else:
        file_path = download_info.get('file_path')
        seek_pos = start_position
    
    if not file_path or not os.path.exists(file_path):
        xbmcgui.Dialog().notification('Error', 'File not found', xbmcgui.NOTIFICATION_ERROR)
        return
    
    list_item = xbmcgui.ListItem(path=file_path)
    set_music_info(list_item, title=download_info['title'], duration=duration)
    
    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
    
    global _active_monitor
    _active_monitor = PlaybackMonitor(
        None, item_id, duration if duration > 0 else 1,
        episode_id=episode_id,
        download_manager=download_manager,
        offline_mode=True
    )
    _active_monitor.start_monitoring_async(seek_pos)


def download_item(item_id, library_id):
    """Download item"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id)
        media = item.get('media', {})
        metadata = media.get('metadata', {})
        audio_files = media.get('audioFiles', [])
        chapters = media.get('chapters', [])
        
        item_data = {
            'title': metadata.get('title', 'Unknown'),
            'duration': media.get('duration', 0),
            'author': metadata.get('authorName', ''),
            'narrator': metadata.get('narratorName', ''),
            'cover_url': f"{url}/api/items/{item_id}/cover?token={token}",
            'audio_files': audio_files,
            'chapters': chapters
        }
        
        if len(audio_files) > 1:
            download_manager.download_audiobook_complete(item_id, item_data, library_service)
        else:
            download_manager.download_item(item_id, item_data, library_service)
        
    except Exception as e:
        xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def download_episode(item_id, episode_id):
    """Download podcast episode locally"""
    library_service, url, token, offline = get_library_service()
    if not library_service:
        return
    
    try:
        item = library_service.get_library_item_by_id(item_id, expanded=1, episode=episode_id)
        episodes = item.get('media', {}).get('episodes', [])
        
        episode = next((ep for ep in episodes if ep.get('id') == episode_id), None)
        if not episode:
            raise ValueError("Episode not found")
        
        item_data = {
            'title': episode.get('title', 'Unknown'),
            'duration': episode.get('duration', 0),
            'description': episode.get('description', ''),
            'cover_url': f"{url}/api/items/{item_id}/cover?token={token}"
        }
        
        download_manager.download_item(item_id, item_data, library_service, episode_id=episode_id)
        
    except Exception as e:
        xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)


def delete_download(item_id=None, episode_id=None, key=None):
    """Delete downloaded item"""
    if key:
        parts = key.split('_', 1)
        item_id = parts[0]
        episode_id = parts[1] if len(parts) > 1 else None
    
    if item_id:
        download_manager.delete_download(item_id, episode_id)
        xbmc.executebuiltin('Container.Refresh')


def router(paramstring):
    params = dict(parse_qsl(paramstring))
    
    if not params:
        list_libraries()
    else:
        action = params.get('action')
        
        if action == 'library':
            list_library_items(params['library_id'])
        elif action == 'episodes':
            list_episodes(params['item_id'])
        elif action == 'parts':
            list_parts(params['item_id'])
        elif action == 'play':
            play_item(params['item_id'])
        elif action == 'play_from_start':
            play_from_start(params['item_id'])
        elif action == 'play_at_position':
            play_at_position(params['item_id'], params['file_ino'], int(params.get('seek_time', 0)))
        elif action == 'play_chapter':
            play_chapter(params['item_id'], int(params['chapter_start']))
        elif action == 'play_episode':
            play_episode(params['item_id'], params['episode_id'])
        elif action == 'downloads':
            list_downloads()
        elif action == 'play_offline':
            key = params.get('key', '')
            parts = key.split('_', 1)
            play_offline_item(parts[0], parts[1] if len(parts) > 1 else None)
        elif action == 'search':
            search_podcasts()
        elif action == 'add_podcast':
            add_podcast_to_library(params['feed_url'], params['name'])
        elif action == 'download_to_server':
            download_episode_to_server(params['item_id'], params['episode_id'])
        elif action == 'download':
            download_item(params['item_id'], params.get('library_id'))
        elif action == 'download_episode':
            download_episode(params['item_id'], params['episode_id'])
        elif action == 'delete_download':
            delete_download(item_id=params.get('item_id'), episode_id=params.get('episode_id'), key=params.get('key'))
        else:
            list_libraries()


if __name__ == '__main__':
    router(sys.argv[2][1:])
