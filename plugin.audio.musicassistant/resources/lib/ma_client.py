"""
Music Assistant 2.7+ API Client for Kodi
A synchronous REST client compatible with Music Assistant Server 2.7+
Based on the official API documentation.
"""

import json
from urllib.parse import urljoin, urlencode, quote

try:
    import requests
except ImportError:
    import xbmc
    xbmc.log("Music Assistant: requests module not found", xbmc.LOGERROR)
    requests = None


# Exceptions
class MusicAssistantError(Exception):
    """Base exception for Music Assistant errors."""
    pass


class CannotConnect(MusicAssistantError):
    """Cannot connect to server."""
    pass


class AuthenticationRequired(MusicAssistantError):
    """Authentication is required."""
    pass


class AuthenticationFailed(MusicAssistantError):
    """Authentication failed."""
    pass


class LoginFailed(MusicAssistantError):
    """Login failed."""
    pass


class InvalidServerVersion(MusicAssistantError):
    """Server version is not compatible."""
    pass


# Enums matching official API
class MediaType:
    ARTIST = 'artist'
    ALBUM = 'album'
    TRACK = 'track'
    PLAYLIST = 'playlist'
    RADIO = 'radio'
    AUDIOBOOK = 'audiobook'
    PODCAST = 'podcast'
    PODCAST_EPISODE = 'podcast_episode'


class QueueOption:
    PLAY = 'play'
    REPLACE = 'replace'
    NEXT = 'next'
    REPLACE_NEXT = 'replace_next'
    ADD = 'add'


class ImageType:
    THUMB = 'thumb'
    FANART = 'fanart'
    LOGO = 'logo'
    LANDSCAPE = 'landscape'


class MusicAssistantClient:
    """
    Synchronous client for Music Assistant Server 2.7+ API.
    Designed to be compatible with Kodi's synchronous plugin environment.
    """
    
    def __init__(self, server_url, token=None, timeout=30):
        """
        Initialize the Music Assistant client.
        
        Args:
            server_url: Base URL of the Music Assistant server (e.g., http://192.168.1.100:8095)
            token: Authentication token
            timeout: Request timeout in seconds
        """
        self.server_url = server_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self._session = None
        self._server_info = None
        
        # Initialize controllers
        self.music = MusicController(self)
        self.players = PlayersController(self)
        self.player_queues = PlayerQueuesController(self)
    
    def close(self):
        """Close the client and cleanup resources."""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
    
    def _get_session(self):
        """Get or create requests session with auth headers."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers['Content-Type'] = 'application/json'
            self._session.headers['Accept'] = 'application/json'
        
        if self.token:
            self._session.headers['Authorization'] = f'Bearer {self.token}'
        
        return self._session
    
    def _api_url(self):
        """Build API URL."""
        return f"{self.server_url}/api"
    
    def send_command(self, command, **args):
        """
        Send a command to the Music Assistant server.
        
        Args:
            command: Command string (e.g., 'music/artists/library_items')
            **args: Command arguments
            
        Returns:
            Response data from server
        """
        if requests is None:
            raise CannotConnect("requests module not available")
        
        session = self._get_session()
        url = self._api_url()
        
        # Build payload
        payload = {
            'command': command
        }
        
        # Only add args if there are any
        if args:
            payload['args'] = args
        
        try:
            response = session.post(url, json=payload, timeout=self.timeout)
            
            if response.status_code == 401:
                raise AuthenticationRequired("Authentication required")
            elif response.status_code == 403:
                raise AuthenticationFailed("Authentication failed - invalid or expired token")
            elif response.status_code >= 500:
                # Try to get error details from response
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict):
                        error_msg = error_data.get('error', error_data.get('message', str(error_data)))
                        if isinstance(error_msg, dict):
                            error_msg = error_msg.get('message', str(error_msg))
                        raise MusicAssistantError(f"Server error: {error_msg}")
                except (ValueError, KeyError):
                    pass
                raise MusicAssistantError(f"Server error {response.status_code}: {response.text[:200]}")
            
            response.raise_for_status()
            
            data = response.json()
            
            # Check for error in response
            if isinstance(data, dict) and 'error' in data:
                error_msg = data.get('error')
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get('message', str(error_msg))
                raise MusicAssistantError(str(error_msg))
            
            # Return result if present, otherwise the full data
            if isinstance(data, dict) and 'result' in data:
                return data['result']
            return data
            
        except requests.exceptions.ConnectionError as e:
            raise CannotConnect(f"Failed to connect to server: {e}")
        except requests.exceptions.Timeout:
            raise CannotConnect("Request timed out")
        except requests.exceptions.HTTPError as e:
            # Try to get more details from the response
            try:
                error_data = e.response.json() if e.response else None
                if error_data and isinstance(error_data, dict):
                    detail = error_data.get('error', error_data.get('detail', error_data.get('message', '')))
                    if detail:
                        raise MusicAssistantError(f"Request failed: {detail}")
            except (ValueError, AttributeError):
                pass
            raise MusicAssistantError(f"Request failed: {e}")
        except requests.exceptions.RequestException as e:
            raise MusicAssistantError(f"Request failed: {e}")
    
    @property
    def server_info(self):
        """Get server information."""
        if self._server_info is None:
            self._server_info = self.get_server_info()
        return self._server_info
    
    def get_server_info(self):
        """Fetch server information via API command (requires auth)."""
        return self.send_command('info')
    
    def test_connection(self):
        """Test connection to server."""
        try:
            info = self.get_server_info()
            return True
        except Exception as e:
            raise CannotConnect(f"Connection test failed: {e}")
    
    def get_media_item_image_url(self, item, image_type=ImageType.THUMB, size=0):
        """
        Get image URL for a media item.
        
        Args:
            item: Media item dict with image/metadata
            image_type: Type of image (thumb, fanart, etc.)
            size: Desired size (0 for original)
            
        Returns:
            Image URL string or None
        """
        if not isinstance(item, dict):
            return None
        
        # Check for image in item
        image = item.get('image')
        if image:
            return self._build_image_url(image, size)
        
        # Check metadata for images
        metadata = item.get('metadata', {})
        if metadata:
            for key in ['images', 'image', 'thumb']:
                if key in metadata and metadata[key]:
                    img = metadata[key]
                    if isinstance(img, list) and img:
                        img = img[0]
                    return self._build_image_url(img, size)
        
        # Check for images list
        images = item.get('images', [])
        if images:
            # Find matching type or use first
            for img in images:
                if isinstance(img, dict) and img.get('type') == image_type:
                    return self._build_image_url(img, size)
            # Fallback to first image
            return self._build_image_url(images[0], size)
        
        return None
    
    def _build_image_url(self, image, size=0):
        """Build full image URL from image data."""
        if isinstance(image, str):
            url = image
        elif isinstance(image, dict):
            url = image.get('path') or image.get('url') or image.get('thumb')
        else:
            return None
        
        if not url:
            return None
        
        # Make relative URLs absolute
        if url.startswith('/'):
            url = f"{self.server_url}{url}"
        
        # Add token if needed
        if self.token and 'token=' not in url:
            separator = '&' if '?' in url else '?'
            url = f"{url}{separator}token={self.token}"
        
        return url
    
    def get_stream_url(self, media_type, item_id, provider_instance_id_or_domain='library'):
        """
        Get streaming URL for a track.
        
        Args:
            media_type: Type of media (track, radio, etc.)
            item_id: Item ID
            provider_instance_id_or_domain: Provider ID or 'library'
            
        Returns:
            Stream URL string or None
        """
        # Get the full track/media item to extract the URL from provider_mappings
        try:
            if media_type == 'track':
                item = self.music.get_track(item_id, provider_instance_id_or_domain)
            elif media_type == 'radio':
                item = self.music.get_radio(item_id, provider_instance_id_or_domain)
            else:
                item = self.send_command(
                    f'music/{media_type}s/get',
                    item_id=str(item_id),
                    provider_instance_id_or_domain=provider_instance_id_or_domain
                )
            
            if not item:
                return None
            
            # Try to get URL from provider_mappings
            provider_mappings = item.get('provider_mappings', [])
            for mapping in provider_mappings:
                url = mapping.get('url')
                if url:
                    # Add token if needed for authentication
                    if self.token and 'token=' not in url:
                        separator = '&' if '?' in url else '?'
                        url = f"{url}{separator}token={self.token}"
                    return url
            
            # Fallback: try to get stream URL from metadata
            metadata = item.get('metadata', {})
            preview_url = metadata.get('preview')
            if preview_url:
                if preview_url.startswith('/'):
                    preview_url = f"{self.server_url}{preview_url}"
                if self.token and 'token=' not in preview_url:
                    separator = '&' if '?' in preview_url else '?'
                    preview_url = f"{preview_url}{separator}token={self.token}"
                return preview_url
            
            # Last resort: try the preview API command
            try:
                url = self.send_command(
                    f'music/{media_type}s/preview',
                    provider_instance_id_or_domain=provider_instance_id_or_domain,
                    item_id=str(item_id)
                )
                if url:
                    if isinstance(url, str) and url.startswith('/'):
                        url = f"{self.server_url}{url}"
                    if self.token and isinstance(url, str) and 'token=' not in url:
                        separator = '&' if '?' in url else '?'
                        url = f"{url}{separator}token={self.token}"
                    return url
            except Exception:
                pass
            
            return None
            
        except Exception as e:
            return None


class MusicController:
    """Controller for music library operations."""
    
    def __init__(self, client):
        self.client = client
    
    # Artists
    def get_library_artists(self, favorite=None, search=None, limit=500, offset=0, 
                           order_by='sort_name', album_artists_only=None):
        """Get artists from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        if album_artists_only is not None:
            args['album_artists_only'] = album_artists_only
        
        return self.client.send_command('music/artists/library_items', **args)
    
    def get_artist(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single artist."""
        return self.client.send_command(
            'music/artists/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    def get_artist_albums(self, item_id, provider_instance_id_or_domain='library', in_library_only=False):
        """Get albums for an artist."""
        return self.client.send_command(
            'music/artists/artist_albums',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain,
            in_library_only=in_library_only
        )
    
    def get_artist_tracks(self, item_id, provider_instance_id_or_domain='library', in_library_only=False):
        """Get tracks for an artist."""
        return self.client.send_command(
            'music/artists/artist_tracks',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain,
            in_library_only=in_library_only
        )
    
    # Albums
    def get_library_albums(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get albums from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/albums/library_items', **args)
    
    def get_album(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single album."""
        return self.client.send_command(
            'music/albums/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    def get_album_tracks(self, item_id, provider_instance_id_or_domain='library', in_library_only=False):
        """Get tracks for an album."""
        return self.client.send_command(
            'music/albums/album_tracks',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain,
            in_library_only=in_library_only
        )
    
    # Tracks
    def get_library_tracks(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get tracks from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/tracks/library_items', **args)
    
    def get_track(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single track."""
        return self.client.send_command(
            'music/tracks/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    # Playlists
    def get_library_playlists(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get playlists from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/playlists/library_items', **args)
    
    def get_playlist(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single playlist."""
        return self.client.send_command(
            'music/playlists/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    def get_playlist_tracks(self, item_id, provider_instance_id_or_domain='library', limit=500, offset=0):
        """Get tracks for a playlist."""
        return self.client.send_command(
            'music/playlists/playlist_tracks',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain,
            limit=limit,
            offset=offset
        )
    
    # Radios
    def get_library_radios(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get radio stations from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/radios/library_items', **args)
    
    def get_radio(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single radio station."""
        return self.client.send_command(
            'music/radios/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    # Podcasts
    def get_library_podcasts(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get podcasts from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/podcasts/library_items', **args)
    
    def get_podcast(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single podcast."""
        return self.client.send_command(
            'music/podcasts/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    def get_podcast_episodes(self, item_id, provider_instance_id_or_domain='library'):
        """Get episodes for a podcast."""
        return self.client.send_command(
            'music/podcasts/podcast_episodes',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    # Audiobooks
    def get_library_audiobooks(self, favorite=None, search=None, limit=500, offset=0, order_by='sort_name'):
        """Get audiobooks from the library."""
        args = {
            'limit': limit,
            'offset': offset,
            'order_by': order_by
        }
        if favorite is not None:
            args['favorite'] = favorite
        if search:
            args['search'] = search
        
        return self.client.send_command('music/audiobooks/library_items', **args)
    
    def get_audiobook(self, item_id, provider_instance_id_or_domain='library'):
        """Get a single audiobook."""
        return self.client.send_command(
            'music/audiobooks/get',
            item_id=str(item_id),
            provider_instance_id_or_domain=provider_instance_id_or_domain
        )
    
    # Search
    def search(self, search_query, media_types=None, limit=25):
        """
        Search across all media types.
        
        Args:
            search_query: Search string
            media_types: List of media types to search (None for all)
            limit: Maximum results per type
        """
        args = {'search_query': search_query}
        if media_types:
            args['media_types'] = media_types
        if limit:
            args['limit'] = limit
        
        return self.client.send_command('music/search', **args)
    
    # Recently played and favorites
    def get_recently_played(self, limit=25, media_types=None):
        """Get recently played items."""
        args = {'limit': limit}
        if media_types:
            args['media_types'] = media_types
        return self.client.send_command('music/recently_played_items', **args)
    
    def add_to_favorites(self, item_id, media_type):
        """Add item to favorites."""
        return self.client.send_command(
            'music/favorites/add_item',
            item_id=str(item_id),
            media_type=media_type
        )
    
    def remove_from_favorites(self, item_id, media_type):
        """Remove item from favorites."""
        return self.client.send_command(
            'music/favorites/remove_item',
            item_id=str(item_id),
            media_type=media_type
        )
    
    # Library management
    def add_to_library(self, item_id, media_type):
        """Add item to library."""
        return self.client.send_command(
            'music/library/add_item',
            item_id=str(item_id),
            media_type=media_type
        )
    
    def remove_from_library(self, item_id, media_type):
        """Remove item from library."""
        return self.client.send_command(
            'music/library/remove_item',
            item_id=str(item_id),
            media_type=media_type
        )


class PlayersController:
    """Controller for player operations."""
    
    def __init__(self, client):
        self.client = client
    
    @property
    def players(self):
        """Get all players."""
        return self.client.send_command('players/all')
    
    def get(self, player_id):
        """Get player by ID."""
        return self.client.send_command('players/get', player_id=player_id)
    
    def get_by_name(self, name):
        """Get player by name."""
        return self.client.send_command('players/get_by_name', name=name)
    
    def play(self, player_id):
        """Start playback."""
        return self.client.send_command('players/cmd/play', player_id=player_id)
    
    def pause(self, player_id):
        """Pause playback."""
        return self.client.send_command('players/cmd/pause', player_id=player_id)
    
    def play_pause(self, player_id):
        """Toggle play/pause."""
        return self.client.send_command('players/cmd/play_pause', player_id=player_id)
    
    def stop(self, player_id):
        """Stop playback."""
        return self.client.send_command('players/cmd/stop', player_id=player_id)
    
    def next(self, player_id):
        """Skip to next track."""
        return self.client.send_command('players/cmd/next', player_id=player_id)
    
    def previous(self, player_id):
        """Go to previous track."""
        return self.client.send_command('players/cmd/previous', player_id=player_id)
    
    def volume_set(self, player_id, volume_level):
        """Set volume (0-100)."""
        return self.client.send_command(
            'players/cmd/volume_set',
            player_id=player_id,
            volume_level=volume_level
        )
    
    def volume_up(self, player_id):
        """Increase volume."""
        return self.client.send_command('players/cmd/volume_up', player_id=player_id)
    
    def volume_down(self, player_id):
        """Decrease volume."""
        return self.client.send_command('players/cmd/volume_down', player_id=player_id)
    
    def volume_mute(self, player_id, muted=True):
        """Mute/unmute player."""
        return self.client.send_command(
            'players/cmd/volume_mute',
            player_id=player_id,
            muted=muted
        )
    
    def power(self, player_id, powered=True):
        """Power on/off player."""
        return self.client.send_command(
            'players/cmd/power',
            player_id=player_id,
            powered=powered
        )
    
    def seek(self, player_id, position):
        """Seek to position in seconds."""
        return self.client.send_command(
            'players/cmd/seek',
            player_id=player_id,
            position=position
        )
    
    def group(self, player_id, target_player):
        """Sync/group player with target player."""
        return self.client.send_command(
            'players/cmd/group',
            player_id=player_id,
            target_player=target_player
        )
    
    def ungroup(self, player_id):
        """Ungroup/unsync player from group."""
        return self.client.send_command(
            'players/cmd/ungroup',
            player_id=player_id
        )


class PlayerQueuesController:
    """Controller for player queue operations."""
    
    def __init__(self, client):
        self.client = client
    
    @property
    def player_queues(self):
        """Get all player queues."""
        return self.client.send_command('player_queues/all')
    
    def get(self, queue_id):
        """Get queue by ID."""
        return self.client.send_command('player_queues/get', queue_id=queue_id)
    
    def get_active_queue(self):
        """Get the active player queue."""
        return self.client.send_command('player_queues/get_active_queue')
    
    def get_queue_items(self, queue_id, limit=100, offset=0):
        """Get items in a queue."""
        return self.client.send_command(
            'player_queues/items',
            queue_id=queue_id,
            limit=limit,
            offset=offset
        )
    
    def play_media(self, queue_id, media, option=None, radio_mode=False):
        """
        Play media on a queue.
        
        Args:
            queue_id: Player queue ID
            media: Media URI(s) or media item(s) to play
            option: Queue option (play, replace, next, add) - optional
            radio_mode: Enable radio mode (continuous similar tracks)
        """
        args = {
            'queue_id': queue_id,
            'media': media,
        }
        # Only add option if explicitly provided
        if option is not None:
            args['option'] = option
        if radio_mode:
            args['radio_mode'] = radio_mode
            
        return self.client.send_command('player_queues/play_media', **args)
    
    def play(self, queue_id):
        """Start queue playback."""
        return self.client.send_command('player_queues/play', queue_id=queue_id)
    
    def pause(self, queue_id):
        """Pause queue playback."""
        return self.client.send_command('player_queues/pause', queue_id=queue_id)
    
    def play_pause(self, queue_id):
        """Toggle play/pause."""
        return self.client.send_command('player_queues/play_pause', queue_id=queue_id)
    
    def stop(self, queue_id):
        """Stop queue playback."""
        return self.client.send_command('player_queues/stop', queue_id=queue_id)
    
    def next(self, queue_id):
        """Skip to next track."""
        return self.client.send_command('player_queues/next', queue_id=queue_id)
    
    def previous(self, queue_id):
        """Go to previous track."""
        return self.client.send_command('player_queues/previous', queue_id=queue_id)
    
    def clear(self, queue_id):
        """Clear the queue."""
        return self.client.send_command('player_queues/clear', queue_id=queue_id)
    
    def shuffle(self, queue_id, shuffle_enabled):
        """Enable/disable shuffle."""
        return self.client.send_command(
            'player_queues/shuffle',
            queue_id=queue_id,
            shuffle_enabled=shuffle_enabled
        )
    
    def repeat(self, queue_id, repeat_mode):
        """Set repeat mode (off, one, all)."""
        return self.client.send_command(
            'player_queues/repeat',
            queue_id=queue_id,
            repeat_mode=repeat_mode
        )
    
    def seek(self, queue_id, position):
        """Seek to position in seconds."""
        return self.client.send_command(
            'player_queues/seek',
            queue_id=queue_id,
            position=position
        )
    
    def play_index(self, queue_id, index):
        """Play item at specific index in queue."""
        return self.client.send_command(
            'player_queues/play_index',
            queue_id=queue_id,
            index=index
        )
    
    def delete_item(self, queue_id, item_id_or_index):
        """Delete item from queue."""
        return self.client.send_command(
            'player_queues/delete_item',
            queue_id=queue_id,
            item_id_or_index=item_id_or_index
        )
    
    def move_item(self, queue_id, item_id, target_index):
        """Move item to target position."""
        return self.client.send_command(
            'player_queues/move_item',
            queue_id=queue_id,
            item_id=item_id,
            target_index=target_index
        )
    
    def transfer(self, source_queue_id, target_queue_id):
        """Transfer queue to another player queue."""
        return self.client.send_command(
            'player_queues/transfer',
            source_queue_id=source_queue_id,
            target_queue_id=target_queue_id
        )


# Authentication helpers - using /auth/login endpoint (not /api command)
def login(server_url, username, password, device_name="Kodi Addon", timeout=30):
    """
    Login with username and password using /auth/login endpoint.
    
    Args:
        server_url: Music Assistant server URL
        username: Username
        password: Password
        device_name: Device name for token
        timeout: Request timeout
        
    Returns:
        Dict with token and user info
    """
    url = f"{server_url.rstrip('/')}/auth/login"
    
    # Use the credentials format from the web UI
    payload = {
        'credentials': {
            'username': username,
            'password': password
        }
    }
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        
        if response.status_code == 401:
            raise LoginFailed("Invalid username or password")
        
        response.raise_for_status()
        data = response.json()
        
        if not data.get('success'):
            error_msg = data.get('error', 'Login failed')
            raise LoginFailed(str(error_msg))
        
        token = data.get('token')
        user = data.get('user', {})
        
        if not token:
            raise LoginFailed("No token received from server")
        
        return {'token': token, 'user': user}
        
    except requests.exceptions.ConnectionError as e:
        raise CannotConnect(f"Failed to connect: {e}")
    except requests.exceptions.RequestException as e:
        raise CannotConnect(f"Request failed: {e}")


def login_with_token(server_url, username, password, token_name="Kodi Addon", timeout=30):
    """
    Login and get token.
    
    Args:
        server_url: Music Assistant server URL
        username: Username
        password: Password
        token_name: Name for the token (unused, kept for API compatibility)
        timeout: Request timeout
        
    Returns:
        Tuple of (user_info, token)
    """
    result = login(server_url, username, password, token_name, timeout)
    return result.get('user', {}), result.get('token', '')


def get_server_info(server_url, token=None, timeout=30):
    """
    Get server information (requires authentication in MA 2.7+).
    
    Args:
        server_url: Music Assistant server URL
        token: Authentication token
        timeout: Request timeout
        
    Returns:
        Server info dict
    """
    url = f"{server_url.rstrip('/')}/api"
    
    payload = {'command': 'info'}
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        
        if 'result' in data:
            return data['result']
        return data
    except requests.exceptions.RequestException as e:
        raise CannotConnect(f"Failed to get server info: {e}")


def check_server_reachable(server_url, timeout=10):
    """
    Check if the server is reachable (simple connection test).
    
    Args:
        server_url: Music Assistant server URL
        timeout: Request timeout
        
    Returns:
        True if server responds, raises CannotConnect otherwise
    """
    try:
        # Just try to connect to the server
        response = requests.get(server_url.rstrip('/'), timeout=timeout, allow_redirects=True)
        # Any response means the server is reachable
        return True
    except requests.exceptions.RequestException as e:
        raise CannotConnect(f"Cannot reach server: {e}")
