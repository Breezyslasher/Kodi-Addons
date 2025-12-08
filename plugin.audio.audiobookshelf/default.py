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
try:
	from urllib.request import urlretrieve
except ImportError:
	from urllib import urlretrieve

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = ADDON.getAddonInfo('path')
ADDON_HANDLE = int(sys.argv[1])
ADDON_URL = sys.argv[0]


def build_url(**kwargs):
	"""Build a plugin URL"""
	return f'{ADDON_URL}?{urlencode(kwargs)}'


def get_cached_credentials():
	"""Get server credentials from settings"""
	return {
		'ip': ADDON.getSetting('ipaddress'),
		'port': ADDON.getSetting('port'),
		'username': ADDON.getSetting('username'),
		'password': ADDON.getSetting('password')
	}


def get_library_service():
	"""Initialize and return library service"""
	creds = get_cached_credentials()
	
	if not all([creds['ip'], creds['port'], creds['username'], creds['password']]):
		xbmcgui.Dialog().ok('Configuration Required', 'Please configure the addon settings first.')
		ADDON.openSettings()
		return None
	
	url = f"http://{creds['ip']}:{creds['port']}"
	
	try:
		login_service = AudioBookShelfService(url)
		response = login_service.login(creds['username'], creds['password'])
		token = response.get('token')
		
		if not token:
			raise ValueError("No token received")
		
		return AudioBookShelfLibraryService(url, token), url, token
	except Exception as e:
		xbmc.log(f"Login failed: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().ok('Login Failed', 'Check your server settings and credentials.')
		return None


def download_cover(url, item_id):
	"""Download cover to cache"""
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


def list_libraries():
	"""List all libraries"""
	xbmcplugin.setContent(ADDON_HANDLE, 'albums')
	
	result = get_library_service()
	if not result:
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
		return
	
	library_service, url, token = result
	
	try:
		data = library_service.get_all_libraries()
		libraries = data.get('libraries', [])
		
		for library in libraries:
			list_item = xbmcgui.ListItem(label=library['name'])
			list_item.setArt({'icon': 'DefaultMusicAlbums.png', 'thumb': 'DefaultMusicAlbums.png'})
			list_item.setInfo('music', {'title': library['name'], 'genre': 'Library'})
			
			url_params = build_url(action='library', library_id=library['id'])
			xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
		
		xbmcplugin.endOfDirectory(ADDON_HANDLE)
	except Exception as e:
		xbmc.log(f"Error listing libraries: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Failed to load libraries', xbmcgui.NOTIFICATION_ERROR)
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_library_items(library_id):
	"""List items in a library"""
	xbmcplugin.setContent(ADDON_HANDLE, 'songs')
	
	result = get_library_service()
	if not result:
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
		return
	
	library_service, url, token = result
	
	try:
		items = library_service.get_library_items(library_id)
		
		for item in items.get('results', []):
			media = item.get('media', {})
			metadata = item.get('media', {}).get('metadata', {})
			media_type = item.get('mediaType', 'book')
			item_id = item['id']
			
			# Download cover
			cover_url = f"{url}/api/items/{item_id}/cover?token={token}"
			local_cover = download_cover(cover_url, item_id)
			
			if not local_cover:
				local_cover = os.path.join(ADDON_PATH, 'resources', 'icon.png')
			
			title = metadata.get('title', 'Unknown')
			author = metadata.get('authorName', '')
			narrator = metadata.get('narratorName', '')
			duration = media.get('duration', 0)
			
			list_item = xbmcgui.ListItem(label=title)
			list_item.setArt({
				'thumb': local_cover,
				'poster': local_cover,
				'fanart': local_cover,
				'icon': local_cover
			})
			
			list_item.setInfo('music', {
				'title': title,
				'artist': author or narrator,
				'album': title,
				'duration': int(duration),
				'mediatype': 'song'
			})
			
			# Check if podcast with episodes or multi-file audiobook
			has_episodes = media_type == 'podcast' and media.get('numEpisodes', 0) > 0
			num_files = media.get('numAudioFiles', 1)
			
			if has_episodes:
				# Podcast - list episodes
				url_params = build_url(action='episodes', item_id=item_id)
				xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
			elif num_files > 1:
				# Multi-file - list parts
				url_params = build_url(action='parts', item_id=item_id)
				xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=True)
			else:
				# Single file - play directly
				list_item.setProperty('IsPlayable', 'true')
				url_params = build_url(action='play', item_id=item_id)
				xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
		
		xbmcplugin.endOfDirectory(ADDON_HANDLE)
	except Exception as e:
		xbmc.log(f"Error listing items: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Failed to load items', xbmcgui.NOTIFICATION_ERROR)
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_episodes(item_id):
	"""List podcast episodes"""
	xbmcplugin.setContent(ADDON_HANDLE, 'episodes')
	
	result = get_library_service()
	if not result:
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id, expanded=1)
		episodes = item.get('media', {}).get('episodes', [])
		
		# Sort episodes
		def get_sort_key(ep):
			if ep.get('index') is not None:
				return (0, ep.get('index'))
			elif ep.get('episode') is not None:
				return (1, ep.get('episode'))
			elif ep.get('publishedAt'):
				return (2, ep.get('publishedAt'))
			else:
				return (3, ep.get('title', ''))
		
		episodes = sorted(episodes, key=get_sort_key, reverse=True)
		
		for episode in episodes:
			title = episode.get('title', 'Unknown Episode')
			episode_id = episode.get('id')
			duration = episode.get('duration', 0)
			
			list_item = xbmcgui.ListItem(label=title)
			list_item.setProperty('IsPlayable', 'true')
			list_item.setInfo('music', {
				'title': title,
				'duration': int(duration),
				'mediatype': 'song'
			})
			
			url_params = build_url(action='play_episode', item_id=item_id, episode_id=episode_id)
			xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
		
		xbmcplugin.endOfDirectory(ADDON_HANDLE)
	except Exception as e:
		xbmc.log(f"Error listing episodes: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Failed to load episodes', xbmcgui.NOTIFICATION_ERROR)
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_parts(item_id):
	"""List audiobook parts/chapters"""
	xbmcplugin.setContent(ADDON_HANDLE, 'songs')
	
	result = get_library_service()
	if not result:
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id)
		audio_files = item.get('media', {}).get('audioFiles', [])
		chapters = item.get('media', {}).get('chapters', [])
		
		if chapters and len(chapters) > 0:
			# Show chapters
			chapters = sorted(chapters, key=lambda x: x.get('start', 0))
			
			for i, chapter in enumerate(chapters):
				title = chapter.get('title', f'Chapter {i+1}')
				start = chapter.get('start', 0)
				end = chapter.get('end', 0)
				duration = end - start
				
				list_item = xbmcgui.ListItem(label=title)
				list_item.setProperty('IsPlayable', 'true')
				list_item.setInfo('music', {
					'title': title,
					'duration': int(duration),
					'tracknumber': i + 1
				})
				
				url_params = build_url(action='play_chapter', item_id=item_id, chapter_start=int(start))
				xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
		else:
			# Show audio files
			audio_files = sorted(audio_files, key=lambda x: x.get('index', 0))
			
			for i, audio_file in enumerate(audio_files):
				metadata = audio_file.get('metadata', {})
				title = metadata.get('title') or metadata.get('filename', f'Part {i+1}')
				duration = audio_file.get('duration', 0)
				ino = audio_file.get('ino')
				
				list_item = xbmcgui.ListItem(label=title)
				list_item.setProperty('IsPlayable', 'true')
				list_item.setInfo('music', {
					'title': title,
					'duration': int(duration),
					'tracknumber': i + 1
				})
				
				url_params = build_url(action='play_file', item_id=item_id, file_ino=ino)
				xbmcplugin.addDirectoryItem(ADDON_HANDLE, url_params, list_item, isFolder=False)
		
		xbmcplugin.endOfDirectory(ADDON_HANDLE)
	except Exception as e:
		xbmc.log(f"Error listing parts: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Failed to load parts', xbmcgui.NOTIFICATION_ERROR)
		xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def play_item(item_id):
	"""Play a single-file audiobook"""
	result = get_library_service()
	if not result:
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id)
		duration = item.get('media', {}).get('duration', 0)
		title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
		
		# Get resume position
		resume_pos = get_resume_position(library_service, item_id)
		start_position = 0
		
		if resume_pos > 0 and ask_resume(resume_pos, duration):
			start_position = resume_pos
		
		# Get play URL
		play_url = library_service.get_file_url(item_id)
		
		# Create list item
		list_item = xbmcgui.ListItem(path=play_url)
		list_item.setInfo('music', {'title': title, 'duration': int(duration)})
		
		# Set resolved URL
		xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
		
		# Wait for playback and monitor
		xbmc.sleep(1000)
		player = xbmc.Player()
		
		if start_position > 0 and player.isPlaying():
			xbmc.sleep(1000)
			player.seekTime(start_position)
		
		# Start monitoring
		monitor = PlaybackMonitor(library_service, item_id, duration)
		monitor.start_monitoring(start_position)
		
		# Wait for playback
		while player.isPlayingAudio():
			xbmc.sleep(1000)
		
		monitor.stop_monitoring()
		
	except Exception as e:
		xbmc.log(f"Error playing item: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_episode(item_id, episode_id):
	"""Play a podcast episode"""
	result = get_library_service()
	if not result:
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id, expanded=1, episode=episode_id)
		episodes = item.get('media', {}).get('episodes', [])
		
		episode = None
		for ep in episodes:
			if ep.get('id') == episode_id:
				episode = ep
				break
		
		if not episode:
			raise ValueError("Episode not found")
		
		title = episode.get('title', 'Unknown')
		duration = episode.get('duration', 0)
		
		# Get resume position
		resume_pos = get_resume_position(library_service, item_id, episode_id)
		start_position = 0
		
		if resume_pos > 0 and ask_resume(resume_pos, duration):
			start_position = resume_pos
		
		# Get play URL
		play_url = library_service.get_file_url(item_id, episode_id=episode_id)
		
		# Create list item
		list_item = xbmcgui.ListItem(path=play_url)
		list_item.setInfo('music', {'title': title, 'duration': int(duration)})
		
		# Set resolved URL
		xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
		
		# Wait and monitor
		xbmc.sleep(1000)
		player = xbmc.Player()
		
		if start_position > 0 and player.isPlaying():
			xbmc.sleep(1000)
			player.seekTime(start_position)
		
		# Start monitoring with Kodi sync
		monitor = PlaybackMonitor(
			library_service, item_id, duration,
			episode_id=episode_id,
			sync_kodi_watched=True,
			episode_title=title
		)
		monitor.start_monitoring(start_position)
		
		while player.isPlayingAudio():
			xbmc.sleep(1000)
		
		monitor.stop_monitoring()
		
	except Exception as e:
		xbmc.log(f"Error playing episode: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_chapter(item_id, chapter_start):
	"""Play from a specific chapter"""
	result = get_library_service()
	if not result:
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id)
		audio_files = item.get('media', {}).get('audioFiles', [])
		duration = item.get('media', {}).get('duration', 0)
		
		# Find correct file for chapter start
		sorted_files = sorted(audio_files, key=lambda x: x.get('index', 0))
		cumulative = 0
		target_file = None
		seek_position = chapter_start
		
		for f in sorted_files:
			file_duration = f.get('duration', 0)
			if cumulative <= chapter_start < cumulative + file_duration:
				target_file = f
				seek_position = chapter_start - cumulative
				break
			cumulative += file_duration
		
		if not target_file:
			target_file = sorted_files[-1] if sorted_files else None
		
		if not target_file:
			raise ValueError("Could not find audio file")
		
		# Get play URL
		ino = target_file.get('ino')
		play_url = f"{url}/api/items/{item_id}/file/{ino}?token={token}"
		
		# Create list item
		title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
		list_item = xbmcgui.ListItem(path=play_url)
		list_item.setInfo('music', {'title': title, 'duration': int(duration)})
		
		# Set resolved URL
		xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
		
		# Wait and seek
		xbmc.sleep(1000)
		player = xbmc.Player()
		
		if seek_position > 0 and player.isPlaying():
			xbmc.sleep(1000)
			player.seekTime(seek_position)
		
		# Monitor with absolute time
		monitor = PlaybackMonitor(library_service, item_id, duration)
		monitor.start_monitoring(chapter_start)
		
		while player.isPlayingAudio():
			xbmc.sleep(1000)
		
		monitor.stop_monitoring()
		
	except Exception as e:
		xbmc.log(f"Error playing chapter: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def play_file(item_id, file_ino):
	"""Play a specific audio file"""
	result = get_library_service()
	if not result:
		return
	
	library_service, url, token = result
	
	try:
		item = library_service.get_library_item_by_id(item_id)
		audio_files = item.get('media', {}).get('audioFiles', [])
		duration = item.get('media', {}).get('duration', 0)
		
		# Find file by ino
		target_file = None
		for f in audio_files:
			if f.get('ino') == file_ino:
				target_file = f
				break
		
		if not target_file:
			raise ValueError("File not found")
		
		# Get play URL
		play_url = f"{url}/api/items/{item_id}/file/{file_ino}?token={token}"
		
		# Create list item
		title = item.get('media', {}).get('metadata', {}).get('title', 'Unknown')
		list_item = xbmcgui.ListItem(path=play_url)
		list_item.setInfo('music', {'title': title, 'duration': int(duration)})
		
		# Set resolved URL
		xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)
		
		# Monitor
		xbmc.sleep(1000)
		player = xbmc.Player()
		
		monitor = PlaybackMonitor(library_service, item_id, duration)
		monitor.start_monitoring(0)
		
		while player.isPlayingAudio():
			xbmc.sleep(1000)
		
		monitor.stop_monitoring()
		
	except Exception as e:
		xbmc.log(f"Error playing file: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().notification('Error', 'Playback failed', xbmcgui.NOTIFICATION_ERROR)


def router(paramstring):
	"""Route to appropriate function"""
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
		elif action == 'play_episode':
			play_episode(params['item_id'], params['episode_id'])
		elif action == 'play_chapter':
			play_chapter(params['item_id'], int(params['chapter_start']))
		elif action == 'play_file':
			play_file(params['item_id'], params['file_ino'])
		else:
			list_libraries()


if __name__ == '__main__':
	router(sys.argv[2][1:])
