"""Streaming-service web links <-> Kodi streaming addons' plugin paths.

Watch Party's Android app and browser extension share a title as its web
link (https://www.netflix.com/watch/80057281, …); Kodi plays addon content
from a plugin:// path. addon_path() turns a shared link into the path of an
addon that plays the same title, for the addons below; the apps do the
reverse (their AddonLinks / addon-links.js). Each format was read from the
addon's own source (2026-11-15):

- Netflix, plugin.video.netflix (CastagnaIT): play/movie/<id>/ — the id in
  netflix.com/watch/<id> (common/videoid.py VideoId.from_path). An episode's
  own path needs its show and season ids too (play/show/<s>/season/<x>/
  episode/<e>/), which a web link doesn't have, so an episode's id goes the
  movie's way: the addon builds the stream from the id alone
  (navigation/player.py get_inputstream_listitem: videoid.value), and its
  metadata lookup asks videos[<id>], which any video has (nfsession_ops.py
  get_videoid_info). Played live action One Piece S1E1 on Kodi (2026-11-17).
- YouTube, plugin.video.youtube: play/?video_id=<id> (yt_play.py docstring).
- Disney+, slyguy.disney.plus: ?_=play&deeplink_id=<id> — the id in
  disneyplus.com/play/<id> and /browse/entity-<id> (plugin.py _play:
  api.deeplink(deeplink_id) with refIdType deeplinkId).
- Hulu, slyguy.hulu: ?_=play&id=<entity id> — the uuid in hulu.com/watch/<id>
  and hulu.com/movie/<slug>-<id> (plugin.py _play: api.deeplink, namespace
  entity).
- Paramount+, slyguy.paramount.plus: ?_=play&video_id=<contentId> — the id
  in paramountplus.com/movies/<name>/<id> and /shows/<show>/video/<id>/…, and
  in the site's own nameless links they redirect to, /movies/video/<id>/ and
  /shows/video/<id>/ (WatchParty's tools/probe/nameless_links.py, 2026-11-16).
- HBO Max, slyguy.max (and Discovery+, slyguy.discovery.plus): ?_=play&edit_id=
  <edit> for a link copied while playing (/video/watch/<video>/<edit>); a link
  with one id (/video/watch/<id>, as JustWatch gives) goes to ?_=play&id=<id>,
  which the addon turns into an edit id with /content/videos/<id>/
  activeVideoForShow (plugin.py play, api.py get_edit_id). That plays a movie
  (Batman Begins, 2026-11-17) but not an episode, whose id isn't a show's:
  the engine finds an episode in the addon instead (addon_search.py).
- Prime Video, plugin.video.amazon-test (Sandmann79): ?asin=<id>&mode=
  PlayVideo — the asin= or gti= of watch.amazon.com/watch, or the asin in
  /gp/video/detail/<asin> and primevideo.com/detail/<asin>.
- Apple TV, plugin.video.appletv: ?action=play&item_id=<umc id>&item_type=
  Movie|Episode — the umc.cmc… id of tv.apple.com/<cc>/movie|episode/<name>/<id>,
  or of tv.apple.com/[<cc>/]movie|episode/<id> (which redirects there).
- Tubi, plugin.video.tubitv: ?mode=GV&url=<id> — the id of
  tubitv.com/movies/<id>, and of an episode's tubitv.com/tv-shows/<id>: Tubi's
  /api/v3/content answers an episode id with its own streams and series_id
  (probed 2026-11-15, WatchParty's tools/probe/tubi_episode.py), so the
  addon's one-part form plays it; since 2026.11.15 the addon also reports
  its progress as an episode (seriesOf()).

- Peacock, plugin.video.skyott (Paco8): ?action=play&slug=<slug> — the slug
  is the path of peacocktv.com/watch/asset<slug> (WatchParty's
  tools/probe/peacock_slug.py looked up a movie's and two episodes' paths with
  the addon's own client, 2026-11-16). Peacock's play deeplink
  (/deeplink?deeplinkData={"pvid":…}) can't be converted: the pvid alone
  finds nothing in the addon's catalogue lookup.
- Crunchyroll, plugin.video.crunchyroll: /video/<episode id>/<stream id> (its
  router's video_movie_play; episodes from its own lists add the series id
  first) — the id in crunchyroll.com/watch/<id>/<name>. A link has no stream
  id, but the addon doesn't need its value: the stream is fetched by
  episode_id (videostream.py _get_stream_data_from_api) and stream_id only
  has to be set (get_player_stream_data) and names the subtitle cache folder,
  so the episode id goes in both places (played on Kodi, 2026-11-18). The
  addon plays that exact version: its language settings only pick versions
  in its own lists. JustWatch's only Crunchyroll link for some shows is a
  dub (My Hero Academia: watch/GE00337172PLPL, Polish; the Japanese/English
  version is G31UXN249, which JustWatch doesn't have), so those play in
  Polish everywhere. Left as is (2026-11-18); a fix on Kodi would find the
  episode through the addon's search and season lists instead.
- Starz, plugin.video.starz (Breezyslasher's, Kodi-Addons branch starz):
  ?action=play&content_id=<id> (its default.py do_play: api.playback by
  Starz's contentId) — the id in starz.com/movies/<id> and
  starz.com/series/<show>/episodes/<id> (what TVs get from JustWatch) and
  starz.com/us/en/play/<id> (phones, and Kodi's own shares). A season page
  (/us/en/series/<name>/season-<n>/<id>) isn't an episode (2026-11-18).
  Movies and episodes played on Kodi and on an Android phone (2026-11-19).
- Plex's free catalogue, plugin.video.plexondemand (Breezyslasher's,
  Kodi-Addons branch plugin.video.plexondemand): ?action=play&rk=<ratingKey>
  — JustWatch's Plex links (watch.plex.tv/movie/<slug>,
  /show/<slug>/season/<n>/episode/<n>) carry it as utm_content: Forensic
  Files S1 E1-2 and Forensic Files II S1 E1 looked up as those episodes
  (WatchParty's tools/probe/plex_vod.py, 2026-11-19). A link without it, and
  Plex's live TV (/live-tv/), aren't titles.
- Fawesome, plugin.video.fawesome (Breezyslasher's, Kodi-Addons branch
  plugin.video.fawesome): ?action=play&nid=<node id> (its default.py play:
  api.detail by node id) — the number in JustWatch's links,
  fawesome.tv/movies/<id>/<name> and /tv-shows/<id>/s01-e01-<name> (an
  episode; Forensic Files S1 E1 is node 10241098, the id in its page's
  window.nodeId; WatchParty's page probe, 2026-11-21).

SlyGuy's paths are built as SlyGuy's router builds them (route in "_",
arguments sorted); the addons parse them the same way.
"""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

_UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


def _slyguy(addon, **args):
    params = sorted([('_', 'play')] + list(args.items()))
    return 'plugin://%s/?%s' % (addon, urlencode(params))


def _wbd(addon, path):
    """HBO Max's and Discovery+'s /video/watch/<video>[/<edit>]."""
    m = re.match(r'^/video/watch/(%s)(?:/(%s))?/?$' % (_UUID, _UUID), path)
    if not m:
        return None, None
    if m.group(2):
        return addon, _slyguy(addon, edit_id=m.group(2).lower())
    return addon, _slyguy(addon, id=m.group(1).lower())


def addon_path(link, media_type=''):
    """(addon id, plugin path) that plays the title `link` points to, or
    (None, None). `media_type` is the item's type ('movie', 'episode' or
    ''), where it decides between forms."""
    link = (link or '').strip()
    url = urlsplit(link)
    if url.scheme not in ('http', 'https'):
        return None, None
    host = (url.hostname or '').lower()
    path = url.path
    query = parse_qs(url.query)

    def on(site):
        return host == site or host.endswith('.' + site)

    if on('netflix.com'):
        m = re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(watch|title)/(\d+)', path)
        # a show's /title/ page isn't an episode
        if m and not (media_type == 'episode' and m.group(1) == 'title'):
            return ('plugin.video.netflix',
                    'plugin://plugin.video.netflix/play/movie/%s/' % m.group(2))
    elif on('youtube.com') or host == 'youtu.be':
        vid = (query.get('v') or [None])[0]
        if host == 'youtu.be':
            vid = path.strip('/').split('/')[0] or None
        if vid and re.match(r'^[A-Za-z0-9_-]{6,}$', vid):
            return ('plugin.video.youtube',
                    'plugin://plugin.video.youtube/play/?video_id=%s' % vid)
    elif on('disneyplus.com'):
        m = re.match(r'^/(?:[a-z]{2}-[a-z]{2}/)?(?:play/|browse/entity-)(%s)' % _UUID, path)
        if m:
            return ('slyguy.disney.plus', _slyguy('slyguy.disney.plus', deeplink_id=m.group(1).lower()))
    elif on('hulu.com'):
        m = re.match(r'^/(?:watch/|movie/[^/]*?-?)(%s)' % _UUID, path)
        if m:
            return ('slyguy.hulu', _slyguy('slyguy.hulu', id=m.group(1).lower()))
    elif on('paramountplus.com'):
        m = (re.match(r'^/(?:[a-z]{2}/)?shows/(?:[^/]+/)?video/([A-Za-z0-9_-]+)', path)
             or re.match(r'^/(?:[a-z]{2}/)?movies/(?:video/|[^/]+/)([A-Za-z0-9_-]+)/?$', path))
        if m:
            return ('slyguy.paramount.plus', _slyguy('slyguy.paramount.plus', video_id=m.group(1)))
    elif on('hbomax.com') or on('max.com'):
        return _wbd('slyguy.max', path)
    elif on('discoveryplus.com'):
        return _wbd('slyguy.discovery.plus', path)
    elif on('amazon.com') or on('primevideo.com'):
        asin = (query.get('asin') or query.get('gti') or [None])[0]
        if not asin:
            m = re.search(r'/(?:gp/video/)?detail/([A-Za-z0-9.-]+)', path)
            asin = m.group(1) if m else None
        if asin:
            return ('plugin.video.amazon-test', 'plugin://plugin.video.amazon-test/?' + urlencode(
                [('asin', asin), ('mode', 'PlayVideo'), ('name', ''), ('adult', '0')]))
    elif host == 'tv.apple.com':
        m = re.match(r'^/(?:[a-z]{2}/)?(movie|episode)/(?:[^/]+/)?(umc\.[A-Za-z0-9.]+)', path)
        if m:
            return ('plugin.video.appletv', 'plugin://plugin.video.appletv/?' + urlencode(
                [('action', 'play'), ('item_id', m.group(2)),
                 ('item_type', 'Movie' if m.group(1) == 'movie' else 'Episode')]))
    elif on('peacocktv.com'):
        m = re.match(r'^/watch/asset(/(?:movies|tv)/[^?#]+?)/?$', path)
        if m:
            return ('plugin.video.skyott', 'plugin://plugin.video.skyott/?' + urlencode(
                [('action', 'play'), ('slug', m.group(1))]))
    elif on('starz.com'):
        m = re.match(r'^/(?:[a-z]{2}/[a-z]{2}/)?(?:play/(\d+)|movies/(?:[^/]+/)?(\d+)/?$|series/[^/]+/episodes/(\d+))', path)
        if m:
            cid = m.group(1) or m.group(2) or m.group(3)
            return ('plugin.video.starz', 'plugin://plugin.video.starz/?' + urlencode(
                [('action', 'play'), ('content_id', cid)]))
    elif host == 'watch.plex.tv':
        rk = (query.get('utm_content') or [''])[0]
        if re.match(r'^/(?:[a-z]{2}/)?(?:movie/[^/]+|show/[^/]+/season/\d+/episode/\d+)/?$', path) \
                and re.match(r'^[0-9a-f]{24}$', rk):
            return ('plugin.video.plexondemand', 'plugin://plugin.video.plexondemand/?' + urlencode(
                [('action', 'play'), ('rk', rk)]))
    elif on('crunchyroll.com'):
        m = re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2})?/)?watch/([A-Z0-9]+)(?:/|$)', path)
        if m:
            return ('plugin.video.crunchyroll',
                    'plugin://plugin.video.crunchyroll/video/%s/%s' % (m.group(1), m.group(1)))
    elif on('fawesome.tv'):
        m = re.match(r'^/(?:movies|tv-shows)/(\d+)(?:/|$)', path)
        if m:
            return ('plugin.video.fawesome', 'plugin://plugin.video.fawesome/?' + urlencode(
                [('action', 'play'), ('nid', m.group(1))]))
    elif on('tubitv.com'):
        m = re.match(r'^/(?:movies|tv-shows)/(\d+)', path)
        if m:
            return ('plugin.video.tubitv',
                    'plugin://plugin.video.tubitv/?mode=GV&url=%s' % m.group(1))
    return None, None
