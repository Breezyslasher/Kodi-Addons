"""The Home and Library readers, against the shapes the captures showed.

Every fixture here is cut down from a real response -- the Library capture of
2026-08-29 19:07 and the Home capture of 19:16 -- and each one exists because
the shape it holds is a trap that a naive reader falls into:

  * the Library's filter grid is a *cross product* flattened row by row, not
    one cell per filter, so pairing selectors[i] with contents[i] (which is
    right for a show page) pairs "Shows" with All's second sort;
  * a scheduled recording that is on the air right now carries no
    watchEndpoint, only a popup dialog with one inside, so it was dropped;
  * two airings of the same show point at the same show page, so a dedupe by
    destination alone collapses them into one row;
  * Home names its rows unpluggedHomeShelfRenderer/primaryText where the
    Library says shelfRenderer/title;
  * the first nextContinuationData in a Home response belongs to the first
    *shelf*, not the page, so "next page" must be read from the section
    list's own continuations or it fetches more of "Top picks for you".

Account data is deliberately not committed: these are the structures, with
the titles and ids replaced.
"""

import base64
import copy
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/stubs")
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE))
                + "/plugin.video.youtubetv")

from lib import epg  # noqa: E402

failures = []


def check(what, got, want):
    if got == want:
        print("  ok   %s == %r" % (what, want))
    else:
        failures.append(what)
        print("  FAIL %s: got %r, wanted %r" % (what, got, want))


def _runs(value):
    return {"runs": [{"text": value}]}


def _dropdown(labels, selected=0):
    return {"dropdownRenderer": {"entries": [
        {"dropdownItemRenderer": {"label": _runs(label),
                                  "isSelected": index == selected}}
        for index, label in enumerate(labels)]}}


def _tile(title, browse_id="", video_id=""):
    tile = {"primaryText": _runs(title),
            "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]}}
    if video_id:
        tile["navigationEndpoint"] = {"watchEndpoint": {"videoId": video_id}}
    elif browse_id:
        tile["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse_id}}
    return {"unpluggedBrowseItemRenderer": tile}


def _cell(items=(), token=""):
    grid = {}
    if items:
        grid["items"] = list(items)
    if token:
        grid["continuations"] = [{"nextContinuationData": {"continuation": token}}]
    return {"unpluggedSelectableSectionContentsRenderer": {"contents": [
        {"shelfRenderer": {"content": {"gridRenderer": grid}}}]}}


def _empty_state(text):
    return {"unpluggedSelectableSectionContentsRenderer": {"contents": [
        {"unpluggedEmptyStateRenderer": {"primaryText": _runs(text)}}]}}


# -- the Library grid ------------------------------------------------------
# Three filters with 2, 1 and 2 sorts: five cells, row-major. The second
# filter is empty. The third filter's *second* sort is the selected one,
# which is what catches an implementation that always takes the first cell
# of a row.
FILTERS = ["All", "Movies", "Purchased"]
GRID = {"continuationContents": {"sectionListContinuation": {"contents": [
    {"shelfRenderer": {
        "title": _runs("Scheduled recordings"),
        "content": {"horizontalListRenderer": {"items": [
            # On the air now: no watchEndpoint, a popup holding one.
            {"unpluggedGridVideoRenderer": {
                "primaryText": _runs("Family Feud"),
                "startTimeSeconds": "1788044400",
                "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
                "navigationEndpoint": {"unpluggedPopupEndpoint": {"popupRenderer": {
                    "unpluggedSelectionMenuDialogRenderer": {"items": [
                        {"unpluggedMenuItemRenderer": {
                            "primaryText": _runs("Join live"),
                            "command": {"watchEndpoint": {"videoId": "LIVEVID"}}}},
                        {"unpluggedMenuItemRenderer": {
                            "primaryText": _runs("Start from beginning"),
                            "command": {"watchEndpoint": {"videoId": "LIVEVID"}}}}]}}}},
                "entityPageNavigationEndpoint": {"browseEndpoint": {"browseId": "UCSHOW"}}}},
            # Two later airings of one show: same destination, two rows.
            {"unpluggedGridVideoRenderer": {
                "primaryText": _runs("Phineas and Ferb"),
                "startTimeSeconds": "1788055200",
                "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
                "navigationEndpoint": {"browseEndpoint": {"browseId": "UCPNF"}}}},
            {"unpluggedGridVideoRenderer": {
                "primaryText": _runs("Phineas and Ferb"),
                "startTimeSeconds": "1788057000",
                "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
                "navigationEndpoint": {"browseEndpoint": {"browseId": "UCPNF"}}}},
        ]}}}},
    {"unpluggedContentDetailsRenderer": {"contents": [
        {"unpluggedSelectableSectionRenderer": {
            "title": _runs("Recordings & purchases"),
            "selectors": [{"unpluggedFilterSortSelectorRenderer": {
                "filterSelector": _dropdown(FILTERS),
                "sortSelectors": [_dropdown(["Recent", "A to Z"]),
                                  _dropdown(["A to Z"]),
                                  _dropdown(["Recently purchased", "A to Z"],
                                            selected=1)]}}],
            "contents": [
                _cell(items=[_tile("Family Feud", browse_id="UCSHOW")]),
                _cell(token="ALL-A-TO-Z"),
                _empty_state("No movies in your library"),
                _cell(token="PURCHASED-RECENT"),
                _cell(items=[_tile("Rogue One", browse_id="UCMOVIE")]),
            ]}}]}},
]}}}

shelves, filters = epg.parse_library(GRID)
check("library shelves", [s.title for s in shelves], ["Scheduled recordings"])
check("scheduled recordings kept all three airings",
      len(shelves[0].items), 3)
check("the live one is playable",
      [i.video_id for i in shelves[0].items if i.playable], ["LIVEVID"])
check("the two later airings stayed two rows",
      [i.browse_id for i in shelves[0].items[1:]], ["UCPNF", "UCPNF"])
check("nothing counted unplayable", epg.unplayable_count(GRID["continuationContents"]
      ["sectionListContinuation"]["contents"][0]), 0)

check("empty filter dropped", [s.title for s in filters], ["All", "Purchased"])
check("All took its selected sort's cell",
      [i.title for i in filters[0].items], ["Family Feud"])
check("Purchased took its *second* sort, the selected one",
      [i.title for i in filters[1].items], ["Rogue One"])

# A grid whose three lists disagree must be declined, not mispaired.
BROKEN = copy.deepcopy(GRID)
section = BROKEN["continuationContents"]["sectionListContinuation"]["contents"][1] \
    ["unpluggedContentDetailsRenderer"]["contents"][0]["unpluggedSelectableSectionRenderer"]
section["contents"] = section["contents"][:3]
check("a reshaped grid lists nothing", epg.library_filters(BROKEN), [])

# -- Home ------------------------------------------------------------------
HOME = {"continuationContents": {"sectionListContinuation": {
    "contents": [
        {"unpluggedHomeShelfRenderer": {
            "primaryText": _runs("Top picks for you"),
            "content": {"horizontalListRenderer": {
                "items": [_tile("A film", video_id="VID1")],
                "continuations": [{"nextContinuationData": {
                    "continuation": "SHELF-TOKEN"}}]}}}},
        {"unpluggedHomeShelfRenderer": {
            "primaryText": _runs("Add to your library"),
            "content": {"horizontalListRenderer": {"items": []}}}},
    ],
    "continuations": [
        {"timedContinuationData": {"timeoutMs": 812969,
                                   "continuation": "REFRESH"}},
        {"reloadContinuationData": {"continuation": "RELOAD"}},
        {"nextContinuationData": {"continuation": "PAGE-TOKEN"}},
    ]}}}

rows = epg.page_shelves(HOME)
check("home rows named from primaryText",
      [s.title for s in rows], ["Top picks for you"])
check("the row kept its own token", rows[0].token, "SHELF-TOKEN")
check("next page is the page's token, not the first shelf's",
      epg.page_continuation(HOME), "PAGE-TOKEN")
check("continuation_token still finds the shelf's, tree-first",
      epg.continuation_token(HOME), "SHELF-TOKEN")

# -- the reader that needs no container name -------------------------------
# The TV client answers the Library with a container neither reader above
# knows, so rows are found by shape instead. The page is itself a titled
# renderer holding every row, so only the innermost titled renderer counts --
# otherwise the page is listed alongside its own children.
NESTED = {"contents": {"pageRenderer": {
    "title": _runs("Recordings & purchases"),
    "contents": [
        {"shelfRenderer": {"title": _runs("Row one"), "content": {"gridRenderer": {
            "items": [_tile("A", browse_id="B1"), _tile("B", browse_id="B2")]}}}},
        {"shelfRenderer": {"title": _runs("Row two"), "content": {"gridRenderer": {
            "items": [_tile("C", browse_id="B3"), _tile("D", browse_id="B4")]}}}},
        # A titled renderer with one item is a tile, not a row.
        {"shelfRenderer": {"title": _runs("Not a row"), "content": {"gridRenderer": {
            "items": [_tile("E", browse_id="B5")]}}}},
    ]}}}

rows = epg.any_rows(NESTED)
check("rows found by shape, innermost only",
      [r.title for r in rows], ["Row one", "Row two"])
check("the page itself is not one of them",
      "Recordings & purchases" in [r.title for r in rows], False)
check("rows by shape on the Library grid",
      [r.title for r in epg.any_rows(GRID)],
      ["Scheduled recordings", "Recordings & purchases"])

described = epg.describe(NESTED)
check("describe names the renderers it saw",
      "shelfRenderer x3" in described, True)
check("describe names the biggest list", "(3:" in described, True)

# -- the shape the TV client actually sends --------------------------------
# Paths and counts taken from the 2026-08-29 19:39 shape dump on a real
# account: continuationContents/unpluggedLibraryContinuation/content/
# unpluggedSelectableSectionRenderer, whose selectors[0] is an
# unpluggedHorizontalChipListRenderer of chips and whose contents holds one
# cell per chip -- nine and nine, no cross product. The web reader found
# nothing here, and the flat fallback listed a single item.
def _chip(label):
    return {"unpluggedChipRenderer": {"chipId": "1", "title": _runs(label)}}


TV = {"continuationContents": {"unpluggedLibraryContinuation": {"content": {
    "unpluggedSelectableSectionRenderer": {
        "selectors": [{"unpluggedHorizontalChipListRenderer": {
            "items": [_chip("All"), _chip("Scheduled"), _chip("Purchased")]}}],
        "contents": [
            _cell(items=[_tile("A", browse_id="B1"), _tile("B", browse_id="B2")]),
            _cell(items=[_tile("C", browse_id="B3")]),
            _cell(token="PURCHASED"),
        ]}}}}}

rows, tabs = epg.parse_library(TV)
check("the TV container yields no page-level rows", rows, [])
check("chips name the tabs", [t.title for t in tabs],
      ["All", "Scheduled", "Purchased"])
check("each tab took its own cell",
      [len(t.items) for t in tabs], [2, 1, 0])
check("a tab with only a token keeps it", tabs[2].token, "PURCHASED")

# Nine chips against eight cells is a shape change, not something to guess at.
SHORT = copy.deepcopy(TV)
sec = SHORT["continuationContents"]["unpluggedLibraryContinuation"]["content"] \
    ["unpluggedSelectableSectionRenderer"]
sec["contents"] = sec["contents"][:2]
check("a chip row that does not match the cells is declined",
      epg.library_filters(SHORT), [])

# -- an endpoint by a name this addon has never seen -----------------------
# The TV client's tiles carry primaryText, thumbnail and navigationEndpoint
# -- everything a tile needs -- and were dropped nineteen times because what
# sits inside that endpoint is called neither watchEndpoint nor
# browseEndpoint. A navigationEndpoint has one destination, so an endpoint
# under it naming an id is that destination whatever it is called. An
# endpoint naming no id at all still means "nowhere to go": those ten Rick
# and Morty episodes are buy prompts, whose
# unpluggedInitiateInlinePurchaseCommand carries only params.
STRANGE = {"contents": [
    {"unpluggedBrowseItemRenderer": {
        "primaryText": _runs("A show"),
        "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
        "navigationEndpoint": {"someFutureBrowseEndpoint": {"browseId": "UCX"}}}},
    {"unpluggedBrowseItemRenderer": {
        "primaryText": _runs("A video"),
        "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
        "navigationEndpoint": {"someFutureWatchEndpoint": {"videoId": "VX"}}}},
    {"unpluggedCompactVideoRenderer": {
        "primaryText": _runs("Buy me"),
        "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
        "navigationEndpoint": {"unpluggedInitiateInlinePurchaseCommand": {
            "params": "ChAKAzUwOBIF"}}}},
]}

found = epg.parse_items(STRANGE)
check("an unknown endpoint naming a browseId is followed",
      [i.browse_id for i in found if i.browse_id], ["UCX"])
check("an unknown endpoint naming a videoId is played",
      [i.video_id for i in found if i.video_id], ["VX"])
check("an endpoint naming no id is still nowhere to go", len(found), 2)
check("and is still counted unplayable", epg.unplayable_count(STRANGE), 1)

# A menu item's watchEndpoint must not become a row of its own.
MENU = {"contents": [
    {"unpluggedGridVideoRenderer": {
        "primaryText": _runs("On now"),
        "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
        "navigationEndpoint": {"unpluggedPopupEndpoint": {"popupRenderer": {
            "unpluggedSelectionMenuDialogRenderer": {"items": [
                {"unpluggedMenuItemRenderer": {
                    "primaryText": _runs("Join live"),
                    "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 9}]},
                    "command": {"watchEndpoint": {"videoId": "LIVE"}}}}]}}}}}},
]}
check("a dialog button is not a row",
      [i.title for i in epg.parse_items(MENU)], ["On now"])

# -- the guide's schedule --------------------------------------------------
# Of the 953 airings in the 2026-08-29 guide, 144 carry a watchEndpoint --
# one per channel, the one on the air -- and the other 809 carry a
# browseEndpoint to the show page with their id in a plain videoId field
# beside it. Reading only the endpoint is why the guide showed what was on
# and nothing after it, on every channel.
def _airing(title, begin, end, video_id=None, endpoint_id=None):
    node = {"title": _runs(title), "beginTimeMs": str(begin),
            "endTimeMs": str(end)}
    if endpoint_id:
        node["navigationEndpoint"] = {"watchEndpoint": {"videoId": endpoint_id}}
        node["videoId"] = endpoint_id
    else:
        node["videoId"] = video_id
        node["navigationEndpoint"] = {"browseEndpoint": {"browseId": "UCSHOW"}}
    return {"epgAiringRenderer": node}


# Timed relative to the moment the check runs: next_up asks the clock, so a
# fixture with fixed timestamps would pass or fail by the calendar.
HOUR = 3600000
NOW_MS = int(time.time() * 1000)
GUIDE = {"contents": [{"epgRowRenderer": {"contents": [
    {"epgStationRenderer": {
        "stationId": "UCCHAN",
        "icon": {"thumbnails": [{"url": "//x/y", "width": 400}],
                 "accessibility": {"accessibilityData": {"label": "A Channel"}}}}},
    _airing("On now", NOW_MS - HOUR // 2, NOW_MS + HOUR // 2, endpoint_id="NOW"),
    _airing("Later", NOW_MS + HOUR // 2, NOW_MS + HOUR, video_id="LATER"),
    _airing("Later still", NOW_MS + HOUR, NOW_MS + 2 * HOUR,
            video_id="LATERSTILL"),
]}}]}

stations = epg.parse_epg(GUIDE)
check("the station parsed", [s.name for s in stations], ["A Channel"])
check("named by its logo's accessibility label, with no name field",
      stations[0].name, "A Channel")
check("every airing kept, not just the one on the air",
      [a.video_id for a in stations[0].airings], ["NOW", "LATER", "LATERSTILL"])
check("only the endpoint-carrying airing is marked on air",
      [a.on_air for a in stations[0].airings], [True, False, False])
check("'now' is the marked airing, whatever the clock says",
      stations[0].now.video_id, "NOW")
check("next up is the one after it", stations[0].next_up.video_id
      if stations[0].next_up else "", "LATER")

# -- the id inside a side-sheet command ------------------------------------
# The TV client's Library tiles carry no browseId anywhere. Each has a
# navigationEndpoint holding unpluggedGetSidesheetCommand, whose base64
# params are a small protobuf with the id nested inside. The outer field
# number varies by what the tile is -- 3 for a movie, 4 for a show, 7 for a
# sports team -- so the reader walks by shape, not by field number.
#
# Tokens here are built rather than copied, so the check proves the reader
# and not one account's library.
def _proto(outer_field, browse_id, extra=b""):
    inner = b"\x0a" + bytes([len(browse_id)]) + browse_id.encode()
    inner += extra
    return bytes([outer_field << 3 | 2, len(inner)]) + inner


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


for field, name in ((3, "a movie"), (4, "a show"), (7, "a sports team")):
    check("the id is read out of %s's params" % name,
          epg.sidesheet_id(_b64(_proto(field, "UCmXMw6OyWJH1O6cA7JZS9Fg"))),
          "UCmXMw6OyWJH1O6cA7JZS9Fg")

# An event's params carry the entity id first and a second, shorter string
# after it; the first is the one that matches what the web client navigates
# to on the tiles that appear in both captures.
check("an event's params give the entity, not the trailing string",
      epg.sidesheet_id(_b64(_proto(5, "UCzqrIe11dHk2TcbCDfLg7mg",
                                   extra=b"\x10\x00\x18\x01 \x01\x2a\x0bAAAAAAAAAAA"))),
      "UCzqrIe11dHk2TcbCDfLg7mg")

check("percent-encoded padding is handled",
      epg.sidesheet_id(_b64(_proto(4, "UCSHOWSHOWSHOWSHOWSHOWx")) + "%3D"),
      "UCSHOWSHOWSHOWSHOWSHOWx")
check("junk is declined, not guessed at", epg.sidesheet_id("not base64 !!"), "")
check("an empty token is declined", epg.sidesheet_id(""), "")
check("a token holding no id is declined",
      epg.sidesheet_id(_b64(b"\x10\x01\x18\x02")), "")

SIDESHEET = {"contents": [{"unpluggedBrowseItemRenderer": {
    "primaryText": _runs("A show"),
    "contentType": "SHOW",
    "thumbnail": {"thumbnails": [{"url": "//x/y", "width": 100}]},
    "navigationEndpoint": {"unpluggedGetSidesheetCommand": {
        "requestType": "UNPLUGGED_SIDESHEET_REQUEST_TYPE_SHOW",
        "params": _b64(_proto(4, "UCmXMw6OyWJH1O6cA7JZS9Fg"))}}}}]}
found = epg.parse_items(SIDESHEET)
check("a side-sheet tile becomes a folder",
      [(i.title, i.browse_id) for i in found],
      [("A show", "UCmXMw6OyWJH1O6cA7JZS9Fg")])
check("and is not counted unplayable", epg.unplayable_count(SIDESHEET), 0)

# -- a later page of the guide ---------------------------------------------
# The second page of the 2026-08-29 guide carried 748 more airings across
# the same 148 channels and named none of them: each row holds a stationId
# and airings, with no epgStationRenderer. parse_epg required that renderer,
# so every airing past the first page was dropped.
PAGE_TWO = {"continuationContents": {"epgPaginationRenderer": {"contents": [
    {"epgRowRenderer": {
        "stationId": "UCCHAN",
        "airings": [
            _airing("Tomorrow", NOW_MS + 3 * HOUR, NOW_MS + 4 * HOUR,
                    video_id="TOMORROW"),
            # Repeated from page one: a guide that lists a programme twice
            # is worse than one that fetches a page for nothing.
            _airing("Later", NOW_MS + HOUR // 2, NOW_MS + HOUR,
                    video_id="LATER"),
        ]}},
    # A channel page one never described: no name and no logo, so listing it
    # would mean a channel called by its own id.
    {"epgRowRenderer": {
        "stationId": "UCUNKNOWN",
        "airings": [_airing("Orphan", NOW_MS, NOW_MS + HOUR,
                            video_id="ORPHAN")]}},
]}}}

later = epg.parse_epg(PAGE_TWO)
check("a later page's rows parse without a station renderer",
      [s.station_id for s in later], ["UCCHAN", "UCUNKNOWN"])

fresh = epg.parse_epg(GUIDE)
added = epg.merge_airings(fresh, epg.parse_epg(PAGE_TWO))
check("only the new airing was folded in", added, 1)
check("and it landed in time order",
      [a.video_id for a in fresh[0].airings],
      ["NOW", "LATER", "LATERSTILL", "TOMORROW"])
check("a channel page one never named is dropped",
      [s.station_id for s in fresh], ["UCCHAN"])
check("the name and logo survive the merge",
      (fresh[0].name, bool(fresh[0].logo)), ("A Channel", True))
check("'now' is still the marked airing after merging",
      fresh[0].now.video_id, "NOW")

print("failures:", len(failures))
sys.exit(1 if failures else 0)
