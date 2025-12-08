import os
import json
import xbmc
import xbmcgui
import xbmcvfs
import requests
import threading
from datetime import datetime


class DownloadManager:
    """Manage offline downloads of audiobooks and podcasts"""
    
    def __init__(self, addon):
        self.addon = addon
        self.download_path = self._get_download_path()
        self.metadata_file = os.path.join(self.download_path, 'downloads.json')
        self.resume_file = os.path.join(self.download_path, 'resume_positions.json')
        self.downloads = self._load_metadata()
        self.resume_positions = self._load_resume_positions()
        self.active_downloads = {}
        
    def _get_download_path(self):
        """Get configured download path"""
        path = self.addon.getSetting('download_path')
        if not path:
            path = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
            path = os.path.join(path, 'downloads')
        
        if not os.path.exists(path):
            os.makedirs(path)
        
        return path
    
    def _load_metadata(self):
        """Load download metadata"""
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_metadata(self):
        """Save download metadata"""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.downloads, f, indent=2)
        except Exception as e:
            xbmc.log(f"Error saving download metadata: {str(e)}", xbmc.LOGERROR)
    
    def _load_resume_positions(self):
        """Load locally saved resume positions"""
        if os.path.exists(self.resume_file):
            try:
                with open(self.resume_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_resume_positions(self):
        """Save resume positions locally"""
        try:
            with open(self.resume_file, 'w') as f:
                json.dump(self.resume_positions, f, indent=2)
        except Exception as e:
            xbmc.log(f"Error saving resume positions: {str(e)}", xbmc.LOGERROR)
    
    def save_resume_position(self, item_id, episode_id, current_time, duration, is_finished=False):
        """Save resume position locally for offline use"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        self.resume_positions[key] = {
            'item_id': item_id,
            'episode_id': episode_id,
            'current_time': current_time,
            'duration': duration,
            'is_finished': is_finished,
            'updated_at': datetime.now().isoformat(),
            'synced': False
        }
        self._save_resume_positions()
    
    def get_local_resume_position(self, item_id, episode_id=None):
        """Get locally saved resume position"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        return self.resume_positions.get(key)
    
    def mark_position_synced(self, item_id, episode_id=None):
        """Mark a resume position as synced to server"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        if key in self.resume_positions:
            self.resume_positions[key]['synced'] = True
            self._save_resume_positions()
    
    def get_unsynced_positions(self):
        """Get all resume positions that haven't been synced"""
        return {k: v for k, v in self.resume_positions.items() if not v.get('synced', False)}
    
    def sync_positions_to_server(self, library_service):
        """Sync all unsynced resume positions to server"""
        unsynced = self.get_unsynced_positions()
        synced_count = 0
        
        for key, pos in unsynced.items():
            try:
                library_service.update_media_progress(
                    pos['item_id'],
                    pos['current_time'],
                    pos['duration'],
                    is_finished=pos.get('is_finished', False),
                    episode_id=pos.get('episode_id')
                )
                self.mark_position_synced(pos['item_id'], pos.get('episode_id'))
                synced_count += 1
            except Exception as e:
                xbmc.log(f"Error syncing position for {key}: {str(e)}", xbmc.LOGERROR)
        
        if synced_count > 0:
            xbmc.log(f"Synced {synced_count} resume positions to server", xbmc.LOGINFO)
        
        return synced_count
    
    def is_downloaded(self, item_id, episode_id=None):
        """Check if item is downloaded"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        if key not in self.downloads:
            return False
        
        download_info = self.downloads[key]
        
        # For multi-file downloads, check if all files exist
        if 'files' in download_info:
            return all(os.path.exists(f['path']) for f in download_info['files'])
        
        # Single file download
        return os.path.exists(download_info.get('file_path', ''))
    
    def get_download_path_for_item(self, item_id, episode_id=None):
        """Get local file path for downloaded item"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        if key not in self.downloads:
            return None
        
        download_info = self.downloads[key]
        
        # For multi-file, return first file (caller should use get_download_info for full list)
        if 'files' in download_info and download_info['files']:
            return download_info['files'][0]['path']
        
        return download_info.get('file_path')
    
    def get_download_info(self, item_id, episode_id=None):
        """Get download metadata"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        return self.downloads.get(key)
    
    def download_item(self, item_id, item_data, library_service, episode_id=None):
        """Download an item for offline playback"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        
        if self.is_downloaded(item_id, episode_id):
            xbmcgui.Dialog().notification('Already Downloaded', 
                                         item_data['title'], 
                                         xbmcgui.NOTIFICATION_INFO)
            return True
        
        if key in self.active_downloads:
            xbmcgui.Dialog().notification('Already Downloading', 
                                         item_data['title'], 
                                         xbmcgui.NOTIFICATION_INFO)
            return True
        
        item_folder = os.path.join(self.download_path, self._sanitize_filename(item_id))
        if not os.path.exists(item_folder):
            os.makedirs(item_folder)
        
        self.active_downloads[key] = True
        
        thread = threading.Thread(
            target=self._download_worker_with_progress,
            args=(item_id, item_data, library_service, episode_id, item_folder, key)
        )
        thread.daemon = True
        thread.start()
        
        return True
    
    def download_audiobook_complete(self, item_id, item_data, library_service):
        """Download complete audiobook with all files"""
        key = item_id
        
        if self.is_downloaded(item_id):
            xbmcgui.Dialog().notification('Already Downloaded', 
                                         item_data['title'], 
                                         xbmcgui.NOTIFICATION_INFO)
            return True
        
        if key in self.active_downloads:
            xbmcgui.Dialog().notification('Already Downloading', 
                                         item_data['title'], 
                                         xbmcgui.NOTIFICATION_INFO)
            return True
        
        item_folder = os.path.join(self.download_path, self._sanitize_filename(item_id))
        if not os.path.exists(item_folder):
            os.makedirs(item_folder)
        
        self.active_downloads[key] = True
        
        thread = threading.Thread(
            target=self._download_multifile_worker,
            args=(item_id, item_data, library_service, item_folder, key)
        )
        thread.daemon = True
        thread.start()
        
        return True
    
    def _download_multifile_worker(self, item_id, item_data, library_service, item_folder, key):
        """Download all files for a multi-file audiobook"""
        progress_dialog = None
        
        try:
            audio_files = item_data.get('audio_files', [])
            if not audio_files:
                raise ValueError("No audio files found")
            
            # Sort by index
            audio_files = sorted(audio_files, key=lambda x: x.get('index', 0))
            
            total_files = len(audio_files)
            downloaded_files = []
            
            progress_dialog = xbmcgui.DialogProgress()
            progress_dialog.create('Downloading Audiobook', f'{item_data["title"]} - 0/{total_files} files')
            
            total_size = sum(f.get('size', 0) for f in audio_files)
            downloaded_total = 0
            
            for i, audio_file in enumerate(audio_files):
                if progress_dialog.iscanceled():
                    progress_dialog.close()
                    self._cleanup_partial_download(downloaded_files)
                    del self.active_downloads[key]
                    xbmcgui.Dialog().notification('Download Cancelled', item_data['title'], xbmcgui.NOTIFICATION_INFO)
                    return
                
                ino = audio_file.get('ino')
                file_index = audio_file.get('index', i)
                file_duration = audio_file.get('duration', 0)
                file_size = audio_file.get('size', 0)
                
                # Create filename
                ext = os.path.splitext(audio_file.get('metadata', {}).get('filename', 'audio.mp3'))[1] or '.mp3'
                filename = f"{self._sanitize_filename(item_data['title'])}_{file_index:03d}{ext}"
                file_path = os.path.join(item_folder, filename)
                
                # Get download URL
                download_url = f"{library_service.base_url}/api/items/{item_id}/file/{ino}?token={library_service.token}"
                
                progress_dialog.update(
                    int((i / total_files) * 100),
                    f'Downloading file {i+1}/{total_files}'
                )
                
                # Download file
                response = requests.get(download_url, stream=True, timeout=30)
                
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            if progress_dialog.iscanceled():
                                f.close()
                                os.remove(file_path)
                                progress_dialog.close()
                                self._cleanup_partial_download(downloaded_files)
                                del self.active_downloads[key]
                                return
                            f.write(chunk)
                            downloaded_total += len(chunk)
                
                downloaded_files.append({
                    'path': file_path,
                    'ino': ino,
                    'index': file_index,
                    'duration': file_duration,
                    'size': os.path.getsize(file_path)
                })
            
            progress_dialog.close()
            
            # Download cover
            cover_path = self._download_cover(item_data.get('cover_url'), item_folder, item_data['title'])
            
            # Save metadata
            self.downloads[key] = {
                'item_id': item_id,
                'episode_id': None,
                'title': item_data['title'],
                'files': downloaded_files,
                'cover_path': cover_path,
                'duration': item_data.get('duration', 0),
                'author': item_data.get('author', ''),
                'narrator': item_data.get('narrator', ''),
                'chapters': item_data.get('chapters', []),
                'downloaded_at': datetime.now().isoformat(),
                'is_multifile': True
            }
            self._save_metadata()
            
            del self.active_downloads[key]
            
            xbmc.log(f"Download completed: {item_data['title']} ({total_files} files)", xbmc.LOGINFO)
            xbmcgui.Dialog().notification('Download Complete', item_data['title'], xbmcgui.NOTIFICATION_INFO)
            
        except Exception as e:
            if progress_dialog:
                try:
                    progress_dialog.close()
                except:
                    pass
            
            if key in self.active_downloads:
                del self.active_downloads[key]
            
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
    
    def _cleanup_partial_download(self, downloaded_files):
        """Clean up partially downloaded files"""
        for f in downloaded_files:
            try:
                if os.path.exists(f['path']):
                    os.remove(f['path'])
            except:
                pass
    
    def _download_cover(self, cover_url, item_folder, title):
        """Download cover image"""
        if not cover_url:
            return None
        
        try:
            cover_filename = f"{self._sanitize_filename(title)}_cover.jpg"
            cover_path = os.path.join(item_folder, cover_filename)
            
            if cover_url.startswith('http'):
                response = requests.get(cover_url, timeout=10)
                with open(cover_path, 'wb') as f:
                    f.write(response.content)
            else:
                import shutil
                shutil.copy(cover_url, cover_path)
            
            return cover_path
        except Exception as e:
            xbmc.log(f"Error downloading cover: {str(e)}", xbmc.LOGWARNING)
            return None
    
    def _download_worker_with_progress(self, item_id, item_data, library_service, episode_id, item_folder, key):
        """Background download worker for single file"""
        progress_dialog = None
        
        try:
            download_url = library_service.get_file_url(item_id, episode_id=episode_id)
            
            title = self._sanitize_filename(item_data['title'])
            if episode_id:
                filename = f"{title}_{episode_id}.m4b"
            else:
                filename = f"{title}.m4b"
            
            file_path = os.path.join(item_folder, filename)
            
            progress_dialog = xbmcgui.DialogProgress()
            progress_dialog.create('Downloading', item_data["title"])
            
            response = requests.get(download_url, stream=True, timeout=30)
            total_size = int(response.headers.get('content-length', 0))
            
            downloaded = 0
            last_percent = 0
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        if progress_dialog.iscanceled():
                            progress_dialog.close()
                            try:
                                os.remove(file_path)
                            except:
                                pass
                            del self.active_downloads[key]
                            xbmcgui.Dialog().notification('Download Cancelled', item_data['title'], xbmcgui.NOTIFICATION_INFO)
                            return
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            if percent != last_percent:
                                last_percent = percent
                                size_mb = downloaded / (1024 * 1024)
                                total_mb = total_size / (1024 * 1024)
                                progress_dialog.update(percent, f'{size_mb:.1f} MB / {total_mb:.1f} MB')
            
            progress_dialog.close()
            
            cover_path = self._download_cover(item_data.get('cover_url'), item_folder, item_data['title'])
            
            self.downloads[key] = {
                'item_id': item_id,
                'episode_id': episode_id,
                'title': item_data['title'],
                'file_path': file_path,
                'cover_path': cover_path,
                'duration': item_data.get('duration', 0),
                'description': item_data.get('description', ''),
                'author': item_data.get('author', ''),
                'narrator': item_data.get('narrator', ''),
                'downloaded_at': datetime.now().isoformat(),
                'file_size': os.path.getsize(file_path),
                'is_multifile': False
            }
            self._save_metadata()
            
            del self.active_downloads[key]
            
            xbmc.log(f"Download completed: {filename}", xbmc.LOGINFO)
            xbmcgui.Dialog().notification('Download Complete', item_data['title'], xbmcgui.NOTIFICATION_INFO)
            
        except Exception as e:
            if progress_dialog:
                try:
                    progress_dialog.close()
                except:
                    pass
            
            if key in self.active_downloads:
                del self.active_downloads[key]
            
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
    
    def delete_download(self, item_id, episode_id=None):
        """Delete downloaded item"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        
        if key not in self.downloads:
            return False
        
        download_info = self.downloads[key]
        
        # Delete files
        if 'files' in download_info:
            for f in download_info['files']:
                if os.path.exists(f['path']):
                    os.remove(f['path'])
        elif download_info.get('file_path') and os.path.exists(download_info['file_path']):
            os.remove(download_info['file_path'])
        
        # Delete cover
        if download_info.get('cover_path') and os.path.exists(download_info['cover_path']):
            os.remove(download_info['cover_path'])
        
        del self.downloads[key]
        self._save_metadata()
        
        xbmcgui.Dialog().notification('Download Deleted', download_info['title'], xbmcgui.NOTIFICATION_INFO)
        return True
    
    def get_all_downloads(self):
        """Get list of all downloaded items"""
        return self.downloads
    
    def _sanitize_filename(self, filename):
        """Sanitize filename for filesystem"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename[:100]  # Limit length
    
    def get_file_for_position(self, item_id, position):
        """Get the correct file and seek position for a multi-file download"""
        download_info = self.get_download_info(item_id)
        if not download_info or not download_info.get('is_multifile'):
            return None, 0
        
        files = download_info.get('files', [])
        files = sorted(files, key=lambda x: x.get('index', 0))
        
        cumulative = 0
        for f in files:
            file_duration = f.get('duration', 0)
            if cumulative <= position < cumulative + file_duration:
                seek_in_file = position - cumulative
                return f['path'], seek_in_file
            cumulative += file_duration
        
        # Position beyond all files, return last file at end
        if files:
            return files[-1]['path'], 0
        
        return None, 0


def is_network_available():
    """Check if network is available"""
    try:
        requests.get('http://www.google.com', timeout=2)
        return True
    except:
        return False
