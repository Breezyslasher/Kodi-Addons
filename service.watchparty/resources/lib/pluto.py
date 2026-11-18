"""Pluto TV on-demand links -> a stream Kodi plays itself.

Kodi's Pluto TV addon (plugin.video.plutotv, Lunatixz) can't play on demand
any more: its stream lookup, api.pluto.tv/v2/episodes/<id>/clips.json,
answers 404 even for a real episode id (2026-11-18). Pluto's public catalog
still has every title's stream, signed out (WatchParty's page probe,
2026-11-18):

  JustWatch's links   pluto.tv/on-demand/series/<show>/season/<n>/episode/<episode>
                      pluto.tv/on-demand/movies/<movie>
  catalog             api.pluto.tv/v3/vod/slugs/<show or movie>?deviceType=web
                      -> the show's seasons and episodes (or the movie), each
                      with its slug, _id and stitched.urls[hls]
  stream              service-stitcher…/stitch/hls/episode/<_id>/master.m3u8?
                      …&deviceId=<uuid>&sid=<uuid> -> an HLS playlist (ads
                      stitched in), English subtitles

So resolve() finds the link's episode (or movie) by its slug and returns
its stream with this device's ids filled in. JustWatch's Pluto links can be
another language's edition of a show (Forensic Files: forensic-files-las,
Spanish); the link is followed as it is.
"""
import json
import re
import uuid
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

CATALOG = 'https://api.pluto.tv/v3/vod/slugs/%s?deviceType=web'
DEVICE = {'appName': 'web', 'appVersion': 'unknown', 'deviceDNT': '0',
          'deviceMake': 'Chrome', 'deviceModel': 'web', 'deviceType': 'web',
          'deviceVersion': 'unknown', 'advertisingId': ''}
_LINK = re.compile(r'^/(?:[a-z]{2}/)?on-demand/(movies|series)/([^/?#]+)'
                   r'(?:/season/\d+/episode/([^/?#]+))?/?$')


def target(link):
    """(catalog slug, item slug) for a Pluto on-demand link, or None: a
    movie, or a show's episode (a show's own page isn't a title)."""
    url = urlsplit((link or '').strip())
    host = (url.hostname or '').lower()
    if host != 'pluto.tv' and not host.endswith('.pluto.tv'):
        return None
    m = _LINK.match(url.path)
    if not m:
        return None
    kind, slug, episode = m.groups()
    if kind == 'movies':
        return slug, slug
    return (slug, episode) if episode else None


def _find(data, slug):
    """The catalog entry with this slug and a stream, anywhere in [data]."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get('slug') == slug and isinstance(node.get('stitched'), dict):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def stream_url(entry, device_id, session_id):
    """[entry]'s HLS stream, with this device's ids in its query."""
    urls = (entry.get('stitched') or {}).get('urls') or []
    hls = next((u.get('url') for u in urls if u.get('type') == 'hls' and u.get('url')), None)
    if not hls:
        return None
    parts = urlsplit(hls)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(DEVICE)
    query.update({'deviceId': device_id, 'sid': session_id})
    return urlunsplit(parts._replace(query=urlencode(sorted(query.items()))))


def resolve(link, fetch, device_id=None):
    """The stream that plays [link], or None. [fetch](url) -> response text."""
    t = target(link)
    if not t:
        return None
    catalog, slug = t
    try:
        data = json.loads(fetch(CATALOG % quote(catalog)))
    except Exception:
        return None
    entry = _find(data, slug)
    if not entry:
        return None
    return stream_url(entry, device_id or str(uuid.uuid4()), str(uuid.uuid4()))
