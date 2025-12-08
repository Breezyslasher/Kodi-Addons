import sys
import xbmcgui
import xbmcaddon
import xbmc
import requests
import json
import threading
from library_service import AudioBookShelfLibraryService
from playback_monitor import PlaybackMonitor, get_resume_position, ask_resume


class AudioBookPlayer(xbmcgui.WindowXMLDialog):
	"""Custom player dialog for audiobooks with chapter navigation"""
	
	def __init__(self, *args, **kwargs):
		super(AudioBookPlayer, self).__init__(*args, **kwargs)
		self.id = kwargs['id']
		self.title = kwargs['title']
		self.cover = kwargs['cover']
		self.description = kwargs['description']
		self.narrator_name = kwargs['narrator_name']
		self.published_year = kwargs['published_year']
		self.publisher = kwargs['publisher']
		self.duration = kwargs['duration']
		self.player = xbmc.Player()
		self.playback_monitor = None
		
		# Get library service - needs to be initialized from outside
		addon = xbmcaddon.Addon()
		ip_address = addon.getSetting('ipaddress')
		port = addon.getSetting('port')
		username = addon.getSetting('username')
		password = addon.getSetting('password')
		
		# Import login service to get token
		from login_service import AudioBookShelfService
		url = f"http://{ip_address}:{port}"
		login_service = AudioBookShelfService(url)
		
		try:
			response_data = login_service.login(username, password)
			token = response_data.get('token')
			self.library_service = AudioBookShelfLibraryService(url, token)
		except Exception as e:
			xbmc.log(f"Failed to initialize library service: {str(e)}", xbmc.LOGERROR)
			self.library_service = None
		
		self.chapters = []
		if self.library_service:
			try:
				self.chapters = self.library_service.get_chapters(self.id)
			except Exception as e:
				xbmc.log(f"Failed to get chapters: {str(e)}", xbmc.LOGERROR)
		
		self.threads = []

	def onInit(self):
		"""Initialize the player dialog"""
		controls_mapping = {
			1: self.title,
			2: self.description,
			3: self.cover,
			4: self.narrator_name,
			5: self.published_year,
			6: self.publisher
		}
		
		for control_id, value in controls_mapping.items():
			try:
				control = self.getControl(control_id)
				if control_id in [1, 4, 5, 6]:  # Label controls
					control.setLabel(str(value))
				elif control_id == 2:  # Textbox control
					control.setText(str(value))
				elif control_id == 3:  # Image control
					control.setImage(str(value))
			except Exception as e:
				xbmc.log(f"Failed to set control {control_id}: {str(e)}", xbmc.LOGDEBUG)

		# Get button controls
		try:
			self.button_controls = [
				self.getControl(1003), self.getControl(1002),
				self.getControl(1001), self.getControl(1010), 
				self.getControl(1007), self.getControl(1008)
			]
			self.set_button_navigation()
			if self.button_controls:
				self.setFocus(self.button_controls[2])
		except Exception as e:
			xbmc.log(f"Failed to set up buttons: {str(e)}", xbmc.LOGERROR)

	def set_button_navigation(self):
		"""Set up navigation between buttons"""
		for index, button in enumerate(self.button_controls):
			left_button = self.button_controls[index - 1] if index > 0 else button
			right_button = self.button_controls[index + 1] if index < len(self.button_controls) - 1 else button
			button.setNavigation(button, button, left_button, right_button)

	def update_progressbar(self):
		"""Update the progress bar based on playback position"""
		try:
			if not self.player.isPlaying():
				return
			
			time = self.player.getTime()
			duration = self.duration
			progress_percentage = (time / duration) * 100 if duration != 0 else 0

			pb = self.getControl(1009)
			pb.setPercent(progress_percentage)
		except Exception as e:
			xbmc.log(f"Error updating progress bar: {str(e)}", xbmc.LOGDEBUG)

	def progressbar_updater(self):
		"""Background thread to update progress bar"""
		while self.player.isPlayingAudio():
			self.update_progressbar()
			xbmc.sleep(5000)

	def chapter_updater(self):
		"""Background thread to update chapter display"""
		while self.player.isPlayingAudio():
			try:
				self.update_chapter(self.player.getTime())
			except Exception as e:
				xbmc.log(f"Error in chapter updater: {str(e)}", xbmc.LOGDEBUG)
			xbmc.sleep(2000)

	def get_chapter_by_time(self, time):
		"""Get the chapter at a specific time"""
		for chapter in self.chapters:
			if chapter['start'] <= time <= chapter['end']:
				return chapter
		return None

	def update_chapter(self, time):
		"""Update the chapter label"""
		try:
			current_chapter = self.get_chapter_by_time(time)
			if current_chapter:
				ccontrol = self.getControl(1011)
				ccontrol.setLabel(current_chapter['title'])
		except Exception as e:
			xbmc.log(f"Error updating chapter: {str(e)}", xbmc.LOGDEBUG)

	def get_next_chapter(self, time):
		"""Get the next chapter from current time"""
		current_chapter = None
		for chapter in self.chapters:
			if chapter['start'] <= time <= chapter['end']:
				current_chapter = chapter
				break
		if current_chapter and self.chapters.index(current_chapter) < len(self.chapters) - 1:
			return self.chapters[self.chapters.index(current_chapter) + 1]
		return None

	def get_previous_chapter(self, time):
		"""Get the previous chapter from current time"""
		current_chapter = None
		for chapter in self.chapters:
			if chapter['start'] <= time <= chapter['end']:
				current_chapter = chapter
				break
		if current_chapter and self.chapters.index(current_chapter) > 0:
			return self.chapters[self.chapters.index(current_chapter) - 1]
		return None

	def update_timer(self):
		"""Background thread to update the playback timer"""
		while self.player.isPlayingAudio():
			try:
				ct = self.player.getTime()
				minutes = int(ct // 60)
				seconds = int(ct % 60)
				formatted_time = "{:02d}:{:02d}".format(minutes, seconds)
				
				timer_control = self.getControl(1012)
				timer_control.setLabel(formatted_time)
			except Exception as e:
				xbmc.log(f"Error updating timer: {str(e)}", xbmc.LOGDEBUG)
			
			xbmc.sleep(500)

	def _start_thread(self, target):
		"""Start a background thread"""
		thread = threading.Thread(target=target)
		thread.daemon = True
		thread.start()
		self.threads.append(thread)

	def onAction(self, action):
		"""Handle user actions"""
		if action.getId() == xbmcgui.ACTION_NAV_BACK:
			if self.player.isPlayingAudio():
				self.player.stop()
			self.close()
		elif action.getId() == xbmcgui.ACTION_SELECT_ITEM:
			focus_id = self.getFocusId()
			
			if focus_id == 1001:  # Play Button
				if not self.library_service:
					xbmcgui.Dialog().notification('Error', 'Library service not available', 
												 xbmcgui.NOTIFICATION_ERROR)
					return
				
				try:
					afile = self.library_service.get_file_url(self.id)
					
					if self.player.isPlayingAudio():
						self.player.pause()
					else:
						# Get resume position from server
						resume_pos = get_resume_position(self.library_service, self.id)
						
						# Ask user if they want to resume
						start_position = 0
						if resume_pos > 0 and ask_resume(resume_pos, self.duration):
							start_position = resume_pos
						
						# Start playing
						self.player.play(afile)
						
						# Wait for player to be ready
						timeout = 0
						while not self.player.isPlaying() and timeout < 10:
							xbmc.sleep(500)
							timeout += 1
						
						# Start playback monitoring
						if self.library_service:
							self.playback_monitor = PlaybackMonitor(
								self.library_service,
								self.id,
								self.duration
							)
							self.playback_monitor.start_monitoring(start_position)
					
					# Wait for pause button to be visible
					timeout = 0
					while not self.getControl(1010).isVisible() and timeout < 10:
						xbmc.sleep(1000)
						timeout += 1
					
					if self.getControl(1010).isVisible():
						self.setFocus(self.getControl(1010))
					
					self.update_chapter(self.player.getTime())
					self._start_thread(self.progressbar_updater)
					self._start_thread(self.chapter_updater)
					self._start_thread(self.update_timer)
				except Exception as e:
					xbmc.log(f"Error playing audio: {str(e)}", xbmc.LOGERROR)
					xbmcgui.Dialog().notification('Error', f'Failed to play: {str(e)}', 
												 xbmcgui.NOTIFICATION_ERROR)

			elif focus_id == 1010:  # Pause Button
				self.player.pause()
				
				# Wait for play button to be visible
				timeout = 0
				while not self.getControl(1001).isVisible() and timeout < 10:
					xbmc.sleep(1000)
					timeout += 1
				
				if self.getControl(1001).isVisible():
					self.setFocus(self.getControl(1001))

			elif focus_id in [1003, 1008]:  # Chapter navigation buttons
				chapter = None
				if focus_id == 1003:
					chapter = self.get_previous_chapter(self.player.getTime())
				elif focus_id == 1008:
					chapter = self.get_next_chapter(self.player.getTime())
				
				if chapter:
					cs = chapter['start']
					self.player.seekTime(cs)

			elif focus_id in [1002, 1007]:  # Time navigation buttons
				try:
					ct = self.player.getTime()
					st = None
					if focus_id == 1002:
						st = ct - 10
					elif focus_id == 1007:
						st = ct + 10

					if st is not None and st >= 0:
						self.player.seekTime(st)
				except Exception as e:
					xbmc.log(f"Error seeking: {str(e)}", xbmc.LOGDEBUG)

	def close(self):
		"""Clean up and close the dialog"""
		# Stop playback monitoring
		if self.playback_monitor:
			self.playback_monitor.stop_monitoring()
			self.playback_monitor = None
		
		if self.player.isPlayingAudio():
			self.player.stop()

		# Wait for threads to finish
		for thread in self.threads:
			if thread.is_alive():
				thread.join(timeout=2)

		super(AudioBookPlayer, self).close()


if __name__ == "__main__":
	if "play" in sys.argv:
		xbmcgui.Dialog().notification('Audiobook Player', 'Play function here!', 
									 xbmcgui.NOTIFICATION_INFO, 2000)
	else:
		myDialog = AudioBookPlayer('audiobook_dialog.xml', xbmcaddon.Addon().getAddonInfo('path'))
		myDialog.doModal()
		del myDialog
