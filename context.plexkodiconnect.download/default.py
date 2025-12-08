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
            
            # Find the Media/Part element with the key
            media = root.find('.//Media/Part')
            if media is not None:
                part_key = media.get('key')
                if part_key:
                    # Construct direct download URL
                    download_url = '{0}://{1}:{2}{3}?X-Plex-Token={4}'.format(
                        protocol, plex_server, plex_port, part_key, plex_token
                    )
                    LOG('Found direct download URL from metadata', xbmc.LOGINFO)
                    return download_url
    except Exception as e:
        LOG('Could not get Plex metadata: {0}'.format(str(e)), xbmc.LOGERROR)
    
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
    
    # If it's a videodb path, get the actual file path using JSON-RPC
    if file_path.startswith('videodb://'):
        LOG('Video database path detected, getting actual file path', xbmc.LOGINFO)
        
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
    
    # Extract Plex ID from the path
    match = re.search(r'plex_id=(\d+)', file_path)
    if match:
        plex_id = match.group(1)
        LOG('Extracted Plex ID: {0}'.format(plex_id), xbmc.LOGINFO)
    
    LOG('Final file path: |{0}|'.format(file_path), xbmc.LOGINFO)
    
    # Check if this is a Plex item or network path
    is_network = (file_path.startswith('smb://') or 
                  file_path.startswith('nfs://') or 
                  file_path.startswith('http://') or 
                  file_path.startswith('https://'))
    
    is_plex = 'plex' in file_path.lower() or file_path.startswith('plugin://plugin.video.plexkodiconnect')
    
    if not is_network and not is_plex:
        LOG('Not a Plex/network item: {0}'.format(file_path), xbmc.LOGERROR)
        return None
    
    return {
        'path': file_path,
        'title': title,
        'plex_id': plex_id,
        'year': listitem.getProperty('year'),
        'type': content_type,
        'episode_id': episode_id,
        'tvshow_id': tvshow_id,
        'season': season_num
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
    
    confirm = xbmcgui.Dialog().yesno(
        'Download Season',
        'Download {0} episodes from season {1}?'.format(len(episodes), season_num)
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    
    for idx, episode in enumerate(episodes):
        # Extract plex_id from episode file path
        match = re.search(r'plex_id=(\d+)', episode['file'])
        if match:
            ep_info = {
                'path': episode['file'],
                'title': episode['title'],
                'plex_id': match.group(1),
                'type': 'episode',
                'season': episode['season'],
                'episode': episode['episode']
            }
            
            if download_single_item(ep_info, show_notifications=False):
                success_count += 1
            else:
                fail_count += 1
    
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
    
    confirm = xbmcgui.Dialog().yesno(
        'Download Entire Show',
        'Download all {0} episodes?'.format(len(episodes))
    )
    
    if not confirm:
        return False
    
    success_count = 0
    fail_count = 0
    
    for idx, episode in enumerate(episodes):
        # Extract plex_id from episode file path
        match = re.search(r'plex_id=(\d+)', episode['file'])
        if match:
            ep_info = {
                'path': episode['file'],
                'title': episode['title'],
                'plex_id': match.group(1),
                'type': 'episode',
                'season': episode['season'],
                'episode': episode['episode']
            }
            
            if download_single_item(ep_info, show_notifications=False):
                success_count += 1
            else:
                fail_count += 1
    
    xbmcgui.Dialog().notification(
        'Show Download Complete',
        'Downloaded: {0}, Failed: {1}'.format(success_count, fail_count),
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
        else:
            download_path = os.path.join(download_path, 'Movies')
        
        if not xbmcvfs.exists(download_path):
            xbmcvfs.mkdirs(download_path)
    
    # Sanitize filename
    safe_title = "".join(c for c in item_info['title'] if c.isalnum() or c in (' ', '-', '_', '.'))
    
    # Format filename for TV episodes
    if item_info['type'] == 'episode':
        season = item_info.get('season', 0)
        episode = item_info.get('episode', 0)
        filename = "S{0:02d}E{1:02d} - {2}".format(season, episode, safe_title)
    else:
        # Format for movies
        year = item_info.get('year', '')
        if year:
            filename = "{0} ({1})".format(safe_title, year)
        else:
            filename = safe_title
    
    # Determine source path
    source_path = item_info['path']
    
    # If it's a plugin path, try to get the direct stream URL
    if source_path.startswith('plugin://') and item_info.get('plex_id'):
        LOG('Plugin path detected, getting direct stream URL', xbmc.LOGINFO)
        stream_url = get_plex_metadata(item_info['plex_id'])
        if stream_url:
            source_path = stream_url
            LOG('Using Plex download URL', xbmc.LOGINFO)
        else:
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
    ext = os.path.splitext(base_path)[1]
    if not ext:
        ext = '.mp4'  # Default for Plex streams
    
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
    """Main entry point - download movie or TV show from Plex"""
    
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
    
    # For TV episodes, offer options to download episode, season, or entire show
    if item_info['type'] == 'episode' and item_info.get('tvshow_id'):
        options = ['Download this episode']
        
        if item_info.get('season') is not None:
            options.append('Download entire season {0}'.format(item_info['season']))
        
        options.append('Download entire show')
        
        choice = xbmcgui.Dialog().select('Download Options', options)
        
        if choice == -1:  # User cancelled
            return False
        elif choice == 0:  # Single episode
            return download_single_item(item_info)
        elif choice == 1 and len(options) == 3:  # Season (if available)
            return download_season(item_info['tvshow_id'], item_info['season'])
        elif (choice == 1 and len(options) == 2) or (choice == 2):  # Entire show
            return download_show(item_info['tvshow_id'])
    else:
        # For movies, just download directly
        return download_single_item(item_info)


if __name__ == '__main__':
    download_from_plex()
