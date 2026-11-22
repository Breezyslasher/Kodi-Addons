import xbmc
import xbmcvfs
import xbmcaddon
import xbmcgui
import time
import threading
import os
import json
from datetime import datetime


# Constants
PROGRESS_THRESHOLD = 5  # Minimum seconds difference to trigger sync
FINISHED_THRESHOLD_DEFAULT = 0.95


def reached_finish(position, duration, threshold=FINISHED_THRESHOLD_DEFAULT):
    """True when position counts as finished. threshold <= 1 is a fraction of the
    duration; a larger value is a number of seconds before the end."""
    if not duration or duration <= 1:
        return False
    if threshold > 1:
        return position >= duration - threshold
    return position / duration >= threshold


class SyncManager:
    """
    Unified sync manager for progress between local storage and server.
    Handles bidirectional sync for both audiobooks and podcasts.
    Singleton pattern ensures consistent state across the plugin.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern to ensure only one sync manager exists"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.addon = xbmcaddon.Addon()
        self.profile_path = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
        
        if not os.path.exists(self.profile_path):
            os.makedirs(self.profile_path)
        
        # Single unified progress file
        self.progress_file = os.path.join(self.profile_path, 'progress_unified.json')
        self.sync_state_file = os.path.join(self.profile_path, 'sync_state.json')
        
        self._progress_data = self._load_progress()
        self._sync_state = self._load_sync_state()
        self._library_service = None
        self._is_online = False
        self._dirty = set()  # Keys changed by this process, merged into the file on save
        
        # Migrate from old files
        self._migrate_old_progress()
        
        xbmc.log("[SYNC_MGR] Initialized v2.1.0", xbmc.LOGINFO)
    
    def _load_progress(self):
        """Load all progress data from unified file"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    xbmc.log(f"[SYNC_MGR] Loaded {len(data)} progress entries", xbmc.LOGDEBUG)
                    return data
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Error loading progress: {e}", xbmc.LOGERROR)
        return {}
    
    def _save_progress(self):
        """Write only the keys this process changed, so other processes' progress isn't lost."""
        try:
            with self._lock:
                data = self._load_progress()
                for key in self._dirty:
                    if key in self._progress_data:
                        data[key] = self._progress_data[key]
                self._dirty.clear()
                tmp = f"{self.progress_file}.{os.getpid()}.tmp"
                with open(tmp, 'w') as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, self.progress_file)
                self._progress_data = data
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Error saving progress: {e}", xbmc.LOGERROR)
    
    def _load_sync_state(self):
        """Load sync state (last sync times, etc.)"""
        try:
            if os.path.exists(self.sync_state_file):
                with open(self.sync_state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Error loading sync state: {e}", xbmc.LOGERROR)
        return {
            'last_full_sync': 0,
            'last_server_poll': 0,
            'was_offline': False,
            'known_items': []  # List of item_ids we know about
        }
    
    def _save_sync_state(self):
        """Save sync state"""
        try:
            with open(self.sync_state_file, 'w') as f:
                json.dump(self._sync_state, f, indent=2)
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Error saving sync state: {e}", xbmc.LOGERROR)
    
    def _migrate_old_progress(self):
        """Migrate progress from old format files"""
        migrated = 0
        
        # List of old files to migrate from
        old_files = [
            ('progress.json', self._migrate_progress_json),
            ('unified_progress.json', self._migrate_unified_json),
            (os.path.join('downloads', 'resume_positions.json'), self._migrate_resume_positions)
        ]
        
        for filename, migrate_func in old_files:
            old_path = os.path.join(self.profile_path, filename)
            if os.path.exists(old_path):
                try:
                    count = migrate_func(old_path)
                    migrated += count
                    if count > 0:
                        # Rename old file
                        backup_path = old_path + '.migrated.' + str(int(time.time()))
                        os.rename(old_path, backup_path)
                        xbmc.log(f"[SYNC_MGR] Migrated {count} entries from {filename}", xbmc.LOGINFO)
                except Exception as e:
                    xbmc.log(f"[SYNC_MGR] Error migrating {filename}: {e}", xbmc.LOGERROR)
        
        if migrated > 0:
            self._dirty.update(self._progress_data.keys())
            self._save_progress()
            xbmc.log(f"[SYNC_MGR] Total migrated: {migrated} entries", xbmc.LOGINFO)
    
    def _migrate_progress_json(self, filepath):
        """Migrate from old progress.json format"""
        count = 0
        with open(filepath, 'r') as f:
            old_data = json.load(f)
        
        for key, data in old_data.items():
            if key not in self._progress_data or self._progress_data[key].get('updated_at', 0) < data.get('updated_at', 0):
                self._progress_data[key] = {
                    'item_id': data.get('item_id', key.split('_')[0]),
                    'episode_id': data.get('episode_id'),
                    'current_time': data.get('current_time', 0),
                    'duration': data.get('duration', 0),
                    'progress': data.get('progress', 0),
                    'is_finished': data.get('is_finished', False),
                    'updated_at': data.get('updated_at', time.time()),
                    'needs_upload': data.get('needs_sync', True),
                    'server_time': 0,
                    'last_synced': 0
                }
                count += 1
        return count
    
    def _migrate_unified_json(self, filepath):
        """Migrate from unified_progress.json format"""
        count = 0
        with open(filepath, 'r') as f:
            old_data = json.load(f)
        
        for key, data in old_data.items():
            if key not in self._progress_data or self._progress_data[key].get('updated_at', 0) < data.get('updated_at', 0):
                self._progress_data[key] = {
                    'item_id': data.get('item_id', key.split('_')[0]),
                    'episode_id': data.get('episode_id'),
                    'current_time': data.get('current_time', 0),
                    'duration': data.get('duration', 0),
                    'progress': data.get('progress', 0),
                    'is_finished': data.get('is_finished', False),
                    'updated_at': data.get('updated_at', time.time()),
                    'needs_upload': data.get('needs_upload', True),
                    'server_time': data.get('server_time', 0),
                    'last_synced': data.get('last_synced_time', 0)
                }
                count += 1
        return count
    
    def _migrate_resume_positions(self, filepath):
        """Migrate from download_manager's resume_positions.json"""
        count = 0
        with open(filepath, 'r') as f:
            old_data = json.load(f)
        
        for key, data in old_data.items():
            if key not in self._progress_data:
                duration = data.get('duration', 0)
                current_time = data.get('current_time', 0)
                self._progress_data[key] = {
                    'item_id': data.get('item_id', key.split('_')[0]),
                    'episode_id': data.get('episode_id'),
                    'current_time': current_time,
                    'duration': duration,
                    'progress': current_time / duration if duration > 0 else 0,
                    'is_finished': data.get('is_finished', False),
                    'updated_at': time.time(),
                    'needs_upload': not data.get('synced', False),
                    'server_time': 0,
                    'last_synced': 0
                }
                count += 1
        return count
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    @staticmethod
    def get_progress_key(item_id, episode_id=None):
        """Generate a unique key for progress lookup"""
        if episode_id:
            return f"{item_id}_{episode_id}"
        return item_id
    
    def set_library_service(self, library_service):
        """Set the library service for server communication"""
        was_offline = self._library_service is None
        self._library_service = library_service
        self._is_online = library_service is not None
        
        if library_service and was_offline:
            xbmc.log("[SYNC_MGR] Library service connected - going online", xbmc.LOGINFO)

            if self._sync_state.get('was_offline', False):
                xbmc.log("[SYNC_MGR] Network reconnected - syncing", xbmc.LOGINFO)
                self._sync_state['was_offline'] = False
                self._save_sync_state()
                self._sync_on_reconnect_async()
            elif self.get_pending_uploads():
                self.sync_all_pending_uploads()
    
    def mark_offline(self):
        """Mark that we're going offline (e.g., entering download mode)"""
        self._sync_state['was_offline'] = True
        self._save_sync_state()
        self._library_service = None
        self._is_online = False
        xbmc.log("[SYNC_MGR] Marked as offline", xbmc.LOGINFO)
    
    def is_online(self):
        """Check if we have a valid library service"""
        return self._is_online and self._library_service is not None
    
    # =========================================================================
    # LOCAL PROGRESS OPERATIONS
    # =========================================================================
    
    def get_local_progress(self, item_id, episode_id=None):
        """Get local progress for an item"""
        key = self.get_progress_key(item_id, episode_id)
        return self._progress_data.get(key)
    
    def save_local_progress(self, item_id, episode_id, current_time, duration,
                           is_finished=False, needs_upload=True, from_server=False, updated_at=None):
        """
        Save progress locally.
        
        Args:
            item_id: Library item ID
            episode_id: Episode ID (for podcasts)
            current_time: Current playback position in seconds
            duration: Total duration in seconds
            is_finished: Whether the item is finished
            needs_upload: Whether this needs to be synced to server
            from_server: If True, this data came from server (don't mark for upload)
            updated_at: When the change happened (defaults to now)
        """
        key = self.get_progress_key(item_id, episode_id)
        
        progress_pct = current_time / duration if duration > 0 else 0
        
        existing = self._progress_data.get(key, {})
        
        self._progress_data[key] = {
            'item_id': item_id,
            'episode_id': episode_id,
            'current_time': current_time,
            'duration': duration,
            'progress': progress_pct,
            'is_finished': is_finished,
            'updated_at': updated_at or time.time(),
            'needs_upload': needs_upload and not from_server,
            'server_time': existing.get('server_time', 0) if not from_server else current_time,
            'last_synced': time.time() if from_server else existing.get('last_synced', 0)
        }
        self._dirty.add(key)
        self._save_progress()
        
        # Add to known items
        if item_id not in self._sync_state.get('known_items', []):
            if 'known_items' not in self._sync_state:
                self._sync_state['known_items'] = []
            self._sync_state['known_items'].append(item_id)
            self._save_sync_state()
        
        xbmc.log(f"[SYNC_MGR] Saved local: {key} = {current_time:.1f}s / {duration:.1f}s "
                f"({progress_pct*100:.1f}%) finished={is_finished} needs_upload={needs_upload and not from_server}", 
                xbmc.LOGINFO)
    
    def mark_uploaded(self, item_id, episode_id=None):
        """Mark progress as uploaded to server"""
        key = self.get_progress_key(item_id, episode_id)
        if key in self._progress_data:
            self._progress_data[key]['needs_upload'] = False
            self._progress_data[key]['last_synced'] = time.time()
            self._progress_data[key]['server_time'] = self._progress_data[key]['current_time']
            self._dirty.add(key)
            self._save_progress()
            xbmc.log(f"[SYNC_MGR] Marked uploaded: {key}", xbmc.LOGINFO)
    
    def get_pending_uploads(self):
        """Get all progress entries that need to be uploaded to server"""
        return {k: v for k, v in self._progress_data.items() 
                if v.get('needs_upload', False)}
    
    # =========================================================================
    # SERVER PROGRESS OPERATIONS
    # =========================================================================
    
    def get_server_progress(self, item_id, episode_id=None):
        """Get progress from server"""
        if not self._library_service:
            return None
        
        try:
            progress = self._library_service.get_media_progress(item_id, episode_id)
            if progress:
                xbmc.log(f"[SYNC_MGR] Server progress for {item_id}/{episode_id}: "
                        f"time={progress.get('currentTime', 0):.1f}s "
                        f"duration={progress.get('duration', 0):.1f}s "
                        f"finished={progress.get('isFinished', False)}", xbmc.LOGINFO)
            return progress
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Error getting server progress: {e}", xbmc.LOGDEBUG)
            return None
    
    def upload_progress_to_server(self, item_id, episode_id, current_time, duration, is_finished=False):
        """Upload progress to server"""
        if not self._library_service:
            xbmc.log("[SYNC_MGR] No library service - cannot upload", xbmc.LOGWARNING)
            return False
        
        try:
            xbmc.log(f"[SYNC_MGR] Uploading to server: {item_id}/{episode_id} = "
                    f"{current_time:.1f}s / {duration:.1f}s finished={is_finished}", xbmc.LOGINFO)
            
            result = self._library_service.update_media_progress(
                item_id, current_time, duration, 
                is_finished=is_finished, 
                episode_id=episode_id
            )
            
            if result is not None:
                self.mark_uploaded(item_id, episode_id)
                xbmc.log(f"[SYNC_MGR] Upload successful: {item_id}/{episode_id}", xbmc.LOGINFO)
                return True
            else:
                xbmc.log(f"[SYNC_MGR] Upload returned None: {item_id}/{episode_id}", xbmc.LOGWARNING)
                return False
                
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Upload error: {e}", xbmc.LOGERROR)
            return False
    
    # =========================================================================
    # BIDIRECTIONAL SYNC OPERATIONS
    # =========================================================================
    
    def _resolve(self, local, server):
        """'pull' (server wins), 'push' (local wins) or None. Already-uploaded local
        data never beats the server; otherwise the most recent change wins."""
        if not server:
            return 'push' if local and local.get('needs_upload') else None
        if not local:
            return 'pull'
        same = (bool(server.get('isFinished')) == bool(local.get('is_finished')) and
                abs((server.get('currentTime') or 0) - (local.get('current_time') or 0)) <= PROGRESS_THRESHOLD)
        if same:
            return None
        if not local.get('needs_upload'):
            return 'pull'
        server_updated = (server.get('lastUpdate') or 0) / 1000.0
        return 'push' if (local.get('updated_at') or 0) >= server_updated else 'pull'

    def _sync_item(self, item_id, episode_id, finished_threshold):
        """Sync one item both ways. Returns (local_state, pulled, pushed)."""
        local = self.get_local_progress(item_id, episode_id)
        server = self.get_server_progress(item_id, episode_id) if self._library_service else None
        duration = max((local or {}).get('duration') or 0, (server or {}).get('duration') or 0)
        action = self._resolve(local, server)
        key = self.get_progress_key(item_id, episode_id)
        xbmc.log(f"[SYNC_MGR] {key}: local={local and local.get('current_time')} "
                 f"server={server and server.get('currentTime')} -> {action}", xbmc.LOGINFO)

        if action == 'pull':
            server_time = server.get('currentTime') or 0
            finished = bool(server.get('isFinished')) or reached_finish(server_time, duration, finished_threshold)
            self.save_local_progress(item_id, episode_id, server_time, duration, is_finished=finished,
                                     needs_upload=False, from_server=True,
                                     updated_at=(server.get('lastUpdate') or 0) / 1000.0 or None)
            return self.get_local_progress(item_id, episode_id), True, False
        if action == 'push':
            pushed = self.upload_progress_to_server(item_id, episode_id, local.get('current_time', 0),
                                                    duration, local.get('is_finished', False))
            return local, False, pushed
        if local and local.get('needs_upload') and server:
            self.mark_uploaded(item_id, episode_id)  # Already matches the server
        if local:
            return local, False, False
        return None, False, False

    def get_best_resume_position(self, item_id, episode_id=None, finished_threshold=None):
        """
        Get the resume position after syncing local and server progress.
        Returns: (position, is_finished, duration)
        """
        if finished_threshold is None:
            finished_threshold = FINISHED_THRESHOLD_DEFAULT
        state, _, _ = self._sync_item(item_id, episode_id, finished_threshold)
        if not state:
            return 0, False, 0
        duration = state.get('duration') or 0
        if state.get('is_finished'):
            return 0, True, duration
        return state.get('current_time') or 0, False, duration

    def sync_item_bidirectional(self, item_id, episode_id=None, finished_threshold=None):
        """
        Perform bidirectional sync for a single item.
        Returns: (synced_from_server, uploaded_to_server)
        """
        if finished_threshold is None:
            finished_threshold = FINISHED_THRESHOLD_DEFAULT
        _, pulled, pushed = self._sync_item(item_id, episode_id, finished_threshold)
        return pulled, pushed

    def sync_all_pending_uploads(self):
        """Upload all pending progress to server"""
        if not self._library_service:
            xbmc.log("[SYNC_MGR] No library service - cannot sync uploads", xbmc.LOGWARNING)
            return 0
        
        pending = self.get_pending_uploads()
        uploaded = 0
        
        for key, data in pending.items():
            try:
                if self.upload_progress_to_server(
                    data['item_id'],
                    data.get('episode_id'),
                    data['current_time'],
                    data['duration'],
                    data.get('is_finished', False)
                ):
                    uploaded += 1
            except Exception as e:
                xbmc.log(f"[SYNC_MGR] Error uploading {key}: {e}", xbmc.LOGERROR)
        
        xbmc.log(f"[SYNC_MGR] Uploaded {uploaded}/{len(pending)} pending items", xbmc.LOGINFO)
        return uploaded
    
    def _sync_on_reconnect_async(self):
        """Async handler for reconnection sync"""
        try:
            self.sync_on_reconnect()
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Reconnect sync error: {e}", xbmc.LOGERROR)
    
    def sync_on_reconnect(self):
        """
        Sync when network reconnects.
        Upload local progress, then check server for newer progress.
        """
        if not self._library_service:
            return 0, 0
        
        xbmc.log("[SYNC_MGR] Starting reconnection sync", xbmc.LOGINFO)
        self._prefetch_server_progress()

        # First, upload all pending local progress
        uploaded = self.sync_all_pending_uploads()
        
        # Then check each local item against server
        downloaded = 0
        for key, local_data in list(self._progress_data.items()):
            item_id = local_data.get('item_id')
            episode_id = local_data.get('episode_id')
            
            if not item_id:
                continue
            
            try:
                synced, _ = self.sync_item_bidirectional(item_id, episode_id)
                if synced:
                    downloaded += 1
            except Exception as e:
                xbmc.log(f"[SYNC_MGR] Error syncing {key}: {e}", xbmc.LOGERROR)
        
        self._sync_state['last_full_sync'] = time.time()
        self._save_sync_state()
        
        xbmc.log(f"[SYNC_MGR] Reconnection sync complete: {uploaded} uploaded, {downloaded} downloaded", 
                xbmc.LOGINFO)
        
        # Show notification if anything was synced
        total = uploaded + downloaded
        if total > 0:
            try:
                xbmcgui.Dialog().notification(
                    'Sync Complete',
                    f'{total} items synced',
                    xbmcgui.NOTIFICATION_INFO,
                    2000
                )
            except:
                pass
        
        return uploaded, downloaded
    
    def startup_sync(self):
        """
        Perform sync on addon startup.
        First uploads pending, then bidirectionally syncs all known items.
        """
        if not self._library_service:
            xbmc.log("[SYNC_MGR] No library service for startup sync", xbmc.LOGINFO)
            return 0, 0
        
        xbmc.log("[SYNC_MGR] Starting startup sync", xbmc.LOGINFO)
        self._prefetch_server_progress()
        
        uploaded = 0
        downloaded = 0
        
        # First sync all pending uploads
        uploaded = self.sync_all_pending_uploads()
        
        # Sync all local items bidirectionally
        for key, local_data in list(self._progress_data.items()):
            item_id = local_data.get('item_id')
            episode_id = local_data.get('episode_id')
            
            if not item_id:
                continue
            
            try:
                synced_from_server, uploaded_to_server = self.sync_item_bidirectional(
                    item_id, episode_id
                )
                if synced_from_server:
                    downloaded += 1
                # uploaded already counted
            except Exception as e:
                xbmc.log(f"[SYNC_MGR] Startup sync error for {key}: {e}", xbmc.LOGERROR)
        
        self._sync_state['last_full_sync'] = time.time()
        self._save_sync_state()
        
        xbmc.log(f"[SYNC_MGR] Startup sync complete: {uploaded} uploaded, {downloaded} downloaded", 
                xbmc.LOGINFO)
        
        return uploaded, downloaded
    
    def _prefetch_server_progress(self):
        """Load all server progress in one request before syncing many items."""
        try:
            if self._library_service and hasattr(self._library_service, 'prefetch_progress'):
                self._library_service.prefetch_progress()
        except Exception as e:
            xbmc.log(f"[SYNC_MGR] Progress prefetch failed: {e}", xbmc.LOGDEBUG)

    # =========================================================================
    # PLAYBACK INTEGRATION
    # =========================================================================
    
    def on_playback_start(self, item_id, episode_id=None, duration=0):
        """
        Called when playback starts - sync with server and get best position.
        Returns: (position, is_finished, duration)
        """
        xbmc.log(f"[SYNC_MGR] Playback starting: {item_id}/{episode_id}", xbmc.LOGINFO)
        
        # Get best position (this will sync if needed)
        position, is_finished, server_duration = self.get_best_resume_position(
            item_id, episode_id
        )
        
        # Use server duration if we don't have one
        if duration == 0 and server_duration > 0:
            duration = server_duration
        
        return position, is_finished, duration
    
    def on_playback_progress(self, item_id, episode_id, current_time, duration, is_finished=False):
        """Called periodically during playback"""
        # Save locally (mark for upload)
        self.save_local_progress(item_id, episode_id, current_time, duration, 
                                is_finished=is_finished, needs_upload=True)
        
        # Upload to server if connected
        if self._library_service:
            self.upload_progress_to_server(item_id, episode_id, current_time, duration, is_finished)
    
    def on_playback_stop(self, item_id, episode_id, current_time, duration, is_finished=False):
        """Called when playback stops - final sync"""
        xbmc.log(f"[SYNC_MGR] Playback stopped: {item_id}/{episode_id} at {current_time:.1f}s finished={is_finished}", xbmc.LOGINFO)
        
        # Save locally
        self.save_local_progress(item_id, episode_id, current_time, duration,
                                is_finished=is_finished, needs_upload=True)
        
        # Upload to server if connected
        if self._library_service:
            self.upload_progress_to_server(item_id, episode_id, current_time, duration, is_finished)


# =========================================================================
# GLOBAL INSTANCE AND CONVENIENCE FUNCTIONS
# =========================================================================

_sync_manager = None


def get_sync_manager():
    """Get the global sync manager instance"""
    global _sync_manager
    if _sync_manager is None:
        _sync_manager = SyncManager()
    return _sync_manager


def get_local_progress(item_id, episode_id=None):
    """Get local progress for an item"""
    return get_sync_manager().get_local_progress(item_id, episode_id)


def save_local_progress(item_id, episode_id, current_time, duration, is_finished=False):
    """Save progress locally"""
    get_sync_manager().save_local_progress(item_id, episode_id, current_time, duration, 
                                           is_finished=is_finished, needs_upload=True)


def get_best_resume_position(library_service, item_id, episode_id=None, finished_threshold=0.95):
    """
    Get the best resume position from local and server.
    Updates sync manager with library service.
    Returns: (position, is_finished, duration)
    """
    sync_mgr = get_sync_manager()
    sync_mgr.set_library_service(library_service)
    return sync_mgr.get_best_resume_position(item_id, episode_id, finished_threshold)


def sync_all_to_server(library_service):
    """Sync all pending progress to server"""
    sync_mgr = get_sync_manager()
    sync_mgr.set_library_service(library_service)
    return sync_mgr.sync_all_pending_uploads()


def mark_synced(item_id, episode_id=None):
    """Mark progress as synced"""
    get_sync_manager().mark_uploaded(item_id, episode_id)


def startup_sync(library_service):
    """Perform startup sync"""
    sync_mgr = get_sync_manager()
    sync_mgr.set_library_service(library_service)
    return sync_mgr.startup_sync()


def on_network_reconnect(library_service):
    """Handle network reconnection"""
    sync_mgr = get_sync_manager()
    sync_mgr.set_library_service(library_service)
    return sync_mgr.sync_on_reconnect()


def mark_offline():
    """Mark that we're going offline"""
    get_sync_manager().mark_offline()

