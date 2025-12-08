import xbmc
import xbmcgui
import time
import threading
try:
    import json
except ImportError:
    import simplejson as json


class PlaybackMonitor:
    """Monitor playback and sync progress"""
    
    def __init__(self, library_service, item_id, duration, episode_id=None, 
                 sync_kodi_watched=False, episode_title=None, auto_delete_enabled=False,
                 download_manager=None, offline_mode=False):
        self.library_service = library_service
        self.item_id = item_id
        self.episode_id = episode_id
        self.duration = max(duration, 1)  # Prevent division by zero
        self.player = xbmc.Player()
        self.session_id = None
        self.is_monitoring = False
        self.monitor_thread = None
        self.last_sync_time = 0
        self.sync_interval = 15  # Sync every 15 seconds
        self.start_time = None
        self.sync_kodi_watched = sync_kodi_watched
        self.episode_title = episode_title
        self.marked_as_watched = False
        self.auto_delete_enabled = auto_delete_enabled
        self.is_finished = False
        self.start_position = 0
        self.download_manager = download_manager
        self.offline_mode = offline_mode
        self.last_server_sync = 0
        self.server_sync_interval = 60  # Sync with server every 60 seconds
    
    def start_monitoring_async(self, start_position=0):
        """Start monitoring in background thread"""
        self.start_position = start_position
        self.is_monitoring = True
        
        self.monitor_thread = threading.Thread(target=self._async_monitor_worker)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        xbmc.log(f"Started playback monitor for {self.item_id}", xbmc.LOGINFO)
    
    def _async_monitor_worker(self):
        """Background worker"""
        try:
            # Wait for player to start (up to 30 seconds)
            wait_count = 0
            while not self.player.isPlaying() and wait_count < 60:
                xbmc.sleep(500)
                wait_count += 1
            
            if not self.player.isPlaying():
                xbmc.log("Player never started", xbmc.LOGWARNING)
                return
            
            # Wait for player to stabilize
            xbmc.sleep(1500)
            
            # Seek to start position
            if self.start_position > 0:
                try:
                    # Wait a bit more before seeking
                    xbmc.sleep(500)
                    self.player.seekTime(self.start_position)
                    xbmc.log(f"Seeked to {self.start_position}s", xbmc.LOGINFO)
                except Exception as e:
                    xbmc.log(f"Seek error: {str(e)}", xbmc.LOGERROR)
            
            # Start playback session on server (if online)
            if self.library_service and not self.offline_mode:
                try:
                    session = self.library_service.start_playback_session(self.item_id, self.episode_id)
                    if session:
                        self.session_id = session.get('id')
                except Exception as e:
                    xbmc.log(f"Session start error: {str(e)}", xbmc.LOGERROR)
            
            self.start_time = time.time()
            self.last_sync_time = time.time()
            self.last_server_sync = time.time()
            
            last_position = self.start_position
            
            # Main monitoring loop
            while self.is_monitoring:
                try:
                    if not self.player.isPlayingAudio():
                        break
                    
                    current_time = self.player.getTime()
                    
                    # Local sync (save position)
                    time_since_sync = time.time() - self.last_sync_time
                    if time_since_sync >= self.sync_interval:
                        self._save_progress(current_time)
                        self.last_sync_time = time.time()
                        last_position = current_time
                    
                    # Server sync (if online)
                    if self.library_service and not self.offline_mode:
                        time_since_server_sync = time.time() - self.last_server_sync
                        if time_since_server_sync >= self.server_sync_interval:
                            self._sync_to_server(current_time)
                            self.last_server_sync = time.time()
                    
                except Exception as e:
                    xbmc.log(f"Monitor loop error: {str(e)}", xbmc.LOGDEBUG)
                
                xbmc.sleep(2000)
            
            # Final save
            try:
                final_time = last_position
                try:
                    if self.player.isPlayingAudio():
                        final_time = self.player.getTime()
                except:
                    pass
                
                if final_time > 0:
                    self._save_progress(final_time, is_final=True)
                    if self.library_service and not self.offline_mode:
                        self._sync_to_server(final_time, is_final=True)
            except Exception as e:
                xbmc.log(f"Final save error: {str(e)}", xbmc.LOGDEBUG)
            
            # Close session
            if self.session_id and self.library_service:
                try:
                    self.library_service.close_playback_session(self.session_id)
                except:
                    pass
            
            xbmc.log("Playback monitor stopped", xbmc.LOGINFO)
            
        except Exception as e:
            xbmc.log(f"Monitor worker error: {str(e)}", xbmc.LOGERROR)
    
    def _save_progress(self, current_time, is_final=False):
        """Save progress locally"""
        try:
            finished = (self.duration - current_time) < 30 or (is_final and (self.duration - current_time) < 60)
            
            if finished:
                self.is_finished = True
            
            # Save to local storage
            if self.download_manager:
                self.download_manager.save_resume_position(
                    self.item_id,
                    self.episode_id,
                    current_time,
                    self.duration,
                    is_finished=finished
                )
            
        except Exception as e:
            xbmc.log(f"Save progress error: {str(e)}", xbmc.LOGERROR)
    
    def _sync_to_server(self, current_time, is_final=False):
        """Sync progress to server"""
        if not self.library_service:
            return
        
        try:
            finished = (self.duration - current_time) < 30 or (is_final and (self.duration - current_time) < 60)
            
            if finished:
                self.is_finished = True
            
            self.library_service.update_media_progress(
                self.item_id,
                current_time,
                self.duration,
                is_finished=finished,
                episode_id=self.episode_id
            )
            
            # Mark local position as synced
            if self.download_manager:
                self.download_manager.mark_position_synced(self.item_id, self.episode_id)
            
            if finished and self.sync_kodi_watched and not self.marked_as_watched:
                self._mark_as_watched_in_kodi()
                self.marked_as_watched = True
            
            # Sync session
            if self.session_id and self.start_time:
                time_listened = time.time() - self.start_time
                try:
                    self.library_service.sync_playback_session(
                        self.session_id,
                        current_time,
                        self.duration,
                        time_listened=int(time_listened)
                    )
                except:
                    pass
            
        except Exception as e:
            xbmc.log(f"Server sync error: {str(e)}", xbmc.LOGERROR)
    
    def _mark_as_watched_in_kodi(self):
        """Mark as watched in Kodi"""
        try:
            json_query = {
                "jsonrpc": "2.0",
                "method": "Files.SetFileDetails",
                "params": {
                    "file": f"audiobookshelf://{self.item_id}/{self.episode_id if self.episode_id else 'item'}",
                    "media": "music",
                    "playcount": 1,
                    "lastplayed": time.strftime("%Y-%m-%d %H:%M:%S")
                },
                "id": 1
            }
            xbmc.executeJSONRPC(json.dumps(json_query))
            
            if self.episode_title:
                xbmcgui.Dialog().notification('Complete', self.episode_title, xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception as e:
            xbmc.log(f"Mark watched error: {str(e)}", xbmc.LOGDEBUG)
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.is_monitoring = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        if self.session_id and self.library_service:
            try:
                self.library_service.close_playback_session(self.session_id)
            except:
                pass
            self.session_id = None


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
            
            xbmc.log(f"Resume position: {current_time}s", xbmc.LOGINFO)
            return current_time
        
        return 0
            
    except Exception as e:
        xbmc.log(f"Get resume error: {str(e)}", xbmc.LOGERROR)
        return 0


def ask_resume(current_time, duration):
    """Ask user if they want to resume"""
    if current_time < 10:
        return False
    
    hours = int(current_time // 3600)
    minutes = int((current_time % 3600) // 60)
    seconds = int(current_time % 60)
    
    if hours > 0:
        time_str = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        time_str = f"{minutes}m {seconds}s"
    else:
        time_str = f"{seconds}s"
    
    percentage = (current_time / duration * 100) if duration > 0 else 0
    
    dialog = xbmcgui.Dialog()
    return dialog.yesno(
        'Resume Playback',
        f'Resume from {time_str} ({percentage:.0f}%)?',
        nolabel='Start Over',
        yeslabel='Resume'
    )
