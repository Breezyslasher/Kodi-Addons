import os
import sys
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
from login_service import AudioBookShelfService
from library_service import AudioBookShelfLibraryService
from audio_book import AudioBookPlayer
from playback_monitor import PlaybackMonitor, get_resume_position, ask_resume
try:
	from urllib.request import urlretrieve
	from urllib.parse import quote
except ImportError:
	from urllib import urlretrieve, quote

MAX_COLUMNS = 3
MAX_ROWS = 2
MAX_PER_PAGE = MAX_COLUMNS * MAX_ROWS
COVER_WIDTH = 200
COVER_HEIGHT = 200
HORIZONTAL_PADDING = 50
VERTICAL_PADDING = 50

ADDON = xbmcaddon.Addon()
CWD = ADDON.getAddonInfo('path')
ERROR_MSG = "Error"


class SettingsDialog(xbmcgui.Dialog):
	"""Dialog to collect addon settings from user"""
	
	def get_input(self, title):
		"""Get input from user"""
		return xbmcgui.Dialog().input(title)

	def get_and_store_settings(self):
		"""Prompt user for settings and store them"""
		ip = self.get_input("Enter IP Address")
		ADDON.setSetting("ipaddress", ip)

		port = self.get_input("Enter Port")
		ADDON.setSetting("port", port)

		username = self.get_input("Enter Username")
		ADDON.setSetting("username", username)

		password = self.get_input("Enter Password")
		ADDON.setSetting("password", password)


class GUI(xbmcgui.WindowXML):
	"""Main GUI for browsing and selecting audiobooks"""
	
	def __init__(self, *args, **kwargs):
		super(GUI, self).__init__(*args, **kwargs)
		self.audiobooks = kwargs.get("optional1", [])
		self.page = 0
		self.button_controls = []
		self.play_controls = []
		self.prev_button = None
		self.next_button = None
		self.last_row = False
		self.selected_index = None

	def onInit(self):
		"""Initialize the GUI"""
		xbmc.log("=== AUDIOBOOKSHELF GUI INIT STARTED ===", xbmc.LOGINFO)
		xbmc.log(f"Number of audiobooks to display: {len(self.audiobooks)}", xbmc.LOGINFO)
		xbmc.log(f"Addon path (CWD): {CWD}", xbmc.LOGINFO)
		
		# Set background
		self.set_background()
		
		# Display audiobooks
		xbmc.log("About to display audiobooks...", xbmc.LOGINFO)
		self.display_audiobooks()
		xbmc.log("=== GUI INIT COMPLETED ===", xbmc.LOGINFO)

	def clear_audiobooks(self):
		"""Clear all audiobook controls from display"""
		for button in self.button_controls:
			self.removeControl(button)
		for play_control in self.play_controls:
			self.removeControl(play_control)

		# Remove prev and next buttons if they exist
		if hasattr(self, 'prev_button') and self.prev_button:
			self.removeControl(self.prev_button)
			self.prev_button = None

		if hasattr(self, 'next_button') and self.next_button:
			self.removeControl(self.next_button)
			self.next_button = None

		# Clear lists
		self.button_controls = []
		self.play_controls = []

	def set_background(self):
		"""Set the background image"""
		try:
			bg_path = os.path.join(CWD, 'resources', 'skins', 'default', 'media', 'background.png')
			xbmc.log(f"Looking for background at: {bg_path}", xbmc.LOGINFO)
			xbmc.log(f"Background exists: {os.path.exists(bg_path)}", xbmc.LOGINFO)
			
			if os.path.exists(bg_path):
				background_control = xbmcgui.ControlImage(0, 0, 1920, 1080, bg_path)
				self.addControl(background_control)
				xbmc.log("Background added successfully", xbmc.LOGINFO)
			else:
				xbmc.log("Background file not found, continuing without it", xbmc.LOGWARNING)
		except Exception as e:
			xbmc.log(f"Error adding background: {str(e)}", xbmc.LOGERROR)

	def display_audiobooks(self):
		"""Display audiobooks for current page"""
		xbmc.log(f"=== DISPLAY_AUDIOBOOKS called for page {self.page} ===", xbmc.LOGINFO)
		
		self.clear_audiobooks()
		self.audiobooks_to_display = self.audiobooks[self.page * MAX_PER_PAGE: (self.page + 1) * MAX_PER_PAGE]
		
		xbmc.log(f"Displaying {len(self.audiobooks_to_display)} audiobooks on this page", xbmc.LOGINFO)
		
		if len(self.audiobooks_to_display) == 0:
			xbmc.log("WARNING: No audiobooks to display!", xbmc.LOGWARNING)
			dialog = xbmcgui.Dialog()
			dialog.ok("No Audiobooks", "No audiobooks found to display")
			return
		
		total_width_for_books = MAX_COLUMNS * COVER_WIDTH + (MAX_COLUMNS - 1) * HORIZONTAL_PADDING
		start_x = (1920 - total_width_for_books) // 2

		total_height_for_books = MAX_ROWS * COVER_HEIGHT + (MAX_ROWS - 1) * VERTICAL_PADDING
		start_y = (1080 - total_height_for_books) // 2

		xbmc.log(f"Grid starting position: x={start_x}, y={start_y}", xbmc.LOGINFO)

		self.create_audiobook_buttons(start_x, start_y)
		self.set_audiobook_navigation()
		
		if self.button_controls:
			xbmc.log(f"Setting focus to first button (total buttons: {len(self.button_controls)})", xbmc.LOGINFO)
			self.setFocus(self.button_controls[0])
		else:
			xbmc.log("ERROR: No button controls created!", xbmc.LOGERROR)

	def create_audiobook_buttons(self, start_x, start_y):
		"""Create button controls for audiobooks"""
		xbmc.log(f"=== CREATING AUDIOBOOK BUTTONS ===", xbmc.LOGINFO)
		
		addon_dir = xbmcaddon.Addon().getAddonInfo('path')
		play_path = os.path.join(addon_dir, 'resources', 'skins', 'default', 'media', 'play.png')
		
		xbmc.log(f"Play icon path: {play_path}", xbmc.LOGINFO)
		xbmc.log(f"Play icon exists: {os.path.exists(play_path)}", xbmc.LOGINFO)

		for row in range(MAX_ROWS):
			for column in range(MAX_COLUMNS):
				index = row * MAX_COLUMNS + column
				if index >= len(self.audiobooks_to_display):
					break

				audiobook = self.audiobooks_to_display[index]
				x_pos = start_x + (COVER_WIDTH + HORIZONTAL_PADDING) * column
				y_pos = start_y + (COVER_HEIGHT + VERTICAL_PADDING) * row

				xbmc.log(f"Creating button {index}: '{audiobook['title']}' at ({x_pos}, {y_pos})", xbmc.LOGINFO)
				xbmc.log(f"  Cover URL: {audiobook['cover_url']}", xbmc.LOGDEBUG)

				try:
					button_control = xbmcgui.ControlButton(
						x_pos, y_pos, COVER_WIDTH, COVER_HEIGHT, "",
						focusTexture=audiobook['cover_url'],
						noFocusTexture=audiobook['cover_url']
					)
					self.addControl(button_control)
					self.button_controls.append(button_control)
					xbmc.log(f"  Button {index} added successfully", xbmc.LOGDEBUG)

					if os.path.exists(play_path):
						play_control = xbmcgui.ControlImage(x_pos, y_pos, COVER_WIDTH, COVER_HEIGHT, play_path)
						self.addControl(play_control)
						self.play_controls.append(play_control)
						play_control.setVisible(False)
						xbmc.log(f"  Play overlay {index} added successfully", xbmc.LOGDEBUG)
				except Exception as e:
					xbmc.log(f"ERROR creating button {index}: {str(e)}", xbmc.LOGERROR)

		xbmc.log(f"Created {len(self.button_controls)} buttons total", xbmc.LOGINFO)

		# Create navigation buttons
		button_width = 50
		button_height = 50
		total_width_for_buttons = 2 * button_width + 150
		center_x = (1920 - total_width_for_buttons) // 2

		prev_button_x = center_x
		prev_button_y = (1080 - button_height) - 100
		next_button_x = prev_button_x + button_width + 150
		next_button_y = prev_button_y

		prev_button_image = os.path.join(addon_dir, 'resources', 'skins', 'default', 'media', 'prev.png')
		next_button_image = os.path.join(addon_dir, 'resources', 'skins', 'default', 'media', 'next.png')
		prev_button_image_focus = os.path.join(addon_dir, 'resources', 'skins', 'default', 'media', 'prevb.png')
		next_button_image_focus = os.path.join(addon_dir, 'resources', 'skins', 'default', 'media', 'nextb.png')

		xbmc.log(f"Prev button exists: {os.path.exists(prev_button_image)}", xbmc.LOGINFO)
		xbmc.log(f"Next button exists: {os.path.exists(next_button_image)}", xbmc.LOGINFO)

		try:
			self.prev_button = xbmcgui.ControlButton(
				prev_button_x, prev_button_y, button_width, button_height, "",
				focusTexture=prev_button_image_focus,
				noFocusTexture=prev_button_image
			)
			self.addControl(self.prev_button)
			xbmc.log("Prev button added", xbmc.LOGINFO)

			self.next_button = xbmcgui.ControlButton(
				next_button_x, next_button_y, button_width, button_height, "",
				focusTexture=next_button_image_focus,
				noFocusTexture=next_button_image
			)
			self.addControl(self.next_button)
			xbmc.log("Next button added", xbmc.LOGINFO)
		except Exception as e:
			xbmc.log(f"ERROR creating navigation buttons: {str(e)}", xbmc.LOGERROR)

	def set_audiobook_navigation(self):
		"""Set up keyboard/remote navigation between controls"""
		for row in range(MAX_ROWS):
			for column in range(MAX_COLUMNS):
				index = row * MAX_COLUMNS + column
				if index >= len(self.button_controls):
					break

				button = self.button_controls[index]
				above = self.button_controls[index - MAX_COLUMNS] if index - MAX_COLUMNS >= 0 else button

				below = self.button_controls[index + MAX_COLUMNS] if (index + MAX_COLUMNS) < len(self.button_controls) else button
				rows_on_current_page = -(-len(self.audiobooks_to_display) // MAX_COLUMNS)  # Ceiling division
				if row == rows_on_current_page - 1:  # Last row on current page
					below = self.next_button

				left = button if column == 0 else self.button_controls[index - 1]
				right = button if column == MAX_COLUMNS - 1 else self.button_controls[index + 1]

				button.setNavigation(above, below, left, right)

		self.prev_button.setNavigation(self.button_controls[0], self.prev_button, self.prev_button, self.next_button)
		self.next_button.setNavigation(self.button_controls[0], self.next_button, self.prev_button, self.next_button)

	def onFocus(self, controlId):
		"""Handle focus changes to show/hide play overlay"""
		for index, button in enumerate(self.button_controls):
			if index < len(self.play_controls):
				self.play_controls[index].setVisible(button.getId() == controlId)
			if button.getId() == controlId:
				self.selected_index = index

	def next_page(self):
		"""Navigate to next page of audiobooks"""
		if (self.page + 1) * MAX_PER_PAGE < len(self.audiobooks):
			self.page += 1
			self.selected_index = None
			self.display_audiobooks()

	def previous_page(self):
		"""Navigate to previous page of audiobooks"""
		if self.page > 0:
			self.page -= 1
			self.selected_index = None
			self.display_audiobooks()

	def getRealIndex(self, current_index):
		"""Convert page-relative index to absolute index"""
		current_page = self.page
		real_index = (current_page) * MAX_PER_PAGE + current_index
		return real_index

	def onAction(self, action):
		"""Handle user actions"""
		if action.getButtonCode() == 216 or action.getId() == xbmcgui.ACTION_NAV_BACK:
			self.close()
		elif action.getId() == xbmcgui.ACTION_SELECT_ITEM:
			focus_id = self.getFocusId()
			if focus_id == self.prev_button.getId():
				self.previous_page()
			elif focus_id == self.next_button.getId():
				self.next_page()
			else:
				for index, button in enumerate(self.button_controls):
					if focus_id == button.getId():
						xbmc.log(f"Selected index: {index}", xbmc.LOGINFO)
						rindex = self.getRealIndex(index)
						xbmc.log(f"Real index: {rindex}", xbmc.LOGINFO)
						self.show_audiobook_player(rindex)
						break

	def show_audiobook_player(self, index):
		"""Show the audiobook player dialog, episode list for podcasts, or file list for multi-file audiobooks"""
		self.selected_index = index
		selected_audiobook = self.audiobooks[index]
		
		# Check if this is a podcast with episodes
		if selected_audiobook.get('media_type') == 'podcast' and selected_audiobook.get('has_episodes'):
			self.show_episode_list(selected_audiobook)
		# Check if this is a multi-file audiobook
		elif selected_audiobook.get('num_audio_files', 1) > 1:
			self.show_audiobook_file_list(selected_audiobook)
		else:
			# Show regular audiobook player for single-file audiobooks
			cover = selected_audiobook['cover_url']
			iid = selected_audiobook['id']
			
			audiobook_data = {
				'id': iid,
				'title': selected_audiobook['title'],
				'cover': cover,
				'description': selected_audiobook['description'],
				'narrator_name': selected_audiobook['narrator_name'],
				'published_year': selected_audiobook['published_year'],
				'publisher': selected_audiobook['publisher'],
				'duration': selected_audiobook['duration'],
			}
				
			dialog = AudioBookPlayer(
				'audiobook_dialog.xml', 
				xbmcaddon.Addon().getAddonInfo('path'), 
				'default', 
				'1080i', 
				**audiobook_data
			)
			dialog.doModal()
			del dialog
	
	def show_audiobook_file_list(self, audiobook):
		"""Show list of audio files for multi-file audiobooks"""
		xbmc.log(f"Showing file list for audiobook: {audiobook['title']}", xbmc.LOGINFO)
		
		try:
			# Get settings for API call
			ip_address = ADDON.getSetting('ipaddress')
			port = ADDON.getSetting('port')
			username = ADDON.getSetting('username')
			password = ADDON.getSetting('password')
			
			url = f"http://{ip_address}:{port}"
			from login_service import AudioBookShelfService
			login_service = AudioBookShelfService(url)
			response_data = login_service.login(username, password)
			token = response_data.get('token')
			
			library_service = AudioBookShelfLibraryService(url, token)
			
			# Get full item details with audio files
			item_details = library_service.get_library_item_by_id(audiobook['id'])
			
			audio_files = item_details.get('media', {}).get('audioFiles', [])
			
			if not audio_files or len(audio_files) <= 1:
				# If only one file, just play it
				self.play_audiobook_file(audiobook, audio_files[0] if audio_files else None)
				return
			
			xbmc.log(f"Found {len(audio_files)} audio files", xbmc.LOGINFO)
			
			# Sort by index
			audio_files_sorted = sorted(audio_files, key=lambda x: x.get('index', 0))
			
			# Create file list for dialog
			file_titles = []
			for i, audio_file in enumerate(audio_files_sorted, 1):
				# Try to get chapter name or use filename
				metadata = audio_file.get('metadata', {})
				title = metadata.get('title') or metadata.get('filename') or f"Part {i}"
				
				# Format duration if available
				duration = audio_file.get('duration', 0)
				if duration > 0:
					hours = int(duration // 3600)
					minutes = int((duration % 3600) // 60)
					if hours > 0:
						duration_str = f"{hours}h {minutes}m"
					else:
						duration_str = f"{minutes}m"
					display_title = f"{i}. {title} ({duration_str})"
				else:
					display_title = f"{i}. {title}"
				
				file_titles.append(display_title)
			
			# Show file selection dialog
			dialog = xbmcgui.Dialog()
			selected = dialog.select(f'{audiobook["title"]} - Select Part', file_titles)
			
			if selected >= 0:
				selected_file = audio_files_sorted[selected]
				xbmc.log(f"Selected file: {selected_file.get('metadata', {}).get('filename', 'unknown')}", xbmc.LOGINFO)
				
				# Play the selected file
				self.play_audiobook_file(audiobook, selected_file)
				
		except Exception as e:
			xbmc.log(f"Error loading audio files: {str(e)}", xbmc.LOGERROR)
			xbmcgui.Dialog().ok('Error', f'Failed to load audio files: {str(e)}')
	
	def play_audiobook_file(self, audiobook, audio_file):
		"""Play a specific audio file from an audiobook with resume support"""
		try:
			# Get settings for API call
			ip_address = ADDON.getSetting('ipaddress')
			port = ADDON.getSetting('port')
			username = ADDON.getSetting('username')
			password = ADDON.getSetting('password')
			
			url = f"http://{ip_address}:{port}"
			from login_service import AudioBookShelfService
			login_service = AudioBookShelfService(url)
			response_data = login_service.login(username, password)
			token = response_data.get('token')
			
			library_service = AudioBookShelfLibraryService(url, token)
			
			# Get play URL for the specific file
			ino = audio_file.get('ino')
			if not ino:
				raise ValueError("Audio file has no ino")
			
			play_url = f"{url}/api/items/{audiobook['id']}/file/{ino}?token={token}"
			duration = audio_file.get('duration', 0)
			
			# Get resume position (note: multi-file resume might not work perfectly server-side)
			resume_pos = get_resume_position(library_service, audiobook['id'])
			
			# Ask user if they want to resume
			start_position = 0
			if resume_pos > 0 and ask_resume(resume_pos, duration):
				start_position = resume_pos
			
			xbmc.log(f"Playing file URL: {play_url}", xbmc.LOGINFO)
			
			# Create player and play
			player = xbmc.Player()
			list_item = xbmcgui.ListItem(path=play_url)
			
			file_title = audio_file.get('metadata', {}).get('title') or audio_file.get('metadata', {}).get('filename', 'Unknown')
			
			list_item.setInfo('music', {
				'title': file_title,
				'artist': audiobook.get('narrator_name', '').replace('Narrator: ', ''),
				'album': audiobook.get('title', 'Unknown Audiobook'),
				'duration': duration
			})
			
			player.play(play_url, list_item)
			
			# Wait for player to start
			timeout = 0
			while not player.isPlaying() and timeout < 10:
				xbmc.sleep(500)
				timeout += 1
			
			# Start playback monitoring
			monitor = PlaybackMonitor(library_service, audiobook['id'], duration)
			monitor.start_monitoring(start_position)
			
			# Wait for playback to finish
			while player.isPlayingAudio():
				xbmc.sleep(1000)
			
			# Stop monitoring
			monitor.stop_monitoring()
			
		except Exception as e:
			xbmc.log(f"Error playing audio file: {str(e)}", xbmc.LOGERROR)
			xbmcgui.Dialog().ok('Error', f'Failed to play audio file: {str(e)}')
	
	def show_episode_list(self, podcast):
		"""Show list of podcast episodes"""
		xbmc.log(f"Showing episodes for podcast: {podcast['title']}", xbmc.LOGINFO)
		
		# Get episode list from server
		try:
			# Get settings for API call
			ip_address = ADDON.getSetting('ipaddress')
			port = ADDON.getSetting('port')
			username = ADDON.getSetting('username')
			password = ADDON.getSetting('password')
			
			url = f"http://{ip_address}:{port}"
			from login_service import AudioBookShelfService
			login_service = AudioBookShelfService(url)
			response_data = login_service.login(username, password)
			token = response_data.get('token')
			
			library_service = AudioBookShelfLibraryService(url, token)
			
			# Get full item details with episodes
			item_details = library_service.get_library_item_by_id(podcast['id'], expanded=1)
			
			episodes = item_details.get('media', {}).get('episodes', [])
			
			if not episodes:
				xbmcgui.Dialog().ok('No Episodes', 'This podcast has no episodes')
				return
			
			xbmc.log(f"Found {len(episodes)} episodes", xbmc.LOGINFO)
			
			# Sort episodes by episode number or published date, handling None values
			def get_sort_key(episode):
				# Try index first
				if episode.get('index') is not None:
					return (0, episode.get('index'))
				# Try episode number
				elif episode.get('episode') is not None:
					return (1, episode.get('episode'))
				# Try published date
				elif episode.get('publishedAt'):
					return (2, episode.get('publishedAt'))
				# Fallback to title
				else:
					return (3, episode.get('title', ''))
			
			episodes_sorted = sorted(episodes, key=get_sort_key, reverse=True)
			
			# Create episode list for dialog
			episode_titles = []
			for ep in episodes_sorted:
				title = ep.get('title', 'Unknown Episode')
				episode_num = ep.get('episode', '')
				season = ep.get('season', '')
				
				# Format: "S01E05 - Episode Title" or just "Episode Title"
				if season and episode_num:
					display_title = f"S{str(season).zfill(2)}E{str(episode_num).zfill(2)} - {title}"
				elif episode_num:
					display_title = f"Episode {episode_num} - {title}"
				else:
					display_title = title
				
				episode_titles.append(display_title)
			
			# Show episode selection dialog
			dialog = xbmcgui.Dialog()
			selected = dialog.select(f'{podcast["title"]} - Episodes', episode_titles)
			
			if selected >= 0:
				selected_episode = episodes_sorted[selected]
				xbmc.log(f"Selected episode: {selected_episode.get('title')}", xbmc.LOGINFO)
				
				# Play the selected episode
				self.play_podcast_episode(podcast, selected_episode)
				
		except Exception as e:
			xbmc.log(f"Error loading episodes: {str(e)}", xbmc.LOGERROR)
			xbmcgui.Dialog().ok('Error', f'Failed to load episodes: {str(e)}')
	
	def play_podcast_episode(self, podcast, episode):
		"""Play a podcast episode with resume support"""
		try:
			# Get settings for API call
			ip_address = ADDON.getSetting('ipaddress')
			port = ADDON.getSetting('port')
			username = ADDON.getSetting('username')
			password = ADDON.getSetting('password')
			
			url = f"http://{ip_address}:{port}"
			from login_service import AudioBookShelfService
			login_service = AudioBookShelfService(url)
			response_data = login_service.login(username, password)
			token = response_data.get('token')
			
			library_service = AudioBookShelfLibraryService(url, token)
			
			# Get play URL for the episode
			episode_id = episode.get('id')
			duration = episode.get('duration', 0)
			play_url = library_service.get_file_url(podcast['id'], episode_id=episode_id)
			
			# Get resume position
			resume_pos = get_resume_position(library_service, podcast['id'], episode_id)
			
			# Ask user if they want to resume
			start_position = 0
			if resume_pos > 0 and ask_resume(resume_pos, duration):
				start_position = resume_pos
			
			xbmc.log(f"Playing episode URL: {play_url}", xbmc.LOGINFO)
			
			# Create player and play
			player = xbmc.Player()
			list_item = xbmcgui.ListItem(path=play_url)
			list_item.setInfo('music', {
				'title': episode.get('title', 'Unknown Episode'),
				'artist': podcast.get('title', 'Unknown Podcast'),
				'duration': duration
			})
			
			player.play(play_url, list_item)
			
			# Wait for player to start
			timeout = 0
			while not player.isPlaying() and timeout < 10:
				xbmc.sleep(500)
				timeout += 1
			
			# Start playback monitoring
			monitor = PlaybackMonitor(library_service, podcast['id'], duration, episode_id=episode_id)
			monitor.start_monitoring(start_position)
			
			# Wait for playback to finish
			while player.isPlayingAudio():
				xbmc.sleep(1000)
			
			# Stop monitoring
			monitor.stop_monitoring()
			
		except Exception as e:
			xbmc.log(f"Error playing episode: {str(e)}", xbmc.LOGERROR)
			xbmcgui.Dialog().ok('Error', f'Failed to play episode: {str(e)}')


def download_cover(url, item_id):
	"""Download cover image to local cache"""
	try:
		# Create cache directory in addon's profile dir (writable)
		profile_path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
		cache_dir = os.path.join(profile_path, 'covers')
		
		if not os.path.exists(cache_dir):
			xbmc.log(f"Creating cache directory: {cache_dir}", xbmc.LOGINFO)
			os.makedirs(cache_dir)
		
		cache_file = os.path.join(cache_dir, f"{item_id}.jpg")
		
		# Return cached file if it exists
		if os.path.exists(cache_file):
			xbmc.log(f"Using cached cover: {cache_file}", xbmc.LOGDEBUG)
			return cache_file
		
		# Download the cover
		xbmc.log(f"Downloading cover from {url} to {cache_file}", xbmc.LOGINFO)
		urlretrieve(url, cache_file)
		
		if os.path.exists(cache_file):
			xbmc.log(f"Cover downloaded successfully: {cache_file}", xbmc.LOGINFO)
			return cache_file
		else:
			xbmc.log(f"Cover download failed", xbmc.LOGERROR)
			return None
	except Exception as e:
		xbmc.log(f"Error downloading cover: {str(e)}", xbmc.LOGERROR)
		return None


def select_library(url, token):
	"""Let user select a library and return its audiobooks"""
	library_service = AudioBookShelfLibraryService(url, token)

	try:
		data = library_service.get_all_libraries()
		libraries = data['libraries']
		library_names = [lib['name'] for lib in libraries]

		dialog = xbmcgui.Dialog()
		selected = dialog.select('Select a Library', library_names)

		if selected != -1:
			selected_library = libraries[selected]
			items = library_service.get_library_items(selected_library['id'])
			audiobooks = []

			xbmc.log(f"Processing {len(items.get('results', []))} items from library", xbmc.LOGINFO)

			for item in items.get("results", []):
				media = item.get('media', {})
				metadata = media.get('metadata', {})
				media_type = item.get('mediaType', 'book')
				
				# Use the item ID directly for the cover URL (not folder name!)
				item_id = item['id']
				cover_url = f"{url}/api/items/{item_id}/cover?token={token}"
				
				# Download cover to local cache
				xbmc.log(f"Downloading cover for: {metadata.get('title', 'Unknown')}", xbmc.LOGINFO)
				local_cover = download_cover(cover_url, item_id)
				
				# Fallback to icon if cover download fails
				if not local_cover:
					xbmc.log(f"Cover download failed, using addon icon", xbmc.LOGWARNING)
					local_cover = os.path.join(CWD, 'resources', 'icon.png')
				
				title = metadata.get('title', 'Unknown Title')
				description = metadata.get('description', '')
				narrator_name = metadata.get('narratorName', '')
				publisher = metadata.get('publisher', '')
				published_year = metadata.get('publishedYear', '')
				duration = media.get('duration', 0.0) or 0.0
				
				# Check if this is a podcast with episodes
				has_episodes = media_type == 'podcast' and media.get('numEpisodes', 0) > 0
				
				# Check number of audio files (for multi-file audiobooks)
				num_audio_files = media.get('numAudioFiles', 1)

				audiobook = {
					"id": item_id,
					"title": title,
					"cover_url": local_cover,  # Use local path instead of remote URL
					"description": description,
					"narrator_name": f"Narrator: {narrator_name}" if narrator_name else "",
					"published_year": f"Year: {published_year}" if published_year else "",
					"publisher": f"Publisher: {publisher}" if publisher else "",
					"duration": duration,
					"media_type": media_type,
					"has_episodes": has_episodes,
					"num_audio_files": num_audio_files,
				}
				audiobooks.append(audiobook)
				xbmc.log(f"Added {media_type}: {title} ({num_audio_files} files) with cover: {local_cover}", xbmc.LOGDEBUG)

			xbmc.log(f"Loaded {len(audiobooks)} audiobooks total", xbmc.LOGINFO)
			return audiobooks
	except Exception as e:
		xbmc.log(f"Error selecting library: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().ok('Error', f'Failed to load library: {str(e)}')
		return []

	return []


if __name__ == '__main__':
	# Get settings
	ip_address = ADDON.getSetting('ipaddress')
	port = ADDON.getSetting('port')
	username = ADDON.getSetting('username')
	password = ADDON.getSetting('password')

	# Prompt for settings if not configured
	if not ip_address or not port or not username or not password:
		dialog = SettingsDialog()
		dialog.get_and_store_settings()
		
		# Reload settings
		ip_address = ADDON.getSetting('ipaddress')
		port = ADDON.getSetting('port')
		username = ADDON.getSetting('username')
		password = ADDON.getSetting('password')

	# Build server URL
	url = f"http://{ip_address}:{port}"
	service = AudioBookShelfService(url)

	# Check server status
	try:
		server_status = service.server_status()
		xbmc.log(f"Server status: {server_status}", xbmc.LOGINFO)
	except Exception as e:
		xbmc.log(f"Server connection failed: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().ok('Error', 'Audiobookshelf server is not reachable')
		sys.exit()

	# Login
	try:
		response_data = service.login(username, password)
		token = response_data.get('token')
		if not token:
			raise ValueError("No token in response")
		xbmc.log("Successfully logged in", xbmc.LOGINFO)
	except Exception as e:
		xbmc.log(f"Login failed: {str(e)}", xbmc.LOGERROR)
		xbmcgui.Dialog().ok('Error', 'Please check your username and password')
		sys.exit()

	# Select library and show audiobooks
	audiobooks = select_library(url, token)
	
	if audiobooks:
		ui = GUI('script-mainwindow.xml', CWD, 'default', '1080i', True, optional1=audiobooks)
		ui.doModal()
		del ui
	else:
		xbmcgui.Dialog().ok('Error', 'No audiobooks found or library selection cancelled')
