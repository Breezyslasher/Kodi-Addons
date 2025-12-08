import xbmc
import xbmcgui
import time
import threading


class PlaybackMonitor:
	"""Monitor playback and sync progress with Audiobookshelf server"""
	
	def __init__(self, library_service, item_id, duration, episode_id=None):
		self.library_service = library_service
		self.item_id = item_id
		self.episode_id = episode_id
		self.duration = duration
		self.player = xbmc.Player()
		self.session_id = None
		self.is_monitoring = False
		self.monitor_thread = None
		self.last_sync_time = 0
		self.sync_interval = 10  # Sync every 10 seconds
		self.start_time = None
		self.total_time_listened = 0
		
	def start_monitoring(self, start_position=0):
		"""Start monitoring playback"""
		xbmc.log(f"Starting playback monitor for item {self.item_id}", xbmc.LOGINFO)
		
		# Start playback session on server
		session = self.library_service.start_playback_session(self.item_id, self.episode_id)
		if session:
			self.session_id = session.get('id')
			xbmc.log(f"Playback session started: {self.session_id}", xbmc.LOGINFO)
		
		# Seek to start position if resuming
		if start_position > 0 and self.player.isPlaying():
			try:
				xbmc.log(f"Seeking to resume position: {start_position}s", xbmc.LOGINFO)
				xbmc.sleep(1000)  # Wait a bit for player to be ready
				self.player.seekTime(start_position)
			except Exception as e:
				xbmc.log(f"Error seeking to start position: {str(e)}", xbmc.LOGERROR)
		
		self.start_time = time.time()
		self.is_monitoring = True
		
		# Start monitoring thread
		self.monitor_thread = threading.Thread(target=self._monitor_loop)
		self.monitor_thread.daemon = True
		self.monitor_thread.start()
	
	def _monitor_loop(self):
		"""Main monitoring loop"""
		last_position = 0
		
		while self.is_monitoring and self.player.isPlayingAudio():
			try:
				current_time = self.player.getTime()
				
				# Calculate time listened since last update
				if self.player.isPlaying():
					time_since_last_sync = time.time() - self.last_sync_time
					if time_since_last_sync >= self.sync_interval:
						# Update progress on server
						self._sync_progress(current_time)
						self.last_sync_time = time.time()
						last_position = current_time
				
			except Exception as e:
				xbmc.log(f"Error in monitor loop: {str(e)}", xbmc.LOGDEBUG)
			
			xbmc.sleep(2000)  # Check every 2 seconds
		
		# Final sync when playback ends
		if last_position > 0:
			try:
				final_time = self.player.getTime() if self.player.isPlayingAudio() else last_position
				self._sync_progress(final_time, is_final=True)
			except:
				pass
		
		xbmc.log("Playback monitor stopped", xbmc.LOGINFO)
	
	def _sync_progress(self, current_time, is_final=False):
		"""Sync current progress to server"""
		try:
			# Calculate if finished (within last 30 seconds)
			is_finished = (self.duration - current_time) < 30 or is_final and (self.duration - current_time) < 60
			
			# Update progress
			self.library_service.update_media_progress(
				self.item_id,
				current_time,
				self.duration,
				is_finished=is_finished,
				episode_id=self.episode_id
			)
			
			# Sync session if we have one
			if self.session_id:
				time_listened = time.time() - self.start_time
				self.library_service.sync_playback_session(
					self.session_id,
					current_time,
					self.duration,
					time_listened=int(time_listened)
				)
			
		except Exception as e:
			xbmc.log(f"Error syncing progress: {str(e)}", xbmc.LOGERROR)
	
	def stop_monitoring(self):
		"""Stop monitoring and close session"""
		xbmc.log("Stopping playback monitor", xbmc.LOGINFO)
		self.is_monitoring = False
		
		# Wait for thread to finish
		if self.monitor_thread and self.monitor_thread.is_alive():
			self.monitor_thread.join(timeout=5)
		
		# Close session on server
		if self.session_id:
			self.library_service.close_playback_session(self.session_id)
			self.session_id = None


def get_resume_position(library_service, item_id, episode_id=None):
	"""Get resume position from server"""
	try:
		progress = library_service.get_media_progress(item_id, episode_id)
		
		if progress:
			current_time = progress.get('currentTime', 0)
			is_finished = progress.get('isFinished', False)
			
			# Don't resume if already finished
			if is_finished:
				xbmc.log("Item already finished, starting from beginning", xbmc.LOGINFO)
				return 0
			
			# Don't resume if less than 10 seconds
			if current_time < 10:
				return 0
			
			xbmc.log(f"Found resume position: {current_time}s", xbmc.LOGINFO)
			return current_time
		else:
			xbmc.log("No resume position found", xbmc.LOGINFO)
			return 0
			
	except Exception as e:
		xbmc.log(f"Error getting resume position: {str(e)}", xbmc.LOGERROR)
		return 0


def ask_resume(current_time, duration):
	"""Ask user if they want to resume"""
	if current_time < 10:
		return False
	
	# Format time nicely
	hours = int(current_time // 3600)
	minutes = int((current_time % 3600) // 60)
	seconds = int(current_time % 60)
	
	if hours > 0:
		time_str = f"{hours}h {minutes}m {seconds}s"
	elif minutes > 0:
		time_str = f"{minutes}m {seconds}s"
	else:
		time_str = f"{seconds}s"
	
	# Calculate percentage
	percentage = (current_time / duration * 100) if duration > 0 else 0
	
	dialog = xbmcgui.Dialog()
	resume = dialog.yesno(
		'Resume Playback',
		f'Resume from {time_str} ({percentage:.0f}%)?',
		nolabel='Start Over',
		yeslabel='Resume'
	)
	
	return resume
