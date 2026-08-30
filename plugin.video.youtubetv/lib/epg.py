"""Turning InnerTube renderers into channels and programmes.

The EPG response is a deep tree of ``*Renderer`` dicts with the interesting
values scattered through it. Rather than walk fixed paths -- which break the
first time Google inserts a wrapper -- everything here searches by key name and
tolerates absence. A missing description is not worth an exception.
"""

import base64
import binascii
import re
import time
from urllib.parse import unquote

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

    __slots__ = ("video_id", "title", "description", "start_ms", "end_ms",
                 "art", "on_air", "show_id")

    def __init__(self, video_id, title, description, start_ms, end_ms, art,
                 on_air=False, show_id=""):
        self.video_id = video_id
        self.title = title
        self.description = description
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.art = art
        # True when the id came from a watchEndpoint rather than the plain
        # videoId field. YouTube TV gives the endpoint to exactly one airing
        # per channel -- the one on the air -- so this is the guide's own
        # word for "now", and worth more than arithmetic on a clock that may
        # not agree with Google's.
        self.on_air = on_air
        # The show this programme belongs to, when the guide says. A
        # side-sheet command names both the programme and its show, and the
        # show is what the DVR records -- YouTube TV records a series, not
        # an airing.
        self.show_id = show_id

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
        # The guide's own marker first, then the clock. YouTube TV gives a
        # watchEndpoint to exactly one airing per channel and it is the one
        # on the air, which beats comparing timestamps against a clock that
        # need not agree with Google's -- run against the 2026-08-29 capture
        # from a later day, is_now picked a different airing on 113 of the
        # 148 stations while the marker was right on all of them.
        #
        # This also keeps what the addon did before the guide carried whole
        # schedules: there was one airing per station, the marked one, and
        # "now" was always it. Falling through to airings[0] now would mean
        # playing whatever was on this morning.
        for airing in self.airings:
            if airing.on_air:
                return airing
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
    """One programme.

    The id is taken from a watchEndpoint if there is one and from the
    renderer's own ``videoId`` field otherwise. Only the programme actually
    on the air carries the endpoint: of the 953 airings in the 2026-08-29
    guide, 144 had one -- one per channel, the one showing now -- and the
    other 809 carried a navigationEndpoint to the show page and their id in
    a plain field beside it. Reading the endpoint alone is why the guide
    listed what was on and nothing after it.
    """
    video_id = ""
    for endpoint in walk(renderer, "watchEndpoint"):
        if isinstance(endpoint, dict) and endpoint.get("videoId"):
            video_id = endpoint["videoId"]
            break
    on_air = bool(video_id)
    if not video_id and isinstance(renderer.get("videoId"), str):
        video_id = renderer["videoId"]
    # Which show this programme belongs to, which is what the DVR records.
    # entitiesDvrStatus names it on every airing, including the one on the
    # air, whose watchEndpoint carries no show id anywhere. Checked against
    # the side-sheet command on the 843 airings that have both: they agree
    # every time, none differ, and this covers the 143 that the side sheet
    # does not. The field carries no state despite its name -- just the id.
    show_id = ""
    status = renderer.get("entitiesDvrStatus")
    if isinstance(status, list) and status and isinstance(status[0], dict):
        found = status[0].get("entityId")
        if isinstance(found, str):
            show_id = found

    for carrier in _ENDPOINT_CARRIERS:
        block = renderer.get(carrier)
        if not isinstance(block, dict):
            continue
        command = block.get("unpluggedGetSidesheetCommand")
        if not isinstance(command, dict):
            continue
        params = command.get("params")
        if not show_id:
            show_id = sidesheet_id(params)
        if not video_id:
            video_id = sidesheet_video_id(params)
        if show_id and video_id:
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
        on_air=on_air,
        show_id=show_id,
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
            # A later page of the guide describes each channel once, on the
            # first page, and then sends rows carrying only a stationId and
            # more airings. Requiring the station renderer threw away all
            # 748 airings of the second page. The row still says which
            # channel it is; the name and logo come from the merge.
            station_id = (row.get("stationId") or row.get("tenxId") or ""
                          ) if isinstance(row, dict) else ""
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
            stations.append(Station(station_id=station_id, name="",
                                    call_sign="", logo="", airings=airings))
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

        # Searched across the whole station renderer rather than under
        # "icon". Only 7 of the 148 stations in the 2026-08-29 guide carry
        # a name or a callSign at all; the other 141 are named solely by the
        # accessibility label on their logo. Naming the key that logo sits
        # under is therefore the difference between a lineup and 141 rows
        # called UC5M1ACzZ9iIL42YKinxZrFQ -- and a client that files it
        # under any other key loses the name and the logo together, which is
        # what "mostly missing station names and no logos" looks like.
        # "icon" is still preferred where it exists -- four of the 148 have
        # a secondaryIcon nearer 400px and would otherwise swap to it -- and
        # the whole renderer is only the fallback. thumbnail() and
        # accessibility_label() both already search at any depth, so that
        # fallback costs nothing and assumes no key name.
        stations.append(Station(
            station_id=station_id,
            name=(text(renderer.get("name"))
                  or text(renderer.get("callSign"))
                  or text(renderer.get("title"))
                  or accessibility_label(renderer.get("icon") or {})
                  or accessibility_label(renderer)
                  or station_id),
            call_sign=text(renderer.get("callSign")),
            logo=(thumbnail(renderer.get("icon") or {}, prefer_width=400)
                  or thumbnail(renderer, prefer_width=400)),
            airings=airings,
        ))

    if not stations:
        kodiutils.log("no epgRowRenderer in the guide response -- the EPG "
                      "shape may have changed")
    return stations


def merge_airings(stations, more):
    """Fold a later page's airings into the stations already parsed.

    Returns how many were new. A later page repeats nothing, but it is
    deduplicated by video id anyway, because a guide that lists a programme
    twice is worse than one that fetches a page for nothing. Airings that
    belong to no station already known are dropped: without the first page's
    station renderer they have no name and no logo, so they would list as a
    channel called by its own id.
    """
    by_id = {station.station_id: station for station in stations}
    added = 0
    for station in more:
        target = by_id.get(station.station_id)
        if target is None:
            continue
        known = {airing.video_id for airing in target.airings}
        for airing in station.airings:
            if airing.video_id in known:
                continue
            known.add(airing.video_id)
            target.airings.append(airing)
            added += 1
        target.airings.sort(key=lambda a: a.start_ms or 0)
    return added


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


# Where a tile keeps the endpoint you get by selecting it, in the order to
# believe them. Still one level deep, deliberately: a renderer's menu carries
# endpoints for other things entirely ("Go to Rick and Morty", "Add to
# library"), so the first matching endpoint anywhere below would often be the
# wrong one.
#
# "command" is *not* on this list, and that is the point of the list. It is
# the key an unpluggedMenuItemRenderer keeps its watchEndpoint under, so
# accepting it would list the two buttons of a "Join live / Start from
# beginning" dialog as two rows of their own.
#
# entityPageNavigationEndpoint comes last: it is the show page behind a tile
# that also has somewhere better to go. No renderer in any capture carries it
# without a navigationEndpoint, so it only ever answers when nothing else has.
_ENDPOINT_CARRIERS = ("navigationEndpoint", "onSelectCommand", "tapCommand",
                      "entityPageNavigationEndpoint")


def _endpoint_id(node, endpoint, key):
    """The id under a named endpoint, searching only this renderer.

    The web client puts it under navigationEndpoint. The TV client answered
    the Library with nine correctly named tabs holding nothing at all --
    nineteen unpluggedBrowseItemRenderers that parse_items could not place --
    which is what a tile whose endpoint is filed elsewhere looks like.
    """
    for carrier in _ENDPOINT_CARRIERS:
        block = node.get(carrier)
        if not isinstance(block, dict):
            continue
        found = block.get(endpoint)
        if isinstance(found, dict) and found.get(key):
            return found[key]
    block = node.get(endpoint)
    if isinstance(block, dict) and block.get(key):
        return block[key]

    # Nothing under the name this addon knows. Take any endpoint that names
    # the id instead, rather than guess at another name.
    #
    # The TV client's unpluggedBrowseItemRenderer carries a primaryText, a
    # thumbnail and a navigationEndpoint -- everything a tile needs -- and
    # was still dropped, nineteen times over, because what sits inside that
    # endpoint is called neither watchEndpoint nor browseEndpoint. A
    # navigationEndpoint has exactly one destination, so an endpoint under
    # it holding a videoId *is* the video and one holding a browseId *is*
    # the page, whatever Google decided to call it this year.
    #
    # Only one level down, so this cannot reach into an
    # unpluggedPopupEndpoint's dialog (still read by _popup_video_id, which
    # picks the right one of its two buttons) or a menu. And it stays silent
    # on the endpoints that name no id at all, so the ten Rick and Morty buy
    # prompts, whose unpluggedInitiateInlinePurchaseCommand carries only
    # params, are still correctly dropped.
    for carrier in _ENDPOINT_CARRIERS:
        block = node.get(carrier)
        if not isinstance(block, dict):
            continue
        for name, value in block.items():
            if (name.endswith("Endpoint") and isinstance(value, dict)
                    and value.get(key)):
                return value[key]
    return ""


def _varint(data, at):
    shift = value = 0
    while at < len(data):
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
        if shift > 63:
            break
    return None, at


def _protobuf_strings(data, depth=0):
    """Every length-delimited field in a protobuf, and those nested inside.

    A deliberately small reader: enough to walk a token whose schema is not
    published, and no more. Anything it cannot make sense of ends the walk
    rather than guessing, because a wrong offset in a protobuf produces
    plausible nonsense rather than an error.
    """
    at = 0
    while at < len(data):
        tag, at = _varint(data, at)
        if tag is None:
            return
        wire = tag & 7
        if wire == 0:
            value, at = _varint(data, at)
            if value is None:
                return
        elif wire == 1:
            at += 8
        elif wire == 5:
            at += 4
        elif wire == 2:
            length, at = _varint(data, at)
            if length is None or at + length > len(data):
                return
            chunk = data[at:at + length]
            at += length
            yield chunk
            if depth < 4:
                for inner in _protobuf_strings(chunk, depth + 1):
                    yield inner
        else:
            return          # a group; not something these tokens use


# What a browse id looks like: "UCmXMw6OyWJH1O6cA7JZS9Fg" for a show or a
# team, "SEEV_g_11z7gny2t0" for an event. Long enough, and made only of the
# characters an id is made of, which is what separates it from the protobuf
# framing around it.
_BROWSE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# A video id is eleven characters; a channel or show id is twenty-four
# beginning "UC". A side-sheet command for a programme carries both -- the
# show it belongs to and the programme itself -- so which one is wanted
# depends on the caller, and neither is guessed at by position.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _sidesheet_ids(params):
    """Every id-shaped string inside a side-sheet command's params."""
    if not isinstance(params, str) or not params:
        return
    token = unquote(params)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        return
    for chunk in _protobuf_strings(raw):
        try:
            found = chunk.decode("ascii")
        except UnicodeDecodeError:
            continue
        if _BROWSE_ID.match(found):
            yield found


def sidesheet_video_id(params):
    """The programme's own video id inside a side-sheet command.

    A guide airing that is not the one currently on the air carries no
    watchEndpoint and no videoId field: its navigationEndpoint holds an
    unpluggedGetSidesheetCommand, exactly as the Library's tiles do. Of the
    989 airings in the 2026-08-29 20:48 guide, 143 had a watchEndpoint --
    one per station, the one on the air -- and the other 846 had a side
    sheet. Every one of those 846 carries exactly two ids: the show's
    twenty-four character one and the programme's eleven-character one.

    Reading only the endpoint is why the guide listed one programme per
    channel and nothing after it.
    """
    for found in _sidesheet_ids(params):
        if _VIDEO_ID.match(found):
            return found
    return ""


def sidesheet_id(params):
    """The browse id buried in an unpluggedGetSidesheetCommand's params.

    The TV client's Library tiles do not navigate: each carries a
    navigationEndpoint holding unpluggedGetSidesheetCommand, which opens a
    detail panel. There is no browseId field anywhere on the tile, so all
    nineteen were dropped as naming nowhere to go.

    The id is inside the command's params, base64 of a small protobuf. In
    the 2026-08-29 capture every one of the nineteen decoded to a nested
    field holding the same id the web client puts in a plain browseEndpoint
    -- UCmXMw6OyWJH1O6cA7JZS9Fg for Pittsburgh Steelers, and so on for all
    seven distinct titles -- which is what makes reading it a measurement
    rather than a hope.

    The outer field number varies by what the tile is (3 for a movie, 4 for
    a show, 7 for a sports team), so the walk is by shape rather than by
    field number: the first nested string that looks like an id.
    """
    for found in _sidesheet_ids(params):
        return found
    return ""


def _sidesheet_browse_id(node):
    """The id a tile hides in a side-sheet command, if it has one."""
    for carrier in _ENDPOINT_CARRIERS:
        block = node.get(carrier)
        if not isinstance(block, dict):
            continue
        command = block.get("unpluggedGetSidesheetCommand")
        if isinstance(command, dict):
            found = sidesheet_id(command.get("params"))
            if found:
                return found
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
    for carrier in _ENDPOINT_CARRIERS:
        block = node.get(carrier)
        if not isinstance(block, dict):
            continue
        popup = block.get("unpluggedPopupEndpoint")
        if not isinstance(popup, dict):
            continue
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
        # A plain videoId/browseId field beside the endpoints counts too:
        # that is where the guide keeps the id of every programme that is
        # not the one currently on the air.
        video_id = (_endpoint_id(renderer, "watchEndpoint", "videoId")
                    or _popup_video_id(renderer)
                    or (renderer.get("videoId")
                        if isinstance(renderer.get("videoId"), str) else ""))
        browse_id = "" if video_id else (
            _endpoint_id(renderer, "browseEndpoint", "browseId")
            or (renderer.get("browseId")
                if isinstance(renderer.get("browseId"), str) else "")
            or _sidesheet_browse_id(renderer))
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
                            and not value.get("videoId")
                            and not value.get("browseId")
                            and not _sidesheet_browse_id(value)
                            and title not in seen):
                        seen.add(title)
                        count += 1
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(response)
    return count


def unreadable_sample(node, limit=3):
    """Key names of renderers that look like tiles but name nowhere to go.

    parse_items drops a renderer carrying a title and no endpoint it knows.
    That is right for an episode the account has no rights to and wrong for a
    tile whose endpoint is simply filed under a key this addon has not seen,
    and the two are indistinguishable in a count: the TV client's Library
    came back as nine correctly named tabs holding nothing, which is what
    nineteen perfectly good tiles look like when their endpoint is somewhere
    unexpected.

    Listing the keys such a renderer actually carries names the one to read
    next, and costs a log line rather than another round trip.
    """
    out = []

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if (len(out) < limit and key.endswith("Renderer")
                        and isinstance(value, dict) and _title_of(value)
                        and value.get("thumbnail")
                        and not _endpoint_id(value, "watchEndpoint", "videoId")
                        and not _endpoint_id(value, "browseEndpoint", "browseId")
                        and not _popup_video_id(value)):
                    inside = []
                    for carrier in _ENDPOINT_CARRIERS:
                        block = value.get(carrier)
                        if isinstance(block, dict):
                            inside.append("%s -> [%s]"
                                          % (carrier,
                                             ", ".join(sorted(block.keys()))))
                    out.append("%s carries [%s]%s"
                               % (key, ", ".join(sorted(value.keys())),
                                  "; " + "; ".join(inside) if inside else ""))
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(node)
    return out


def renderer_sample(response, limit=8):
    """The keys one example of each renderer name carries.

    unreadable_sample asks for a title and a thumbnail before it reports a
    renderer, so a client that files *those* elsewhere too gets reported as
    nothing at all -- which is what happened: a page holding nineteen tiles
    produced no sample line. This asks for nothing. It cannot come back
    empty while the response holds any renderer, which is the property a
    last-resort diagnostic needs.

    Ordered by how many of each there are, so the tile renderer -- the
    numerous one -- leads.
    """
    counts = {}
    first_seen = {}

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Renderer") and isinstance(value, dict):
                    counts[key] = counts.get(key, 0) + 1
                    if key not in first_seen:
                        first_seen[key] = sorted(value.keys())
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(response)
    ranked = sorted(counts.items(), key=lambda pair: -pair[1])[:limit]
    return ["%s x%d carries [%s]" % (name, count,
                                     ", ".join(first_seen[name]))
            for name, count in ranked]


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
