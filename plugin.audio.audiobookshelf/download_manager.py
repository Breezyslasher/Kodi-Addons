import os
import json
import xbmc
import xbmcgui
import xbmcvfs
import requests
import threading
import subprocess
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
        
        # special:// paths translate to local folders; network paths (smb://,
        # nfs://) don't, and the file code here needs a real filesystem path.
        if path and path.strip():
            path = xbmcvfs.translatePath(path)
            if not is_local_path(path):
                xbmc.log(f"Download folder {path} is not a local path; using the add-on profile instead",
                         xbmc.LOGWARNING)
                path = ''
        if not path:
            path = os.path.join(xbmcvfs.translatePath(self.addon.getAddonInfo('profile')), 'downloads')
        
        # Only create directory if downloads are enabled
        if self.addon.getSetting('enable_downloads').lower() == 'true':
            if not os.path.exists(path):
                try:
                    os.makedirs(path)
                except:
                    pass
        
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
    
    def download_item(self, item_id, item_data, library_service, episode_id=None, show_progress=True):
        """Download an item for offline playback"""
        key = f"{item_id}_{episode_id}" if episode_id else item_id
        
        if self.is_downloaded(item_id, episode_id):
            if show_progress:
                xbmcgui.Dialog().notification('Already Downloaded', 
                                             item_data['title'], 
                                             xbmcgui.NOTIFICATION_INFO)
            return True
        
        if key in self.active_downloads:
            if show_progress:
                xbmcgui.Dialog().notification('Already Downloading', 
                                             item_data['title'], 
                                             xbmcgui.NOTIFICATION_INFO)
            return True
        
        item_folder = os.path.join(self.download_path, self._sanitize_filename(item_id))
        if not os.path.exists(item_folder):
            os.makedirs(item_folder)
        
        self.active_downloads[key] = True
        
        if show_progress:
            thread = threading.Thread(
                target=self._download_worker_with_progress,
                args=(item_id, item_data, library_service, episode_id, item_folder, key)
            )
            thread.daemon = True
            thread.start()
        else:
            # Synchronous download without dialog (for batch downloads)
            self._download_worker_silent(item_id, item_data, library_service, episode_id, item_folder, key)
        
        return True
    
    # --- helpers -------------------------------------------------------------

    _MIME_EXT = {
        'audio/mpeg': '.mp3', 'audio/mp3': '.mp3', 'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a',
        'audio/m4a': '.m4a', 'audio/m4b': '.m4b', 'audio/x-m4b': '.m4b', 'audio/aac': '.aac',
        'audio/flac': '.flac', 'audio/x-flac': '.flac', 'audio/ogg': '.ogg', 'audio/opus': '.opus',
        'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/webm': '.webm',
    }

    def _fetch_to_file(self, url, path, on_progress=None):
        """Stream url to path via a .part file; raises on HTTP errors. Returns the Content-Type."""
        part = path + '.part'
        try:
            with requests.get(url, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                total = int(response.headers.get('content-length', 0) or 0)
                done = 0
                with open(part, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                on_progress(done, total)
                content_type = (response.headers.get('content-type') or '').split(';')[0].strip().lower()
            os.replace(part, path)
            return content_type
        except Exception:
            if os.path.exists(part):
                os.remove(part)
            raise

    def _audio_ext(self, audio_file=None, content_type='', fallback='.m4b'):
        """File extension from the server's file metadata, else the Content-Type."""
        meta = (audio_file or {}).get('metadata') or {}
        ext = meta.get('ext') or os.path.splitext(meta.get('filename') or '')[1]
        if ext:
            return ext if ext.startswith('.') else '.' + ext
        return self._MIME_EXT.get(content_type, fallback)

    def _download_single(self, item_id, item_data, library_service, episode_id, item_folder, on_progress=None):
        """Download one audio file and return (file_path, cover_path)."""
        download_url = library_service.get_file_url(item_id, episode_id=episode_id)
        title = self._sanitize_filename(item_data['title'])
        base = f"{title}_{episode_id}" if episode_id else title
        tmp_path = os.path.join(item_folder, base + '.download')
        content_type = self._fetch_to_file(download_url, tmp_path, on_progress)
        audio_file = item_data.get('audio_file') or (item_data.get('audio_files') or [None])[0]
        file_path = os.path.join(item_folder, base + self._audio_ext(audio_file, content_type))
        os.replace(tmp_path, file_path)

        cover_path = self._download_cover(item_data.get('cover_url'), item_folder, item_data['title'])
        if cover_path and os.path.exists(cover_path):
            self._embed_cover_in_file(file_path, cover_path, dict(item_data, episode_id=episode_id))
        return file_path, cover_path

    def _record_single(self, key, item_id, item_data, episode_id, file_path, cover_path):
        self.downloads[key] = {
            'item_id': item_id,
            'episode_id': episode_id,
            'title': item_data['title'],
            'podcast_title': item_data.get('podcast_title', ''),
            'file_path': file_path,
            'cover_path': cover_path,
            'duration': item_data.get('duration', 0),
            'description': item_data.get('description', ''),
            'author': item_data.get('author', ''),
            'narrator': item_data.get('narrator', ''),
            # Podcast episode publish time (epoch ms) so offline auto-play
            # can order episodes chronologically, matching the online queue.
            'published_at': item_data.get('publishedAt', 0),
            'chapters': item_data.get('chapters', []),
            'downloaded_at': datetime.now().isoformat(),
            'file_size': os.path.getsize(file_path),
            'is_multifile': False
        }
        self._save_metadata()

    # --- workers -------------------------------------------------------------

    def _download_worker_silent(self, item_id, item_data, library_service, episode_id, item_folder, key):
        """Download without a progress dialog (batch downloads)"""
        try:
            file_path, cover_path = self._download_single(item_id, item_data, library_service, episode_id, item_folder)
            self._record_single(key, item_id, item_data, episode_id, file_path, cover_path)
            xbmc.log(f"Download completed: {os.path.basename(file_path)}", xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            raise
        finally:
            self.active_downloads.pop(key, None)

    def _download_worker_with_progress(self, item_id, item_data, library_service, episode_id, item_folder, key):
        """Background download worker for a single file"""
        title = item_data['title']
        shown = [0]

        def on_progress(done, total):
            if total <= 0:
                return
            pct = int(done * 100 / total)
            for mark in (75, 50, 25):
                if pct >= mark > shown[0]:
                    xbmcgui.Dialog().notification('Download Progress', f'{title} - {mark}%',
                                                  xbmcgui.NOTIFICATION_INFO, 3000)
                    shown[0] = mark
                    break

        try:
            xbmcgui.Dialog().notification('Download Started', title, xbmcgui.NOTIFICATION_INFO, 5000)
            file_path, cover_path = self._download_single(item_id, item_data, library_service, episode_id,
                                                          item_folder, on_progress)
            self._record_single(key, item_id, item_data, episode_id, file_path, cover_path)
            xbmc.log(f"Download completed: {os.path.basename(file_path)}", xbmc.LOGINFO)
            xbmcgui.Dialog().notification('Download Complete', title, xbmcgui.NOTIFICATION_INFO)
        except Exception as e:
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        finally:
            self.active_downloads.pop(key, None)

    def download_audiobook_complete(self, item_id, item_data, library_service):
        """Download a multi-file audiobook, combined into one file when ffmpeg is available"""
        key = item_id

        if self.is_downloaded(item_id):
            xbmcgui.Dialog().notification('Already Downloaded', item_data['title'], xbmcgui.NOTIFICATION_INFO)
            return True

        if key in self.active_downloads:
            xbmcgui.Dialog().notification('Already Downloading', item_data['title'], xbmcgui.NOTIFICATION_INFO)
            return True

        item_folder = os.path.join(self.download_path, self._sanitize_filename(item_id))
        if not os.path.exists(item_folder):
            os.makedirs(item_folder)

        self.active_downloads[key] = True

        thread = threading.Thread(
            target=self._download_combined_worker,
            args=(item_id, item_data, library_service, item_folder, key)
        )
        thread.daemon = True
        thread.start()

        return True

    def _download_parts(self, item_id, item_data, library_service, item_folder):
        """Download every audio file of a book. Returns the list of file records."""
        audio_files = sorted(item_data.get('audio_files', []), key=lambda x: x.get('index', 0))
        if not audio_files:
            raise ValueError("No audio files found")
        title = self._sanitize_filename(item_data['title'])
        parts = []
        for i, audio_file in enumerate(audio_files):
            ino = audio_file.get('ino')
            file_index = audio_file.get('index', i)
            if len(audio_files) > 1:
                xbmcgui.Dialog().notification('Download Progress',
                                              f'{item_data["title"]} - File {i+1}/{len(audio_files)}',
                                              xbmcgui.NOTIFICATION_INFO, 2000)
            url = f"{library_service.base_url}/api/items/{item_id}/file/{ino}?token={library_service.token}"
            tmp_path = os.path.join(item_folder, f"{title}_{file_index:03d}.download")
            content_type = self._fetch_to_file(url, tmp_path)
            path = os.path.join(item_folder, f"{title}_{file_index:03d}{self._audio_ext(audio_file, content_type, '.mp3')}")
            os.replace(tmp_path, path)
            parts.append({
                'path': path,
                'ino': ino,
                'index': file_index,
                'duration': audio_file.get('duration', 0),
                'size': os.path.getsize(path)
            })
        return parts

    def _download_combined_worker(self, item_id, item_data, library_service, item_folder, key):
        """Download all parts and combine them; if that fails, keep them as a multi-file book."""
        parts = []
        try:
            xbmcgui.Dialog().notification('Download Started', f'Preparing {item_data["title"]}',
                                          xbmcgui.NOTIFICATION_INFO, 5000)
            parts = self._download_parts(item_id, item_data, library_service, item_folder)
            cover_path = self._download_cover(item_data.get('cover_url'), item_folder, item_data['title'])
            chapters = item_data.get('chapters', [])

            combined_path = os.path.join(item_folder, f"{self._sanitize_filename(item_data['title'])}.m4b")
            if len(parts) > 1:
                xbmcgui.Dialog().notification('Download Progress', f'{item_data["title"]} - Combining files...',
                                              xbmcgui.NOTIFICATION_INFO, 3000)
            combined = len(parts) > 1 and self._combine_audio_files(parts, combined_path, chapters,
                                                                    dict(item_data, cover_path=cover_path))
            if combined:
                self._cleanup_temp_files(parts)
                self.downloads[key] = {
                    'item_id': item_id,
                    'episode_id': None,
                    'title': item_data['title'],
                    'file_path': combined_path,
                    'cover_path': cover_path,
                    'duration': item_data.get('duration', 0),
                    'author': item_data.get('author', ''),
                    'narrator': item_data.get('narrator', ''),
                    'chapters': chapters,
                    'downloaded_at': datetime.now().isoformat(),
                    'file_size': os.path.getsize(combined_path),
                    'is_multifile': False
                }
            else:
                self.downloads[key] = {
                    'item_id': item_id,
                    'episode_id': None,
                    'title': item_data['title'],
                    'files': parts,
                    'cover_path': cover_path,
                    'duration': item_data.get('duration', 0),
                    'author': item_data.get('author', ''),
                    'narrator': item_data.get('narrator', ''),
                    'chapters': chapters,
                    'downloaded_at': datetime.now().isoformat(),
                    'is_multifile': True
                }
            self._save_metadata()

            xbmc.log(f"Download completed: {item_data['title']} ({'combined' if combined else f'{len(parts)} files'})",
                     xbmc.LOGINFO)
            xbmcgui.Dialog().notification('Download Complete', item_data['title'], xbmcgui.NOTIFICATION_INFO)

        except Exception as e:
            self._cleanup_temp_files(parts)
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        finally:
            self.active_downloads.pop(key, None)

    def _download_multifile_worker(self, item_id, item_data, library_service, item_folder, key):
        """Download all files for a multi-file audiobook without combining (legacy)"""
        try:
            parts = self._download_parts(item_id, item_data, library_service, item_folder)
            cover_path = self._download_cover(item_data.get('cover_url'), item_folder, item_data['title'])
            self.downloads[key] = {
                'item_id': item_id,
                'episode_id': None,
                'title': item_data['title'],
                'files': parts,
                'cover_path': cover_path,
                'duration': item_data.get('duration', 0),
                'author': item_data.get('author', ''),
                'narrator': item_data.get('narrator', ''),
                'chapters': item_data.get('chapters', []),
                'downloaded_at': datetime.now().isoformat(),
                'is_multifile': True
            }
            self._save_metadata()
            xbmcgui.Dialog().notification('Download Complete', item_data['title'], xbmcgui.NOTIFICATION_INFO)
        except Exception as e:
            xbmc.log(f"Download error: {str(e)}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification('Download Failed', str(e)[:50], xbmcgui.NOTIFICATION_ERROR)
        finally:
            self.active_downloads.pop(key, None)

    # --- ffmpeg --------------------------------------------------------------

    def _combine_audio_files(self, parts, output_path, chapters, item_data):
        """Join parts into one M4B with chapters and cover; True on success.
        Tries a stream copy first, then re-encodes."""
        list_file = output_path + '.list.txt'
        metadata_file = self._create_chapter_metadata(chapters, output_path) if chapters else None
        cover_path = item_data.get('cover_path')
        try:
            with open(list_file, 'w', encoding='utf-8') as f:
                for part in sorted(parts, key=lambda x: x['index']):
                    f.write("file '%s'\n" % part['path'].replace("'", "'\\''"))

            inputs = ['-f', 'concat', '-safe', '0', '-i', list_file]
            maps = ['-map', '0:a']
            next_input = 1
            if metadata_file:
                inputs += ['-i', metadata_file]
                maps += ['-map_metadata', str(next_input), '-map_chapters', str(next_input)]
                next_input += 1
            if cover_path and os.path.exists(cover_path):
                inputs += ['-i', cover_path]
                maps += ['-map', f'{next_input}:v', '-c:v', 'mjpeg', '-disposition:v:0', 'attached_pic']
            tags = [
                '-metadata', f'title={item_data.get("title", "")}',
                '-metadata', f'artist={item_data.get("author", "")}',
                '-metadata', f'album={item_data.get("title", "")}',
                '-metadata', 'genre=Audiobook',
            ]

            for codec in (['-c:a', 'copy'], ['-c:a', 'aac', '-b:a', '128k']):
                cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error'] + inputs + maps + codec + tags + \
                      ['-f', 'mp4', output_path]
                try:
                    # Re-encoding a long book takes a while; allow up to 6 hours.
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
                except FileNotFoundError:
                    xbmc.log("ffmpeg not found; keeping parts as separate files", xbmc.LOGINFO)
                    return False
                if result.returncode == 0 and os.path.exists(output_path):
                    xbmc.log(f"Combined audio files with ffmpeg ({codec[1]})", xbmc.LOGINFO)
                    return True
                xbmc.log(f"ffmpeg combine ({codec[1]}) failed: {result.stderr[-500:]}", xbmc.LOGWARNING)
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
        except Exception as e:
            xbmc.log(f"Combining failed: {e}", xbmc.LOGWARNING)
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
        finally:
            for path in (list_file, metadata_file):
                if path and os.path.exists(path):
                    os.remove(path)

    def _create_chapter_metadata(self, chapters, output_path):
        """Create FFmpeg metadata file for chapters"""
        try:
            metadata_path = os.path.splitext(output_path)[0] + '_metadata.txt'

            with open(metadata_path, 'w', encoding='utf-8') as f:
                f.write(';FFMETADATA1\n')

                for i, chapter in enumerate(chapters):
                    start_ms = int(chapter.get('start', 0) * 1000)
                    end_ms = int(chapter.get('end', 0) * 1000)
                    title = chapter.get('title', f'Chapter {i+1}')
                    # Escape characters that are special in FFMETADATA
                    for ch in ('\\', '=', ';', '#', '\n'):
                        title = title.replace(ch, '\\' + ch)

                    f.write('[CHAPTER]\n')
                    f.write('TIMEBASE=1/1000\n')
                    f.write(f'START={start_ms}\n')
                    f.write(f'END={end_ms}\n')
                    f.write(f'title={title}\n')

            return metadata_path

        except Exception as e:
            xbmc.log(f"Failed to create chapter metadata: {str(e)}", xbmc.LOGERROR)
            return None

    def _embed_cover_in_file(self, audio_path, cover_path, item_data):
        """Embed cover image into audio file using FFmpeg"""
        root, ext = os.path.splitext(audio_path)
        temp_output = f"{root}_temp{ext}"
        try:
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', audio_path,
                '-i', cover_path,
                '-map', '0:a',
                '-map', '1:v',
                '-c:a', 'copy',
                '-c:v', 'mjpeg',
                '-disposition:v:0', 'attached_pic',
                '-metadata', f'title={item_data.get("title", "")}',
                '-metadata', f'artist={item_data.get("author", "")}',
                '-metadata', f'album={item_data.get("title", "")}',
                '-metadata', 'genre=Podcast' if item_data.get('episode_id') else 'genre=Audiobook',
                temp_output,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                os.replace(temp_output, audio_path)
                xbmc.log(f"Embedded cover in {os.path.basename(audio_path)}", xbmc.LOGINFO)
            else:
                xbmc.log(f"Cover embedding failed: {result.stderr[-300:]}", xbmc.LOGWARNING)

        except FileNotFoundError:
            pass  # No ffmpeg on this system; the cover is still kept next to the file
        except Exception as e:
            xbmc.log(f"Cover embedding error: {str(e)}", xbmc.LOGWARNING)
        finally:
            if os.path.exists(temp_output):
                os.remove(temp_output)

    def _cleanup_temp_files(self, temp_files):
        """Clean up temporary files"""
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file['path']):
                    os.remove(temp_file['path'])
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
                response = requests.get(cover_url, timeout=(5, 30))
                response.raise_for_status()
                with open(cover_path, 'wb') as f:
                    f.write(response.content)
            else:
                import shutil
                shutil.copy(cover_url, cover_path)

            return cover_path
        except Exception as e:
            xbmc.log(f"Error downloading cover: {str(e)}", xbmc.LOGWARNING)
            return None

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
    
    def delete_all_downloads(self):
        """Delete all downloaded items"""
        deleted_count = 0
        failed_count = 0
        
        for key in list(self.downloads.keys()):
            try:
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
                
                # Delete item folder if empty
                item_folder = os.path.dirname(download_info.get('file_path') or 
                                             (download_info.get('files', [{}])[0].get('path') if download_info.get('files') else ''))
                if item_folder and os.path.exists(item_folder):
                    try:
                        if not os.listdir(item_folder):  # Folder is empty
                            os.rmdir(item_folder)
                    except:
                        pass
                
                deleted_count += 1
            except Exception as e:
                xbmc.log(f"Error deleting download {key}: {str(e)}", xbmc.LOGERROR)
                failed_count += 1
        
        # Clear metadata
        self.downloads.clear()
        self._save_metadata()
        
        # Clear resume positions
        self.resume_positions.clear()
        self._save_resume_positions()
        
        return deleted_count, failed_count
    
    def _sanitize_filename(self, filename):
        """Sanitize filename for filesystem"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename[:100]  # Limit length
    
    def get_file_for_position(self, item_id, position):
        """
        Get the correct file and seek position for a download.
        
        Returns:
            (file_path, seek_in_file, file_offset)
            - file_path: Path to the file containing this position
            - seek_in_file: Position to seek to within this file
            - file_offset: Where this file starts in the overall audiobook timeline
        """
        download_info = self.get_download_info(item_id)
        if not download_info:
            return None, 0, 0
        
        # For combined single files, just return the file and position
        if not download_info.get('is_multifile'):
            file_path = download_info.get('file_path')
            return file_path, position, 0
        
        # Legacy multi-file handling
        files = download_info.get('files', [])
        files = sorted(files, key=lambda x: x.get('index', 0))
        
        cumulative = 0
        for f in files:
            file_duration = f.get('duration', 0)
            if cumulative <= position < cumulative + file_duration:
                seek_in_file = position - cumulative
                file_offset = cumulative  # Where this file starts
                return f['path'], seek_in_file, file_offset
            cumulative += file_duration
        
        # Position beyond all files, return last file at end
        if files:
            last_file = files[-1]
            last_offset = cumulative - last_file.get('duration', 0)
            return last_file['path'], 0, last_offset
        
        return None, 0, 0


def is_local_path(path):
    """False for Kodi VFS URLs such as smb:// or nfs:// that os functions can't open."""
    return bool(path) and '://' not in path


def is_network_available(server_url):
    """True when the Audiobookshelf server answers (any HTTP response counts)."""
    try:
        requests.get(f"{server_url}/ping", timeout=(3, 5))
        return True
    except requests.exceptions.RequestException:
        return False
