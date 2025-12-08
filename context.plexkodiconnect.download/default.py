# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys
import os
import json
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
import re

addon = xbmcaddon.Addon()
addonID = addon.getAddonInfo('id')
if sys.version_info.major == 3:
    addonFolder = xbmcvfs.translatePath('special://home/addons/' + addonID)
else:
    addonFolder = xbmc.translatePath('special://home/addons/' + addonID).decode('utf-8')


def LOG(msg, level=xbmc.LOGINFO):
    """Log message to Kodi log"""
    if sys.version_info.major == 3:
        log_message = '{0}: {1}'.format(addonID, msg)
        xbmc.log(log_message, level)
    else:
        log_message = u'{0}: {1}'.format(addonID, msg)
        xbmc.log(log_message.encode("utf-8"), level)


def getDownloadPath():
    """Get download path from settings"""
    path = addon.getSetting('download_path')
    if not path:
        path = xbmcgui.Dialog().browse(3, "Select Download Directory", 'files', '', False, True)
    if not path:
        return None
    addon.setSetting('download_path', path)
    return path


def get_plex_metadata(plex_id):
    """Get metadata from Plex to find the actual media file info"""
    LOG('Getting Plex metadata for ID: {0}'.format(plex_id), xbmc.LOGINFO)
    
    try:
        pkc_addon = xbmcaddon.Addon('plugin.video.plexkodiconnect')
        plex_token = pkc_addon.getSetting('accessToken')
        plex_server = pkc_addon.getSetting('ipaddress')
        plex_port = pkc_addon.getSetting('port')
        use_https = pkc_addon.getSetting('https') == 'true'
        
        if plex_server and plex_token:
            protocol = 'https' if use_https else 'http'
            
            # Get metadata to find the media part key
            import xml.etree.ElementTree as ET
            if sys.version_info.major == 3:
                import urllib.request
                import urllib.parse
                metadata_url = '{0}://{1}:{2}/library/metadata/{3}?X-Plex-Token={4}'.format(
                    protocol, plex_server, plex_port, plex_id, plex_token
                )
                req = urllib.request.Request(metadata_url)
                response = urllib.request.urlopen(req)
                xml_data = response.read()
            else:
                import urllib2
                metadata_url = '{0}://{1}:{2}/library/metadata/{3}?X-Plex-Token={4}'.format(
                    protocol, plex_server, plex_port, plex_id, plex_token
                )
                response = urllib2.urlopen(metadata_url)
                xml_data = response.read()
            
            root = ET.fromstring(xml_data)
            
            # Check if it's a music track
            track = root.find('.//Track')
            if track is not None:
                LOG('Found music track metadata', xbmc.LOGINFO)
                metadata = {
                    'type': 'track',
                    'title': track.get('title'),
                    'artist': track.get('grandparentTitle'),
                    'album': track.get('parentTitle'),
                    'track': track.get('index'),
                    'year': track.get('parentYear') or track.get('year'),
                    'duration': track.get('duration')
                }
                
                # Get download URL
                media = track.find('.//Media/Part')
                if media is not None:
                    part_key = media.get('key')
                    if part_key:
                        download_url = '{0}://{1}:{2}{3}?X-Plex-Token={4}'.format(
                            protocol, plex_server, plex_port, part_key, plex_token
                        )
                        metadata['download_url'] = download_url
                        return metadata
            
            # Otherwise check for video
            media = root.find('.//Media/Part')
            if media is not None:
                part_key = media.get('key')
                if part_key:
                    # Construct direct download URL
                    download_url = '{0}://{1}:{2}{3}?X-Plex-Token={4}'.format(
                        protocol, plex_server, plex_port, part_key, plex_token
                    )
                    LOG('Found direct download URL from metadata', xbmc.LOGINFO)
                    return {'download_url': download_url}
    except Exception as e:
        LOG('Could not get Plex metadata: {0}'.format(str(e)), xbmc.LOGERROR)
    
    return None


def get_plex_track_by_path(file_path):
    """Search Plex for a track by its file path and return download info"""
    LOG('Searching Plex for track by path: {0}'.format(file_path), xbmc.LOGINFO)
    
    try:
        pkc_addon = xbmcaddon.Addon('plugin.video.plexkodiconnect')
        plex_token = pkc_addon.getSetting('accessToken')
        plex_server = pkc_addon.getSetting('ipaddress')
        plex_port = pkc_addon.getSetting('port')
        use_https = pkc_addon.getSetting('https') == 'true'
        
        if not plex_server or not plex_token:
            LOG('Plex server or token not configured', xbmc.LOGERROR)
            return None
        
        protocol = 'https' if use_https else 'http'
        
        import xml.etree.ElementTree as ET
        if sys.version_info.major == 3:
            import urllib.request
            import urllib.parse
            
            # Extract filename from path for searching
            filename = os.path.basename(file_path)
            # Remove extension for better search
            search_term = os.path.splitext(filename)[0]
            
            # Search Plex for this track
            search_url = '{0}://{1}:{2}/search?query={3}&type=10&X-Plex-Token={4}'.format(
                protocol, plex_server, plex_port, urllib.parse.quote(search_term), plex_token
            )
            LOG('Plex search URL: {0}'.format(search_url.replace(plex_token, '***')), xbmc.LOGINFO)
            
            req = urllib.request.Request(search_url)
            response = urllib.request.urlopen(req)
            xml_data = response.read()
        else:
            import urllib2
            import urllib
            
            filename = os.path.basename(file_path)
            search_term = os.path.splitext(filename)[0]
            
            search_url = '{0}://{1}:{2}/search?query={3}&type=10&X-Plex-Token={4}'.format(
                protocol, plex_server, plex_port, urllib.quote(search_term), plex_token
            )
            
            response = urllib2.urlopen(search_url)
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        
        # Find matching track
        for track in root.findall('.//Track'):
            # Check if file path matches
            part = track.find('.//Media/Part')
            if part is not None:
                plex_file = part.get('file', '')
                # Compare filenames (paths may differ due to mount points)
                if os.path.basename(plex_file) == os.path.basename(file_path):
                    LOG('Found matching track in Plex: {0}'.format(track.get('title')), xbmc.LOGINFO)
                    
                    part_key = part.get('key')
                    if part_key:
                        download_url = '{0}://{1}:{2}{3}?X-Plex-Token={4}'.format(
                            protocol, plex_server, plex_port, part_key, plex_token
                        )
                        return {
                            'plex_id': track.get('ratingKey'),
                            'download_url': download_url,
                            'title': track.get('title'),
                            'artist': track.get('grandparentTitle'),
                            'album': track.get('parentTitle'),
                            'track': track.get('index')
                        }
        
        LOG('No matching track found in Plex search results', xbmc.LOGINFO)
        
    except Exception as e:
        LOG('Error searching Plex for track: {0}'.format(str(e)), xbmc.LOGERROR)
    
    return None


def get_season_episodes(tvshow_id, season_num):
    """Get all episodes in a season"""
    LOG('Getting episodes for show ID {0}, season {1}'.format(tvshow_id, season_num), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetEpisodes",
        "params": {
            "tvshowid": tvshow_id,
            "season": season_num,
            "properties": ["file", "title", "episode", "season"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'episodes' in result['result']:
        return result['result']['episodes']
    
    return []


def get_all_episodes(tvshow_id):
    """Get all episodes in a TV show"""
    LOG('Getting all episodes for show ID {0}'.format(tvshow_id), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetEpisodes",
        "params": {
            "tvshowid": tvshow_id,
            "properties": ["file", "title", "episode", "season"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'episodes' in result['result']:
        return result['result']['episodes']
    
    return []


def get_tvshow_seasons(tvshow_id):
    """Get all seasons in a TV show"""
    LOG('Getting seasons for show ID {0}'.format(tvshow_id), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetSeasons",
        "params": {
            "tvshowid": tvshow_id,
            "properties": ["season", "episode"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'seasons' in result['result']:
        return result['result']['seasons']
    
    return []


def get_tvshow_details(tvshow_id):
    """Get TV show details including title"""
    LOG('Getting show details for ID {0}'.format(tvshow_id), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetTVShowDetails",
        "params": {
            "tvshowid": tvshow_id,
            "properties": ["title", "year"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'tvshowdetails' in result['result']:
        return result['result']['tvshowdetails']
    
    return None


def get_album_songs(album_id):
    """Get all songs in an album"""
    LOG('Getting songs for album ID {0}'.format(album_id), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "AudioLibrary.GetSongs",
        "params": {
            "filter": {"albumid": album_id},
            "properties": ["file", "title", "track", "artist", "album"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'songs' in result['result']:
        return result['result']['songs']
    
    return []


def get_artist_songs(artist_name):
    """Get all songs by an artist"""
    LOG('Getting songs for artist: {0}'.format(artist_name), xbmc.LOGINFO)
    
    query = {
        "jsonrpc": "2.0",
        "method": "AudioLibrary.GetSongs",
        "params": {
            "filter": {"artist": artist_name},
            "properties": ["file", "title", "track", "artist", "album", "albumid"]
        },
        "id": 1
    }
    
    response = xbmc.executeJSONRPC(json.dumps(query))
    result = json.loads(response)
    
    if 'result' in result and 'songs' in result['result']:
        return result['result']['songs']
    
    return []


def write_nfo_file(dest_file, item_info, metadata=None):
    """Write .nfo metadata file for Kodi"""
    nfo_file = os.path.splitext(dest_file)[0] + '.nfo'
    
    try:
        if item_info['type'] == 'song' or item_info['type'] == 'music':
            # Music NFO format
            nfo_content = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            nfo_content += '<musicvideo>\n'
            nfo_content += '    <title>{0}</title>\n'.format(item_info.get('title', 'Unknown'))
            
            if metadata:
                if metadata.get('artist'):
                    nfo_content += '    <artist>{0}</artist>\n'.format(metadata['artist'])
                if metadata.get('album'):
                    nfo_content += '    <album>{0}</album>\n'.format(metadata['album'])
                if metadata.get('track'):
                    nfo_content += '    <track>{0}</track>\n'.format(metadata['track'])
                if metadata.get('year'):
                    nfo_content += '    <year>{0}</year>\n'.format(metadata['year'])
            else:
                artist = item_info.get('artist', 'Unknown')
                if isinstance(artist, list):
                    artist = artist[0] if artist else 'Unknown'
                nfo_content += '    <artist>{0}</artist>\n'.format(artist)
                nfo_content += '    <album>{0}</album>\n'.format(item_info.get('album', 'Unknown'))
                if item_info.get('track'):
                    nfo_content += '    <track>{0}</track>\n'.format(item_info['track'])
            
            nfo_content += '</musicvideo>\n'
        
        elif item_info['type'] == 'episode':
            # TV Episode NFO format
            nfo_content = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            nfo_content += '<episodedetails>\n'
            nfo_content += '    <title>{0}</title>\n'.format(item_info.get('title', 'Unknown'))
            if item_info.get('season'):
                nfo_content += '    <season>{0}</season>\n'.format(item_info['season'])
            if item_info.get('episode'):
                nfo_content += '    <episode>{0}</episode>\n'.format(item_info['episode'])
            nfo_content += '</episodedetails>\n'
        
        else:
            # Movie NFO format
            nfo_content = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            nfo_content += '<movie>\n'
            nfo_content += '    <title>{0}</title>\n'.format(item_info.get('title', 'Unknown'))
            if item_info.get('year'):
                nfo_content += '    <year>{0}</year>\n'.format(item_info['year'])
            nfo_content += '</movie>\n'
        
        # Write NFO file
        nfo_file_obj = xbmcvfs.File(nfo_file, 'w')
        nfo_file_obj.write(nfo_content.encode('utf-8') if sys.version_info.major == 3 else nfo_content)
        nfo_file_obj.close()
        
        LOG('Wrote NFO file: {0}'.format(nfo_file), xbmc.LOGINFO)
        return True
    
    except Exception as e:
        LOG('Could not write NFO file: {0}'.format(str(e)), xbmc.LOGERROR)
        return False


def get_plex_item_info():
    """Get information about the currently selected Plex item"""
    if not hasattr(sys, 'listitem'):
        LOG('Could not access listitem', xbmc.LOGERROR)
        return None
    
    listitem = sys.listitem
    file_path = listitem.getPath()
    title = listitem.getLabel()
    
    LOG('ListItem Path: |{0}|'.format(file_path), xbmc.LOGINFO)
    LOG('ListItem Title: |{0}|'.format(title), xbmc.LOGINFO)
    
    plex_id = None
    content_type = 'movie'
    episode_id = None
    tvshow_id = None
    season_num = None
    song_id = None
    album_id = None
    artist_id = None
    
    # If it's a videodb path, get the actual file path using JSON-RPC
    if file_path.startswith('videodb://'):
        LOG('Video database path detected, getting actual file path', xbmc.LOGINFO)
        
        # Strip query string for pattern matching
        base_path = file_path.split('?')[0]
        
        # Check if it's a TV show directory (e.g., videodb://tvshows/titles/123/)
        tvshow_dir_match = re.search(r'videodb://tvshows/titles/(\d+)/$', base_path)
        if tvshow_dir_match:
            tvshow_id = int(tvshow_dir_match.group(1))
            content_type = 'tvshow'
            LOG('Detected TV show directory, show ID: {0}'.format(tvshow_id), xbmc.LOGINFO)
            
            # Get show details
            show_details = get_tvshow_details(tvshow_id)
            if show_details:
                title = show_details.get('title', title)
            
            return {
                'path': file_path,
                'title': title,
                'plex_id': None,
                'year': listitem.getProperty('year'),
                'type': content_type,
                'episode_id': None,
                'tvshow_id': tvshow_id,
                'season': None,
                'song_id': None,
                'album_id': None,
                'artist_id': None,
                'artist': None,
                'album': None,
                'track': 0
            }
        
        # Check if it's a season directory (e.g., videodb://tvshows/titles/123/1/ or videodb://tvshows/titles/123/-1/)
        season_dir_match = re.search(r'videodb://tvshows/titles/(\d+)/(-?\d+)/$', base_path)
        if season_dir_match:
            tvshow_id = int(season_dir_match.group(1))
            season_num = int(season_dir_match.group(2))
            content_type = 'season'
            LOG('Detected season directory, show ID: {0}, season: {1}'.format(tvshow_id, season_num), xbmc.LOGINFO)
            
            # Get show details for the title
            show_details = get_tvshow_details(tvshow_id)
            show_title = show_details.get('title', 'Unknown') if show_details else 'Unknown'
            
            return {
                'path': file_path,
                'title': title,
                'show_title': show_title,
                'plex_id': None,
                'year': listitem.getProperty('year'),
                'type': content_type,
                'episode_id': None,
                'tvshow_id': tvshow_id,
                'season': season_num,
                'song_id': None,
                'album_id': None,
                'artist_id': None,
                'artist': None,
                'album': None,
                'track': 0
            }
        
        # Check if it's a movie
        movie_match = re.search(r'videodb://movies/titles/(\d+)', file_path)
        if movie_match:
            movie_id = int(movie_match.group(1))
            LOG('Extracted movie ID: {0}'.format(movie_id), xbmc.LOGINFO)
            
            query = {
                "jsonrpc": "2.0",
                "method": "VideoLibrary.GetMovieDetails",
                "params": {
                    "movieid": movie_id,
                    "properties": ["file"]
                },
                "id": 1
            }
            
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            LOG('JSON-RPC response: {0}'.format(result), xbmc.LOGINFO)
            
            if 'result' in result and 'moviedetails' in result['result']:
                file_path = result['result']['moviedetails'].get('file', '')
                LOG('Got actual file path from database: |{0}|'.format(file_path), xbmc.LOGINFO)
        
        # Check if it's a TV episode
        episode_match = re.search(r'videodb://tvshows/titles/(-?\d+)/(-?\d+)/(\d+)', file_path)
        if episode_match:
            episode_id = int(episode_match.group(3))
            content_type = 'episode'
            LOG('Extracted episode ID: {0}'.format(episode_id), xbmc.LOGINFO)
            
            query = {
                "jsonrpc": "2.0",
                "method": "VideoLibrary.GetEpisodeDetails",
                "params": {
                    "episodeid": episode_id,
                    "properties": ["file", "tvshowid", "season"]
                },
                "id": 1
            }
            
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            LOG('JSON-RPC response: {0}'.format(result), xbmc.LOGINFO)
            
            if 'result' in result and 'episodedetails' in result['result']:
                file_path = result['result']['episodedetails'].get('file', '')
                tvshow_id = result['result']['episodedetails'].get('tvshowid')
                season_num = result['result']['episodedetails'].get('season')
                LOG('Got episode file path: |{0}|'.format(file_path), xbmc.LOGINFO)
    
    # Check if it's a music item
    if file_path.startswith('musicdb://'):
        LOG('Music database path detected, getting actual file path', xbmc.LOGINFO)
        
        # Strip query string for pattern matching
        base_music_path = file_path.split('?')[0]
        
        # Check if it's an artist directory (e.g., musicdb://artists/123/)
        artist_dir_match = re.search(r'musicdb://artists/(\d+)/?$', base_music_path)
        if artist_dir_match:
            artist_id = int(artist_dir_match.group(1))
            content_type = 'artist'
            LOG('Detected artist directory, artist ID: {0}'.format(artist_id), xbmc.LOGINFO)
            
            # Get artist details
            query = {
                "jsonrpc": "2.0",
                "method": "AudioLibrary.GetArtistDetails",
                "params": {
                    "artistid": artist_id,
                    "properties": ["artist"]
                },
                "id": 1
            }
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            
            artist_name = title
            if 'result' in result and 'artistdetails' in result['result']:
                details = result['result']['artistdetails']
                artist_name = details.get('artist', title)
            
            return {
                'path': file_path,
                'title': artist_name,
                'plex_id': None,
                'year': listitem.getProperty('year'),
                'type': content_type,
                'episode_id': None,
                'tvshow_id': None,
                'season': None,
                'song_id': None,
                'album_id': None,
                'artist_id': artist_id,
                'artist': artist_name,
                'album': None,
                'track': 0
            }
        
        # Check if it's an album directory (e.g., musicdb://albums/123/ or musicdb://artists/1/123/)
        album_dir_match = re.search(r'musicdb://(?:albums|artists/\d+)/(\d+)/?$', base_music_path)
        if album_dir_match:
            album_id = int(album_dir_match.group(1))
            content_type = 'album'
            LOG('Detected album directory, album ID: {0}'.format(album_id), xbmc.LOGINFO)
            
            # Get album details
            query = {
                "jsonrpc": "2.0",
                "method": "AudioLibrary.GetAlbumDetails",
                "params": {
                    "albumid": album_id,
                    "properties": ["title", "artist", "artistid"]
                },
                "id": 1
            }
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            
            album_title = title
            artist_name = None
            if 'result' in result and 'albumdetails' in result['result']:
                details = result['result']['albumdetails']
                album_title = details.get('title', title)
                artists = details.get('artist', [])
                if artists:
                    artist_name = artists[0]
            
            return {
                'path': file_path,
                'title': album_title,
                'plex_id': None,
                'year': listitem.getProperty('year'),
                'type': content_type,
                'episode_id': None,
                'tvshow_id': None,
                'season': None,
                'song_id': None,
                'album_id': album_id,
                'artist_id': None,
                'artist': artist_name,
                'album': album_title,
                'track': 0
            }
        
        content_type = 'song'
        
        # Check if it's a song
        song_match = re.search(r'musicdb://songs/(\d+)', file_path)
        if song_match:
            song_id = int(song_match.group(1))
            LOG('Extracted song ID: {0}'.format(song_id), xbmc.LOGINFO)
            
            query = {
                "jsonrpc": "2.0",
                "method": "AudioLibrary.GetSongDetails",
                "params": {
                    "songid": song_id,
                    "properties": ["file", "albumid", "artist", "album", "track", "title"]
                },
                "id": 1
            }
            
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            LOG('JSON-RPC response: {0}'.format(result), xbmc.LOGINFO)
            
            if 'result' in result and 'songdetails' in result['result']:
                details = result['result']['songdetails']
                file_path = details.get('file', '')
                album_id = details.get('albumid')
                title = details.get('title', title)
                LOG('Got song file path: |{0}|'.format(file_path), xbmc.LOGINFO)
    
    # Extract Plex ID from the path
    match = re.search(r'plex_id=(\d+)', file_path)
    if match:
        plex_id = match.group(1)
        LOG('Extracted Plex ID: {0}'.format(plex_id), xbmc.LOGINFO)
    
    LOG('Final file path: |{0}|'.format(file_path), xbmc.LOGINFO)
    LOG('Content type: |{0}|'.format(content_type), xbmc.LOGINFO)
    
    # Check if this is a Plex item or network path
    is_network = (file_path.startswith('smb://') or 
                  file_path.startswith('nfs://') or 
                  file_path.startswith('http://') or 
                  file_path.startswith('https://'))
    
    is_plex = ('plex' in file_path.lower() or 
               file_path.startswith('plugin://plugin.video.plexkodiconnect') or 
               file_path.startswith('plugin://plugin.audio.plexkodiconnect'))
    
    # Also allow musicdb paths that contain plex content (determined by checking actual files)
    is_musicdb = file_path.startswith('musicdb://')
    
    if not is_network and not is_plex and not is_musicdb:
        LOG('Not a Plex/network/musicdb item: {0}'.format(file_path), xbmc.LOGERROR)
        return None
    
    return {
        'path': file_path,
        'title': title,
        'plex_id': plex_id,
        'year': listitem.getProperty('year'),
        'type': content_type,
        'episode_id': episode_id,
        'tvshow_id': tvshow_id,
        'season': season_num,
        'song_id': song_id,
        'album_id': album_id,
        'artist_id': artist_id,
        'artist': listitem.getProperty('artist') if content_type == 'song' else None,
        'album': listitem.getProperty('album') if content_type == 'song' else None,
        'track': int(listitem.getProperty('tracknumber')) if listitem.getProperty('tracknumber') else 0
    }


def copy_with_progress(source, dest, title, pDialog):
    """Copy file with progress updates"""
    LOG("Copying from {0} to {1}".format(source, dest), xbmc.LOGINFO)
    
    try:
        # Get file size for progress calculation
        if source.startswith('http://') or source.startswith('https://'):
            # For HTTP streams, we'll use chunked reading
            if sys.version_info.major == 3:
                import urllib.request
                req = urllib.request.Request(source)
                response = urllib.request.urlopen(req)
            else:
                import urllib2
                response = urllib2.urlopen(source)
            
            file_size = int(response.headers.get('Content-Length', 0))
            
            # Download in chunks
            chunk_size = 8192
            bytes_downloaded = 0
            
            with open(dest if dest.startswith('/') else xbmcvfs.translatePath(dest), 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    
                    if file_size > 0:
                        percent = int((bytes_downloaded / file_size) * 100)
                        pDialog.update(percent, 'Downloading {0}... {1}%'.format(title, percent))
            
            return True
        else:
            # For local/network files, use xbmcvfs
            pDialog.update(10, 'Copying {0}...'.format(title))
            
            if xbmcvfs.copy(source, dest):
                pDialog.update(100, 'Download complete')
                return True
            else:
                # Alternative method - read entire file
                LOG("xbmcvfs.copy failed, trying alternative method", xbmc.LOGINFO)
                pDialog.update(20, 'Reading file...')
                
                src_file = xbmcvfs.File(source, 'rb')
                file_data = src_file.readBytes()
                src_file.close()
                
                pDialog.update(60, 'Writing file...')
                
                dst_file = xbmcvfs.File(dest, 'wb')
                dst_file.write(file_data)
                dst_file.close()
                
                # Verify
                if xbmcvfs.exists(dest):
                    src_stat = xbmcvfs.Stat(source)
                    dst_stat = xbmcvfs.Stat(dest)
                    
                    if sys.version_info.major == 3:
                        src_size = src_stat.st_size()
                        dst_size = dst_stat.st_size()
                    else:
                        src_size = src_stat.st_size()
                        dst_size = dst_stat.st_size()
                    
                    if src_size == dst_size:
                        pDialog.update(100, 'Download complete')
                        return True
                
                return False
    except Exception as e:
        LOG('Copy error: {0}'.format(str(e)), xbmc.LOGERROR)
        return False


def download_season(tvshow_id, season_num):
    """Download an entire season"""
    episodes = get_season_episodes(tvshow_id, season_num)
    
    if not episodes:
        xbmcgui.Dialog().notification(
            'PlexKodiConnect Download',
            'No episodes found in season',
            os.path.join(addonFolder, "icon.png"),
            5000,
            True
        )
        return False
    
    # Get show title for the confirmation message
    show_details = get_tvshow_details(tvshow_id)
    show_title = show_details.get('title', 'Unknown Show') if show_details else 'Unknown Show'
    
    if season_num == 0:
        season_label = 'Specials'
    else:
        season_label = 'Season {0}'.format(season_num)
    
    confirm = xbmcgui.Dialog().yesno(
        'Download {0}'.format(season_label),
        'Download {0} episodes of "{1}" - {2}?'.format(len(episodes), show_title, season_label)
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    
    # Show overall progress
    pDialog = xbmcgui.DialogProgress()
    pDialog.create('Downloading {0}'.format(season_label), 'Starting download...')
    
    for idx, episode in enumerate(episodes):
        if pDialog.iscanceled():
            LOG("Season download cancelled by user", xbmc.LOGINFO)
            break
        
        overall_percent = int((idx / len(episodes)) * 100)
        pDialog.update(overall_percent, 'Episode {0} of {1}: {2}'.format(idx + 1, len(episodes), episode['title']))
        
        # Extract plex_id from episode file path
        match = re.search(r'plex_id=(\d+)', episode['file'])
        if match:
            ep_info = {
                'path': episode['file'],
                'title': episode['title'],
                'plex_id': match.group(1),
                'type': 'episode',
                'season': episode['season'],
                'episode': episode['episode'],
                'tvshow_id': tvshow_id
            }
            
            if download_single_item(ep_info, show_notifications=False):
                success_count += 1
            else:
                fail_count += 1
    
    pDialog.close()
    
    xbmcgui.Dialog().notification(
        'Season Download Complete',
        'Downloaded: {0}, Failed: {1}'.format(success_count, fail_count),
        os.path.join(addonFolder, "icon.png"),
        5000,
        True
    )
    
    return True


def download_show(tvshow_id):
    """Download an entire TV show"""
    episodes = get_all_episodes(tvshow_id)
    
    if not episodes:
        xbmcgui.Dialog().notification(
            'PlexKodiConnect Download',
            'No episodes found',
            os.path.join(addonFolder, "icon.png"),
            5000,
            True
        )
        return False
    
    # Get show title for the confirmation message
    show_details = get_tvshow_details(tvshow_id)
    show_title = show_details.get('title', 'Unknown Show') if show_details else 'Unknown Show'
    
    # Count seasons
    seasons = set(ep['season'] for ep in episodes)
    
    confirm = xbmcgui.Dialog().yesno(
        'Download Entire Show',
        'Download all {0} episodes ({1} seasons) of "{2}"?'.format(len(episodes), len(seasons), show_title)
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    
    # Show overall progress
    pDialog = xbmcgui.DialogProgress()
    pDialog.create('Downloading "{0}"'.format(show_title), 'Starting download...')
    
    for idx, episode in enumerate(episodes):
        if pDialog.iscanceled():
            LOG("Show download cancelled by user", xbmc.LOGINFO)
            break
        
        overall_percent = int((idx / len(episodes)) * 100)
        pDialog.update(overall_percent, 'Episode {0} of {1}: S{2:02d}E{3:02d} - {4}'.format(
            idx + 1, len(episodes), episode['season'], episode['episode'], episode['title']
        ))
        
        # Extract plex_id from episode file path
        match = re.search(r'plex_id=(\d+)', episode['file'])
        if match:
            ep_info = {
                'path': episode['file'],
                'title': episode['title'],
                'plex_id': match.group(1),
                'type': 'episode',
                'season': episode['season'],
                'episode': episode['episode'],
                'tvshow_id': tvshow_id
            }
            
            if download_single_item(ep_info, show_notifications=False):
                success_count += 1
            else:
                fail_count += 1
    
    pDialog.close()
    
    xbmcgui.Dialog().notification(
        'Show Download Complete',
        'Downloaded: {0}, Failed: {1}'.format(success_count, fail_count),
        os.path.join(addonFolder, "icon.png"),
        5000,
        True
    )
    
    return True


def download_album(album_id, album_name=None):
    """Download an entire album"""
    songs = get_album_songs(album_id)
    
    LOG('download_album: Found {0} songs for album ID {1}'.format(len(songs) if songs else 0, album_id), xbmc.LOGINFO)
    
    if not songs:
        xbmcgui.Dialog().notification(
            'PlexKodiConnect Download',
            'No songs found in album',
            os.path.join(addonFolder, "icon.png"),
            5000,
            True
        )
        return False
    
    if not album_name and songs:
        album_name = songs[0].get('album', 'Unknown Album')
    
    confirm = xbmcgui.Dialog().yesno(
        'Download Album',
        'Download {0} songs from "{1}"?'.format(len(songs), album_name)
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    skipped_count = 0
    
    # Show progress dialog
    pDialog = xbmcgui.DialogProgress()
    pDialog.create('Downloading Album', 'Starting download...')
    
    for idx, song in enumerate(songs):
        if pDialog.iscanceled():
            LOG("Album download cancelled by user", xbmc.LOGINFO)
            break
        
        overall_percent = int((idx / len(songs)) * 100)
        pDialog.update(overall_percent, 'Song {0} of {1}: {2}'.format(idx + 1, len(songs), song.get('title', 'Unknown')))
        
        file_path = song.get('file', '')
        LOG('download_album: Processing song "{0}" with path: {1}'.format(song.get('title', 'Unknown'), file_path), xbmc.LOGINFO)
        
        # Check if this is already a Plex direct download URL
        # Format: https://xxx.plex.direct:32400/library/parts/12345/...
        plex_direct_match = re.search(r'plex\.direct.*?/library/parts/(\d+)/', file_path)
        
        plex_id = None
        download_url = None
        
        # Extract plex_id from song file path (plugin style)
        plex_id_match = re.search(r'plex_id=(\d+)', file_path)
        
        if plex_id_match:
            plex_id = plex_id_match.group(1)
        elif plex_direct_match:
            # This is already a direct Plex download URL - use it directly
            plex_id = plex_direct_match.group(1)  # Use part ID as identifier
            download_url = file_path  # The path IS the download URL
            LOG('download_album: Found Plex direct URL with part ID: {0}'.format(plex_id), xbmc.LOGINFO)
        else:
            # Try to find the track in Plex by searching
            LOG('download_album: No plex_id in path, searching Plex...', xbmc.LOGINFO)
            plex_track = get_plex_track_by_path(file_path)
            if plex_track:
                plex_id = plex_track.get('plex_id')
                download_url = plex_track.get('download_url')
                LOG('download_album: Found track in Plex with ID: {0}'.format(plex_id), xbmc.LOGINFO)
            else:
                LOG('download_album: Could not find track in Plex, skipping: {0}'.format(file_path), xbmc.LOGWARNING)
                skipped_count += 1
                continue
        
        song_info = {
            'path': download_url if download_url else file_path,
            'title': song['title'],
            'plex_id': plex_id,
            'type': 'song',
            'track': song.get('track', 0),
            'artist': song.get('artist', ['Unknown'])[0] if song.get('artist') else 'Unknown',
            'album': song.get('album', 'Unknown'),
            'album_id': album_id
        }
        
        if download_single_item(song_info, show_notifications=False):
            success_count += 1
        else:
            fail_count += 1
    
    pDialog.close()
    
    message = 'Downloaded: {0}, Failed: {1}'.format(success_count, fail_count)
    if skipped_count > 0:
        message += ', Skipped: {0}'.format(skipped_count)
    
    xbmcgui.Dialog().notification(
        'Album Download Complete',
        message,
        os.path.join(addonFolder, "icon.png"),
        5000,
        True
    )
    
    return True


def download_artist(artist_name):
    """Download all songs by an artist"""
    songs = get_artist_songs(artist_name)
    
    LOG('download_artist: Found {0} songs for artist "{1}"'.format(len(songs) if songs else 0, artist_name), xbmc.LOGINFO)
    
    if not songs:
        xbmcgui.Dialog().notification(
            'PlexKodiConnect Download',
            'No songs found for artist',
            os.path.join(addonFolder, "icon.png"),
            5000,
            True
        )
        return False
    
    confirm = xbmcgui.Dialog().yesno(
        'Download Artist',
        'Download all {0} songs by "{1}"?'.format(len(songs), artist_name)
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    skipped_count = 0
    
    # Show progress dialog
    pDialog = xbmcgui.DialogProgress()
    pDialog.create('Downloading Artist', 'Starting download...')
    
    for idx, song in enumerate(songs):
        if pDialog.iscanceled():
            LOG("Artist download cancelled by user", xbmc.LOGINFO)
            break
        
        overall_percent = int((idx / len(songs)) * 100)
        pDialog.update(overall_percent, 'Song {0} of {1}: {2}'.format(idx + 1, len(songs), song.get('title', 'Unknown')))
        
        file_path = song.get('file', '')
        LOG('download_artist: Processing song "{0}" with path: {1}'.format(song.get('title', 'Unknown'), file_path), xbmc.LOGINFO)
        
        # Check if this is already a Plex direct download URL
        # Format: https://xxx.plex.direct:32400/library/parts/12345/...
        plex_direct_match = re.search(r'plex\.direct.*?/library/parts/(\d+)/', file_path)
        
        plex_id = None
        download_url = None
        
        # Extract plex_id from song file path (plugin style)
        plex_id_match = re.search(r'plex_id=(\d+)', file_path)
        
        if plex_id_match:
            plex_id = plex_id_match.group(1)
        elif plex_direct_match:
            # This is already a direct Plex download URL - use it directly
            plex_id = plex_direct_match.group(1)  # Use part ID as identifier
            download_url = file_path  # The path IS the download URL
            LOG('download_artist: Found Plex direct URL with part ID: {0}'.format(plex_id), xbmc.LOGINFO)
        else:
            # Try to find the track in Plex by searching
            LOG('download_artist: No plex_id in path, searching Plex...', xbmc.LOGINFO)
            plex_track = get_plex_track_by_path(file_path)
            if plex_track:
                plex_id = plex_track.get('plex_id')
                download_url = plex_track.get('download_url')
                LOG('download_artist: Found track in Plex with ID: {0}'.format(plex_id), xbmc.LOGINFO)
            else:
                LOG('download_artist: Could not find track in Plex, skipping: {0}'.format(file_path), xbmc.LOGWARNING)
                skipped_count += 1
                continue
        
        song_info = {
            'path': download_url if download_url else file_path,
            'title': song['title'],
            'plex_id': plex_id,
            'type': 'song',
            'track': song.get('track', 0),
            'artist': song.get('artist', ['Unknown'])[0] if song.get('artist') else 'Unknown',
            'album': song.get('album', 'Unknown'),
            'album_id': song.get('albumid')
        }
        
        if download_single_item(song_info, show_notifications=False):
            success_count += 1
        else:
            fail_count += 1
    
    pDialog.close()
    
    message = 'Downloaded: {0}, Failed: {1}'.format(success_count, fail_count)
    if skipped_count > 0:
        message += ', Skipped: {0}'.format(skipped_count)
    
    xbmcgui.Dialog().notification(
        'Artist Download Complete',
        message,
        os.path.join(addonFolder, "icon.png"),
        5000,
        True
    )
    
    return True


def download_single_item(item_info, show_notifications=True):
    """Download a single movie or episode from Plex to local storage"""
    
    # Get download directory
    download_path = getDownloadPath()
    
    if not download_path:
        LOG("No download path selected", xbmc.LOGERROR)
        return False
    
    # Check if download directory exists and is writable
    if not xbmcvfs.exists(download_path):
        LOG("Download directory does not exist!", xbmc.LOGERROR)
        if show_notifications:
            xbmcgui.Dialog().notification(
                'PlexKodiConnect Download',
                'Download path does not exist',
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
        return False
    
    # Test if directory is writable
    LOG("Checking if destination path {0} is writeable".format(download_path), xbmc.LOGINFO)
    test_file = os.path.join(download_path, "koditmp.txt")
    f = xbmcvfs.File(test_file, 'w')
    writeconfirm = f.write(str("1"))
    f.close()
    
    if writeconfirm:
        xbmcvfs.delete(test_file)
        LOG("Destination path is writeable", xbmc.LOGINFO)
    else:
        LOG("Destination path not writeable", xbmc.LOGERROR)
        if show_notifications:
            xbmcgui.Dialog().notification(
                'PlexKodiConnect Download',
                'Download path is not writeable',
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
        return False
    
    # Organize by type if setting is enabled
    organize_by_type = addon.getSetting('organize_by_type') == 'true'
    if organize_by_type:
        media_type = item_info.get('type', 'movie')
        if media_type == 'episode':
            download_path = os.path.join(download_path, 'TV Shows')
        elif media_type == 'song':
            download_path = os.path.join(download_path, 'Music')
        else:
            download_path = os.path.join(download_path, 'Movies')
        
        if not xbmcvfs.exists(download_path):
            xbmcvfs.mkdirs(download_path)
    
    # Sanitize filename
    safe_title = "".join(c for c in item_info['title'] if c.isalnum() or c in (' ', '-', '_', '.'))
    
    # For music, try to get album info if we don't have it yet
    if item_info['type'] == 'song' and item_info.get('song_id') and not item_info.get('album'):
        query = {
            "jsonrpc": "2.0",
            "method": "AudioLibrary.GetSongDetails",
            "params": {
                "songid": item_info['song_id'],
                "properties": ["album", "artist", "track"]
            },
            "id": 1
        }
        response = xbmc.executeJSONRPC(json.dumps(query))
        result = json.loads(response)
        if 'result' in result and 'songdetails' in result['result']:
            details = result['result']['songdetails']
            item_info['album'] = details.get('album', 'Unknown Album')
            if not item_info.get('artist'):
                artists = details.get('artist', ['Unknown Artist'])
                item_info['artist'] = artists[0] if artists else 'Unknown Artist'
            if not item_info.get('track') and details.get('track'):
                item_info['track'] = details.get('track', 0)
            LOG('Retrieved album info from Kodi: {0}'.format(item_info['album']), xbmc.LOGINFO)
    
    # Format filename for TV episodes
    if item_info['type'] == 'episode':
        season = item_info.get('season', 0)
        episode = item_info.get('episode', 0)
        
        # Try to extract episode info from title if it contains format like "9x18. Title" or "9x18 - Title"
        title_ep_match = re.search(r'(\d+)x(\d+)[.\s-]+(.+)', item_info['title'])
        if title_ep_match:
            # Use episode info from title
            season = int(title_ep_match.group(1))
            episode = int(title_ep_match.group(2))
            clean_title = title_ep_match.group(3).strip()
            safe_title = "".join(c for c in clean_title if c.isalnum() or c in (' ', '-', '_', '.'))
        
        filename = "S{0:02d}E{1:02d} - {2}".format(season, episode, safe_title)
    elif item_info['type'] == 'song':
        # Format for music: "01 - Song Title"
        track = item_info.get('track', 0)
        if track > 0:
            filename = "{0:02d} - {1}".format(track, safe_title)
        else:
            filename = safe_title
    else:
        # Format for movies
        year = item_info.get('year', '')
        if year:
            filename = "{0} ({1})".format(safe_title, year)
        else:
            filename = safe_title
    
    # Determine source path
    source_path = item_info['path']
    metadata = None
    
    # If it's a plugin path, try to get the direct stream URL and metadata
    if source_path.startswith('plugin://') and item_info.get('plex_id'):
        LOG('Plugin path detected, getting direct stream URL', xbmc.LOGINFO)
        plex_data = get_plex_metadata(item_info['plex_id'])
        
        if plex_data and isinstance(plex_data, dict):
            # For music tracks, we get enhanced metadata
            if plex_data.get('type') == 'track':
                metadata = plex_data
                source_path = plex_data.get('download_url')
                
                # Update item_info with better metadata from Plex
                if metadata.get('artist'):
                    item_info['artist'] = metadata['artist']
                if metadata.get('album'):
                    item_info['album'] = metadata['album']
                if metadata.get('track'):
                    item_info['track'] = int(metadata['track'])
                if metadata.get('title'):
                    item_info['title'] = metadata['title']
                
                # Force type to be song if we got track metadata
                item_info['type'] = 'song'
                
                LOG('Updated with Plex music metadata: {0}'.format(metadata), xbmc.LOGINFO)
            elif plex_data.get('download_url'):
                source_path = plex_data['download_url']
                LOG('Using Plex download URL', xbmc.LOGINFO)
        
        if not source_path or source_path.startswith('plugin://'):
            if show_notifications:
                xbmcgui.Dialog().notification(
                    'PlexKodiConnect Download',
                    'Could not get download URL. Check PlexKodiConnect settings.',
                    os.path.join(addonFolder, "icon.png"),
                    5000,
                    True
                )
            return False
    
    # Get file extension from source (before query parameters)
    base_path = source_path.split('?')[0]  # Remove query parameters
    ext = os.path.splitext(base_path)[1].lower()
    
    # Handle various audio/video formats
    if item_info['type'] == 'song' or item_info['type'] == 'music':
        # For music, keep original format but map to common extensions
        if ext in ['.m4a', '.m4b', '.aac']:
            ext = '.m4a'  # Keep m4a for Apple formats
        elif ext in ['.flac', '.wav', '.ogg']:
            ext = ext  # Keep lossless formats as-is
        elif not ext or ext not in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']:
            ext = '.mp3'  # Default to mp3 for unknown audio
    elif ext == '.ts':
        ext = '.mp4'  # Convert transport streams to mp4
    elif not ext:
        ext = '.mp4'  # Default for video
    
    # For music, organize into Artist/Album folders if enabled
    if item_info['type'] == 'song':
        organize_music = addon.getSetting('organize_music') == 'true'
        if organize_music:
            artist_name = item_info.get('artist', 'Unknown Artist')
            album_name = item_info.get('album', 'Unknown Album')
            
            safe_artist = "".join(c for c in artist_name if c.isalnum() or c in (' ', '-', '_', '.'))
            safe_album = "".join(c for c in album_name if c.isalnum() or c in (' ', '-', '_', '.'))
            
            # Create: Music/Artist/Album/
            artist_path = os.path.join(download_path, safe_artist)
            album_path = os.path.join(artist_path, safe_album)
            
            if not xbmcvfs.exists(artist_path):
                xbmcvfs.mkdirs(artist_path)
            if not xbmcvfs.exists(album_path):
                xbmcvfs.mkdirs(album_path)
            
            download_path = album_path
    
    # For TV shows, organize into show/season folders if enabled
    elif item_info['type'] == 'episode':
        organize_tvshows = addon.getSetting('organize_tvshows') == 'true'
        if organize_tvshows:
            # Get show name from JSON-RPC
            query = {
                "jsonrpc": "2.0",
                "method": "VideoLibrary.GetTVShowDetails",
                "params": {
                    "tvshowid": item_info.get('tvshow_id'),
                    "properties": ["title"]
                },
                "id": 1
            }
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            
            if 'result' in result and 'tvshowdetails' in result['result']:
                show_title = result['result']['tvshowdetails'].get('title', 'Unknown')
                safe_show_title = "".join(c for c in show_title if c.isalnum() or c in (' ', '-', '_', '.'))
                season_num = item_info.get('season', 1)
                
                # Create: TV Shows/Show Name/Season 1/
                show_path = os.path.join(download_path, safe_show_title)
                season_path = os.path.join(show_path, 'Season {0}'.format(season_num))
                
                if not xbmcvfs.exists(show_path):
                    xbmcvfs.mkdirs(show_path)
                if not xbmcvfs.exists(season_path):
                    xbmcvfs.mkdirs(season_path)
                
                download_path = season_path
    
    dest_filename = filename + ext
    dest_file = os.path.join(download_path, dest_filename)
    
    # Check if file already exists
    if xbmcvfs.exists(dest_file):
        LOG("File already exists: {0}".format(dest_filename), xbmc.LOGINFO)
        
        # For batch downloads, skip existing files
        if not show_notifications:
            return True
        
        continuedownload = xbmcgui.Dialog().yesno(
            'File Exists',
            '{0} already exists![CR][CR]Delete and download again?'.format(dest_filename)
        )
        if not continuedownload:
            LOG("User chose not to overwrite existing file", xbmc.LOGINFO)
            xbmcgui.Dialog().notification(
                'PlexKodiConnect Download',
                'Download cancelled',
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
            return False
        else:
            LOG("Deleting existing file as requested", xbmc.LOGINFO)
            xbmcvfs.delete(dest_file)
    
    # Show confirmation dialog for single downloads
    if show_notifications:
        confirm = xbmcgui.Dialog().yesno(
            'Download from Plex',
            'Download "{0}" to:[CR]{1}'.format(item_info["title"], download_path),
            nolabel='Cancel',
            yeslabel='Download'
        )
        
        if not confirm:
            return False
    
    # Notify if playing (only for single downloads)
    if show_notifications and xbmc.Player().isPlaying():
        xbmcgui.Dialog().notification(
            'Download started',
            item_info['title'],
            os.path.join(addonFolder, "icon.png"),
            3000,
            False
        )
    
    # Show progress dialog (regular dialog for better visibility)
    pDialog = xbmcgui.DialogProgress()
    pDialog.create('PlexKodiConnect Download', 'Preparing to download {0}...'.format(item_info["title"]))
    
    try:
        # Copy file with progress
        success = copy_with_progress(source_path, dest_file, item_info["title"], pDialog)
        
        xbmc.sleep(500)
        pDialog.close()
        
        if success:
            # Write metadata NFO file
            write_metadata = addon.getSetting('write_metadata') == 'true'
            if write_metadata:
                write_nfo_file(dest_file, item_info, metadata)
            
            if show_notifications:
                xbmcgui.Dialog().notification(
                    'Download Complete',
                    '{0} saved successfully!'.format(item_info["title"]),
                    os.path.join(addonFolder, "icon.png"),
                    5000,
                    True
                )
                
                # Ask if user wants to play the downloaded file
                auto_play_setting = addon.getSetting('auto_play') == 'true'
                if auto_play_setting:
                    play_now = xbmcgui.Dialog().yesno(
                        'Download Complete',
                        'Would you like to play the downloaded file now?'
                    )
                    
                    if play_now:
                        xbmc.Player().play(dest_file)
            
            return True
        else:
            if show_notifications:
                xbmcgui.Dialog().notification(
                    'Download Failed',
                    'Could not download file from Plex',
                    os.path.join(addonFolder, "icon.png"),
                    5000,
                    True
                )
            return False
    
    except Exception as e:
        pDialog.close()
        LOG("Download error: {0}".format(str(e)), xbmc.LOGERROR)
        if show_notifications:
            xbmcgui.Dialog().notification(
                'Download Error',
                str(e),
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
        return False


def download_from_plex():
    """Main entry point - download movie, TV show, or music from Plex"""
    
    item_info = get_plex_item_info()
    
    if not item_info:
        xbmcgui.Dialog().notification(
            'PlexKodiConnect Download',
            'Could not retrieve Plex item information',
            os.path.join(addonFolder, "icon.png"),
            5000,
            True
        )
        return False
    
    # Handle TV show directory (download entire show or select seasons)
    if item_info['type'] == 'tvshow' and item_info.get('tvshow_id'):
        tvshow_id = item_info['tvshow_id']
        show_title = item_info.get('title', 'Unknown Show')
        
        # Get available seasons
        seasons = get_tvshow_seasons(tvshow_id)
        
        if not seasons:
            xbmcgui.Dialog().notification(
                'PlexKodiConnect Download',
                'No seasons found for this show',
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
            return False
        
        # Build options list
        options = ['Download entire show ({0} seasons)'.format(len(seasons))]
        
        for season in seasons:
            season_num = season.get('season', 0)
            episode_count = season.get('episode', 0)
            if season_num == 0:
                options.append('Specials ({0} episodes)'.format(episode_count))
            else:
                options.append('Season {0} ({1} episodes)'.format(season_num, episode_count))
        
        choice = xbmcgui.Dialog().select('Download "{0}"'.format(show_title), options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Entire show
            return download_show(tvshow_id)
        else:
            # Individual season selected
            selected_season = seasons[choice - 1]
            return download_season(tvshow_id, selected_season.get('season', 0))
    
    # Handle season directory (download entire season or select episodes)
    elif item_info['type'] == 'season' and item_info.get('tvshow_id'):
        tvshow_id = item_info['tvshow_id']
        season_num = item_info['season']
        show_title = item_info.get('show_title', 'Unknown Show')
        
        # Get episodes in this season
        episodes = get_season_episodes(tvshow_id, season_num)
        
        if not episodes:
            xbmcgui.Dialog().notification(
                'PlexKodiConnect Download',
                'No episodes found in this season',
                os.path.join(addonFolder, "icon.png"),
                5000,
                True
            )
            return False
        
        # Build options
        if season_num == 0:
            season_label = 'Specials'
        else:
            season_label = 'Season {0}'.format(season_num)
        
        # Check setting for showing "Download entire show" option
        show_download_show = addon.getSetting('show_download_show_from_season') == 'true'
        
        options = ['Download entire {0} ({1} episodes)'.format(season_label, len(episodes))]
        
        if show_download_show:
            options.append('Download entire show')
        
        # If only one option, skip the dialog and download the season directly
        if len(options) == 1:
            return download_season(tvshow_id, season_num)
        
        choice = xbmcgui.Dialog().select('Download "{0}" - {1}'.format(show_title, season_label), options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Entire season
            return download_season(tvshow_id, season_num)
        elif choice == 1:  # Entire show
            return download_show(tvshow_id)
    
    # For TV episodes, offer options to download episode, season, or entire show
    elif item_info['type'] == 'episode' and item_info.get('tvshow_id'):
        # Check settings for menu options
        show_season_option = addon.getSetting('show_download_season_from_episode') == 'true'
        show_show_option = addon.getSetting('show_download_show_from_episode') == 'true'
        
        options = ['Download this episode']
        season_idx = -1
        show_idx = -1
        
        if show_season_option and item_info.get('season') is not None:
            options.append('Download entire season {0}'.format(item_info['season']))
            season_idx = len(options) - 1
        
        if show_show_option:
            options.append('Download entire show')
            show_idx = len(options) - 1
        
        # If only one option, skip the dialog and download directly
        if len(options) == 1:
            return download_single_item(item_info)
        
        choice = xbmcgui.Dialog().select('Download Options', options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Single episode
            return download_single_item(item_info)
        elif choice == season_idx:  # Season
            return download_season(item_info['tvshow_id'], item_info['season'])
        elif choice == show_idx:  # Entire show
            return download_show(item_info['tvshow_id'])
    
    # Handle artist directory (download entire artist discography)
    elif item_info['type'] == 'artist' and item_info.get('artist'):
        artist_name = item_info['artist']
        return download_artist(artist_name)
    
    # Handle album directory (download entire album or artist)
    elif item_info['type'] == 'album' and item_info.get('album_id'):
        album_id = item_info['album_id']
        album_title = item_info.get('title', 'Unknown Album')
        artist_name = item_info.get('artist')
        
        # Check setting for showing "Download entire artist" option
        show_artist_option = addon.getSetting('show_download_artist_from_album') == 'true'
        
        options = ['Download entire album']
        
        if show_artist_option and artist_name:
            options.append('Download all by {0}'.format(artist_name))
        
        # If only one option, skip the dialog and download the album directly
        if len(options) == 1:
            return download_album(album_id, album_title)
        
        choice = xbmcgui.Dialog().select('Download "{0}"'.format(album_title), options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Entire album
            return download_album(album_id, album_title)
        elif choice == 1:  # Entire artist
            return download_artist(artist_name)
    
    # For music, offer options to download song, album, or artist
    elif item_info['type'] == 'song':
        # Check settings for menu options
        show_album_option = addon.getSetting('show_download_album_from_song') == 'true'
        show_artist_option = addon.getSetting('show_download_artist_from_song') == 'true'
        
        options = ['Download this song']
        album_idx = -1
        artist_idx = -1
        
        if show_album_option and item_info.get('album_id'):
            options.append('Download entire album')
            album_idx = len(options) - 1
        
        # Get artist name from JSON-RPC
        if show_artist_option and item_info.get('song_id'):
            query = {
                "jsonrpc": "2.0",
                "method": "AudioLibrary.GetSongDetails",
                "params": {
                    "songid": item_info['song_id'],
                    "properties": ["artist"]
                },
                "id": 1
            }
            response = xbmc.executeJSONRPC(json.dumps(query))
            result = json.loads(response)
            if 'result' in result and 'songdetails' in result['result']:
                artists = result['result']['songdetails'].get('artist', [])
                if artists:
                    item_info['artist'] = artists[0]
                    options.append('Download all by {0}'.format(artists[0]))
                    artist_idx = len(options) - 1
        
        # If only one option, skip the dialog and download directly
        if len(options) == 1:
            return download_single_item(item_info)
        
        choice = xbmcgui.Dialog().select('Download Options', options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Single song
            return download_single_item(item_info)
        elif choice == album_idx:  # Album
            # Get album name
            if item_info.get('song_id'):
                query = {
                    "jsonrpc": "2.0",
                    "method": "AudioLibrary.GetSongDetails",
                    "params": {
                        "songid": item_info['song_id'],
                        "properties": ["album"]
                    },
                    "id": 1
                }
                response = xbmc.executeJSONRPC(json.dumps(query))
                result = json.loads(response)
                if 'result' in result and 'songdetails' in result['result']:
                    album_name = result['result']['songdetails'].get('album', 'Unknown')
                    return download_album(item_info['album_id'], album_name)
            return download_album(item_info['album_id'])
        elif choice == artist_idx:  # Artist
            return download_artist(item_info.get('artist', 'Unknown'))
    
    else:
        # For movies, just download directly
        return download_single_item(item_info)


if __name__ == '__main__':
    download_from_plex()
