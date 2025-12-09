"""
Playback Monitor v2.1.0 - Unified progress tracking for streamed and downloaded content
Uses sync_manager for all progress storage and synchronization
"""
import xbmc
import xbmcaddon
import time
import threading

# Import from sync_manager for all progress operations
from sync_manager import (
    get_sync_manager, 
    get_local_progress, 
    save_local_progress, 
    get_best_resume_position,
    sync_all_to_server,
    mark_synced
)


class PlaybackMonitor:
    """Monitor playback and sync progress - works for both streamed and downloaded content"""
    
    def __init__(self, library_service, item_id, duration, episode_id=None,
                 sync_enabled=True, sync_on_stop=True, sync_interval=15,
                 finished_threshold=0.95):
        self.library_service = library_service
        self.item_id = item_id
        self.episode_id = episode_id
        self.duration = max(duration, 1)
        self.player = xbmc.Player()
        self.session_id = None
        self.is_monitoring = False
        self.monitor_thread = None
        self.sync_enabled = sync_enabled
        self.sync_on_stop = sync_on_stop
        self.sync_interval = sync_interval
        self.finished_threshold = finished_threshold
        self.start_position = 0
        self.last_position = 0
        self.last_synced_position = 0
        self.is_finished = False
        
        # Initialize sync manager with library service
        self.sync_mgr = get_sync_manager()
        self.sync_mgr.set_library_service(library_service)
        
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        xbmc.log(f"[MONITOR] Created for {key}, duration={duration:.1f}s, sync_enabled={sync_enabled}, "
                f"library_service={'YES' if library_service else 'NO'}", xbmc.LOGINFO)
    
    def start_monitoring_async(self, start_position=0):
        """Start monitoring in background"""
        self.start_position = start_position
        self.is_monitoring = True
        
        self.monitor_thread = threading.Thread(target=self._monitor_worker, daemon=True)
        self.monitor_thread.start()
        
        xbmc.log(f"[MONITOR] Started for {self.item_id} at {start_position:.1f}s", xbmc.LOGINFO)
    
    def _monitor_worker(self):
        """Background monitoring worker"""
        try:
            # Wait for player to start
            wait_count = 0
            while not self.player.isPlaying() and wait_count < 60:
                xbmc.sleep(500)
                wait_count += 1
            
            if not self.player.isPlaying():
                xbmc.log("[MONITOR] Player never started", xbmc.LOGWARNING)
                return
            
            xbmc.sleep(1500)
            
            # Try to get duration from player if we don't have a valid one
            if self.duration <= 1:
                try:
                    player_duration = self.player.getTotalTime()
                    if player_duration > 1:
                        self.duration = player_duration
                        xbmc.log(f"[MONITOR] Got duration from player: {self.duration:.1f}s", xbmc.LOGINFO)
                except:
                    pass
            
            # Seek to position if needed
            if self.start_position > 0:
                try:
                    xbmc.sleep(500)
                    self.player.seekTime(self.start_position)
                    xbmc.log(f"[MONITOR] Seeked to {self.start_position:.1f}s", xbmc.LOGINFO)
                except Exception as e:
                    xbmc.log(f"[MONITOR] Seek error: {str(e)}", xbmc.LOGERROR)
            
            # Start session if we have library service
            if self.library_service and self.sync_enabled:
                try:
                    session = self.library_service.start_playback_session(self.item_id, self.episode_id)
                    if session:
                        self.session_id = session.get('id')
                        xbmc.log(f"[MONITOR] Started session: {self.session_id}", xbmc.LOGINFO)
                except Exception as e:
                    xbmc.log(f"[MONITOR] Session start error: {str(e)}", xbmc.LOGDEBUG)
            
            last_sync_time = time.time()
            self.last_position = self.start_position
            self.last_synced_position = self.start_position
            
            # Main monitoring loop
            while self.is_monitoring:
                try:
                    if not self.player.isPlayingAudio():
                        xbmc.log("[MONITOR] Audio playback stopped", xbmc.LOGINFO)
                        break
                    
                    current_time = self.player.getTime()
                    self.last_position = current_time
                    
                    # Update duration from player if still not set properly
                    if self.duration <= 1:
                        try:
                            player_duration = self.player.getTotalTime()
                            if player_duration > 1:
                                self.duration = player_duration
                                xbmc.log(f"[MONITOR] Updated duration from player: {self.duration:.1f}s", xbmc.LOGINFO)
                        except:
                            pass
                    
                    # Periodic sync
                    elapsed = time.time() - last_sync_time
                    if self.sync_enabled and elapsed >= self.sync_interval:
                        # Only sync if position changed significantly (more than 5 seconds)
                        if abs(current_time - self.last_synced_position) > 5:
                            xbmc.log(f"[MONITOR] Periodic sync at {current_time:.1f}s", xbmc.LOGINFO)
                            self._save_progress(current_time, is_final=False)
                            self.last_synced_position = current_time
                        last_sync_time = time.time()
                    
                except Exception as e:
                    xbmc.log(f"[MONITOR] Loop error: {str(e)}", xbmc.LOGERROR)
                
                xbmc.sleep(2000)
            
            # Final sync when playback stops
            if self.sync_on_stop and self.last_position > 0:
                xbmc.log(f"[MONITOR] Final sync at {self.last_position:.1f}s", xbmc.LOGINFO)
                self._save_progress(self.last_position, is_final=True)
            
            # Close session
            if self.session_id and self.library_service:
                try:
                    self.library_service.close_playback_session(self.session_id)
                    xbmc.log(f"[MONITOR] Closed session: {self.session_id}", xbmc.LOGINFO)
                except:
                    pass
            
            xbmc.log("[MONITOR] Stopped", xbmc.LOGINFO)
            
        except Exception as e:
            xbmc.log(f"[MONITOR] Worker error: {str(e)}", xbmc.LOGERROR)
    
    def _save_progress(self, current_time, is_final=False):
        """Save progress using sync_manager"""
        try:
            # Calculate progress percentage
            progress_pct = current_time / self.duration if self.duration > 0 else 0
            
            # Determine if finished - only on final sync and if past threshold
            finished = False
            if is_final and progress_pct >= self.finished_threshold:
                finished = True
                xbmc.log(f"[MONITOR] Marking as finished: {progress_pct*100:.1f}% >= {self.finished_threshold*100:.1f}%", xbmc.LOGINFO)
            
            if finished:
                self.is_finished = True
            
            xbmc.log(f"[MONITOR] Saving: {current_time:.1f}s / {self.duration:.1f}s ({progress_pct*100:.1f}%) "
                    f"finished={finished} is_final={is_final}", xbmc.LOGINFO)
            
            # Use sync_manager for all progress operations
            if is_final:
                # Final sync - use on_playback_stop for full sync handling
                self.sync_mgr.on_playback_stop(
                    self.item_id, 
                    self.episode_id, 
                    current_time, 
                    self.duration, 
                    is_finished=finished
                )
            else:
                # Periodic sync
                self.sync_mgr.on_playback_progress(
                    self.item_id, 
                    self.episode_id, 
                    current_time, 
                    self.duration, 
                    is_finished=finished
                )
            
        except Exception as e:
            xbmc.log(f"[MONITOR] Save progress error: {str(e)}", xbmc.LOGERROR)
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.is_monitoring = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)


# Re-export the functions from sync_manager for backward compatibility
# These are already imported at the top and can be used directly:
# - get_local_progress
# - save_local_progress  
# - get_best_resume_position
# - sync_all_to_server
# - mark_synced

# For explicit backward compatibility exports
__all__ = [
    'PlaybackMonitor',
    'get_local_progress',
    'save_local_progress',
    'get_best_resume_position',
    'sync_all_to_server',
    'mark_synced'
]
