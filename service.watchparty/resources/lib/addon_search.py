"""Find an episode inside SlyGuy's HBO Max / Discovery+ addons by browsing them.

A shared HBO Max or Discovery+ link with one id (/video/watch/<id>, what
JustWatch gives) names the video. The addons play a movie from that id
(?_=play&id=, tested with Batman Begins 2026-11-17), but for an episode HBO
Max answers "No active video found for show id" (Gold Rush, 2026-11-17):
the addon's id is a show's. Their episode items play by edit id, so this
walks the addon's own folders, as read from its source (plugin.py, the same
in both addons):

  ?_=search&query=<show>         search results: shows (?_=series&id=<id>),
                                 movies, and sometimes the episodes themselves
  ?_=series&id=<id>&season=<n>   season n's episodes (?_=play&edit_id=<edit>),
                                 paged: a last item adds &page=<n+1>

and picks the episode by its season and episode number. list_dir(path) is
Kodi's Files.GetDirectory, returning its 'files'.

A Discovery+ link from JustWatch's web offer names the show, so Watch Party's
Android app and browser share "<show id>/s<N>/e<N>" as the item's ids.wbd
(their wbdEpisode, from www.discoveryplus.com/show/<show>/s<N>/e<N>-<name>/
<video>): find_by_show() opens that season directly, with the service's own
numbers. HBO Max and Discovery+ share one catalog, so the show id is tried
in both addons; whether slyguy.max takes Discovery+'s show ids is untested.
"""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

WBD_ADDONS = ('slyguy.max', 'slyguy.discovery.plus')
MAX_PAGES = 5


def _norm(title):
    title = re.sub(r'\s*\(\d{4}\)\s*$', '', str(title or ''))
    return re.sub(r'[^0-9a-z]+', ' ', title.lower().replace('’', "'").replace("'", '')).strip()


def _args(path):
    return {k: v[0] for k, v in parse_qs(urlsplit(path or '').query).items()}


def _url(addon, **args):
    return 'plugin://%s/?%s' % (addon, urlencode(sorted(args.items())))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_episode(entry, season, episode):
    return (_args(entry.get('file')).get('_') == 'play'
            and _int(entry.get('season')) == season
            and _int(entry.get('episode')) == episode)


_WBD = re.compile(r'^([0-9a-fA-F-]{36})/s(\d+)/e(\d+)$')


def parse_wbd(value):
    """ids.wbd "<show id>/s<N>/e<N>" -> (show id, N, N), or None."""
    m = _WBD.match(str(value or '').strip())
    return (m.group(1).lower(), int(m.group(2)), int(m.group(3))) if m else None


def _in_season(addon, show_id, season, episode, list_dir):
    path = _url(addon, _='series', id=show_id, season=season)
    for _page in range(MAX_PAGES):
        entries = list_dir(path) or []
        for entry in entries:
            if _is_episode(entry, season, episode):
                return entry['file']
        more = [e['file'] for e in entries
                if _args(e.get('file')).get('_') == 'series' and _args(e.get('file')).get('page')]
        if not more:
            break
        path = more[0]
    return None


def find_by_show(addon, wbd, list_dir):
    """The addon's path for ids.wbd's show / season / episode, or None."""
    parsed = parse_wbd(wbd)
    if addon not in WBD_ADDONS or not parsed:
        return None
    return _in_season(addon, parsed[0], parsed[1], parsed[2], list_dir)


def find_episode(addon, show, season, episode, list_dir):
    """The addon's plugin path that plays `show` season/episode, or None."""
    season, episode = _int(season), _int(episode)
    if addon not in WBD_ADDONS or not show or season is None or episode is None:
        return None
    want = _norm(show)
    results = list_dir(_url(addon, _='search', query=show)) or []

    for entry in results:
        if _is_episode(entry, season, episode) and _norm(entry.get('showtitle')) == want:
            return entry['file']

    series = [(_norm(e.get('title') or e.get('label')), _args(e.get('file')).get('id'))
              for e in results if _args(e.get('file')).get('_') == 'series']
    ids = [i for name, i in series if i and name == want]
    if not ids:
        # a show listed under a slightly different name, if it's the only one
        close = [i for name, i in series if i and (want in name or name in want)
                 and 'asl' not in name.split()]
        ids = close[:1] if len(close) == 1 else []

    for show_id in ids:
        found = _in_season(addon, show_id, season, episode, list_dir)
        if found:
            return found
    return None
