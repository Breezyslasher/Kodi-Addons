"""Turning InnerTube renderers into channels and programmes.

The EPG response is a deep tree of ``*Renderer`` dicts with the interesting
values scattered through it. Rather than walk fixed paths -- which break the
first time Google inserts a wrapper -- everything here searches by key name and
tolerates absence. A missing description is not worth an exception.
"""

import time

from . import kodiutils


def walk(node, key):
    """Yield every value stored under ``key``, at any depth."""
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                yield value
            for found in walk(value, key):
                yield found
    elif isinstance(node, list):
        for value in node:
            for found in walk(value, key):
                yield found


def first(node, key, default=None):
    for value in walk(node, key):
        return value
    return default


def text(node, default=""):
    """Flatten a ``{"runs": [{"text": ...}]}`` or ``{"simpleText": ...}``."""
    if not isinstance(node, dict):
        return default
    if "simpleText" in node:
        return node["simpleText"] or default
    runs = node.get("runs")
    if isinstance(runs, list):
        joined = "".join(run.get("text", "") for run in runs if isinstance(run, dict))
        if joined:
            return joined
    return default


def accessibility_label(node, default=""):
    """The label Google attaches to a thumbnail for screen readers.

    Worth having because some stations come back with ``name`` and ``callSign``
    both null -- the icon's accessibility label is then the only human-readable
    name in the row.
    """
    for block in walk(node, "accessibilityData"):
        if isinstance(block, dict) and block.get("label"):
            return block["label"]
    return default


def thumbnail(node, prefer_width=0):
    """Best thumbnail URL from a ``{"thumbnails": [...]}`` block.

    URLs come back protocol-relative ("//yt3.ggpht.com/..."), which Kodi will
    not fetch, so they are made absolute here.
    """
    thumbs = []
    for block in walk(node, "thumbnails"):
        if isinstance(block, list):
            thumbs.extend(t for t in block if isinstance(t, dict) and t.get("url"))
    if not thumbs:
        return ""
    if prefer_width:
        thumbs.sort(key=lambda t: abs(int(t.get("width") or 0) - prefer_width))
    else:
        thumbs.sort(key=lambda t: int(t.get("width") or 0), reverse=True)
    url = thumbs[0]["url"]
    if url.startswith("//"):
        url = "https:" + url
    return url


class Airing(object):
    """One programme on one channel."""

    __slots__ = ("video_id", "title", "description", "start_ms", "end_ms", "art")

    def __init__(self, video_id, title, description, start_ms, end_ms, art):
        self.video_id = video_id
        self.title = title
        self.description = description
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.art = art

    @property
    def is_now(self):
        now = time.time() * 1000
        return bool(self.start_ms and self.end_ms
                    and self.start_ms <= now < self.end_ms)

    def label(self):
        if not self.start_ms:
            return self.title
        when = time.strftime("%H:%M", time.localtime(self.start_ms / 1000.0))
        return "%s  %s" % (when, self.title)


class Station(object):
    """One channel, with whatever schedule came back alongside it."""

    __slots__ = ("station_id", "name", "call_sign", "logo", "airings")

    def __init__(self, station_id, name, call_sign, logo, airings):
        self.station_id = station_id
        self.name = name
        self.call_sign = call_sign
        self.logo = logo
        self.airings = airings

    @property
    def now(self):
        for airing in self.airings:
            if airing.is_now:
                return airing
        return self.airings[0] if self.airings else None

    @property
    def next_up(self):
        now = time.time() * 1000
        upcoming = [a for a in self.airings if a.start_ms and a.start_ms > now]
        upcoming.sort(key=lambda a: a.start_ms)
        return upcoming[0] if upcoming else None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_airing(renderer):
    video_id = ""
    for endpoint in walk(renderer, "watchEndpoint"):
        if isinstance(endpoint, dict) and endpoint.get("videoId"):
            video_id = endpoint["videoId"]
            break

    title = text(renderer.get("title")) or text(renderer.get("primaryText"))
    description = (text(renderer.get("quaternaryText"))
                   or text(renderer.get("tertiaryText")))
    return Airing(
        video_id=video_id,
        title=title or "Unknown",
        description=description,
        start_ms=_int(renderer.get("beginTimeMs")),
        end_ms=_int(renderer.get("endTimeMs")),
        art=thumbnail(renderer.get("thumbnail") or {}, prefer_width=1280),
    )


def parse_epg(response):
    """Stations, in the order the guide returned them.

    Rows pair a station with its airings, so they are read row by row rather
    than by collecting the two renderer types separately -- that would lose
    which programme belongs to which channel.
    """
    stations = []
    seen = set()

    for row in walk(response, "epgRowRenderer"):
        renderer = first(row, "epgStationRenderer")
        if not isinstance(renderer, dict):
            continue
        station_id = renderer.get("stationId") or renderer.get("tenxId") or ""
        if not station_id or station_id in seen:
            continue
        seen.add(station_id)

        airings = []
        for block in walk(row, "epgAiringRenderer"):
            if isinstance(block, dict):
                airing = parse_airing(block)
                if airing.video_id:
                    airings.append(airing)
        airings.sort(key=lambda a: a.start_ms or 0)

        stations.append(Station(
            station_id=station_id,
            name=(text(renderer.get("name"))
                  or text(renderer.get("callSign"))
                  or accessibility_label(renderer.get("icon") or {})
                  or station_id),
            call_sign=text(renderer.get("callSign")),
            logo=thumbnail(renderer.get("icon") or {}, prefer_width=400),
            airings=airings,
        ))

    if not stations:
        kodiutils.log("no epgRowRenderer in the guide response -- the EPG "
                      "shape may have changed")
    return stations


def continuation_token(response):
    """The token for the next page of guide, if there is one.

    The EPG uses the older ``nextContinuationData`` shape, nested under
    epgPaginationRenderer, rather than the ``continuationCommand`` that most of
    InnerTube has moved to. Both are checked so this keeps working if the guide
    is migrated.
    """
    for block in walk(response, "nextContinuationData"):
        if isinstance(block, dict) and block.get("continuation"):
            return block["continuation"]
    for command in walk(response, "continuationCommand"):
        if isinstance(command, dict) and command.get("token"):
            return command["token"]
    for endpoint in walk(response, "continuationEndpoint"):
        token = first(endpoint, "token")
        if token:
            return token
    return None


class Item(object):
    """Something the UI can show: a folder to browse, or a video to play."""

    __slots__ = ("video_id", "browse_id", "title", "subtitle", "art",
                 "start_ms", "end_ms")

    def __init__(self, video_id="", browse_id="", title="", subtitle="",
                 art="", start_ms=0, end_ms=0):
        self.video_id = video_id
        self.browse_id = browse_id
        self.title = title
        self.subtitle = subtitle
        self.art = art
        self.start_ms = start_ms
        self.end_ms = end_ms

    @property
    def playable(self):
        return bool(self.video_id)


def _endpoint_id(node, endpoint, key):
    """The id under a named endpoint, searching only this renderer.

    Deliberately shallow-ish: a renderer's menu carries endpoints for other
    things entirely ("Go to Rick and Morty", "Add to library"), so the first
    matching endpoint anywhere below would often be the wrong one. The
    navigationEndpoint is checked first for that reason.
    """
    nav = node.get("navigationEndpoint")
    if isinstance(nav, dict):
        block = nav.get(endpoint)
        if isinstance(block, dict) and block.get(key):
            return block[key]
    block = node.get(endpoint)
    if isinstance(block, dict) and block.get(key):
        return block[key]
    return ""


def _title_of(node):
    for field in ("primaryText", "title", "headline", "label"):
        value = text(node.get(field))
        if value:
            return value
    return ""


def _subtitle_of(node):
    parts = []
    for field in ("secondaryText", "tertiaryText", "descriptionSnippet"):
        value = text(node.get(field))
        if value and value not in parts:
            parts.append(value)
    return " • ".join(parts)


def _seconds_ms(node, field):
    try:
        return int(node[field]) * 1000
    except (KeyError, TypeError, ValueError):
        return 0


def _selector_labels(selectors):
    """The names a page gives its own deferred shelves, in order.

    The web client wraps each in a dropdownItemRenderer; the TV client does
    not, and asking for that name by itself logged ten shelves called
    "shelf". Take any renderer under the selector block that carries a label
    or a title, which is what a selector is, rather than one renderer name.
    """
    labels = []
    def visit(node):
        if isinstance(node, dict):
            for name, value in node.items():
                if (name.endswith("Renderer") and isinstance(value, dict)):
                    label = text(value.get("label")) or text(value.get("title"))
                    if label:
                        labels.append(label)
                        continue
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(selectors)
    return labels


def section_continuations(response):
    """(label, token) for every deferred shelf behind a page's own selector.

    A show page does not carry its episodes. Browsing Rick and Morty answers
    with the two newest episodes inline and nothing else: the ten seasons
    ("Season 1".."Season 9", "Extras") are ten empty shelves under an
    unpluggedSelectableSectionRenderer, each holding only a continuation
    token. The addon asked for the page and listed what was in it, which is
    why the cookie path showed two episodes where the account is entitled to
    nine -- the other seven were one request away and never requested.

    The selector labels and the shelves are two parallel lists rather than one
    structure, so they are paired by position: selectors[i] names contents[i].
    Labels are returned so a caller can say which shelf a request is for; the
    order is the page's own.
    """
    pairs = []
    for section in walk(response, "unpluggedSelectableSectionRenderer"):
        if not isinstance(section, dict):
            continue
        labels = _selector_labels(section.get("selectors"))
        for index, contents in enumerate(section.get("contents") or []):
            token = first(contents, "continuation")
            if not isinstance(token, str) or not token:
                continue
            pairs.append((labels[index] if index < len(labels) else "", token))
    return pairs


def parse_items(response):
    """Every renderer that names something to play or browse.

    Matched by shape rather than by renderer name. The first version of this
    listed the renderer names it expected -- tileRenderer, videoRenderer and
    friends -- and returned nothing at all, because YouTube TV uses its own
    (unpluggedGridVideoRenderer, unpluggedBrowseItemRenderer,
    unpluggedCompactVideoVersionRenderer). Names change; carrying a
    watchEndpoint and a title does not.
    """
    items = []
    seen = set()

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Renderer") and isinstance(value, dict):
                    _collect(value)
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    def _collect(renderer):
        title = _title_of(renderer)
        if not title:
            return
        video_id = _endpoint_id(renderer, "watchEndpoint", "videoId")
        browse_id = "" if video_id else _endpoint_id(renderer, "browseEndpoint",
                                                     "browseId")
        if not video_id and not browse_id:
            return
        key = video_id or browse_id
        if key in seen:
            return
        seen.add(key)
        items.append(Item(
            video_id=video_id,
            browse_id=browse_id,
            title=title,
            subtitle=_subtitle_of(renderer),
            art=thumbnail(renderer.get("thumbnail") or {}, prefer_width=1280),
            start_ms=_seconds_ms(renderer, "startTimeSeconds"),
            end_ms=_seconds_ms(renderer, "endTimeSeconds"),
        ))

    visit(response)
    return items


def unplayable_count(response):
    """How many things a response names that cannot be opened.

    parse_items drops a renderer with a title and no endpoint, which is what
    an episode the account has no rights to looks like: YouTube TV lists it,
    greyed, with no watchEndpoint. That is the correct thing to drop and the
    wrong thing to drop silently -- a shelf holding ten unplayable episodes
    and a shelf holding nothing both logged "0 of 0", and only one of those
    is a show you cannot watch.

    Counted the same way parse_items decides, so the two always agree.
    """
    count = 0
    seen = set()

    def visit(node):
        nonlocal count
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Renderer") and isinstance(value, dict):
                    title = _title_of(value)
                    # A thumbnail is what separates a tile from a badge or a
                    # menu entry, which also carry a title and no endpoint.
                    if (title and value.get("thumbnail")
                            and not _endpoint_id(value, "watchEndpoint",
                                                 "videoId")
                            and not _endpoint_id(value, "browseEndpoint",
                                                 "browseId")
                            and title not in seen):
                        seen.add(title)
                        count += 1
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(response)
    return count


def parse_search(response):
    """Search hits.

    YouTube TV answers a search with shows and scheduled airings, which carry a
    browseEndpoint rather than a watchEndpoint -- you browse into them to reach
    an episode. So most results are folders, and any that happen to be directly
    playable come back playable.
    """
    return parse_items(response)
