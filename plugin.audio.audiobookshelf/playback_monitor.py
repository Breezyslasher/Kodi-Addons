import xbmc
import xbmcgui
import time
import threading


class PlaybackMonitor:
    """Monitor playback and sync progress"""
    
    def __init__(self, library_service, item_id, duration, episode_id=None,
                 download_manager=None, offline_mode=False, sync_enabled=True,
                 sync_on_stop=True, sync_interval=15):
        self.library_service = library_service
        self.item_id = item_id
        self.episode_id = episode_id
        self.duration = max(duration, 1)
        self.player = xbmc.Player()
        self.session_id = None
        self.is_monitoring = False
        self.monitor_thread = None
        self.download_manager = download_manager
        self.offline_mode = offline_mode
        self.sync_enabled = sync_enabled
        self.sync_on_stop = sync_on_stop
        self.sync_interval = sync_interval
        self.start_position = 0
        self.last_position = 0
        self.is_finished = False
    
    def start_monitoring_async(self, start_position=0):
        """Start monitoring in background"""
        self.start_position = start_position
        self.is_monitoring = True
        
        self.monitor_thread = threading.Thread(target=self._monitor_worker)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        xbmc.log(f"Started playback monitor for {self.item_id}", xbmc.LOGINFO)
    
    def _monitor_worker(self):
        """Background monitoring worker"""
        try:
            # Wait for player
            wait_count = 0
            while not self.player.isPlaying() and wait_count < 60:
                xbmc.sleep(500)
                wait_count += 1
            
            if not self.player.isPlaying():
                xbmc.log("Player never started", xbmc.LOGWARNING)
                return
            
            xbmc.sleep(1500)
            
            # Seek to position
            if self.start_position > 0:
                try:
                    xbmc.sleep(500)
                    self.player.seekTime(self.start_position)
                    xbmc.log(f"Seeked to {self.start_position}s", xbmc.LOGINFO)
                except Exception as e:
                    xbmc.log(f"Seek error: {str(e)}", xbmc.LOGERROR)
            
            # Start session if online
            if self.library_service and not self.offline_mode and self.sync_enabled:
                try:
                    session = self.library_service.start_playback_session(self.item_id, self.episode_id)
                    if session:
                        self.session_id = session.get('id')
                except:
                    pass
            
            last_sync_time = time.time()
            self.last_position = self.start_position
            
            # Main loop
            while self.is_monitoring:
                try:
                    if not self.player.isPlayingAudio():
                        break
                    
                    current_time = self.player.getTime()
                    self.last_position = current_time
                    
                    # Sync periodically
                    if self.sync_enabled and time.time() - last_sync_time >= self.sync_interval:
                        self._save_progress(current_time)
                        last_sync_time = time.time()
                    
                except:
                    pass
                
                xbmc.sleep(2000)
            
            # Final sync on stop
            if self.sync_on_stop and self.last_position > 0:
                self._save_progress(self.last_position, is_final=True)
            
            # Close session
            if self.session_id and self.library_service:
                try:
                    self.library_service.close_playback_session(self.session_id)
                except:
                    pass
            
            xbmc.log("Playback monitor stopped", xbmc.LOGINFO)
            
        except Exception as e:
            xbmc.log(f"Monitor error: {str(e)}", xbmc.LOGERROR)
    
    def _save_progress(self, current_time, is_final=False):
        """Save progress locally and to server"""
        try:
            # Check if finished
            finished = (self.duration - current_time) < 30
            if is_final and (self.duration - current_time) < 60:
                finished = True
            
            if finished:
                self.is_finished = True
            
            # Save locally
            if self.download_manager:
                self.download_manager.save_resume_position(
                    self.item_id,
                    self.episode_id,
                    current_time,
                    self.duration,
                    is_finished=finished
                )
            
            # Sync to server if online
            if self.library_service and not self.offline_mode:
                self.library_service.update_media_progress(
                    self.item_id,
                    current_time,
                    self.duration,
                    is_finished=finished,
                    episode_id=self.episode_id
                )
                
                # Mark as synced
                if self.download_manager:
                    self.download_manager.mark_position_synced(self.item_id, self.episode_id)
            
        except Exception as e:
            xbmc.log(f"Save progress error: {str(e)}", xbmc.LOGERROR)
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.is_monitoring = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)


def get_resume_position(library_service, item_id, episode_id=None):
    """Get resume position from server"""
    try:
        progress = library_service.get_media_progress(item_id, episode_id)
        
        if progress:
            current_time = progress.get('currentTime', 0)
            is_finished = progress.get('isFinished', False)
            
            if is_finished:
                return 0
            
            if current_time < 10:
                return 0
            
            return current_time
        
        return 0
        
    except:
        return 0
