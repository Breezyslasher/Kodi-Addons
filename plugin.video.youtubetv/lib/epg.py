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


def _popup_video_id(node):
    """The video a tile hides behind a "how would you like to watch?" dialog.

    A scheduled recording that is on the air right now carries no
    watchEndpoint at all: its navigationEndpoint is an
    unpluggedPopupEndpoint whose dialog offers "Join live" and "Start from
    beginning". Both name the same videoId, so either will do, and the
    params that separate them are not something route_play carries anyway.

    Every recording that has not started yet carries a plain browseEndpoint
    instead -- measured across the seven tiles in the Library capture, where
    only the one then on the air had the popup. So the popup's presence is
    YouTube TV's own statement that this one is playable now, and no clock
    arithmetic is needed here. Without this the live tile was dropped
    entirely: it has a title and a thumbnail and, as far as _endpoint_id
    could see, nowhere to go.
    """
    popup = (node.get("navigationEndpoint") or {}).get("unpluggedPopupEndpoint")
    if not isinstance(popup, dict):
        return ""
    for endpoint in walk(popup, "watchEndpoint"):
        if isinstance(endpoint, dict) and endpoint.get("videoId"):
            return endpoint["videoId"]
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
        video_id = (_endpoint_id(renderer, "watchEndpoint", "videoId")
                    or _popup_video_id(renderer))
        browse_id = "" if video_id else _endpoint_id(renderer, "browseEndpoint",
                                                     "browseId")
        if not video_id and not browse_id:
            return
        # Keyed by destination *and* start time. Destination alone collapsed
        # two Phineas and Ferb recordings an hour apart into one row, because
        # both point at the same show page; two airings are two rows. Nothing
        # without a start time is affected, which is every show page.
        key = (video_id or browse_id, _seconds_ms(renderer, "startTimeSeconds"))
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
                            and not _popup_video_id(value)
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


class Section(object):
    """A named row of a page: what it holds, and how to ask for more."""

    __slots__ = ("title", "items", "token")

    def __init__(self, title, items, token=""):
        self.title = title
        self.items = items
        self.token = token


def _section_list(response):
    """The page's own top-level list of rows.

    A first request answers with sectionListRenderer; the Library arrives as
    a continuation, so it answers with sectionListContinuation. Only the
    direct entries are wanted -- searching the whole tree for shelfRenderer
    would also find the nineteen cells inside the filter grid below, which
    are shelves too and are not rows of this page.
    """
    for key in ("sectionListContinuation", "sectionListRenderer",
                "unpluggedLibraryContinuation"):
        block = first(response, key)
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("contents"), list):
            return block["contents"]
        # unpluggedLibraryContinuation, which the TV client answers the
        # Library with, holds one renderer under "content" rather than a
        # list of rows: the whole page is a single selectable section.
        if isinstance(block.get("content"), dict):
            return [block["content"]]
    return []


def _dropdown(block):
    """(label, is_selected) for each entry of a dropdownRenderer."""
    entries = []
    for entry in (block.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        item = entry.get("dropdownItemRenderer")
        if isinstance(item, dict):
            entries.append((text(item.get("label")), bool(item.get("isSelected"))))
    return entries


def _chip_labels(selectors):
    """The names on a chip row.

    The TV client puts the Library's tabs in an
    unpluggedHorizontalChipListRenderer rather than the web client's filter
    and sort dropdowns. Read explicitly rather than through
    _selector_labels, which stops at the first renderer carrying a title:
    if the chip list itself ever gains one, that would return a single
    label for nine tabs and the pairing below would rightly refuse to run.
    """
    labels = []
    for chip in walk(selectors, "unpluggedChipRenderer"):
        if isinstance(chip, dict):
            labels.append(text(chip.get("title")) or text(chip.get("label")))
    return labels


def _filter_cells(section):
    """(label, cell) for each tab of a selectable section, or None.

    Two selectors, two pairings, and getting them the wrong way round names
    a tab with somebody else's row -- so each is checked against the number
    of cells before it is used, and a mismatch returns None rather than a
    best effort.

    The web client sends an unpluggedFilterSortSelectorRenderer: a filter
    dropdown and one sort dropdown per filter, with ``contents`` holding the
    cross product flattened row by row. Six filters, 4+6+1+4+1+3 sorts,
    nineteen cells. Each filter is represented by its selected sort.

    The TV client sends an unpluggedHorizontalChipListRenderer instead --
    nine chips, nine cells, one each -- so there is no cross product to
    unpick and the pairing is positional.
    """
    contents = section.get("contents") or []
    selector = first(section, "unpluggedFilterSortSelectorRenderer")
    if isinstance(selector, dict):
        filters = _dropdown((selector.get("filterSelector") or {})
                            .get("dropdownRenderer") or {})
        sorts = [_dropdown(entry.get("dropdownRenderer") or {})
                 for entry in (selector.get("sortSelectors") or [])
                 if isinstance(entry, dict)]
        total = sum(len(row) for row in sorts)
        if len(sorts) != len(filters) or total != len(contents):
            kodiutils.log("library: %d filter(s), %d sort list(s) totalling "
                          "%d, but %d cell(s) -- the grid has changed shape"
                          % (len(filters), len(sorts), total, len(contents)))
            return None
        pairs = []
        at = 0
        for index, (name, _selected) in enumerate(filters):
            row = sorts[index]
            chosen = next((i for i, (_l, s) in enumerate(row) if s), 0)
            pairs.append((name, contents[at + chosen]))
            at += len(row)
        return pairs

    labels = _chip_labels(section.get("selectors"))
    if not labels:
        labels = _selector_labels(section.get("selectors"))
    if not labels or len(labels) != len(contents):
        kodiutils.log("library: %d tab name(s) but %d cell(s) -- the "
                      "selector has changed shape" % (len(labels), len(contents)))
        return None
    return list(zip(labels, contents))


def library_filters(response):
    """The Library's filter tabs, each with the row behind it.

    The Library is not a page of shelves like a show page. One
    unpluggedSelectableSectionRenderer carries the tabs and one cell of
    ``contents`` per tab, and the two clients spell the tabs differently --
    dropdowns on the web, a chip row on the TV. _filter_cells pairs them,
    and refuses rather than mispair.

    A tab with nothing in it comes back as an unpluggedEmptyStateRenderer
    ("No movies in your library") -- no items and no token -- and is dropped,
    so an empty tab never becomes an empty folder. A tab YouTube TV has not
    selected arrives with only a continuation token, which the caller
    follows.
    """
    sections = []
    for block in walk(response, "unpluggedSelectableSectionRenderer"):
        if not isinstance(block, dict):
            continue
        pairs = _filter_cells(block)
        if not pairs:
            continue
        for name, cell in pairs:
            items = parse_items(cell)
            token = first(cell, "continuation") or ""
            if not items and not token:
                continue
            sections.append(Section(name or "Library", items,
                                    token if isinstance(token, str) else ""))
    return sections


# The renderers a page uses for a named row, and where each keeps its name.
# The Library calls them shelfRenderer with a "title"; Home calls them
# unpluggedHomeShelfRenderer with a "primaryText". Same thing, twice named.
_SHELVES = (("shelfRenderer", "title"),
            ("unpluggedHomeShelfRenderer", "primaryText"))


def page_shelves(response):
    """The named rows of a page, in the page's own order.

    The Library's "New in your library", "Most watched" and "Scheduled
    recordings"; Home's "Resume watching", "Top picks for you", "Sports" and
    the twenty genre rows behind them.

    A row with a name and nothing in it is dropped -- Home answers with
    "Add to your library" and "Upcoming games" holding no items at all, and
    an empty folder is worse than no folder.
    """
    sections = []
    for entry in _section_list(response):
        if not isinstance(entry, dict):
            continue
        for name, title_key in _SHELVES:
            shelf = entry.get(name)
            if not isinstance(shelf, dict):
                continue
            title = text(shelf.get(title_key))
            items = parse_items(shelf)
            if not title or not items:
                continue
            token = first(shelf, "continuation") or ""
            sections.append(Section(title, items,
                                    token if isinstance(token, str) else ""))
            break
    return sections


def page_continuation(response):
    """The token for the page's *next* page, and nothing else.

    Deliberately not continuation_token, which searches the whole tree: the
    first nextContinuationData in a Home response belongs to the first
    shelf, not to the page, and following it would fetch more of "Top picks
    for you" while believing it had fetched the next twenty rows. The page's
    own token sits in the section list's ``continuations``, alongside a
    timedContinuationData (a refresh timer) and a reloadContinuationData
    (the page again), neither of which is a next page.
    """
    for key in ("sectionListContinuation", "sectionListRenderer"):
        block = first(response, key)
        if not isinstance(block, dict):
            continue
        for entry in (block.get("continuations") or []):
            if not isinstance(entry, dict):
                continue
            data = entry.get("nextContinuationData")
            if isinstance(data, dict) and data.get("continuation"):
                return data["continuation"]
    return None


def parse_library(response):
    """(shelves, filters) for the Library page.

    Kept apart because they are read differently and shown differently: the
    shelves are curated rows, the filters are one collection sliced six ways.
    """
    return page_shelves(response), library_filters(response)


_TITLE_KEYS = ("title", "primaryText", "headerText", "shelfTitle")


def any_rows(response, least=2):
    """Named rows found by shape, for a page whose container is unknown.

    page_shelves knows two containers, both read off web-client captures.
    The TV client answers the Library with something else -- 0 rows, 0
    filters, and a flat listing of whatever parse_items could reach -- so
    this is the reader that does not need to know the container's name.

    A row is any renderer that carries a title and has at least ``least``
    playable-or-browsable things under it. Only the innermost such renderer
    is kept: a page is itself a titled renderer containing every row, and
    returning that alongside its own children would list everything twice.
    Containment is tracked with an ancestor stack rather than inferred from
    the item sets, which would be a guess whenever two rows happen to
    overlap.
    """
    found = []

    def visit(node, ancestors):
        if isinstance(node, dict):
            for key, value in node.items():
                if not (key.endswith("Renderer") and isinstance(value, dict)):
                    visit(value, ancestors)
                    continue
                title = ""
                for name in _TITLE_KEYS:
                    title = text(value.get(name))
                    if title:
                        break
                if not title:
                    visit(value, ancestors)
                    continue
                items = parse_items(value)
                if len(items) < least:
                    visit(value, ancestors)
                    continue
                mine = len(found)
                found.append([title, items, first(value, "continuation") or "",
                              set(ancestors), False])
                for index in ancestors:
                    found[index][4] = True      # an ancestor has a child row
                visit(value, ancestors + [mine])
        elif isinstance(node, list):
            for value in node:
                visit(value, ancestors)

    visit(response, [])
    rows = []
    for title, items, token, _ancestors, has_child in found:
        if has_child:
            continue
        rows.append(Section(title, items, token if isinstance(token, str) else ""))
    return rows


def describe(response, limit=40):
    """A compact account of a response's shape, for the log.

    Printed when a page arrives in a container this addon does not know.
    Naming the renderers that came back, and the lists they sit in, is the
    difference between "the Library did not parse" and knowing what to
    write next.
    """
    counts = {}
    lists = []

    def visit(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Renderer") and isinstance(value, dict):
                    counts[key] = counts.get(key, 0) + 1
                visit(value, path + "/" + key)
        elif isinstance(node, list):
            names = [name for entry in node if isinstance(entry, dict)
                     for name in entry if name.endswith("Renderer")]
            if len(names) >= 2:
                seen = []
                for name in names:
                    if name not in seen:
                        seen.append(name)
                lists.append((path, len(node), ",".join(seen[:4])))
            for index, value in enumerate(node):
                visit(value, path + "[%d]" % index)

    visit(response, "")
    ranked = sorted(counts.items(), key=lambda pair: -pair[1])[:limit]
    lists.sort(key=lambda row: -row[1])
    return ("top-level keys: %s\n  renderers: %s\n  lists: %s"
            % (", ".join(sorted(response.keys())) if isinstance(response, dict)
               else type(response).__name__,
               ", ".join("%s x%d" % pair for pair in ranked) or "none",
               " | ".join("%s (%d: %s)" % row for row in lists[:8]) or "none"))
