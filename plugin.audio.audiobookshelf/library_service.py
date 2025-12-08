import requests
import xbmc
import json

class AudioBookShelfLibraryService:
	"""Library service for Audiobookshelf API - Kodi 21 compatible"""
	
	def __init__(self, base_url=None, token=None):
		"""Initialize the library service with base URL and authentication token"""
		self.token = token
		self.base_url = base_url
		self.headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {token}"
		}

	def get_all_libraries(self):
		"""Get all available libraries from the server"""
		url = f"{self.base_url}/api/libraries"
		response = requests.get(url, headers=self.headers)
		response.raise_for_status()
		return response.json()

	def get_library(self, library_id, include_filterdata=False):
		"""Get details for a specific library"""
		url = f"{self.base_url}/api/libraries/{library_id}"
		params = {}
		if include_filterdata:
			params["include"] = "filterdata"
		
		response = requests.get(url, headers=self.headers, params=params)
		response.raise_for_status()
		return response.json()

	def get_library_items(self, library_id, limit=None, page=None, sort=None, desc=None, 
						  filter=None, minified=None, collapseseries=None, include=None):
		"""Get items from a specific library with optional filters"""
		url = f"{self.base_url}/api/libraries/{library_id}/items"
		params = {}
		
		if limit is not None:
			params["limit"] = limit
		if page is not None:
			params["page"] = page
		if sort is not None:
			params["sort"] = sort
		if desc is not None:
			params["desc"] = desc
		if filter is not None:
			params["filter"] = filter
		if minified is not None:
			params["minified"] = minified
		if collapseseries is not None:
			params["collapseseries"] = collapseseries
		if include is not None:
			params["include"] = include
			
		response = requests.get(url, headers=self.headers, params=params)
		response.raise_for_status()
		return response.json()

	def get_library_item_by_id(self, item_id, expanded=None, include=None, episode=None):
		"""Get detailed information about a specific library item"""
		url = f"{self.base_url}/api/items/{item_id}"
		params = {}
		
		if expanded is not None:
			params["expanded"] = expanded
		if include is not None:
			params["include"] = include
		if episode is not None:
			params["episode"] = episode
		
		response = requests.get(url, headers=self.headers, params=params)
		response.raise_for_status()
		return response.json()

	def play_library_item_by_id(self, item_id, episode_id=None, device_info=None, 
								force_direct_play=False, force_transcode=False, 
								supported_mime_types=None, media_player="unknown"):
		"""Request playback information for a library item"""
		if episode_id:
			url = f"{self.base_url}/api/items/{item_id}/play/{episode_id}"
		else:
			url = f"{self.base_url}/api/items/{item_id}/play"

		payload = {
			"forceDirectPlay": force_direct_play,
			"forceTranscode": force_transcode,
			"mediaPlayer": media_player
		}

		if device_info:
			payload["deviceInfo"] = device_info

		if supported_mime_types:
			payload["supportedMimeTypes"] = supported_mime_types

		response = requests.post(url, headers=self.headers, json=payload)
		response.raise_for_status()
		return response.json()

	def get_file_url(self, iid, episode_id=None):
		"""Get the streaming URL for an audiobook file or podcast episode"""
		try:
			# First try to get the item details to find direct file access
			item = self.get_library_item_by_id(iid, expanded=1 if episode_id else None, episode=episode_id)
			
			# For podcast episodes
			if episode_id:
				xbmc.log(f"Getting file URL for episode {episode_id}", xbmc.LOGINFO)
				
				# Look for the episode in the media
				episodes = item.get('media', {}).get('episodes', [])
				episode_data = None
				
				for ep in episodes:
					if ep.get('id') == episode_id:
						episode_data = ep
						break
				
				if episode_data and 'audioFile' in episode_data:
					audio_file = episode_data['audioFile']
					ino = audio_file.get('ino')
					
					if ino:
						# Use direct file streaming endpoint for episode
						direct_url = f"{self.base_url}/api/items/{iid}/file/{ino}?token={self.token}"
						xbmc.log(f"Using direct episode file URL: {direct_url}", xbmc.LOGINFO)
						return direct_url
			
			# For regular audiobooks - check if we can get direct file access
			if 'media' in item and 'audioFiles' in item['media']:
				audio_files = item['media']['audioFiles']
				if audio_files and len(audio_files) > 0:
					# Get the first audio file's ino
					audio_file = audio_files[0]
					ino = audio_file.get('ino')
					
					if ino:
						# Use direct file streaming endpoint
						direct_url = f"{self.base_url}/api/items/{iid}/file/{ino}?token={self.token}"
						xbmc.log(f"Using direct file URL: {direct_url}", xbmc.LOGINFO)
						return direct_url
			
			# Fallback to play session API (HLS)
			xbmc.log("Falling back to play session API", xbmc.LOGINFO)
			response = self.play_library_item_by_id(
				iid,
				episode_id=episode_id,
				force_direct_play=True,  # Request direct play, not transcoding
				supported_mime_types=["audio/flac", "audio/mpeg", "audio/mp4", "audio/m4b"]
			)

			full_content_url = None
			if "audioTracks" in response and len(response["audioTracks"]) > 0:
				relative_content_url = response["audioTracks"][0]["contentUrl"]
				full_content_url = f"{self.base_url}{relative_content_url}?token={self.token}"
				xbmc.log(f"Using audioTrack URL: {full_content_url}", xbmc.LOGINFO)

			if not full_content_url:
				raise Exception("Content URL not found or empty.")
			
			return full_content_url
		except Exception as e:
			xbmc.log(f"Error getting file URL: {str(e)}", xbmc.LOGERROR)
			raise

	def get_media_progress(self, library_item_id, episode_id=None):
		"""Get playback progress for a library item"""
		endpoint = f"/api/me/progress/{library_item_id}"
		if episode_id:
			endpoint += f"/{episode_id}"

		try:
			response = requests.get(self.base_url + endpoint, headers=self.headers)
			
			# 404 means no progress saved yet (not an error)
			if response.status_code == 404:
				xbmc.log(f"No progress found for item (new item)", xbmc.LOGINFO)
				return None
			
			response.raise_for_status()
			return response.json()
		except json.JSONDecodeError:
			xbmc.log("Failed to decode JSON response for media progress", xbmc.LOGERROR)
			xbmc.log(response.text, xbmc.LOGDEBUG)
			return None
		except Exception as e:
			xbmc.log(f"Error getting media progress: {str(e)}", xbmc.LOGDEBUG)
			return None

	def update_media_progress(self, library_item_id, current_time, duration, is_finished=False, episode_id=None):
		"""Update playback progress on the server"""
		endpoint = f"/api/me/progress/{library_item_id}"
		if episode_id:
			endpoint += f"/{episode_id}"

		data = {
			"currentTime": current_time,
			"duration": duration,
			"isFinished": is_finished,
			"progress": (current_time / duration) if duration > 0 else 0
		}
		
		try:
			response = requests.patch(self.base_url + endpoint, headers=self.headers, json=data)
			response.raise_for_status()
			xbmc.log(f"Progress updated: {current_time:.1f}s / {duration:.1f}s ({data['progress']*100:.1f}%)", xbmc.LOGINFO)
			return response.json()
		except json.JSONDecodeError:
			xbmc.log("Invalid or empty JSON response received", xbmc.LOGERROR)
			return None
		except Exception as e:
			xbmc.log(f"Error updating media progress: {str(e)}", xbmc.LOGERROR)
			return None
	
	def start_playback_session(self, library_item_id, episode_id=None):
		"""Start a playback session on the server"""
		endpoint = f"/api/session/local"
		
		data = {
			"libraryItemId": library_item_id,
			"mediaPlayer": "Kodi",
			"deviceInfo": {
				"deviceId": "kodi-audiobookshelf-client",
				"clientName": "Kodi Audiobookshelf Client"
			}
		}
		
		if episode_id:
			data["episodeId"] = episode_id
		
		try:
			response = requests.post(self.base_url + endpoint, headers=self.headers, json=data)
			response.raise_for_status()
			session = response.json()
			xbmc.log(f"Started playback session: {session.get('id')}", xbmc.LOGINFO)
			return session
		except Exception as e:
			xbmc.log(f"Error starting playback session: {str(e)}", xbmc.LOGERROR)
			return None
	
	def sync_playback_session(self, session_id, current_time, duration, time_listened=0):
		"""Sync playback session with server"""
		endpoint = f"/api/session/local/{session_id}/sync"
		
		data = {
			"currentTime": current_time,
			"duration": duration,
			"timeListened": time_listened
		}
		
		try:
			response = requests.post(self.base_url + endpoint, headers=self.headers, json=data)
			response.raise_for_status()
			return response.json()
		except Exception as e:
			xbmc.log(f"Error syncing playback session: {str(e)}", xbmc.LOGDEBUG)
			return None
	
	def close_playback_session(self, session_id):
		"""Close a playback session on the server"""
		endpoint = f"/api/session/local/{session_id}/close"
		
		try:
			response = requests.post(self.base_url + endpoint, headers=self.headers)
			response.raise_for_status()
			xbmc.log(f"Closed playback session: {session_id}", xbmc.LOGINFO)
			return True
		except Exception as e:
			xbmc.log(f"Error closing playback session: {str(e)}", xbmc.LOGERROR)
			return False

	def get_chapters(self, library_item_id):
		"""Get chapter information for a library item"""
		try:
			item = self.get_library_item_by_id(library_item_id)
			chapters = item.get('media', {}).get('chapters', [])
			return chapters
		except Exception as e:
			xbmc.log(f"Error getting chapters: {str(e)}", xbmc.LOGERROR)
			return []
