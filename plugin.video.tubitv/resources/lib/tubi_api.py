# -*- coding: utf-8 -*-
# Tubi content API
#
# The /oz endpoints this addon used to browse with are gone. Tubi now serves
# browsing, search and playback from the public API hosts below, and every one
# of them authenticates with a bearer token rather than a session cookie - the
# signed in user token when there is one, the anonymous device token otherwise
# (see tubi_auth).
#
# Endpoints, all plain GETs:
#
#   tensor-cdn  /api/v1/browse_list            the category list
#   tensor-cdn  /api/v7/containers/{id}        a category's contents, paged
#                                              through the container cursor
#   search      /api/v3/search                 catalogue search
#   content-cdn /api/v3/series/{id}/episodes   a series' seasons and episode ids
#   content-cdn /api/v3/content                a title, its metadata and its
#                                              playable video resources
#
import requests

BROWSE_API = 'https://tensor-cdn.production-public.tubi.io'
SEARCH_API = 'https://search.production-public.tubi.io'
CONTENT_API = 'https://content-cdn.production-public.tubi.io'
EPG_API = 'https://epg-cdn.production-public.tubi.io'

# Tubi's linear channel line-up
EPG_MODE = 'tubitv_us_linear'
# The programming endpoint takes a batch of channels per call - the site asks
# for around twenty at a time.
EPG_BATCH = 20

APP_ID = 'tubitv'
PLATFORM = 'web'
TIMEOUT = 30

PAGE_SIZE = 50
# Tubi pages a series one season at a time, in blocks of this many episodes.
SEASON_PAGE_SIZE = 20

# Artwork comes back pre-sized, these are the sizes the web client asks for.
IMAGES = [('images[posterarts]', 'w408h583_poster'),
          ('images[backgrounds]', 'w1614h906_background'),
          ('images[hero_16x9]', 'w1280h720_hero'),
          ('images[landscape_images]', 'w978h549_landscape')]

# Only the resource types Kodi can actually play: clear HLS, and Widevine for
# the titles Tubi encrypts. PlayReady and FairPlay are of no use here.
VIDEO_RESOURCES = [('video_resources[]', 'hlsv6'),
                   ('video_resources[]', 'hlsv6_widevine_nonclearlead')]
LIMIT_RESOLUTIONS = [('limit_resolutions[]', 'h264_1080p'),
                     ('limit_resolutions[]', 'h265_1080p')]

WIDEVINE = 'hlsv6_widevine_nonclearlead'
CLEAR = 'hlsv6'
# The linear channels are served as plain hlsv3, never encrypted
LIVE = 'hlsv3'
CLEAR_TYPES = (CLEAR, LIVE)


class TubiApiError(Exception):
    """Raised when a Tubi API call cannot be completed."""


class TubiApi(object):

    def __init__(self, headers, deviceId, userId=None):
        self.headers = headers
        self.deviceId = deviceId
        self.userId = userId

    def get(self, url, params):
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as err:
            raise TubiApiError(str(err))

    # ---------------------------------------------------------------- browse

    def browseList(self):
        """The category rows, in the order Tubi lists them."""
        data = self.get(''.join([BROWSE_API, '/api/v1/browse_list']),
                        [('is_kids_mode', 'false')])
        return data.get('containers') or []

    def container(self, containerId, cursor=0):
        """One page of a category.

        Returns the contents in the order the container lists them, plus the
        cursor for the next page (None when the category is exhausted).
        """
        params = [('contents_limit', PAGE_SIZE),
                  ('cursor', cursor),
                  ('include_channels', 'true'),
                  ('include_sponsorships', 'true'),
                  ('is_kids_mode', 'false')] + IMAGES
        data = self.get(''.join([BROWSE_API, '/api/v7/containers/', containerId]), params)
        container = data.get('container') or {}
        contents = data.get('contents') or {}
        children = container.get('children') or []
        items = [contents[key] for key in children if key in contents]
        # The container hands back the offset to ask for next. A short page
        # means the category is exhausted.
        nextCursor = container.get('cursor')
        if len(children) < PAGE_SIZE or nextCursor in (None, cursor):
            nextCursor = None
        return items, nextCursor

    def search(self, query):
        params = [('search', query),
                  ('include_channels', 'true'),
                  ('include_linear', 'true'),
                  ('is_kids_mode', 'false')] + IMAGES
        data = self.get(''.join([SEARCH_API, '/api/v3/search']), params)
        contents = data.get('contents') or {}
        # The containers list carries the relevance ordering, the contents map
        # does not, so walk it when it is there.
        ordered = []
        for container in data.get('containers') or []:
            for item in container.get('items') or []:
                if item.get('id') in contents:
                    ordered.append(contents[item['id']])
        return ordered or list(contents.values())

    # ---------------------------------------------------------------- titles

    def content(self, contentId, season=None, page=1):
        """A single title. With a season, one page of that season's episodes."""
        params = [('app_id', APP_ID),
                  ('platform', PLATFORM),
                  ('content_id', contentId),
                  ('device_id', self.deviceId),
                  ('include_channels', 'true')] + IMAGES + VIDEO_RESOURCES + LIMIT_RESOLUTIONS
        if season is not None:
            params.extend([('pagination[season]', season),
                           ('pagination[page_in_season]', page),
                           ('pagination[page_size_in_season]', SEASON_PAGE_SIZE)])
        return self.get(''.join([CONTENT_API, '/api/v3/content']), params)

    # ------------------------------------------------------------- live tv

    def liveChannels(self):
        """Tubi's linear channels.

        Returns (channels in line-up order, {channel id: group name}). Every
        channel carries its own live HLS manifest, none of them are encrypted
        and none of them need an account.
        """
        params = [('mode', EPG_MODE),
                  ('platform', PLATFORM),
                  ('device_id', self.deviceId)]
        if self.userId is not None:
            params.append(('user_id', self.userId))
        data = self.get(''.join([BROWSE_API, '/api/v2/epg']), params)

        contents = data.get('contents') or {}
        groups = {}
        ordered = []
        for container in data.get('containers') or []:
            for channelId in container.get('contents') or []:
                if channelId not in contents:
                    continue
                groups.setdefault(channelId, container.get('name'))
                if contents[channelId] not in ordered:
                    ordered.append(contents[channelId])
        # Anything Tubi did not file under a group still belongs in the guide
        for channelId, channel in contents.items():
            if channel not in ordered:
                ordered.append(channel)
        return ordered, groups

    def liveProgramming(self, channelIds):
        """The programme guide for the given channels, a batch at a time."""
        rows = []
        for start in range(0, len(channelIds), EPG_BATCH):
            batch = channelIds[start:start + EPG_BATCH]
            params = [('platform', PLATFORM),
                      ('device_id', self.deviceId),
                      ('lookahead', 1),
                      ('content_id', ','.join(str(i) for i in batch))]
            if self.userId is not None:
                params.append(('user_id', self.userId))
            data = self.get(''.join([EPG_API, '/content/epg/programming']), params)
            rows.extend(data.get('rows') or [])
        return rows

    # ---------------------------------------------------------------- series

    def seasons(self, seriesId):
        """The season index for a series: names, numbers and episode counts."""
        data = self.get(''.join([CONTENT_API, '/api/v3/series/', str(seriesId), '/episodes']),
                        [('platform', PLATFORM)])
        return data.get('episodes_by_season') or []

    def episodes(self, seriesId, season, expected=None):
        """Every episode of one season, paging until the season runs out."""
        episodes = []
        page = 1
        while True:
            data = self.content(seriesId, season=season, page=page)
            found = []
            for child in data.get('children') or []:
                found.extend(child.get('children') or [])
            episodes.extend(found)
            if len(found) < SEASON_PAGE_SIZE:
                break
            if expected is not None and len(episodes) >= expected:
                break
            page += 1
        return episodes


def requiresHdcp(resource):
    """Whether a stream's licence will only release its keys over HDCP.

    Tubi grades the same title twice: its 720p renditions come with
    `hdcp_v1` and Widevine security level 2, its 576p ones with HDCP
    disabled and level 1. A software CDM - which is all a desktop or a
    Flatpak Kodi has - cannot satisfy the former, and Widevine answers by
    handing back keys marked output-restricted, so the stream opens and
    then fails to decode.
    """
    hdcp = (resource.get('license_server') or {}).get('hdcp_version') or ''
    return hdcp not in ('', 'hdcp_disabled')


def resolutionOf(resource):
    digits = ''.join(c for c in (resource.get('resolution') or '') if c.isdigit())
    return int(digits) if digits else 0


def pickResource(content, allowHdcp=False):
    """Choose the stream to play from a title's video resources.

    Returns (manifest url, license url or None).

    Clear HLS always wins when Tubi offers it - some titles are encrypted
    and some are not, and a clear stream needs no CDM at all. Among the
    encrypted ones the HDCP-free rendition is preferred unless the caller
    asks otherwise, then the highest resolution, then H.264 over H.265
    because far more Kodi devices can decode it.
    """
    resources = [r for r in content.get('video_resources') or []
                 if r.get('type') in CLEAR_TYPES + (WIDEVINE,)
                 and (r.get('manifest') or {}).get('url')]

    def rank(resource):
        quality = (-resolutionOf(resource),
                   0 if resource.get('codec') == 'VIDEO_CODEC_H264' else 1)
        encrypted = 0 if resource.get('type') in CLEAR_TYPES else 1
        if allowHdcp:
            return (encrypted,) + quality
        return (encrypted, 1 if requiresHdcp(resource) else 0) + quality

    for resource in sorted(resources, key=rank):
        return (resource['manifest']['url'],
                (resource.get('license_server') or {}).get('url'))

    # Older shaped payloads carry the manifest at the top level instead
    if content.get('url'):
        return content['url'], None
    return None, None
