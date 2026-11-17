"""Streaming-service web links <-> Kodi streaming addons' plugin paths.

Watch Party's Android app and browser extension share a title as its web
link (https://www.netflix.com/watch/80057281, …); Kodi plays addon content
from a plugin:// path. addon_path() turns a shared link into the path of an
addon that plays the same title, for the addons below; the apps do the
reverse (their AddonLinks / addon-links.js). Each format was read from the
addon's own source (2026-11-15):

- Netflix, plugin.video.netflix (CastagnaIT): play/movie/<id>/ — the id in
  netflix.com/watch/<id> (common/videoid.py VideoId.from_path). An episode
  needs its show and season ids too (play/show/<s>/season/<x>/episode/<e>/),
  which a web link doesn't have: episodes aren't converted.
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
        m = re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:watch|title)/(\d+)', path)
        if m and media_type != 'episode':
            return ('plugin.video.netflix',
                    'plugin://plugin.video.netflix/play/movie/%s/' % m.group(1))
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
    elif on('tubitv.com'):
        m = re.match(r'^/(?:movies|tv-shows)/(\d+)', path)
        if m:
            return ('plugin.video.tubitv',
                    'plugin://plugin.video.tubitv/?mode=GV&url=%s' % m.group(1))
    return None, None
