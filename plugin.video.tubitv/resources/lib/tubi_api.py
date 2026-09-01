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
import concurrent.futures

import requests

BROWSE_API = 'https://tensor-cdn.production-public.tubi.io'
SEARCH_API = 'https://search.production-public.tubi.io'
CONTENT_API = 'https://content-cdn.production-public.tubi.io'
EPG_API = 'https://epg-cdn.production-public.tubi.io'
RELATED_API = 'https://autopilot-cdn.production-public.tubi.io'
QUEUE_API = 'https://user-queue.production-public.tubi.io'
HISTORY_API = 'https://lishi.production-public.tubi.io'
ACCOUNT_API = 'https://account.production-public.tubi.io'

# Tubi describes a title as one of these. Its saved list only ever uses the
# first two; its watch history uses the first and the third, an episode
# carrying its series as a parent_id alongside.
MOVIE = 'movie'
SERIES = 'series'
EPISODE = 'episode'
# The only kind of saved list Tubi keeps
WATCH_LATER = 'watch_later'
# What a title's rating can be, and the four ways to change it
RATING_NONE = 'none'
RATING_LIKED = 'liked'
RATING_DISLIKED = 'disliked'
LIKE, DISLIKE = 'like', 'dislike'
UNLIKE, UNDISLIKE = 'remove-like', 'remove-dislike'

# Tubi's linear channel line-up
EPG_MODE = 'tubitv_us_linear'
# The programming endpoint takes a batch of channels per call - the site asks
# for around twenty at a time.
EPG_BATCH = 20
# The whole line-up is far more batches than one round trip. Fetching them a
# few at a time keeps a guide refresh to seconds instead of a minute, which
# matters because the tools that ask for it are waiting on a socket.
EPG_WORKERS = 5

APP_ID = 'tubitv'
PLATFORM = 'web'
TIMEOUT = 30

PAGE_SIZE = 50
# Tubi pages a series one season at a time, in blocks of this many episodes.
SEASON_PAGE_SIZE = 20
# The home screen comes in two calls: the first seven rows, then every row
# after them, which is what a group size of -1 asks for.
HOME_GROUP_SIZE = 7
HOME_GROUP_ALL = -1

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

    def __init__(self, headers, deviceId, userId=None, kids=False):
        self.headers = headers
        self.deviceId = deviceId
        self.userId = userId
        self.kids = kids

    @property
    def kidsMode(self):
        return 'true' if self.kids else 'false'

    def get(self, url, params):
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as err:
            raise TubiApiError(str(err))

    def post(self, url, payload):
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json() if response.content else True
        except Exception as err:
            raise TubiApiError(str(err))

    def patch(self, url, payload):
        try:
            response = requests.patch(url, json=payload, headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            return True
        except Exception as err:
            raise TubiApiError(str(err))

    def delete(self, url, params):
        try:
            response = requests.delete(url, params=params, headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            return True
        except Exception as err:
            raise TubiApiError(str(err))

    @staticmethod
    def inOrder(data, contents=None):
        """Pull a payload's contents out in the order its containers list them.

        The browse, search and related endpoints all answer with a map of
        contents plus containers naming which ones to show and in what order.
        """
        contents = data.get('contents') if contents is None else contents
        contents = contents or {}
        ordered = []
        for container in data.get('containers') or []:
            for item in container.get('items') or container.get('children') or []:
                key = item.get('id') if isinstance(item, dict) else item
                if key in contents and contents[key] not in ordered:
                    ordered.append(contents[key])
        return ordered or list(contents.values())

    # ---------------------------------------------------------------- browse

    def browseList(self):
        """The category rows, in the order Tubi lists them."""
        data = self.get(''.join([BROWSE_API, '/api/v1/browse_list']),
                        [('is_kids_mode', self.kidsMode)])
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
                  ('is_kids_mode', self.kidsMode)] + IMAGES
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

    def homescreen(self, cursor=0, size=HOME_GROUP_SIZE):
        """The personalised rows Tubi puts on its own home screen.

        Returns (rows, next group cursor). Each row is a container in the
        same shape the category endpoint answers with, so opening one goes
        back through container().
        """
        params = [('include_channels', 'true'),
                  ('contents_limit', 10),
                  ('include_empty_history', 'true'),
                  ('include_empty_queue', 'true'),
                  ('include_sponsorships', 'true'),
                  ('include_ui_customization', 'true'),
                  ('group_start', cursor),
                  ('group_size', size),
                  ('is_kids_mode', self.kidsMode)] + IMAGES
        data = self.get(''.join([BROWSE_API, '/api/v8/homescreen']), params)
        rows = [c for c in data.get('containers') or [] if c.get('children')]
        return rows, data.get('group_cursor')

    def homescreenAll(self):
        """Every home row, the two calls the Tubi site makes for its own home.

        The site asks for the first seven rows, then asks again from the
        cursor it was handed with a group_size of -1, which answers with all
        the rest. Asking only for the first group - which is what this did -
        gives a home screen a seventh the length of Tubi's own.
        """
        rows, cursor = self.homescreen()
        if cursor:
            more, _ = self.homescreen(cursor, HOME_GROUP_ALL)
            seen = set(filter(None, (r.get('id') for r in rows)))
            rows.extend(r for r in more if r.get('id') not in seen)
        return rows

    def related(self, contentId):
        """The "You May Also Like" titles for one title."""
        params = [('content_id', contentId),
                  ('limit', 18),
                  ('include_ui_customization', 'true')] + IMAGES + VIDEO_RESOURCES
        return self.inOrder(self.get(''.join([RELATED_API, '/api/v3/related']), params))

    def queue(self):
        """The viewer's saved list.

        Each entry carries its own id - which is what removing one needs,
        not the content id - plus the content id and whether it is a film or
        a series. The titles themselves have to be fetched separately.
        """
        data = self.get(''.join([QUEUE_API, '/api/v2/queues']), [])
        return [q for q in data.get('queues') or [] if q.get('content_id')]

    def queueEntry(self, contentId):
        """The saved list entry for a title, if it is on the list at all."""
        for entry in self.queue():
            if str(entry.get('content_id')) == str(contentId):
                return entry
        return None

    def addToQueue(self, contentId, contentType):
        return self.post(''.join([QUEUE_API, '/api/v2/queues']),
                         {'type': WATCH_LATER,
                          'content_id': int(contentId),
                          'content_type': contentType})

    def removeFromQueue(self, queueId):
        return self.delete(''.join([QUEUE_API, '/api/v2/queues']),
                           [('queue_id', queueId)])

    def contents(self, contentIds):
        """Fetch several titles at once - there is no bulk endpoint."""
        found = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=EPG_WORKERS) as pool:
            for result in pool.map(self._safeContent, contentIds):
                if result is not None:
                    found.append(result)
        return found

    def _safeContent(self, contentId):
        try:
            return self.content(contentId)
        except TubiApiError:
            return None

    @staticmethod
    def ratingId(contentId, isSeries):
        """Tubi prefixes a series id with a zero everywhere ratings are concerned.

        The same convention shows up in the related endpoint and as the keys
        of a search's contents map.
        """
        contentId = str(contentId)
        return ''.join(['0', contentId]) if isSeries and not contentId.startswith('0') else contentId

    def rating(self, contentId, isSeries=False):
        """Whether a title is liked, disliked, or neither."""
        url = ''.join([ACCOUNT_API, '/user/preferences/rate/title/',
                       self.ratingId(contentId, isSeries)])
        return (self.get(url, []) or {}).get('status', RATING_NONE)

    def rate(self, contentId, isSeries, action):
        """like, dislike, remove-like or remove-dislike a title."""
        return self.patch(''.join([ACCOUNT_API, '/user/preferences/rate']),
                          {'target': 'title',
                           'action': action,
                           'data': [self.ratingId(contentId, isSeries)]})

    def history(self):
        """What the account has part-watched.

        One entry per film or series. A series carries its part-watched
        episodes nested inside it, and the entry's own id - not the title's -
        is what removing it from Continue Watching needs.
        """
        params = [('page_enabled', 'false'),
                  ('expand', 'false'),
                  ('platform', PLATFORM),
                  ('deviceId', self.deviceId)]
        data = self.get(''.join([HISTORY_API, '/api/v2/view_history']), params)
        return data.get('items') or []

    def removeFromHistory(self, historyId):
        """Drop a title from Continue Watching."""
        return self.delete(''.join([HISTORY_API, '/api/v2/view_history/', str(historyId)]), [])

    def reportProgress(self, contentId, contentType, position, parentId=None):
        """Tell Tubi how far into a title the viewer got.

        A film goes across on its own. An episode goes across as itself with
        its series named as the parent, which Tubi sends as a string where
        the content id is a number.

        Only meaningful signed in - the history belongs to the account.
        """
        if self.userId is None:
            return False
        payload = {'content_id': int(contentId),
                   'content_type': contentType,
                   'position': int(position),
                   'platform': PLATFORM}
        if parentId is not None:
            payload['parent_id'] = str(parentId)
        payload['user_id'] = int(self.userId)
        return self.post(''.join([HISTORY_API, '/api/v2/view_history']), payload)

    def search(self, query):
        params = [('search', query),
                  ('include_channels', 'true'),
                  ('include_linear', 'true'),
                  ('is_kids_mode', self.kidsMode)] + IMAGES
        # The containers list carries the relevance ordering, the contents
        # map does not, so the shared helper walks it.
        return self.inOrder(self.get(''.join([SEARCH_API, '/api/v3/search']), params))

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

    def _programmingBatch(self, batch):
        params = [('platform', PLATFORM),
                  ('device_id', self.deviceId),
                  ('lookahead', 1),
                  ('content_id', ','.join(str(i) for i in batch))] + LIMIT_RESOLUTIONS
        if self.userId is not None:
            params.append(('user_id', self.userId))
        data = self.get(''.join([EPG_API, '/content/epg/programming']), params)
        return data.get('rows') or []

    def liveProgramming(self, channelIds, tolerant=False):
        """The programme guide for the given channels.

        The line-up is split into batches and fetched a few at a time, since
        the whole guide is a couple of hundred channels and doing it one
        request after another takes long enough to matter.

        With tolerant set, a batch that fails is skipped instead of losing
        the whole guide - a partial guide beats none at all.
        """
        batches = [channelIds[start:start + EPG_BATCH]
                   for start in range(0, len(channelIds), EPG_BATCH)]
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=EPG_WORKERS) as pool:
            for result in pool.map(lambda b: self._safeBatch(b, tolerant), batches):
                rows.extend(result)
        return rows

    def _safeBatch(self, batch, tolerant):
        try:
            return self._programmingBatch(batch)
        except TubiApiError:
            if not tolerant:
                raise
            return []

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

    Tubi grades an encrypted title by resolution: 720p and above come with
    `hdcp_v1`, 576p and below with `hdcp_disabled`. A software CDM - which
    is all a desktop or a Flatpak Kodi has - cannot satisfy the former, and
    Widevine answers by handing back keys marked output-restricted, so the
    stream opens and then fails to decode.
    """
    hdcp = (resource.get('license_server') or {}).get('hdcp_version') or ''
    return hdcp not in ('', 'hdcp_disabled')


def describeResource(resource):
    """A one-line account of a rendition, for the log.

    Which of a title's renditions was chosen is the whole of what the HDCP
    preference does, and it is not visible from anywhere else.
    """
    if resource is None:
        return 'none'
    licence = resource.get('license_server') or {}
    return '%s %s %s hdcp=%s' % (
        resource.get('type') or '?',
        (resource.get('resolution') or '?').replace('VIDEO_RESOLUTION_', ''),
        (resource.get('codec') or '?').replace('VIDEO_CODEC_', ''),
        licence.get('hdcp_version') or 'none')


def resolutionOf(resource):
    digits = ''.join(c for c in (resource.get('resolution') or '') if c.isdigit())
    return int(digits) if digits else 0


def chooseResource(content, allowHdcp=False):
    """The rendition to play, or None if the title offers nothing playable.

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
        return resource
    return None


def pickResource(content, allowHdcp=False):
    """(manifest url, license url or None) for the rendition chosen."""
    resource = chooseResource(content, allowHdcp)
    if resource is not None:
        return (resource['manifest']['url'],
                (resource.get('license_server') or {}).get('url'))

    # Older shaped payloads carry the manifest at the top level instead
    if content.get('url'):
        return content['url'], None
    return None, None
