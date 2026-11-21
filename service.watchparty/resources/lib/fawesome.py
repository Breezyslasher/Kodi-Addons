"""Fawesome links -> a stream Kodi plays itself, when the Fawesome addon
(plugin.video.fawesome, addon_links.py) isn't installed or fails.

A title's page has its stream in it, signed out and without DRM (WatchParty's
page probe, 2026-11-21):

  JustWatch's links   fawesome.tv/movies/<id>/<name>
                      fawesome.tv/tv-shows/<id>/s01-e01-<name>   (an episode)
  the page            the title's JSON, with "video_hls_url" (HLS),
                      "video_url" (MP4), "drm" and "node_id": <id>. Related
                      titles' JSON can be on the page too, so the one with
                      this node_id is picked.

A link with only the number (/tv-shows/<id>) answers with an empty page
shell; any name after it gives the full page (/tv-shows/<id>/x), so a link
without one gets "watch".
"""
import json
import re
from urllib.parse import urlsplit

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Safari/537.36')
_LINK = re.compile(r'^/(movies|tv-shows)/(\d+)(?:/([^/?#]+))?/?$')
_URL_FIELD = re.compile(r'"(video_hls_url|video_url)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_NODE = re.compile(r'"node_id"\s*:\s*"?(\d+)')
_DRM = re.compile(r'"drm"\s*:\s*"?(\d)')


def target(link):
    """(page URL, node id) for a Fawesome title link, or None."""
    url = urlsplit((link or '').strip())
    host = (url.hostname or '').lower()
    if host != 'fawesome.tv' and not host.endswith('.fawesome.tv'):
        return None
    m = _LINK.match(url.path)
    if not m:
        return None
    kind, nid, name = m.groups()
    return 'https://fawesome.tv/%s/%s/%s' % (kind, nid, name or 'watch'), nid


def _text(raw):
    try:
        return json.loads('"%s"' % raw)
    except ValueError:
        return raw.replace('\\/', '/')


def stream_in(page, nid):
    """The stream of node [nid] in a title page: its HLS, else its MP4 (not
    one with DRM), or None.

    In each title's JSON the fields run "drm", "video_url", "video_hls_url",
    …, "node_id" (the page probe), so a title's fields are the ones between
    the previous title's node_id and its own."""
    start = 0
    for node in _NODE.finditer(page):
        segment, start = page[start:node.start()], node.end()
        if node.group(1) != str(nid):
            continue
        found = {}
        for m in _URL_FIELD.finditer(segment):
            url = _text(m.group(2))
            if url.startswith('http') and 'comingsoon' not in url.lower():
                found[m.group(1)] = url   # the last one: the closest to this node_id
        drms = _DRM.findall(segment)
        if found.get('video_hls_url'):
            return found['video_hls_url']
        if found.get('video_url') and not (drms and drms[-1] == '1'):
            return found['video_url']
    return None


def resolve(link, fetch):
    """The stream that plays [link], for Kodi (with the browser User-Agent
    the site's player sends), or None. [fetch](url, headers) -> page text."""
    t = target(link)
    if not t:
        return None
    page_url, nid = t
    try:
        page = fetch(page_url, {'User-Agent': UA})
    except Exception:
        return None
    url = stream_in(page or '', nid)
    return url + '|User-Agent=' + UA.replace(' ', '%20') if url else None
