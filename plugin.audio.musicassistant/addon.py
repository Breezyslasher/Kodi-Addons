"""
Music Assistant Kodi Add-on
Browse and play music from your Music Assistant 2.7+ server.
"""

import sys
import os
import subprocess
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

# Add lib directory to path
ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_VERSION = ADDON.getAddonInfo('version')

sys.path.insert(0, os.path.join(ADDON_PATH, 'resources', 'lib'))

from ma_client import (
    MusicAssistantClient, MusicAssistantError, CannotConnect,
    AuthenticationRequired, AuthenticationFailed, LoginFailed,
    MediaType, QueueOption, ImageType,
    login_with_token, get_server_info
)

# Global sendspin process reference
_sendspin_process = None
_sendspin_lock = threading.Lock()


def log(message, level=xbmc.LOGINFO):
    """Log a message to Kodi log."""
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def get_setting(key):
    """Get addon setting."""
    return ADDON.getSetting(key)


def set_setting(key, value):
    """Set addon setting."""
    ADDON.setSetting(key, str(value))


def get_localized_string(string_id):
    """Get localized string."""
    return ADDON.getLocalizedString(string_id)


def get_hostname():
    """Get the device hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "Kodi"


def find_sendspin():
    """Find sendspin executable by testing execution."""
    import shutil
    
    log("Searching for sendspin...")
    
    # Check if we're in Flatpak
    in_flatpak = shutil.which("flatpak-spawn") is not None
    
    # Direct paths to try - test each by actually running it
    paths_to_try = [
        '/usr/local/bin/sendspin',
        '/usr/bin/sendspin',
        os.path.expanduser('~/.local/bin/sendspin'),
    ]
    
    # If in Flatpak, also try common host user paths
    if in_flatpak:
        # Try to get host username
        try:
            result = subprocess.run(
                ['flatpak-spawn', '--host', 'whoami'],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                host_user = result.stdout.decode().strip()
                paths_to_try.insert(0, f'/home/{host_user}/.local/bin/sendspin')
                log(f"Detected host user: {host_user}")
        except Exception:
            pass
        
        # Common user paths on various systems
        paths_to_try.extend([
            '/home/deck/.local/bin/sendspin',  # Steam Deck
            '/home/kodi/.local/bin/sendspin',
            '/home/osmc/.local/bin/sendspin',
            '/var/home/deck/.local/bin/sendspin',  # Steam Deck alternate
        ])
    else:
        paths_to_try.extend([
            '/home/kodi/.local/bin/sendspin',
            '/home/osmc/.local/bin/sendspin',
            '/storage/.local/bin/sendspin',
        ])
    
    for path in paths_to_try:
        try:
            log(f"Testing path: {path}")
            if not os.path.exists(path) and not in_flatpak:
                log(f"  Does not exist")
                continue
            
            # For Flatpak, use flatpak-spawn with login shell to test
            if in_flatpak:
                cmd = ['flatpak-spawn', '--host', '/bin/bash', '-lc', f'{path} --version']
            else:
                cmd = [path, '--version']
            
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode == 0:
                log(f"Found working sendspin at: {path}")
                return path
            else:
                stderr = result.stderr.decode()[:100] if result.stderr else ''
                log(f"  Exit code {result.returncode}: {stderr}")
        except FileNotFoundError:
            log(f"  FileNotFoundError")
        except PermissionError:
            log(f"  PermissionError")
        except subprocess.TimeoutExpired:
            log(f"  Timeout")
        except Exception as e:
            log(f"  Error: {e}")
    
    # Try using shell to find in PATH (works better in some environments)
    log("Trying shell: which sendspin")
    try:
        if in_flatpak:
            # Use login shell to get proper PATH
            result = subprocess.run(
                ['flatpak-spawn', '--host', '/bin/bash', '-lc', 'which sendspin'],
                capture_output=True,
                timeout=10
            )
        else:
            result = subprocess.run(
                'which sendspin',
                shell=True,
                capture_output=True,
                timeout=5
            )
        if result.returncode == 0:
            path = result.stdout.decode().strip()
            if path:
                log(f"Found via which: {path}")
                return path
    except Exception as e:
        log(f"  which failed: {e}")
    
    log("Sendspin not found")
    return None


def is_sendspin_running():
    """Check if sendspin is already running (system-wide check)."""
    global _sendspin_process
    import shutil
    
    # First check our tracked process
    with _sendspin_lock:
        if _sendspin_process is not None:
            poll = _sendspin_process.poll()
            if poll is None:
                log("Sendspin is running (tracked process)")
                return True
            else:
                _sendspin_process = None
    
    # Check if we're in Flatpak
    in_flatpak = shutil.which("flatpak-spawn") is not None
    
    # Check system-wide using pgrep
    try:
        if in_flatpak:
            # Check on host system
            result = subprocess.run(
                ['flatpak-spawn', '--host', 'pgrep', '-f', 'sendspin.*--headless'],
                capture_output=True,
                timeout=5
            )
        else:
            result = subprocess.run(
                ['pgrep', '-f', 'sendspin.*--headless'],
                capture_output=True,
                timeout=5
            )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.decode().strip().split('\n')
            log(f"Sendspin already running system-wide (PIDs: {pids})")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Fallback: check using ps
    try:
        if in_flatpak:
            result = subprocess.run(
                ['flatpak-spawn', '--host', 'ps', 'aux'],
                capture_output=True,
                timeout=5
            )
        else:
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                timeout=5
            )
        if result.returncode == 0:
            output = result.stdout.decode()
            if 'sendspin' in output and '--headless' in output:
                log("Sendspin already running (found in ps)")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return False


def start_sendspin(server_url=None):
    """Start sendspin player in background."""
    global _sendspin_process
    
    if is_sendspin_running():
        log("Sendspin already running")
        return True
    
    # Check for manual path setting first
    manual_path = get_setting('sendspin_path')
    if manual_path and manual_path.strip():
        sendspin_path = manual_path.strip()
        
        # Expand ~ - but for Flatpak we need the HOST's home
        if sendspin_path.startswith('~'):
            import shutil
            if shutil.which("flatpak-spawn"):
                # Get host user's home directory
                try:
                    result = subprocess.run(
                        ['flatpak-spawn', '--host', 'sh', '-c', 'echo $HOME'],
                        capture_output=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        host_home = result.stdout.decode().strip()
                        # Ensure host_home starts with /
                        if not host_home.startswith('/'):
                            host_home = '/' + host_home
                        sendspin_path = sendspin_path.replace('~', host_home, 1)
                except Exception:
                    pass
            else:
                sendspin_path = os.path.expanduser(sendspin_path)
        
        # Ensure path starts with / if it looks like an absolute path
        if sendspin_path.startswith('home/'):
            sendspin_path = '/' + sendspin_path
        
        log(f"Using manual sendspin path: {sendspin_path}")
    else:
        sendspin_path = find_sendspin()
    
    if not sendspin_path:
        log("Sendspin not found. Install with: pip install sendspin")
        return False
    
    # Build command - use flatpak-spawn with login shell for Flatpak sandbox
    import shutil
    if shutil.which("flatpak-spawn"):
        log("Detected Flatpak environment, using flatpak-spawn with login shell")
        # Use login shell (-l) to initialize environment (needed for Steam Deck)
        cmd = [
            "flatpak-spawn",
            "--host",
            "/bin/bash",
            "-lc",
            f"{sendspin_path} --headless"
        ]
    else:
        cmd = [sendspin_path, "--headless"]
    
    try:
        log(f"Starting sendspin: {' '.join(cmd)}")
        with _sendspin_lock:
            _sendspin_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        log(f"Sendspin started with PID: {_sendspin_process.pid}")
        
        # Give it a moment to connect
        time.sleep(2)
        
        # Check if it's still running
        if _sendspin_process.poll() is not None:
            log("Sendspin exited immediately - check dependencies", xbmc.LOGWARNING)
            return False
        
        return True
    except Exception as e:
        log(f"Failed to start sendspin: {e}", xbmc.LOGERROR)
        return False


def stop_sendspin():
    """Stop sendspin player."""
    global _sendspin_process
    
    with _sendspin_lock:
        if _sendspin_process is not None:
            try:
                _sendspin_process.terminate()
                _sendspin_process.wait(timeout=5)
                log("Sendspin stopped")
            except subprocess.TimeoutExpired:
                _sendspin_process.kill()
                log("Sendspin killed")
            except Exception as e:
                log(f"Error stopping sendspin: {e}", xbmc.LOGWARNING)
            finally:
                _sendspin_process = None


def get_client():
    """Create and return a configured Music Assistant client."""
    server_url = get_setting('server_url')
    auth_token = get_setting('auth_token')
    
    if not server_url:
        xbmcgui.Dialog().ok(
            get_localized_string(30503),
            get_localized_string(30502)
        )
        ADDON.openSettings()
        return None
    
    if not server_url.startswith('http'):
        server_url = f'http://{server_url}'
    
    # If we don't have a token, try to login with username/password
    if not auth_token:
        username = get_setting('username')
        password = get_setting('password')
        
        if username and password:
            try:
                user, token = login_with_token(
                    server_url, username, password,
                    token_name=f"Kodi {ADDON_VERSION}"
                )
                set_setting('auth_token', token)
                auth_token = token
                log(f"Successfully logged in as {user.get('display_name', user.get('username', username))}")
            except LoginFailed as e:
                log(f"Login failed: {e}", xbmc.LOGERROR)
                xbmcgui.Dialog().ok(
                    get_localized_string(30504),
                    str(e)
                )
                ADDON.openSettings()
                return None
            except CannotConnect as e:
                log(f"Cannot connect to server: {e}", xbmc.LOGERROR)
                xbmcgui.Dialog().ok(
                    get_localized_string(30500),
                    str(e)
                )
                return None
        else:
            # No credentials - prompt user
            xbmcgui.Dialog().ok(
                get_localized_string(30502),
                get_localized_string(30509)
            )
            ADDON.openSettings()
            return None
    
    return MusicAssistantClient(server_url=server_url, token=auth_token)


def build_url(params):
    """Build plugin URL with parameters."""
    return f'plugin://{ADDON_ID}/?{urlencode(params)}'


def get_params():
    """Get parameters from plugin URL."""
    params = {}
    if len(sys.argv) > 2:
        params = dict(parse_qsl(sys.argv[2][1:]))
    return params


class MusicAssistantAddon:
    """Main addon class."""
    
    def __init__(self):
        self.handle = int(sys.argv[1])
        self.params = get_params()
        self.client = None
        
    def run(self):
        """Run the addon based on parameters."""
        try:
            # Auto-start sendspin if enabled
            if get_setting('auto_start_sendspin') == 'true':
                server_url = get_setting('server_url')
                if server_url and not is_sendspin_running():
                    if start_sendspin(server_url):
                        log("Sendspin auto-started for local playback")
                    # If start_sendspin fails, it will log the warning
            
            self.client = get_client()
            if not self.client:
                return
            
            self.client.test_connection()
            
            action = self.params.get('action', 'main_menu')
            
            actions = {
                'main_menu': self.show_main_menu,
                'artists': self.show_artists,
                'artist': self.show_artist,
                'albums': self.show_albums,
                'album': self.show_album,
                'tracks': self.show_tracks,
                'playlists': self.show_playlists,
                'playlist': self.show_playlist,
                'radios': self.show_radios,
                'podcasts': self.show_podcasts,
                'podcast': self.show_podcast,
                'audiobooks': self.show_audiobooks,
                'search': self.show_search,
                'search_results': self.show_search_results,
                'play_track': self.play_track,
                'play_track_redirect': self.play_track_redirect,
                'play_album': self.play_album,
                'play_playlist': self.play_playlist,
                'play_radio': self.play_radio,
                'queue': self.show_queue,
                'players': self.show_players,
                'favorites': self.show_favorites,
                'recent': self.show_recent,
                'set_default_player': self.set_default_player,
                'select_player': self.select_player,
                # Play options
                'play_now': self.play_now,
                'play_replace': self.play_replace,
                'play_next': self.play_next,
                'add_to_queue': self.add_to_queue,
                'play_on_player': self.play_on_player,
                # Player management
                'hide_player': self.hide_player,
                'unhide_player': self.unhide_player,
                # Player controls
                'player_controls': self.show_player_controls,
                'ctrl_play_pause': self.ctrl_play_pause,
                'ctrl_next': self.ctrl_next,
                'ctrl_previous': self.ctrl_previous,
                'ctrl_stop': self.ctrl_stop,
                'ctrl_shuffle': self.ctrl_shuffle,
                'ctrl_clear_queue': self.ctrl_clear_queue,
                'ctrl_volume_up': self.ctrl_volume_up,
                'ctrl_volume_down': self.ctrl_volume_down,
                'ctrl_sync_player': self.ctrl_sync_player,
                'ctrl_unsync_player': self.ctrl_unsync_player,
                'ctrl_transfer_queue': self.ctrl_transfer_queue,
            }
            
            handler = actions.get(action, self.show_main_menu)
            handler()
            
        except AuthenticationRequired as e:
            log(f"Authentication required: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(get_localized_string(30502), get_localized_string(30509))
            ADDON.openSettings()
        except AuthenticationFailed as e:
            log(f"Authentication failed: {e}", xbmc.LOGERROR)
            set_setting('auth_token', '')
            xbmcgui.Dialog().ok(get_localized_string(30504), get_localized_string(30505))
            ADDON.openSettings()
        except CannotConnect as e:
            log(f"Connection error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(get_localized_string(30500), get_localized_string(30501))
        except MusicAssistantError as e:
            log(f"Music Assistant error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, str(e), xbmcgui.NOTIFICATION_ERROR)
        except Exception as e:
            log(f"Unexpected error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, str(e), xbmcgui.NOTIFICATION_ERROR)
        finally:
            if self.client:
                self.client.close()
    
    def show_main_menu(self):
        """Show main menu."""
        items = [
            (get_localized_string(30001), 'artists', 'DefaultMusicArtists.png'),
            (get_localized_string(30002), 'albums', 'DefaultMusicAlbums.png'),
            (get_localized_string(30003), 'tracks', 'DefaultMusicSongs.png'),
            (get_localized_string(30004), 'playlists', 'DefaultMusicPlaylists.png'),
            (get_localized_string(30005), 'radios', 'DefaultMusicGenres.png'),
            (get_localized_string(30011), 'podcasts', 'DefaultMusicGenres.png'),
            (get_localized_string(30012), 'audiobooks', 'DefaultMusicAlbums.png'),
            (get_localized_string(30006), 'search', 'DefaultAddonsSearch.png'),
            (get_localized_string(30013), 'favorites', 'DefaultFavourites.png'),
            (get_localized_string(30010), 'recent', 'DefaultMusicRecentlyPlayed.png'),
            ('Player Controls', 'player_controls', 'DefaultAddonMusic.png'),
            (get_localized_string(30008), 'queue', 'DefaultMusicPlaylists.png'),
            (get_localized_string(30007), 'players', 'DefaultAddonMusic.png'),
        ]
        
        for label, action, icon in items:
            li = xbmcgui.ListItem(label=label)
            li.setArt({'icon': icon, 'thumb': icon})
            url = build_url({'action': action})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_artists(self):
        """Show artists list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        favorite = self.params.get('favorite') == 'true'
        
        items = self.client.music.get_library_artists(limit=limit, offset=offset, favorite=favorite if favorite else None)
        
        for artist in (items or []):
            self._add_artist_item(artist)
        
        if len(items or []) >= limit:
            self._add_load_more('artists', offset + limit, favorite=favorite)
        
        xbmcplugin.setContent(self.handle, 'artists')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_LABEL)
        xbmcplugin.endOfDirectory(self.handle)
    
    def _add_artist_item(self, artist):
        """Add an artist item to the list."""
        name = artist.get('name', 'Unknown Artist')
        item_id = artist.get('item_id', '')
        provider = artist.get('provider', 'library')
        
        li = xbmcgui.ListItem(label=name)
        image_url = self.client.get_media_item_image_url(artist)
        if image_url:
            li.setArt({'thumb': image_url, 'icon': image_url, 'fanart': image_url})
        else:
            li.setArt({'icon': 'DefaultMusicArtists.png'})
        
        info_tag = li.getMusicInfoTag()
        info_tag.setArtist(name)
        info_tag.setMediaType('artist')
        
        url = build_url({'action': 'artist', 'id': item_id, 'provider': provider})
        xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
    
    def show_artist(self):
        """Show artist details."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        artist = self.client.music.get_artist(item_id, provider)
        
        try:
            albums = self.client.music.get_artist_albums(item_id, provider)
            for album in (albums or []):
                self._add_album_item(album, artist.get('name', ''))
        except Exception as e:
            log(f"Error loading artist albums: {e}")
        
        try:
            tracks = self.client.music.get_artist_tracks(item_id, provider)
            for track in (tracks or []):
                self._add_track_item(track)
        except Exception as e:
            log(f"Error loading artist tracks: {e}")
        
        xbmcplugin.setContent(self.handle, 'albums')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_albums(self):
        """Show albums list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        favorite = self.params.get('favorite') == 'true'
        
        items = self.client.music.get_library_albums(limit=limit, offset=offset, favorite=favorite if favorite else None)
        
        for album in (items or []):
            self._add_album_item(album)
        
        if len(items or []) >= limit:
            self._add_load_more('albums', offset + limit, favorite=favorite)
        
        xbmcplugin.setContent(self.handle, 'albums')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_ALBUM)
        xbmcplugin.endOfDirectory(self.handle)
    
    def _add_album_item(self, album, artist_name=None):
        """Add an album item to the list."""
        name = album.get('name', 'Unknown Album')
        item_id = album.get('item_id', '')
        provider = album.get('provider', 'library')
        year = album.get('year', '')
        
        if not artist_name:
            artists = album.get('artists', [])
            artist_name = artists[0].get('name', '') if artists and isinstance(artists[0], dict) else ''
        
        label = f"{name} ({year})" if year else name
        li = xbmcgui.ListItem(label=label)
        
        image_url = self.client.get_media_item_image_url(album)
        if image_url:
            li.setArt({'thumb': image_url, 'icon': image_url, 'fanart': image_url})
        else:
            li.setArt({'icon': 'DefaultMusicAlbums.png'})
        
        info_tag = li.getMusicInfoTag()
        info_tag.setAlbum(name)
        info_tag.setArtist(artist_name)
        info_tag.setMediaType('album')
        if year:
            try:
                info_tag.setYear(int(year))
            except (ValueError, TypeError):
                pass
        
        context_menu = [
            ("Play Album", f'RunPlugin({build_url({"action": "play_now", "id": item_id, "provider": provider, "type": "album"})})'),
            ("Play Album (Clear Queue)", f'RunPlugin({build_url({"action": "play_replace", "id": item_id, "provider": provider, "type": "album"})})'),
            ("Play Album Next", f'RunPlugin({build_url({"action": "play_next", "id": item_id, "provider": provider, "type": "album"})})'),
            ("Add Album to Queue", f'RunPlugin({build_url({"action": "add_to_queue", "id": item_id, "provider": provider, "type": "album"})})'),
            ("Play on Different Player", f'RunPlugin({build_url({"action": "play_on_player", "id": item_id, "provider": provider, "type": "album"})})'),
        ]
        li.addContextMenuItems(context_menu)
        
        url = build_url({'action': 'album', 'id': item_id, 'provider': provider})
        xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
    
    def show_album(self):
        """Show album tracks."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        album = self.client.music.get_album(item_id, provider)
        tracks = self.client.music.get_album_tracks(item_id, provider)
        
        for track in (tracks or []):
            track['_album'] = album
            self._add_track_item(track)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_TRACKNUM)
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_tracks(self):
        """Show tracks list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        favorite = self.params.get('favorite') == 'true'
        
        items = self.client.music.get_library_tracks(limit=limit, offset=offset, favorite=favorite if favorite else None)
        
        for track in (items or []):
            self._add_track_item(track)
        
        if len(items or []) >= limit:
            self._add_load_more('tracks', offset + limit, favorite=favorite)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_TITLE)
        xbmcplugin.endOfDirectory(self.handle)
    
    def _add_track_item(self, track):
        """Add a track item to the list."""
        name = track.get('name', 'Unknown Track')
        item_id = track.get('item_id', '')
        provider = track.get('provider', 'library')
        
        artists = track.get('artists', [])
        artist_name = artists[0].get('name', '') if artists and isinstance(artists[0], dict) else ''
        
        album = track.get('_album') or track.get('album')
        album_name = album.get('name', '') if isinstance(album, dict) else str(album or '')
        
        duration = track.get('duration', 0)
        track_num = track.get('track_number', 0)
        
        label = f"{name} - {artist_name}" if artist_name else name
        li = xbmcgui.ListItem(label=label)
        
        image_url = self.client.get_media_item_image_url(track)
        if not image_url and album and isinstance(album, dict):
            image_url = self.client.get_media_item_image_url(album)
        
        if image_url:
            li.setArt({'thumb': image_url, 'icon': image_url, 'fanart': image_url})
        else:
            li.setArt({'icon': 'DefaultMusicSongs.png'})
        
        info_tag = li.getMusicInfoTag()
        info_tag.setTitle(name)
        info_tag.setArtist(artist_name)
        info_tag.setAlbum(album_name)
        info_tag.setMediaType('song')
        if duration:
            try:
                info_tag.setDuration(int(duration))
            except (ValueError, TypeError):
                pass
        if track_num:
            try:
                info_tag.setTrack(int(track_num))
            except (ValueError, TypeError):
                pass
        
        # Don't set IsPlayable - we handle playback via MA player
        # li.setProperty('IsPlayable', 'true')
        
        context_menu = [
            ("Play Now", f'RunPlugin({build_url({"action": "play_now", "id": item_id, "provider": provider})})'),
            ("Play Now (Clear Queue)", f'RunPlugin({build_url({"action": "play_replace", "id": item_id, "provider": provider})})'),
            ("Play Next", f'RunPlugin({build_url({"action": "play_next", "id": item_id, "provider": provider})})'),
            ("Add to Queue", f'RunPlugin({build_url({"action": "add_to_queue", "id": item_id, "provider": provider})})'),
            ("Play on Different Player", f'RunPlugin({build_url({"action": "play_on_player", "id": item_id, "provider": provider})})'),
        ]
        li.addContextMenuItems(context_menu)
        
        # Use RunPlugin for click action so it works like context menu "Play Now"
        url = f'RunPlugin({build_url({"action": "play_track", "id": item_id, "provider": provider})})'
        
        # Add as folder=False but with RunPlugin URL wrapped in a special way
        # Actually, we need to use a different approach - add a redirect action
        play_url = build_url({'action': 'play_track_redirect', 'id': item_id, 'provider': provider})
        xbmcplugin.addDirectoryItem(handle=self.handle, url=play_url, listitem=li, isFolder=False)
    
    def show_playlists(self):
        """Show playlists list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        
        items = self.client.music.get_library_playlists(limit=limit, offset=offset)
        
        for playlist in (items or []):
            self._add_playlist_item(playlist)
        
        if len(items or []) >= limit:
            self._add_load_more('playlists', offset + limit)
        
        xbmcplugin.setContent(self.handle, 'albums')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_LABEL)
        xbmcplugin.endOfDirectory(self.handle)
    
    def _add_playlist_item(self, playlist):
        """Add a playlist item to the list."""
        name = playlist.get('name', 'Unknown Playlist')
        item_id = playlist.get('item_id', '')
        provider = playlist.get('provider', 'library')
        
        li = xbmcgui.ListItem(label=name)
        
        image_url = self.client.get_media_item_image_url(playlist)
        if image_url:
            li.setArt({'thumb': image_url, 'icon': image_url, 'fanart': image_url})
        else:
            li.setArt({'icon': 'DefaultMusicPlaylists.png'})
        
        info_tag = li.getMusicInfoTag()
        info_tag.setTitle(name)
        info_tag.setMediaType('album')
        
        context_menu = [
            ("Play Playlist", f'RunPlugin({build_url({"action": "play_now", "id": item_id, "provider": provider, "type": "playlist"})})'),
            ("Play Playlist (Clear Queue)", f'RunPlugin({build_url({"action": "play_replace", "id": item_id, "provider": provider, "type": "playlist"})})'),
            ("Play Playlist Next", f'RunPlugin({build_url({"action": "play_next", "id": item_id, "provider": provider, "type": "playlist"})})'),
            ("Add Playlist to Queue", f'RunPlugin({build_url({"action": "add_to_queue", "id": item_id, "provider": provider, "type": "playlist"})})'),
            ("Play on Different Player", f'RunPlugin({build_url({"action": "play_on_player", "id": item_id, "provider": provider, "type": "playlist"})})'),
        ]
        li.addContextMenuItems(context_menu)
        
        url = build_url({'action': 'playlist', 'id': item_id, 'provider': provider})
        xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
    
    def show_playlist(self):
        """Show playlist tracks."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        tracks = self.client.music.get_playlist_tracks(item_id, provider)
        
        for track in (tracks or []):
            self._add_track_item(track)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_radios(self):
        """Show radio stations list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        
        items = self.client.music.get_library_radios(limit=limit, offset=offset)
        
        for radio in (items or []):
            self._add_radio_item(radio)
        
        if len(items or []) >= limit:
            self._add_load_more('radios', offset + limit)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.addSortMethod(self.handle, xbmcplugin.SORT_METHOD_LABEL)
        xbmcplugin.endOfDirectory(self.handle)
    
    def _add_radio_item(self, radio):
        """Add a radio station item to the list."""
        name = radio.get('name', 'Unknown Station')
        item_id = radio.get('item_id', '')
        provider = radio.get('provider', 'library')
        
        li = xbmcgui.ListItem(label=name)
        
        image_url = self.client.get_media_item_image_url(radio)
        if image_url:
            li.setArt({'thumb': image_url, 'icon': image_url, 'fanart': image_url})
        else:
            li.setArt({'icon': 'DefaultMusicGenres.png'})
        
        info_tag = li.getMusicInfoTag()
        info_tag.setTitle(name)
        info_tag.setMediaType('song')
        
        li.setProperty('IsPlayable', 'true')
        
        url = build_url({'action': 'play_radio', 'id': item_id, 'provider': provider})
        xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=False)
    
    def show_podcasts(self):
        """Show podcasts list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        
        items = self.client.music.get_library_podcasts(limit=limit, offset=offset)
        
        for podcast in (items or []):
            name = podcast.get('name', 'Unknown Podcast')
            item_id = podcast.get('item_id', '')
            provider = podcast.get('provider', 'library')
            
            li = xbmcgui.ListItem(label=name)
            image_url = self.client.get_media_item_image_url(podcast)
            if image_url:
                li.setArt({'thumb': image_url, 'icon': image_url})
            else:
                li.setArt({'icon': 'DefaultMusicGenres.png'})
            
            url = build_url({'action': 'podcast', 'id': item_id, 'provider': provider})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        if len(items or []) >= limit:
            self._add_load_more('podcasts', offset + limit)
        
        xbmcplugin.setContent(self.handle, 'albums')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_podcast(self):
        """Show podcast episodes."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        podcast = self.client.music.get_podcast(item_id, provider)
        episodes = self.client.music.get_podcast_episodes(item_id, provider)
        
        for episode in (episodes or []):
            name = episode.get('name', 'Unknown Episode')
            ep_id = episode.get('item_id', '')
            ep_provider = episode.get('provider', 'library')
            
            li = xbmcgui.ListItem(label=name)
            image_url = self.client.get_media_item_image_url(episode) or self.client.get_media_item_image_url(podcast)
            if image_url:
                li.setArt({'thumb': image_url, 'icon': image_url})
            
            li.setProperty('IsPlayable', 'true')
            
            url = build_url({'action': 'play_track', 'id': ep_id, 'provider': ep_provider, 'type': 'podcast_episode'})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=False)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_audiobooks(self):
        """Show audiobooks list."""
        limit = int(get_setting('items_per_page') or 50)
        offset = int(self.params.get('offset', 0))
        
        items = self.client.music.get_library_audiobooks(limit=limit, offset=offset)
        
        for audiobook in (items or []):
            name = audiobook.get('name', 'Unknown Audiobook')
            item_id = audiobook.get('item_id', '')
            provider = audiobook.get('provider', 'library')
            
            li = xbmcgui.ListItem(label=name)
            image_url = self.client.get_media_item_image_url(audiobook)
            if image_url:
                li.setArt({'thumb': image_url, 'icon': image_url})
            else:
                li.setArt({'icon': 'DefaultMusicAlbums.png'})
            
            url = build_url({'action': 'audiobook', 'id': item_id, 'provider': provider})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        if len(items or []) >= limit:
            self._add_load_more('audiobooks', offset + limit)
        
        xbmcplugin.setContent(self.handle, 'albums')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_favorites(self):
        """Show favorites menu."""
        items = [
            (get_localized_string(30001), 'artists', {'favorite': 'true'}),
            (get_localized_string(30002), 'albums', {'favorite': 'true'}),
            (get_localized_string(30003), 'tracks', {'favorite': 'true'}),
        ]
        
        for label, action, extra_params in items:
            li = xbmcgui.ListItem(label=label)
            li.setArt({'icon': 'DefaultFavourites.png'})
            params = {'action': action}
            params.update(extra_params)
            url = build_url(params)
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_recent(self):
        """Show recently played items."""
        items = self.client.music.recently_played(limit=50)
        
        for item in (items or []):
            media_type = item.get('media_type', '')
            if media_type == MediaType.TRACK:
                self._add_track_item(item)
            elif media_type == MediaType.ALBUM:
                self._add_album_item(item)
            elif media_type == MediaType.ARTIST:
                self._add_artist_item(item)
            elif media_type == MediaType.RADIO:
                self._add_radio_item(item)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_search(self):
        """Show search dialog."""
        keyboard = xbmc.Keyboard('', get_localized_string(30701))
        keyboard.doModal()
        
        if keyboard.isConfirmed():
            query = keyboard.getText()
            if query:
                # Show search results
                self._do_search(query)
                return
        
        # User cancelled - just end directory
        xbmcplugin.endOfDirectory(self.handle, succeeded=False)
    
    def _do_search(self, query):
        """Perform search and show results."""
        try:
            results = self.client.music.search(query, limit=50)
            log(f"Search results for '{query}': {list(results.keys()) if results else 'None'}")
        except Exception as e:
            log(f"Search error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Search failed: {e}", xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(self.handle, succeeded=False)
            return
        
        if not results:
            xbmcgui.Dialog().notification(ADDON_NAME, get_localized_string(30507), xbmcgui.NOTIFICATION_INFO)
            xbmcplugin.endOfDirectory(self.handle, succeeded=False)
            return
        
        has_results = False
        
        # Add items from search results
        for artist in (results.get('artists') or []):
            self._add_artist_item(artist)
            has_results = True
        for album in (results.get('albums') or []):
            self._add_album_item(album)
            has_results = True
        for track in (results.get('tracks') or []):
            self._add_track_item(track)
            has_results = True
        for playlist in (results.get('playlists') or []):
            self._add_playlist_item(playlist)
            has_results = True
        # Note: API returns 'radio' not 'radios'
        for radio in (results.get('radio') or results.get('radios') or []):
            self._add_radio_item(radio)
            has_results = True
        
        if not has_results:
            xbmcgui.Dialog().notification(ADDON_NAME, get_localized_string(30507), xbmcgui.NOTIFICATION_INFO)
        
        xbmcplugin.setContent(self.handle, 'songs')
        xbmcplugin.endOfDirectory(self.handle, succeeded=has_results)
    
    def show_search_results(self):
        """Show search results (for backwards compatibility)."""
        query = self.params.get('query', '')
        
        if not query:
            self.show_search()
            return
        
        self._do_search(query)
    
    def play_track(self):
        """Play a track via Music Assistant player."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        # Get or select a player
        player_id = self._get_player_id()
        if not player_id:
            # Tell Kodi we failed to resolve
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return
        
        # Get track info and URI
        try:
            if media_type == 'track':
                item = self.client.music.get_track(item_id, provider)
            else:
                item = self.client.send_command(
                    f'music/{media_type}s/get',
                    item_id=str(item_id),
                    provider_instance_id_or_domain=provider
                )
            uri = item.get('uri', f"library://{media_type}/{item_id}")
            name = item.get('name', 'Unknown')
        except Exception as e:
            log(f"Error getting item: {e}")
            uri = f"library://{media_type}/{item_id}"
            name = 'Unknown'
        
        # Play on MA player
        try:
            log(f"Playing on MA player {player_id}: {uri}")
            self.client.player_queues.play_media(player_id, uri)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playing: {name}", xbmcgui.NOTIFICATION_INFO, 3000)
            # Tell Kodi we handled it - use a dummy resolved URL that won't actually play
            # This prevents Kodi from skipping to next item
            li = xbmcgui.ListItem()
            xbmcplugin.setResolvedUrl(self.handle, True, li)
        except Exception as e:
            error_msg = str(e)
            log(f"Playback error: {error_msg}", xbmc.LOGERROR)
            # Show more detailed error in notification
            short_error = error_msg[:100] if len(error_msg) > 100 else error_msg
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playback failed: {short_error}", xbmcgui.NOTIFICATION_ERROR, 5000)
            # Tell Kodi we failed
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
    
    def play_track_redirect(self):
        """Play a track when clicking on it - show options menu."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        # Show play options dialog
        options = [
            "Play Now",
            "Play Now (Clear Queue)",
            "Play Next",
            "Add to Queue",
            "Play on Different Player...",
        ]
        
        selected = xbmcgui.Dialog().select("Play Options", options)
        
        if selected < 0:
            return
        
        if selected == 4:  # Play on Different Player
            self._play_on_different_player(item_id, provider, media_type)
            return
        
        # Map selection to queue option
        option_map = {
            0: None,      # Play Now (default behavior)
            1: 'replace', # Clear queue and play
            2: 'next',    # Play next
            3: 'add',     # Add to end of queue
        }
        queue_option = option_map.get(selected)
        
        # Get or select a player
        player_id = self._get_player_id()
        if not player_id:
            return
        
        self._play_item(item_id, provider, media_type, player_id, queue_option)
    
    def _play_item(self, item_id, provider, media_type, player_id, queue_option=None):
        """Play an item with the specified queue option."""
        # Get item info and URI
        try:
            if media_type == 'track':
                item = self.client.music.get_track(item_id, provider)
            else:
                item = self.client.send_command(
                    f'music/{media_type}s/get',
                    item_id=str(item_id),
                    provider_instance_id_or_domain=provider
                )
            uri = item.get('uri', f"library://{media_type}/{item_id}")
            name = item.get('name', 'Unknown')
        except Exception as e:
            log(f"Error getting item: {e}")
            uri = f"library://{media_type}/{item_id}"
            name = 'Unknown'
        
        # Play on MA player
        try:
            log(f"Playing on MA player {player_id}: {uri} (option: {queue_option})")
            self.client.player_queues.play_media(player_id, uri, option=queue_option)
            
            # Show appropriate notification
            action_text = {
                None: "Playing",
                'replace': "Playing (queue cleared)",
                'next': "Playing next",
                'add': "Added to queue",
            }.get(queue_option, "Playing")
            
            xbmcgui.Dialog().notification(ADDON_NAME, f"{action_text}: {name}", xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception as e:
            error_msg = str(e)
            log(f"Playback error: {error_msg}", xbmc.LOGERROR)
            short_error = error_msg[:100] if len(error_msg) > 100 else error_msg
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playback failed: {short_error}", xbmcgui.NOTIFICATION_ERROR, 5000)
    
    def _play_on_different_player(self, item_id, provider, media_type):
        """Play item on a different player (one-time selection)."""
        try:
            players = self.client.players.players
            if not players:
                xbmcgui.Dialog().notification(ADDON_NAME, "No players available", xbmcgui.NOTIFICATION_ERROR)
                return
            
            # Get hidden players list
            hidden_players = get_setting('hidden_players') or ''
            hidden_list = [p.strip() for p in hidden_players.split(',') if p.strip()]
            
            # Filter available and non-hidden players
            available_players = [p for p in players if p.get('available', False) and p.get('player_id') not in hidden_list]
            if not available_players:
                available_players = [p for p in players if p.get('player_id') not in hidden_list]
            
            if not available_players:
                xbmcgui.Dialog().notification(ADDON_NAME, "No players available", xbmcgui.NOTIFICATION_ERROR)
                return
            
            names = [p.get('name', p.get('player_id', 'Unknown')) for p in available_players]
            
            selected = xbmcgui.Dialog().select("Select Player", names)
            if selected < 0:
                return
            
            player_id = available_players[selected].get('player_id')
            
            # Ask for play option
            options = [
                "Play Now",
                "Play Now (Clear Queue)",
                "Play Next",
                "Add to Queue",
            ]
            
            option_selected = xbmcgui.Dialog().select("Play Option", options)
            if option_selected < 0:
                return
            
            option_map = {
                0: None,
                1: 'replace',
                2: 'next',
                3: 'add',
            }
            queue_option = option_map.get(option_selected)
            
            self._play_item(item_id, provider, media_type, player_id, queue_option)
            
        except Exception as e:
            log(f"Error selecting player: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
    
    def play_now(self):
        """Play item now (default behavior)."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        player_id = self._get_player_id()
        if not player_id:
            return
        
        self._play_item(item_id, provider, media_type, player_id, None)
    
    def play_replace(self):
        """Play item and clear queue."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        player_id = self._get_player_id()
        if not player_id:
            return
        
        self._play_item(item_id, provider, media_type, player_id, 'replace')
    
    def play_next(self):
        """Play item next in queue."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        player_id = self._get_player_id()
        if not player_id:
            return
        
        self._play_item(item_id, provider, media_type, player_id, 'next')
    
    def add_to_queue(self):
        """Add item to end of queue."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        player_id = self._get_player_id()
        if not player_id:
            return
        
        self._play_item(item_id, provider, media_type, player_id, 'add')
    
    def play_on_player(self):
        """Play on a different player (context menu action)."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        media_type = self.params.get('type', 'track')
        
        self._play_on_different_player(item_id, provider, media_type)
    
    def _get_player_id(self):
        """Get configured player ID or prompt user to select one."""
        player_id = get_setting('ma_player_id')
        
        if player_id:
            return player_id
        
        # Get hidden players list
        hidden_players = get_setting('hidden_players') or ''
        hidden_list = [p.strip() for p in hidden_players.split(',') if p.strip()]
        
        # No player configured - try to find and auto-select sendspin player
        try:
            players = self.client.players.players
            if not players:
                xbmcgui.Dialog().ok(
                    ADDON_NAME, 
                    "No Music Assistant players found.\n\n"
                    "For local playback, install sendspin-cli:\n"
                    "pip install sendspin\n\n"
                    "Then enable 'Auto-start Sendspin' in settings."
                )
                return None
            
            # Build list of available players (excluding hidden)
            available_players = [p for p in players if p.get('available', False) and p.get('player_id') not in hidden_list]
            if not available_players:
                # Show all non-hidden if none available
                available_players = [p for p in players if p.get('player_id') not in hidden_list]
            
            if not available_players:
                xbmcgui.Dialog().ok(ADDON_NAME, "No players available (all hidden or unavailable)")
                return None
            
            # Try to auto-select sendspin player if we started one
            sendspin_player_name = get_setting('sendspin_player_name')
            if not sendspin_player_name:
                sendspin_player_name = f"Kodi ({get_hostname()})"
            
            # Look for our sendspin player
            for p in available_players:
                p_name = p.get('name', '')
                p_id = p.get('player_id', '')
                # Match by name or if it contains 'sendspin' and our hostname
                if p_name == sendspin_player_name or (
                    'sendspin' in p_id.lower() and get_hostname().lower() in p_name.lower()
                ):
                    player_id = p_id
                    set_setting('ma_player_id', player_id)
                    log(f"Auto-selected sendspin player: {p_name} ({p_id})")
                    xbmcgui.Dialog().notification(
                        ADDON_NAME, 
                        f"Using player: {p_name}", 
                        xbmcgui.NOTIFICATION_INFO, 
                        3000
                    )
                    return player_id
            
            # No auto-match - show selection dialog
            names = [p.get('name', p.get('player_id', 'Unknown')) for p in available_players]
            
            selected = xbmcgui.Dialog().select("Select Music Assistant Player", names)
            
            if selected < 0:
                return None
            
            player_id = available_players[selected].get('player_id', '')
            
            # Save selection
            set_setting('ma_player_id', player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Player set: {names[selected]}", xbmcgui.NOTIFICATION_INFO)
            
            return player_id
            
        except Exception as e:
            log(f"Error getting players: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(ADDON_NAME, f"Error getting players: {e}")
            return None
    
    def play_album(self):
        """Play an album via Music Assistant player."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        # Get or select a player
        player_id = self._get_player_id()
        if not player_id:
            return
        
        # Get album info and URI
        try:
            album = self.client.music.get_album(item_id, provider)
            uri = album.get('uri', f"library://album/{item_id}")
            name = album.get('name', 'Unknown Album')
        except Exception as e:
            log(f"Error getting album: {e}")
            uri = f"library://album/{item_id}"
            name = 'Album'
        
        # Play on MA player
        try:
            log(f"Playing album on MA player {player_id}: {uri}")
            self.client.player_queues.play_media(player_id, uri)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playing: {name}", xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception as e:
            log(f"Playback error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playback failed: {e}", xbmcgui.NOTIFICATION_ERROR)
    
    def play_playlist(self):
        """Play a playlist via Music Assistant player."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        # Get or select a player
        player_id = self._get_player_id()
        if not player_id:
            return
        
        # Get playlist info and URI
        try:
            playlist = self.client.music.get_playlist(item_id, provider)
            uri = playlist.get('uri', f"library://playlist/{item_id}")
            name = playlist.get('name', 'Unknown Playlist')
        except Exception as e:
            log(f"Error getting playlist: {e}")
            uri = f"library://playlist/{item_id}"
            name = 'Playlist'
        
        # Play on MA player
        try:
            log(f"Playing playlist on MA player {player_id}: {uri}")
            self.client.player_queues.play_media(player_id, uri)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playing: {name}", xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception as e:
            log(f"Playback error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playback failed: {e}", xbmcgui.NOTIFICATION_ERROR)
    
    def play_radio(self):
        """Play a radio station via Music Assistant player."""
        item_id = self.params.get('id')
        provider = self.params.get('provider', 'library')
        
        # Get or select a player
        player_id = self._get_player_id()
        if not player_id:
            return
        
        # Get radio info and URI
        try:
            radio = self.client.music.get_radio(item_id, provider)
            uri = radio.get('uri', f"library://radio/{item_id}")
            name = radio.get('name', 'Unknown Station')
        except Exception as e:
            log(f"Error getting radio: {e}")
            uri = f"library://radio/{item_id}"
            name = 'Radio'
        
        # Play on MA player
        try:
            log(f"Playing radio on MA player {player_id}: {uri}")
            self.client.player_queues.play_media(player_id, uri)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playing: {name}", xbmcgui.NOTIFICATION_INFO, 3000)
        except Exception as e:
            log(f"Playback error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Playback failed: {e}", xbmcgui.NOTIFICATION_ERROR)
    
    def show_player_controls(self):
        """Show player control menu."""
        player_id = self._get_player_id()
        if not player_id:
            xbmcplugin.endOfDirectory(self.handle, succeeded=False)
            return
        
        # Get current queue state
        try:
            queue = self.client.player_queues.get(player_id)
            shuffle_status = "ON" if queue.get('shuffle_enabled') else "OFF"
            state = queue.get('state', 'unknown')
            current_item = queue.get('current_item', {})
            current_track = current_item.get('name', 'Nothing playing') if current_item else 'Nothing playing'
        except Exception:
            shuffle_status = "?"
            state = "unknown"
            current_track = "Unknown"
        
        # Info item - now playing
        li = xbmcgui.ListItem(label=f"[B]Now Playing:[/B] {current_track}")
        li.setArt({'icon': 'DefaultMusicSongs.png'})
        xbmcplugin.addDirectoryItem(handle=self.handle, url='', listitem=li, isFolder=False)
        
        # Control items - as folders that execute the action
        controls = [
            ("Play / Pause", 'ctrl_play_pause', 'DefaultAddonMusic.png'),
            ("Next Track", 'ctrl_next', 'DefaultAddonMusic.png'),
            ("Previous Track", 'ctrl_previous', 'DefaultAddonMusic.png'),
            ("Stop", 'ctrl_stop', 'DefaultAddonMusic.png'),
            (f"Shuffle ({shuffle_status})", 'ctrl_shuffle', 'DefaultAddonMusic.png'),
            ("Clear Queue", 'ctrl_clear_queue', 'DefaultMusicPlaylists.png'),
            ("Volume Up", 'ctrl_volume_up', 'DefaultAddonMusic.png'),
            ("Volume Down", 'ctrl_volume_down', 'DefaultAddonMusic.png'),
            ("Sync with Another Player", 'ctrl_sync_player', 'DefaultAddonMusic.png'),
            ("Unsync Player", 'ctrl_unsync_player', 'DefaultAddonMusic.png'),
            ("Transfer Queue to Player", 'ctrl_transfer_queue', 'DefaultMusicPlaylists.png'),
            ("Refresh", 'player_controls', 'DefaultAddonMusic.png'),
        ]
        
        for label, action, icon in controls:
            li = xbmcgui.ListItem(label=label)
            li.setArt({'icon': icon})
            # Use as folder - this prevents Kodi from trying to play it
            url = build_url({'action': action, 'player_id': player_id})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.endOfDirectory(self.handle)
    
    def ctrl_play_pause(self):
        """Toggle play/pause."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.player_queues.play_pause(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Play/Pause", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Play/Pause error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        # Go back to controls menu
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_next(self):
        """Skip to next track."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.player_queues.next(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Next Track", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Next error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_previous(self):
        """Go to previous track."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.player_queues.previous(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Previous Track", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Previous error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_stop(self):
        """Stop playback."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.player_queues.stop(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Stopped", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Stop error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_shuffle(self):
        """Toggle shuffle mode."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            # Get current state and toggle
            queue = self.client.player_queues.get(player_id)
            current_shuffle = queue.get('shuffle_enabled', False)
            new_shuffle = not current_shuffle
            self.client.player_queues.shuffle(player_id, new_shuffle)
            status = "ON" if new_shuffle else "OFF"
            xbmcgui.Dialog().notification(ADDON_NAME, f"Shuffle: {status}", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Shuffle error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_clear_queue(self):
        """Clear the queue."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        
        # Confirm before clearing
        if not xbmcgui.Dialog().yesno(ADDON_NAME, "Clear the queue?"):
            xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
            return
        
        try:
            self.client.player_queues.clear(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Queue Cleared", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Clear queue error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_volume_up(self):
        """Increase volume."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.players.volume_up(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Volume Up", xbmcgui.NOTIFICATION_INFO, 1000)
        except Exception as e:
            log(f"Volume up error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_volume_down(self):
        """Decrease volume."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.players.volume_down(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Volume Down", xbmcgui.NOTIFICATION_INFO, 1000)
        except Exception as e:
            log(f"Volume down error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_sync_player(self):
        """Sync current player with another player."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        
        try:
            players = self.client.players.players
            if not players or len(players) < 2:
                xbmcgui.Dialog().notification(ADDON_NAME, "No other players available", xbmcgui.NOTIFICATION_INFO)
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            # Build list of other players
            other_players = [p for p in players if p.get('player_id') != player_id]
            if not other_players:
                xbmcgui.Dialog().notification(ADDON_NAME, "No other players available", xbmcgui.NOTIFICATION_INFO)
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            names = [p.get('name', p.get('player_id', 'Unknown')) for p in other_players]
            
            selected = xbmcgui.Dialog().select("Sync with player:", names)
            if selected < 0:
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            target_player = other_players[selected].get('player_id')
            self.client.players.group(player_id, target_player)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Synced with {names[selected]}", xbmcgui.NOTIFICATION_INFO, 2000)
        except Exception as e:
            log(f"Sync error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_unsync_player(self):
        """Unsync player from group."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        try:
            self.client.players.ungroup(player_id)
            xbmcgui.Dialog().notification(ADDON_NAME, "Player Unsynced", xbmcgui.NOTIFICATION_INFO, 1500)
        except Exception as e:
            log(f"Unsync error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def ctrl_transfer_queue(self):
        """Transfer queue to another player."""
        player_id = self.params.get('player_id') or self._get_player_id()
        if not player_id:
            return
        
        try:
            players = self.client.players.players
            if not players or len(players) < 2:
                xbmcgui.Dialog().notification(ADDON_NAME, "No other players available", xbmcgui.NOTIFICATION_INFO)
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            # Build list of other players
            other_players = [p for p in players if p.get('player_id') != player_id]
            if not other_players:
                xbmcgui.Dialog().notification(ADDON_NAME, "No other players available", xbmcgui.NOTIFICATION_INFO)
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            names = [p.get('name', p.get('player_id', 'Unknown')) for p in other_players]
            
            selected = xbmcgui.Dialog().select("Transfer queue to:", names)
            if selected < 0:
                xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
                return
            
            target_player = other_players[selected].get('player_id')
            self.client.player_queues.transfer(player_id, target_player)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Queue transferred to {names[selected]}", xbmcgui.NOTIFICATION_INFO, 2000)
        except Exception as e:
            log(f"Transfer error: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "player_controls"})})')
    
    def show_queue(self):
        """Show player queues."""
        queues = self.client.player_queues.player_queues
        
        for queue in (queues or []):
            queue_id = queue.get('queue_id', '')
            name = queue.get('display_name', queue.get('name', queue_id))
            
            li = xbmcgui.ListItem(label=name)
            li.setArt({'icon': 'DefaultMusicPlaylists.png'})
            
            url = build_url({'action': 'queue_items', 'queue_id': queue_id})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.endOfDirectory(self.handle)
    
    def show_players(self):
        """Show available players."""
        players = self.client.players.players
        
        # Get hidden players list
        hidden_players = get_setting('hidden_players') or ''
        hidden_list = [p.strip() for p in hidden_players.split(',') if p.strip()]
        
        # Check if we should show hidden players
        show_hidden = self.params.get('show_hidden') == 'true'
        
        # Add show/hide toggle at top
        if hidden_list:
            if show_hidden:
                toggle_label = "Hide Hidden Players"
                toggle_url = build_url({'action': 'players', 'show_hidden': 'false'})
            else:
                toggle_label = f"Show Hidden Players ({len(hidden_list)})"
                toggle_url = build_url({'action': 'players', 'show_hidden': 'true'})
            
            li = xbmcgui.ListItem(label=f"[I]{toggle_label}[/I]")
            li.setArt({'icon': 'DefaultAddonMusic.png'})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=toggle_url, listitem=li, isFolder=True)
        
        for player in (players or []):
            player_id = player.get('player_id', '')
            name = player.get('name', player_id)
            state = player.get('playback_state', 'unknown')
            available = player.get('available', False)
            
            is_hidden = player_id in hidden_list
            
            # Skip hidden players unless show_hidden is true
            if is_hidden and not show_hidden:
                continue
            
            status = '🟢' if available else '🔴'
            if is_hidden:
                label = f"{status} [I]{name}[/I] [{state}] (hidden)"
            else:
                label = f"{status} {name} [{state}]"
            
            li = xbmcgui.ListItem(label=label)
            li.setArt({'icon': 'DefaultAddonMusic.png'})
            
            # Context menu with hide/show option
            if is_hidden:
                hide_action = ("Show Player", f'RunPlugin({build_url({"action": "unhide_player", "player_id": player_id})})')
            else:
                hide_action = ("Hide Player", f'RunPlugin({build_url({"action": "hide_player", "player_id": player_id})})')
            
            context_menu = [
                (get_localized_string(30608), f'RunPlugin({build_url({"action": "set_default_player", "player_id": player_id})})'),
                hide_action,
            ]
            li.addContextMenuItems(context_menu)
            
            url = build_url({'action': 'player_info', 'player_id': player_id})
            xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=False)
        
        xbmcplugin.setContent(self.handle, 'files')
        xbmcplugin.endOfDirectory(self.handle)
    
    def hide_player(self):
        """Hide a player from the list."""
        player_id = self.params.get('player_id', '')
        if not player_id:
            return
        
        hidden_players = get_setting('hidden_players') or ''
        hidden_list = [p.strip() for p in hidden_players.split(',') if p.strip()]
        
        if player_id not in hidden_list:
            hidden_list.append(player_id)
            set_setting('hidden_players', ','.join(hidden_list))
            xbmcgui.Dialog().notification(ADDON_NAME, "Player hidden", xbmcgui.NOTIFICATION_INFO, 1500)
        
        # Refresh players list
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "players"})})')
    
    def unhide_player(self):
        """Unhide a player."""
        player_id = self.params.get('player_id', '')
        if not player_id:
            return
        
        hidden_players = get_setting('hidden_players') or ''
        hidden_list = [p.strip() for p in hidden_players.split(',') if p.strip()]
        
        if player_id in hidden_list:
            hidden_list.remove(player_id)
            set_setting('hidden_players', ','.join(hidden_list))
            xbmcgui.Dialog().notification(ADDON_NAME, "Player visible", xbmcgui.NOTIFICATION_INFO, 1500)
        
        # Refresh players list
        xbmc.executebuiltin(f'Container.Update({build_url({"action": "players", "show_hidden": "true"})})')
    
    def set_default_player(self):
        """Set a player as the default."""
        player_id = self.params.get('player_id', '')
        
        if player_id:
            set_setting('ma_player_id', player_id)
            
            # Get player name for notification
            try:
                player = self.client.players.get(player_id)
                name = player.get('name', player_id)
            except Exception:
                name = player_id
            
            xbmcgui.Dialog().notification(ADDON_NAME, f"Default player: {name}", xbmcgui.NOTIFICATION_INFO)
    
    def select_player(self):
        """Show player selection dialog."""
        try:
            players = self.client.players.players
            if not players:
                xbmcgui.Dialog().ok(ADDON_NAME, "No Music Assistant players found.")
                return
            
            # Build list of players
            names = []
            player_ids = []
            current_player = get_setting('ma_player_id')
            
            for p in players:
                pid = p.get('player_id', '')
                name = p.get('name', pid)
                available = p.get('available', False)
                status = '🟢' if available else '🔴'
                
                if pid == current_player:
                    names.append(f"{status} {name} [Current]")
                else:
                    names.append(f"{status} {name}")
                player_ids.append(pid)
            
            # Show selection dialog
            selected = xbmcgui.Dialog().select("Select Music Assistant Player", names)
            
            if selected >= 0:
                set_setting('ma_player_id', player_ids[selected])
                xbmcgui.Dialog().notification(ADDON_NAME, f"Player set: {names[selected]}", xbmcgui.NOTIFICATION_INFO)
                
        except Exception as e:
            log(f"Error selecting player: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(ADDON_NAME, f"Error: {e}")
    
    def _add_load_more(self, action, offset, **extra_params):
        """Add a 'Load More' item."""
        li = xbmcgui.ListItem(label="[Load More...]")
        li.setArt({'icon': 'DefaultFolder.png'})
        
        params = {'action': action, 'offset': offset}
        params.update(extra_params)
        
        url = build_url(params)
        xbmcplugin.addDirectoryItem(handle=self.handle, url=url, listitem=li, isFolder=True)


if __name__ == '__main__':
    addon = MusicAssistantAddon()
    addon.run()
