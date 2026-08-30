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
    list's own continuations or it fetches more of "Top picks for you";
  * the Browse tab's five category chips are one browseId, FEunplugged_chips,
    and differ only in params, so a dedupe keyed on the destination alone
    reports one category where there are five;
  * a DVR call reports what it did in an actions block and nowhere else, and
    the button beside the message is reached before the message is.

Account data is deliberately not committed: these are the structures, with
the titles and ids replaced.
"""

import base64
import copy
import os
import sys
import time

from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/stubs")
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE))
                + "/plugin.video.youtubetv")

from lib import api, epg, kodiutils  # noqa: E402
import default  # noqa: E402

# Kept before anything stubs them, so the checks that want the real ones
# can put them back. Two of these checks replace them wholesale.
REAL_REMEMBERED_META = api.remembered_meta
REAL_REMEMBER_META = api.remember_meta

failures = []


def check(what, got, want):
    if got == want:
        print("  ok   %s == %r" % (what, want))
    else:
        failures.append(what)
        print("  FAIL %s: got %r, wanted %r" % (what, got, want))


def _runs(value):
    return {"runs": [{"text": value}]}


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


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

# -- a guide airing that is not on the air ---------------------------------
# The TV client gives the airing currently on the air a watchEndpoint and
# every other airing an unpluggedGetSidesheetCommand, with no videoId field
# anywhere. Of 989 airings in the 2026-08-29 20:48 guide, 143 had the
# endpoint -- one per station -- and all 846 others had a side sheet
# carrying exactly two ids: the show's 24-character one and the programme's
# 11-character one. Taking the wrong one lists the show, not the programme.
def _sheet(show_id, video_id):
    inner = (b"\x0a" + bytes([len(show_id)]) + show_id.encode()
             + b"\x10\x00\x18\x01\x20\x01"
             + b"\x32" + bytes([len(video_id)]) + video_id.encode())
    return _b64(bytes([4 << 3 | 2, len(inner)]) + inner)


SHEET_PARAMS = _sheet("UCTy7yMhdCqhduRTvA_Bx9TQ", "_u_J5hBoorE")
check("the programme's id is the 11-character one",
      epg.sidesheet_video_id(SHEET_PARAMS), "_u_J5hBoorE")
check("the show's id is still what sidesheet_id gives",
      epg.sidesheet_id(SHEET_PARAMS), "UCTy7yMhdCqhduRTvA_Bx9TQ")
check("junk gives neither",
      (epg.sidesheet_video_id("!!"), epg.sidesheet_id("!!")), ("", ""))

TV_GUIDE = {"contents": [{"epgRowRenderer": {
    "stationId": "UCCHAN",
    "station": {"epgStationRenderer": {
        "stationId": "UCCHAN",
        "icon": {"thumbnails": [{"url": "//x/y", "width": 400}],
                 "accessibility": {"accessibilityData": {"label": "A Channel"}}}}},
    "airings": [
        {"epgAiringRenderer": {
            "primaryText": _runs("On now"),
            "beginTimeMs": str(NOW_MS - HOUR // 2),
            "endTimeMs": str(NOW_MS + HOUR // 2),
            "navigationEndpoint": {"watchEndpoint": {"videoId": "ONAIRVIDEO"}}}},
        {"epgAiringRenderer": {
            "primaryText": _runs("Later"),
            "beginTimeMs": str(NOW_MS + HOUR // 2),
            "endTimeMs": str(NOW_MS + HOUR),
            "navigationEndpoint": {"unpluggedGetSidesheetCommand": {
                "requestType": "UNPLUGGED_SIDESHEET_REQUEST_TYPE_SHOW",
                "params": SHEET_PARAMS}}}},
    ]}}]}

tv = epg.parse_epg(TV_GUIDE)
check("both airings are kept, not just the one on the air",
      [a.video_id for a in tv[0].airings], ["ONAIRVIDEO", "_u_J5hBoorE"])
check("the later one takes the programme's id, not the show's",
      tv[0].airings[1].video_id, "_u_J5hBoorE")
check("only the endpoint-carrying one is marked on air",
      [a.on_air for a in tv[0].airings], [True, False])
check("'now' is still the one on the air", tv[0].now.video_id, "ONAIRVIDEO")

# -- recording a show ------------------------------------------------------
# start_dvr and stop_dvr take the show's id twice: plainly as "id", and
# inside a small protobuf as 1 { 1: 1, 2: <id> }. This is the exact params
# string from the 2026-08-27 capture.
check("the DVR params are rebuilt exactly",
      api.dvr_params("UCUX5tSsqvZbXbsguBrS5UWw"),
      "ChwIARIYVUNVWDV0U3NxdlpiWGJzZ3VCclM1VVd3")
check("a different show gives different params",
      api.dvr_params("UCUX5tSsqvZbXbsguBrS5UWw")
      == api.dvr_params("UCTy7yMhdCqhduRTvA_Bx9TQ"), False)

# YouTube TV records a series, not an airing, so every airing must name its
# show. entitiesDvrStatus does, on all 989 in the capture -- including the
# one on the air, whose watchEndpoint carries no show id anywhere. It agreed
# with the side-sheet command on all 843 airings that have both.
DVR_GUIDE = {"contents": [{"epgRowRenderer": {
    "stationId": "UCCHAN",
    "station": {"epgStationRenderer": {
        "stationId": "UCCHAN",
        "icon": {"thumbnails": [{"url": "//x/y", "width": 400}],
                 "accessibility": {"accessibilityData": {"label": "A Channel"}}}}},
    "airings": [
        {"epgAiringRenderer": {          # on the air: no id but this one
            "primaryText": _runs("On now"),
            "beginTimeMs": str(NOW_MS - HOUR // 2),
            "endTimeMs": str(NOW_MS + HOUR // 2),
            "entitiesDvrStatus": [{"entityId": "UCSHOWAAAAAAAAAAAAAAAAA1"}],
            "navigationEndpoint": {"watchEndpoint": {"videoId": "ONAIRVIDEO"}}}},
        {"epgAiringRenderer": {          # later: both, and they agree
            "primaryText": _runs("Later"),
            "beginTimeMs": str(NOW_MS + HOUR),
            "endTimeMs": str(NOW_MS + 2 * HOUR),
            "entitiesDvrStatus": [{"entityId": "UCTy7yMhdCqhduRTvA_Bx9TQ"}],
            "navigationEndpoint": {"unpluggedGetSidesheetCommand": {
                "params": SHEET_PARAMS}}}},
    ]}}]}

dvr = epg.parse_epg(DVR_GUIDE)[0].airings
check("every airing names its show, the one on the air included",
      [a.show_id for a in dvr],
      ["UCSHOWAAAAAAAAAAAAAAAAA1", "UCTy7yMhdCqhduRTvA_Bx9TQ"])
check("and the later one still has its own video id",
      dvr[1].video_id, "_u_J5hBoorE")

# -- the channel-order token -----------------------------------------------
# The guide's order is chosen with a continuation sent *alongside* the
# browseId. The token repeats the same epgOptions the request body carries,
# so it is rebuilt from those values rather than copied -- a stale copy
# would ask for somebody else's window. These five are the exact tokens the
# 2026-08-29 live tab's own order dropdown offered; reproducing them byte
# for byte is what makes the builder a reconstruction and not a guess.
REAL_ORDER_TOKENS = {
    "default": "4qmFsgJBEg9GRXVucGx1Z2dlZF9lcGcaEDhnTUVJZ0l3QVElM0QlM0SyARsKGQgYGICIsqACIICrgf6ENCiQkaAKMLCMuwU%3D",
    "custom": "4qmFsgJBEg9GRXVucGx1Z2dlZF9lcGcaEDhnTUVJZ0l3QWclM0QlM0SyARsKGQgYGICIsqACIICrgf6ENCiQkaAKMLCMuwU%3D",
    "watched": "4qmFsgJBEg9GRXVucGx1Z2dlZF9lcGcaEDhnTUVJZ0l3QXclM0QlM0SyARsKGQgYGICIsqACIICrgf6ENCiQkaAKMLCMuwU%3D",
    "az": "4qmFsgJBEg9GRXVucGx1Z2dlZF9lcGcaEDhnTUVJZ0l3QkElM0QlM0SyARsKGQgYGICIsqACIICrgf6ENCiQkaAKMLCMuwU%3D",
    "za": "4qmFsgJBEg9GRXVucGx1Z2dlZF9lcGcaEDhnTUVJZ0l3QlElM0QlM0SyARsKGQgYGICIsqACIICrgf6ENCiQkaAKMLCMuwU%3D",
}

for name, want_token in REAL_ORDER_TOKENS.items():
    check("the %s order token is rebuilt exactly" % name,
          api.epg_order_token(api.EPG_ORDERS[name], 24, 604800000,
                              1788044400000, 21498000, 11454000),
          want_token)

check("the five orders are distinct",
      len(set(REAL_ORDER_TOKENS.values())), 5)
check("a window change changes the token",
      api.epg_order_token(1, 24, 604800000, 1788044400000, 21498000,
                          11454000)
      == api.epg_order_token(1, 40, 604800000, 1788044400000, 21498000,
                             11454000),
      False)

# -- the Browse tab --------------------------------------------------------
# Cut down from the 2026-08-29 23:06 capture of FEunplugged_browse, which
# answered with two shelves: five category chips over a grid of 256
# networks. The trap is the chips -- all five are browseId
# FEunplugged_chips and differ only in params, so a reader keyed on the
# destination alone reports one category where there are five.
BROWSE_TAB = {
    "contents": {"sectionListRenderer": {"contents": [
        {"shelfRenderer": {
            "title": {"runs": [{"text": "Browse"}]},
            "content": {"unpluggedHorizontalChipListRenderer": {"items": [
                {"unpluggedIntentChipRenderer": {
                    "title": {"runs": [{"text": "Sports"}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "FEunplugged_chips",
                        "params": "8gMGKgQI75wB"}}}},
                {"unpluggedIntentChipRenderer": {
                    "title": {"runs": [{"text": "Movies"}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "FEunplugged_chips",
                        "params": "8gMFKgMIoFQ%3D"}}}},
            ]}}}},
        {"shelfRenderer": {
            "title": {"runs": [{"text": "Networks"}]},
            "content": {"horizontalListRenderer": {"items": [
                {"unpluggedGridChannelRenderer": {
                    "title": {"runs": [{"text": "ABC"}]},
                    "primaryText": {"runs": [{"text": "ABC"}]},
                    "thumbnail": {"thumbnails": [
                        {"url": "//yt3.ggpht.com/abc=ns-nd",
                         "width": 400, "height": 400}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "UCNETWORKAAAAAAAAAAAAA1"}}}},
                {"unpluggedGridChannelRenderer": {
                    "title": {"runs": [{"text": "AMC"}]},
                    "primaryText": {"runs": [{"text": "AMC"}]},
                    "thumbnail": {"thumbnails": [
                        {"url": "//yt3.ggpht.com/amc=ns-nd",
                         "width": 400, "height": 400}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "UCNETWORKAAAAAAAAAAAAA2"}}}},
            ]}}}},
    ]}},
}

browse_rows = epg.page_shelves(BROWSE_TAB)
check("the Browse tab reads as its two rows",
      [(row.title, len(row.items)) for row in browse_rows],
      [("Browse", 2), ("Networks", 2)])
check("five categories behind one browseId stay five",
      [(i.title, i.browse_id, i.params) for i in browse_rows[0].items],
      [("Sports", "FEunplugged_chips", "8gMGKgQI75wB"),
       ("Movies", "FEunplugged_chips", "8gMFKgMIoFQ%3D")])
check("a network names a page and asks for nothing within it",
      [(i.browse_id, i.params, i.playable) for i in browse_rows[1].items],
      [("UCNETWORKAAAAAAAAAAAAA1", "", False),
       ("UCNETWORKAAAAAAAAAAAAA2", "", False)])
check("and its logo is made absolute",
      browse_rows[1].items[0].art, "https://yt3.ggpht.com/abc=ns-nd")

# -- a network page is tabs -----------------------------------------------
# Cut down from ABC, AMC and Adult Swim (2026-08-29 23:06). A network page
# is a singleColumnBrowseResultsRenderer of tabs and **only the selected one
# ships with anything in it** -- ABC came back as LIVE plus seven tabs whose
# entire content is one nextContinuationData. Listing the page flat showed
# what was on now and nothing else.
#
# Two traps here. A tab titles itself with a plain string where every shelf
# in this file uses runs; and the selected tab's own shelves carry
# continuations of their own, which fetch more of one shelf and are not the
# tab's token.
NETWORK_PAGE = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {
        "title": "LIVE",
        "selected": True,
        "content": {"sectionListRenderer": {"contents": [
            {"shelfRenderer": {
                "title": {"runs": [{"text": "WTAE 4"}]},
                "content": {"horizontalListRenderer": {"items": [
                    {"unpluggedVideoRenderer": {
                        "title": {"runs": [{"text": "Action News 4"}]},
                        "navigationEndpoint": {"watchEndpoint": {
                            "videoId": "LIVEVIDEOID1"}}}},
                ]}},
                "continuations": [{"nextContinuationData": {
                    "continuation": "MORE-OF-THIS-SHELF"}}],
            }},
        ]}},
    }},
    {"tabRenderer": {
        "title": "SERIES",
        "content": {"sectionListRenderer": {"continuations": [
            {"nextContinuationData": {"continuation": "SERIES-TOKEN"}}]}},
    }},
    {"tabRenderer": {
        "title": "LATE NIGHT",
        "content": {"sectionListRenderer": {"continuations": [
            {"nextContinuationData": {"continuation": "LATE-TOKEN"}}]}},
    }},
    {"tabRenderer": {"title": "EMPTY",
                     "content": {"sectionListRenderer": {}}}},
]}}}

tabs = epg.browse_tabs(NETWORK_PAGE)
check("a network page reads as its tabs, and drops the one holding nothing",
      [(t.title, len(t.items), t.token) for t in tabs],
      [("LIVE", 1, ""), ("SERIES", 0, "SERIES-TOKEN"),
       ("LATE NIGHT", 0, "LATE-TOKEN")])
check("the live tab's own shelf continuation is not the tab's token",
      tabs[0].token, "")
check("and that shelf token really is in there to be got wrong",
      epg.continuation_token(NETWORK_PAGE), "MORE-OF-THIS-SHELF")

# FEunplugged_overlays answers with one empty tab, and a page of one tab is
# not a page of tabs: the caller must go on listing it the way it always did.
check("one tab is not a page of tabs",
      epg.browse_tabs({"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [
          {"tabRenderer": {"title": "", "tabIdentifier": "x"}}]}}}),
      [])
check("nor is a page with no tabs at all",
      epg.browse_tabs(BROWSE_TAB), [])

# The TV client answers a network with fewer tabs than the web one: Adult
# Swim came back with four to a browser and two to Kodi. A tab that arrives
# with no content at all is the shape that would explain the gap -- it can
# carry a token, or name a page of its own to go and fetch, and neither is
# reachable through a "content" key that is not there.
BARE_TABS = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"title": "Live", "selected": True,
                     "content": {"sectionListRenderer": {"contents": [
                         {"shelfRenderer": {
                             "title": {"runs": [{"text": "On now"}]},
                             "content": {"horizontalListRenderer": {"items": [
                                 {"unpluggedVideoRenderer": {
                                     "title": {"runs": [{"text": "Rick and Morty"}]},
                                     "navigationEndpoint": {"watchEndpoint": {
                                         "videoId": "NOWVIDEOID1"}}}}]}}}}]}}}},
    {"tabRenderer": {"title": "Series",
                     "endpoint": {"browseEndpoint": {
                         "browseId": "UCNETWORKAAAAAAAAAAAAA2",
                         "params": "8gMGKgQI75wB"}}}},
    {"tabRenderer": {"title": "Movies",
                     "continuation": "BARE-TOKEN"}},
    {"tabRenderer": {"title": "Nothing"}},
]}}}

bare = epg.browse_tabs(BARE_TABS)
check("a tab with no content is read by what it does carry",
      [(t.title, len(t.items), t.token, t.browse_id, t.params) for t in bare],
      [("Live", 1, "", "", ""),
       ("Series", 0, "", "UCNETWORKAAAAAAAAAAAAA2", "8gMGKgQI75wB"),
       ("Movies", 0, "BARE-TOKEN", "", ""),
      ])
check("tab_shapes names every tab, including the one dropped",
      epg.tab_shapes(BARE_TABS),
      ["Live carries [content, selected, title]",
       "Series carries [endpoint, title]",
       "Movies carries [continuation, title]",
       "Nothing carries [title]"])
# Every tab of a network page carries [content, title, trackingParams], the
# ones that read and the ones that do not alike, so the tab's own keys
# cannot say why one was dropped. What is inside the content can.
check("and says what an unread tab's content holds",
      epg.tab_shapes({"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
          {"tabRenderer": {"title": "Live", "content": {"sectionListRenderer": {
              "contents": [{"unpluggedVideoRenderer": {
                  "title": {"runs": [{"text": "On now"}]},
                  "navigationEndpoint": {"watchEndpoint": {
                      "videoId": "V"}}}}]}}}},
          {"tabRenderer": {"title": "Series", "content": {"sectionListRenderer": {
              "contents": [{"continuationItemRenderer": {
                  "continuationEndpoint": {"continuationCommand": {
                      "token": "T"}}}}]}}}},
      ]}}})[1],
      "Series carries [content, title]; content [sectionListRenderer] "
      "holding sectionListRenderer x1, continuationItemRenderer x1; "
      "strings under sectionListRenderer/contents/continuationItemRenderer"
      "/continuationEndpoint/continuationCommand/token")

# The path, not the last key. Two tabs -- one this reads, one it did not --
# both held exactly one string, both under a key called "continuation", both
# inside one sectionListRenderer, so the log line for each was identical
# character for character while one worked and the other did not. The
# wrapper between is the whole difference, and it is now printed.
def _one_tab(inner):
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"title": "A", "content": inner}},
        {"tabRenderer": {"title": "B", "content": inner}}]}}}

WRAPPED = _one_tab({"sectionListRenderer": {"continuations": [
    {"nextContinuationData": {"continuation": "T"}}]}})
BARE = _one_tab({"sectionListRenderer": {"continuations": [
    {"reloadContinuationData": {"continuation": "T"}}]}})
check("two tabs that differ only in the wrapper no longer log the same line",
      epg.tab_shapes(WRAPPED)[0] == epg.tab_shapes(BARE)[0], False)
check("and both are read, because a tab holding nothing else holds its own",
      [t.token for t in epg.browse_tabs(WRAPPED)]
      + [t.token for t in epg.browse_tabs(BARE)],
      ["T", "T", "T", "T"])
check("but a refresh timer is not a tab's content",
      epg.browse_tabs(_one_tab({"sectionListRenderer": {"continuations": [
          {"timedContinuationData": {"continuation": "T",
                                     "timeoutMs": 30000}}]}})),
      [])
# The whole-tab search is safe only where there is no content to confuse it.
check("a tab that HAS content still reads its token from its own list",
      [t.token for t in epg.browse_tabs(NETWORK_PAGE)],
      ["", "SERIES-TOKEN", "LATE-TOKEN"])

# The TV client defers a tab in the shape most of InnerTube has moved to.
# Adult Swim answered Kodi with four tabs, every one carrying a content, and
# the two not on screen held neither an item nor a nextContinuationData
# (2026-08-29 23:52) -- so they were dropped and the network opened on what
# was on now. A tab holding no items holds no shelves either, so any
# continuation in such a content is that tab's own.
MODERN_TABS = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"title": "Live", "selected": True,
                     "content": {"sectionListRenderer": {"contents": [
                         {"shelfRenderer": {
                             "title": {"runs": [{"text": "On now"}]},
                             "content": {"horizontalListRenderer": {"items": [
                                 {"unpluggedVideoRenderer": {
                                     "title": {"runs": [{"text": "Smiling Friends"}]},
                                     "navigationEndpoint": {"watchEndpoint": {
                                         "videoId": "NOWVIDEOID1"}}}}]}},
                             "continuations": [{"nextContinuationData": {
                                 "continuation": "MORE-OF-THIS-SHELF"}}]}}]}}}},
    {"tabRenderer": {"title": "Series", "content": {"sectionListRenderer": {
        "contents": [{"continuationItemRenderer": {"continuationEndpoint": {
            "continuationCommand": {"token": "SERIES-COMMAND-TOKEN"}}}}]}}}},
]}}}

modern = epg.browse_tabs(MODERN_TABS)
check("a tab deferred as a continuationCommand is still a tab",
      [(t.title, len(t.items), t.token) for t in modern],
      [("Live", 1, ""), ("Series", 0, "SERIES-COMMAND-TOKEN")])
check("and the tab that has items still ignores its shelf's token",
      modern[0].token, "")

# -- a category is rows, under chips that name no row ----------------------
# The Movies category (2026-08-29 23:26). Its genre chips sit in a shelf
# with no title, which page_shelves drops -- rightly, a folder called
# nothing is worse than no folder -- so they are read separately.
CATEGORY = {"contents": {"sectionListRenderer": {"contents": [
    {"shelfRenderer": {"content": {"unpluggedHorizontalChipListRenderer": {
        "items": [
            {"unpluggedChipRenderer": {
                "title": {"runs": [{"text": "Action"}]},
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "FEunplugged_chips",
                    "params": "8gMJKgcIoFQIn5YB"}}}},
            {"unpluggedChipRenderer": {
                "title": {"runs": [{"text": "Comedy"}]},
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "FEunplugged_chips",
                    "params": "8gMIKgYIoFQIkGg%3D"}}}},
        ]}}}},
    {"shelfRenderer": {
        "title": {"runs": [{"text": "Picked for you"}]},
        "content": {"horizontalListRenderer": {"items": [
            {"unpluggedGridVideoRenderer": {
                "title": {"runs": [{"text": "Jaws"}]},
                "navigationEndpoint": {"watchEndpoint": {
                    "videoId": "MOVIEVIDEOID1"}}}},
        ]}}}},
]}}}

check("a category's named row is a row",
      [(r.title, len(r.items)) for r in epg.page_shelves(CATEGORY)],
      [("Picked for you", 1)])
check("and its nameless chip shelf is read separately, all of it",
      [(c.title, c.browse_id, c.params) for c in epg.page_chips(CATEGORY)],
      [("Action", "FEunplugged_chips", "8gMJKgcIoFQIn5YB"),
       ("Comedy", "FEunplugged_chips", "8gMIKgYIoFQIkGg%3D")])
check("a page whose chip shelf is titled has no loose chips -- it has a row",
      epg.page_chips(BROWSE_TAB), [])

# Not everything with a browse id is a show, and the DVR takes a show's id.
check("an item remembers which renderer named it",
      sorted({i.source for row in epg.page_shelves(BROWSE_TAB)
              for i in row.items}),
      ["unpluggedGridChannelRenderer", "unpluggedIntentChipRenderer"])

# -- a tile says what it is ------------------------------------------------
# From the Movies category (2026-08-29 23:26). A film's tile names no video
# id at all -- its navigationEndpoint is a browseEndpoint and its menu
# offers "Go to <title>" -- so what separates a film, which has one thing to
# play, from a series, which is a folder of episodes, is contentType. It is
# on the tile plainly: 1048 MOVIE, 303 SHOW and 1 EVENT across the captures.
MOVIE_TILE = {"contents": {"sectionListRenderer": {"contents": [
    {"shelfRenderer": {
        "title": {"runs": [{"text": "Thriller movies"}]},
        "content": {"horizontalListRenderer": {"items": [
            {"unpluggedBrowseItemRenderer": {
                "contentType": "MOVIE",
                "primaryText": {"runs": [{"text": "The Accountant"}]},
                "secondaryText": {"runs": [{"text": "2016 \u2022 R"}]},
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "UCUlwmd1Fk7Tr2XLsLe3rYcQ"}},
                "menu": {"menuRenderer": {"items": [
                    {"toggleMenuServiceItemRenderer": {
                        "defaultText": {"runs": [{"text": "Add to library"}]},
                        "defaultServiceEndpoint": {"startDvrEndpoint": {
                            "startDvrParams": "ChwIARIYVUNVbHdtZDFGazdUcjJYTHNMZTNyWWNR",
                            "id": "UCUlwmd1Fk7Tr2XLsLe3rYcQ"}}}},
                ]}}}},
            {"unpluggedGridVideoRenderer": {
                "title": {"runs": [{"text": "The Two Towers"}]},
                "navigationEndpoint": {"watchEndpoint": {
                    "videoId": "ONAIRVIDEOID"}}}},
        ]}}}},
]}}}

tiles = epg.page_shelves(MOVIE_TILE)[0].items
check("a film's tile names its page, not a stream, and says it is a film",
      [(t.title, t.content_type, t.video_id, t.browse_id, t.playable)
       for t in tiles],
      [("The Accountant", "MOVIE", "", "UCUlwmd1Fk7Tr2XLsLe3rYcQ", False),
       ("The Two Towers", "", "ONAIRVIDEOID", "", True)])
check("and its year and rating become the plot",
      tiles[0].subtitle, "2016 \u2022 R")

# Not every listing carries contentType. The one search answered with did
# not, so a film reached from search was drawn as a folder and opened its
# page (2026-08-30 00:37). The tile's own DVR toast names the kind too, and
# it is written per kind: "Movie added to your library...", "Show added to
# your library...", "Event added to your library...".
check("a tile with no contentType is still a film if its toast says so",
      [i.content_type for i in epg.parse_items(
          {"unpluggedBrowseItemRenderer": {
              "primaryText": {"runs": [{"text": "The Blues Brothers"}]},
              "navigationEndpoint": {"browseEndpoint": {"browseId": "UC8"}},
              "menu": {"menuRenderer": {"items": [
                  {"toggleMenuServiceItemRenderer": {"defaultToastText": {
                      "runs": [{"text": "Movie added to your library. We'll "
                                        "record it as it becomes available."}]
                  }}}]}}}})],
      ["MOVIE"])
check("and a series is still a series",
      [i.content_type for i in epg.parse_items(
          {"unpluggedBrowseItemRenderer": {
              "primaryText": {"runs": [{"text": "Rick and Morty"}]},
              "navigationEndpoint": {"browseEndpoint": {"browseId": "UC9"}},
              "menu": {"menuRenderer": {"items": [
                  {"toggleMenuServiceItemRenderer": {"defaultToastText": {
                      "runs": [{"text": "Show added to your library. We'll "
                                        "record upcoming episodes."}]
                  }}}]}}}})],
      ["SHOW"])
check("contentType still wins where the tile has one",
      tiles[0].content_type, "MOVIE")

# Search answers with contentType on the same renderer -- the log named the
# field: unpluggedBrowseItemRenderer x14 carries [contentType, ...] -- and
# not one of the 14 read as a film. So the vocabulary is not identical
# everywhere, and the kind is matched on the word rather than on the whole
# string. A value with no word in it is handed back whole, so the log names
# it rather than swallowing it.
def _kind_of(value):
    return epg.parse_items({"unpluggedBrowseItemRenderer": {
        "contentType": value,
        "primaryText": {"runs": [{"text": "X"}]},
        "navigationEndpoint": {"browseEndpoint": {"browseId": "UC1"}}}})[0].content_type

check("a longer name for the same kind still reads as that kind",
      [_kind_of("MOVIE"), _kind_of("UNPLUGGED_CONTENT_TYPE_MOVIE"),
       _kind_of("CONTENT_TYPE_SHOW"), _kind_of("EVENT")],
      ["MOVIE", "MOVIE", "SHOW", "EVENT"])
check("and a kind with no word this knows is reported, not dropped",
      _kind_of("SPORTS_TEAM"), "SPORTS_TEAM")

# The tile's menu carries the DVR params outright. They are not read from
# there -- api.dvr_params rebuilds them -- but a capture taken two days
# after the one that builder was written from agreeing byte for byte is
# worth pinning: it is the only independent confirmation there is.
check("the rebuilt DVR params match a second capture's, byte for byte",
      unquote(api.dvr_params("UCUlwmd1Fk7Tr2XLsLe3rYcQ")),
      epg.first(MOVIE_TILE, "startDvrParams"))

# -- the Browse tab, split for the front page ------------------------------
# The five categories go on the addon's front page and the networks one
# folder from it, so the tab has to come apart into those two. Told apart by
# size, not by name: "Browse" and "Networks" are YouTube TV's words for them
# and a rename should not lose 256 channels.
BIG_TAB = copy.deepcopy(BROWSE_TAB)
_grid = BIG_TAB["contents"]["sectionListRenderer"]["contents"][1][
    "shelfRenderer"]["content"]["horizontalListRenderer"]["items"]
for _n in range(3, 9):                      # make the networks row the big one
    _more = copy.deepcopy(_grid[0])
    _more["unpluggedGridChannelRenderer"]["title"] = {
        "runs": [{"text": "Network %d" % _n}]}
    _more["unpluggedGridChannelRenderer"]["primaryText"] = {
        "runs": [{"text": "Network %d" % _n}]}
    _more["unpluggedGridChannelRenderer"]["navigationEndpoint"] = {
        "browseEndpoint": {"browseId": "UCNETWORK%d" % _n}}
    _grid.append(_more)

cats, nets = epg.browse_rows(BIG_TAB, least=4)
check("the tab comes apart into categories and networks",
      ([c.title for c in cats], nets.title, len(nets.items)),
      (["Sports", "Movies"], "Networks", 8))
check("the biggest row wins, not the first one over the line",
      epg.browse_rows(BIG_TAB, least=2)[1].title, "Networks")
check("and no row big enough means no networks row at all",
      epg.browse_rows(BIG_TAB, least=500)[1], None)

# -- search groups its results, and defers the films -----------------------
# The search of 2026-08-30 01:00. "blues" answers with Shows, Sports and
# "On now & upcoming" and hands back a continuation carrying "From your
# library", "On demand" and Movies -- where The Blues Brothers is, saying
# MOVIE plainly. Read one page deep, a search finds no film at all, which
# is what made it look like search could not tell a film from a show.
SEARCH_PAGE1 = {"contents": {"sectionListRenderer": {
    "contents": [
        {"shelfRenderer": {
            "title": {"runs": [{"text": "Shows"}]},
            "content": {"horizontalListRenderer": {"items": [
                {"unpluggedBrowseItemRenderer": {
                    "contentType": "SHOW",
                    "primaryText": {"runs": [{"text": "Blue Bloods"}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "UCSHOW1"}}}}]}}}},
        {"shelfRenderer": {
            "title": {"runs": [{"text": "Sports"}]},
            "content": {"horizontalListRenderer": {"items": [
                {"unpluggedBrowseItemRenderer": {
                    "contentType": "SPORTS_TEAM",
                    "primaryText": {"runs": [{"text": "St. Louis Blues"}]},
                    "navigationEndpoint": {"browseEndpoint": {
                        "browseId": "UCTEAM1"}}}}]}}}},
    ],
    "continuations": [{"nextContinuationData": {"continuation": "PAGE-2"}}]}}}

SEARCH_PAGE2 = {"contents": {"sectionListRenderer": {"contents": [
    {"shelfRenderer": {
        "title": {"runs": [{"text": "Movies"}]},
        "content": {"horizontalListRenderer": {"items": [
            {"unpluggedBrowseItemRenderer": {
                "contentType": "MOVIE",
                "primaryText": {"runs": [{"text": "The Blues Brothers"}]},
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "UC8todI5O2ZpZ5FhZ6aVMmKw"}}}}]}}}},
]}}}

check("a search is rows, grouped by what its results are",
      [(r.title, len(r.items)) for r in epg.page_shelves(SEARCH_PAGE1)],
      [("Shows", 1), ("Sports", 1)])
check("and its first page names no film at all",
      [i.content_type for r in epg.page_shelves(SEARCH_PAGE1) for i in r.items],
      ["SHOW", "SPORTS_TEAM"])
check("the films are behind the page's own continuation",
      epg.page_continuation(SEARCH_PAGE1), "PAGE-2")
check("and there The Blues Brothers says MOVIE plainly",
      [(r.title, [(i.title, i.content_type) for i in r.items])
       for r in epg.page_shelves(SEARCH_PAGE2)],
      [("Movies", [("The Blues Brothers", "MOVIE")])])

# -- a page about one title, against a page about a channel ----------------
# Rogue One's page, kept by the diagnostic on 2026-08-30 00:49. Two things
# a network page does not have: a header that names the title and says
# whether it is in the library, and tabs that are not a menu -- Watch now
# and Suggested are the film and then some notes about it, so opening it on
# two folders puts a page in front of the thing that was picked.
TITLE_PAGE = {
    "header": {"unpluggedContentDetailsHeaderRenderer": {
        "title": {"simpleText": "Rogue One: A Star Wars Story"},
        "contentType": "MOVIE",
        "secondaryText": {"simpleText": "PG-13 \u2022 2016"},
        "subscribeButton": {"dvrButtonRenderer": {
            "dvrOn": False,
            "dvrOnAndRecording": False,
            "serviceEndpoints": [
                {"startDvrEndpoint": {"id": "UCzr9LCG9h5w1Pj102XRVLGw"}},
                {"stopDvrEndpoint": {"id": "UCzr9LCG9h5w1Pj102XRVLGw"}}]}}}},
    "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"title": "Watch now", "selected": True,
                         "content": {"sectionListRenderer": {"contents": [
                             {"unpluggedGridVideoRenderer": {
                                 "primaryText": {"simpleText":
                                                 "Rogue One: A Star Wars Story"},
                                 "duration": {"simpleText": "2:13:57"},
                                 "navigationEndpoint": {"watchEndpoint": {
                                     "videoId": "joldJiP04hk"}}}}]}}}},
        {"tabRenderer": {"title": "About", "content": {"sectionListRenderer": {
            "contents": [{"unpluggedContentDetailsAboutFieldsRenderer": {
                "description": {"simpleText":
                                "Recruited by the Rebel Alliance, Jyn Erso "
                                "joins forces with a spy."}}}]}}}},
        {"tabRenderer": {"title": "Suggested", "content": {"sectionListRenderer": {
            "contents": [{"unpluggedBrowseItemRenderer": {
                "contentType": "MOVIE",
                "primaryText": {"simpleText": "Solo"},
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "UCSOLO"}}}}]}}}},
    ]}}}

check("a title page is told from a channel page by its header",
      [bool(epg.title_header(TITLE_PAGE)),
       bool(epg.title_header(NETWORK_PAGE))],
      [True, False])
check("its tabs are the title and then notes about it",
      [(t.title, len(t.items)) for t in epg.browse_tabs(TITLE_PAGE)],
      [("Watch now", 1), ("Suggested", 1)])
check("the synopsis is in About and nowhere else",
      epg.page_description(TITLE_PAGE),
      "Recruited by the Rebel Alliance, Jyn Erso joins forces with a spy.")
check("2:13:57 becomes a runtime in seconds",
      epg.browse_tabs(TITLE_PAGE)[0].items[0].duration, 8037)

# A tile cannot say whether a title is in the library -- isToggled is on
# none of the 2007 toggle renderers in any capture -- but the title's own
# page can, and offering both actions when the answer is known is how a
# recording gets cancelled by somebody aiming for the other one.
check("a title page says whether it is in the library",
      epg.dvr_state(TITLE_PAGE), False)
check("and a page with no such button says nothing rather than guessing",
      epg.dvr_state(NETWORK_PAGE), None)

# -- what a DVR call answers with ------------------------------------------
# Both DVR endpoints came back with actions + responseContext and nothing
# else (2026-08-29). The message inside actions is the only report of what
# happened -- a refusal is a 200 with a different sentence in it -- so it is
# read out and shown instead of a message this addon made up.
TOAST = {
    "responseContext": {"visitorData": "x"},
    "actions": [{"openPopupAction": {
        "popupType": "TOAST",
        "popup": {"notificationActionRenderer": {
            "responseText": {"runs": [{"text": "Added to your library"}]}}},
    }}],
}

check("a toast's words are read out of the actions block",
      epg.action_text(TOAST), "Added to your library")

# The toast carries a button whose own label is stored under "text" too,
# and it is reached first. Asking for the message key by key -- the toast's
# own field everywhere before "text" anywhere -- is what stops "Undo" being
# reported as the outcome; a plain search for "text" returns the label.
LABELLED = {"actions": [{"openPopupAction": {"popup": {
    "notificationActionRenderer": {
        "actionButton": {"buttonRenderer": {"text": {"simpleText": "Undo"}}},
        "responseText": {"runs": [{"text": "Added to your library"}]},
    }}}}]}

check("a button label reached first does not win",
      epg.action_text(LABELLED), "Added to your library")
check("and the plain search it beats really does find the label",
      epg.text(epg.first(LABELLED["actions"], "text")), "Undo")

check("simpleText reads the same as runs",
      epg.action_text({"actions": [{"a": {"responseText":
                                          {"simpleText": "Removed"}}}]}),
      "Removed")

check("a response naming no message says so rather than guessing",
      epg.action_text({"responseContext": {}, "actions": []}), "")
check("and so does one with no actions at all",
      epg.action_text({"responseContext": {}}), "")

# -- typing a search result by its own page --------------------------------
# Search types a film SHOW and carries no menu, so nothing on the tile
# contradicts it. The page one level down does, in the header this addon
# already reads. These pin who gets asked: not what is playable, not what
# already says MOVIE, not what has been asked before, and never more than
# the limit however many folders come back.
# The remembered kinds are stubbed out. Left alone, the first run would
# write UCFILM into the profile and every run after it would ask nothing and
# fail -- a check that passes once is worse than none.
api.remembered_kinds = lambda: {}
api.remember_kinds = lambda found: None


class _FakeClient(object):
    def __init__(self, kinds, suggests=None, defers_cast=False):
        self.kinds = kinds
        self.suggests = suggests or {}
        self.defers_cast = defers_cast
        self.asked = []
        self.followed = []
        self.suggested = 0

    def browse(self, browse_id, params=None):
        self.asked.append(browse_id)
        page = {"header": {"unpluggedContentDetailsHeaderRenderer": {
            "contentType": self.kinds.get(browse_id, "SHOW")}}}
        if self.defers_cast:
            page["contents"] = {"singleColumnBrowseResultsRenderer": {"tabs": [
                {"tabRenderer": {"title": "RECENT", "content": {
                    "sectionListRenderer": {"contents": [
                        {"unpluggedVideoRenderer": {
                            "title": {"simpleText": "an episode"},
                            "navigationEndpoint": {"watchEndpoint": {
                                "videoId": "V9"}}}}]}}}},
                {"tabRenderer": {"title": "LEAD CAST", "content": {
                    "sectionListRenderer": {"continuations": [
                        {"reloadContinuationData": {
                            "continuation": "CAST-%s" % browse_id}}]}}}},
            ]}}
        return page

    def continuation(self, token):
        self.followed.append(token)
        return {"unpluggedPersonRenderer": {
            "name": {"simpleText": "Sarah Chalke"},
            "role": {"simpleText": "Beth"}}}

    def suggest(self, query):
        self.suggested += 1
        return {"contents": [{"searchSuggestionsSectionRenderer": {"contents": [
            {"entitySuggestionRenderer": {
                "navigationEndpoint": {"browseEndpoint": {"browseId": bid}},
                "secondaryContainer": {"unpluggedBadgedTextRenderer": {"items": [
                    {"unpluggedTextBadgeRenderer": {"label": {"simpleText": "$"}}},
                    {"unpluggedTextRenderer": {"text": {"simpleText": word}}},
                ]}}}}
            for bid, word in self.suggests.items()]}}]}


def _row(*items):
    return [epg.Section("Top picks", list(items))]


# The remembered details are stubbed the way the remembered kinds are: left
# alone, a run would write its fixtures into the profile and the next run
# would ask for nothing and pass for the wrong reason.
remembered = {}
api.remembered_meta = lambda: dict(remembered)
api.remember_meta = lambda found: remembered.update(found)
default._REMEMBERED_META.clear()
default._LOADED_META[0] = False

# A page is owed for two reasons, and both are checked here: an id whose
# kind is unknown, and an id whose kind is known but whose details are not.
# Asking only the first is why a listing stayed bare -- once the kinds were
# remembered nothing was fetched again, so the genres and the cast had
# nowhere to come from.
remembered.clear()
remembered["UCDETAILED"] = {"genres": ["Comedy"]}
default._REMEMBERED_META.clear()
default._LOADED_META[0] = False

client = _FakeClient({"UCFILM": "MOVIE", "UCKNOWNFILM": "MOVIE",
                      "UCDETAILED": "MOVIE"})
row = _row(epg.Item(browse_id="UCFILM", title="The Blues Brothers",
                    content_type="SHOW"),
           epg.Item(video_id="V1", title="An airing"),
           epg.Item(browse_id="UCKNOWNFILM", title="Rogue One",
                    content_type="MOVIE"),
           epg.Item(browse_id="UCDETAILED", title="Airplane!",
                    content_type="MOVIE"))
asked, films = default._type_results(client, row)
check("a film the search called a show is put right by its own page",
      [(i.title, i.content_type) for i in row[0].items],
      [("The Blues Brothers", "MOVIE"), ("An airing", ""),
       ("Rogue One", "MOVIE"), ("Airplane!", "MOVIE")])
check("a page is asked for an unknown kind and for unknown details alike",
      (sorted(client.asked), asked), (["UCFILM", "UCKNOWNFILM"], 2))
check("but not for an airing, nor for one already known through and through",
      ("V1" in client.asked, "UCDETAILED" in client.asked), (False, False))
remembered.clear()
default._REMEMBERED_META.clear()

# A search whose results are all remembered should cost nothing.
api.remembered_kinds = lambda: {"UCFILM": "MOVIE", "UCTEAM": "SPORTS_TEAM"}
remembered["UCFILM"] = {"genres": ["Comedy"]}
default._REMEMBERED_META.clear()
default._LOADED_META[0] = False
quiet = _FakeClient({}, suggests={"UCFILM": "Movie"})
quiet_row = _row(epg.Item(browse_id="UCFILM", title="The Blues Brothers",
                          content_type="SHOW"),
                 epg.Item(browse_id="UCTEAM", title="St. Louis Blues",
                          content_type="SPORTS_TEAM"))
asked2, films2 = default._type_results(quiet, quiet_row)
check("a search that needs nothing asks for nothing",
      (asked2, quiet.suggested, quiet.asked), (0, 0, []))
check("and it is still typed, from memory",
      [i.content_type for i in quiet_row[0].items], ["MOVIE", "SPORTS_TEAM"])
api.remembered_kinds = lambda: {}
remembered.clear()
default._REMEMBERED_META.clear()

# suggest is not one of the steps, and this is why. In a browser it answers
# with the kind in words beside the browse id, which is what this reads;
# asked by *this* client it answers with ten searchSuggestionRenderers --
# plain query text -- and no entity suggestions at all. The reader is kept
# because the shape is real; the call is not, because it cost a request per
# search and returned nothing.
browser_suggest = _FakeClient(
    {}, suggests={"UCFILM": "Movie", "UCTEAM": "Team"}).suggest("blues")
check("a browser's suggestion names the kind beside the browse id",
      epg.suggestion_kinds(browser_suggest),
      {"UCFILM": "MOVIE", "UCTEAM": "SPORTS_TEAM"})
# The "$" badge sits in the same container and says a title must be bought.
check("and the price badge beside it is not mistaken for a kind",
      "MOVIE" in epg.suggestion_kinds(browser_suggest).values(), True)
# What this client actually sends: query text, no entities, nothing to read.
check("this client's suggestions carry no kind to read at all",
      epg.suggestion_kinds({"contents": [{"searchSuggestionsSectionRenderer": {
          "contents": [{"searchSuggestionRenderer": {
              "suggestion": {"runs": [{"text": "john wick"}]},
              "navigationEndpoint": {"searchEndpoint": {"query": "john wick"}}}}
          ]}}]}),
      {})

busy = _FakeClient({})
many = _row(*[epg.Item(browse_id="UC%d" % n, title="t%d" % n,
                       content_type="SHOW") for n in range(50)])
default._type_results(busy, many, limit=24)
check("a query answering with fifty folders does not make fifty requests",
      len(busy.asked), 24)

# -- what a typed result becomes on screen ---------------------------------
# The bug this exists for: search results were typed where the *rows* were
# listed -- folder names and a log line -- and not where the items are, so
# a film from search stayed a folder. Nothing caught it, because every
# reader was correct and the typing worked; it was landing on the wrong
# screen. So this checks the screen.
import xbmcplugin                                                # noqa: E402

xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(browse_id="UCFILM", title="The Blues Brothers",
                           content_type="MOVIE"))
default._add_item(epg.Item(browse_id="UCSHOW", title="Air Disasters",
                           content_type="SHOW"))
default._add_item(epg.Item(video_id="V1", title="An airing"))
film, show, airing = xbmcplugin.ITEMS

check("a film is listed playable, not as a folder",
      (film[2], film[3]._p.get("IsPlayable"), "action=play_movie" in film[0]),
      (False, "true", True))
check("and it offers what is left of its page beside it",
      [label for label, _command in film[3].menu][:1],
      ["Extras and suggested titles"])
check("a show is still a folder",
      (show[2], "action=browse" in show[0]), (True, True))
check("and an airing plays what it is",
      (airing[2], "action=play&" in airing[0]), (False, True))

# -- what a title's page says about it -------------------------------------
# A tile carries a year and a rating. Rogue One's page carries genres, a
# synopsis, the studio, who directed it and sixteen cast members with the
# parts they played -- and all of it is already fetched, to find out whether
# the thing was a film at all.
ABOUT = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"title": "About", "content": {"sectionListRenderer": {
        "contents": [{"unpluggedContentDetailsAboutFieldsRenderer": {
            "description": {"simpleText": "Jyn Erso joins forces with a spy."},
            "attributes": [
                {"simpleText": "Science fiction, Adventure, Action"},
                {"runs": [{"text": "Released "}, {"text": "2016"}]},
                {"runs": [{"text": "On "}, {"text": "FX"}]},
                {"runs": [{"text": "Provider: ", "bold": True},
                          {"text": "Disney"}]},
                {"runs": [{"text": "Directors", "bold": True},
                          {"text": ": ", "bold": True},
                          {"text": "Gareth Edwards"}]},
                {"runs": [{"text": "Production Companies", "bold": True},
                          {"text": ": ", "bold": True},
                          {"text": "Lucasfilm, Allison Shearmur Productions"}]},
                {"runs": [{"text": "Composers", "bold": True},
                          {"text": ": ", "bold": True},
                          {"text": "Michael Giacchino"}]},
            ]}}]}}}},
    {"tabRenderer": {"title": "Lead cast", "content": {"sectionListRenderer": {
        "contents": [
            {"unpluggedPersonRenderer": {
                "name": {"simpleText": "Felicity Jones"},
                "role": {"simpleText": "Jyn Erso"},
                "thumbnail": {"thumbnails": [
                    {"url": "//yt3.ggpht.com/jyn=p-ns-nd-df",
                     "width": 288, "height": 288}]}}},
            {"unpluggedPersonRenderer": {"name": {"simpleText": "Diego Luna"},
                                         "role": {"simpleText": "Cassian Andor"}}},
        ]}}}},
]}}}

fields = epg.about_fields(ABOUT)
check("the About tab is read field by field",
      (fields["genres"], fields["year"], fields["studio"],
       fields["network"], fields["directors"]),
      (["Science fiction", "Adventure", "Action"], 2016, ["Disney"], ["FX"],
       ["Gareth Edwards"]))
# Named by the diagnostic, not guessed at: "about says something new --
# Production Companies: Escape Artists, Zhiv, Mace Neufeld Productions".
check("production companies are several, and are read as several",
      fields["companies"], ["Lucasfilm", "Allison Shearmur Productions"])
# "Adult Swim, Cartoon Network, HBO Max" is three studios, not one long
# name, which is how it was reaching Kodi.
check("and so is a list of networks",
      epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
          "attributes": [{"runs": [{"text": "On "},
                                   {"text": "Adult Swim, Cartoon Network"}]}]
      }})["network"],
      ["Adult Swim", "Cartoon Network"])
# -- a message that fits the box it is shown in ----------------------------
# Kodi's ok dialog is a fixed box, about four lines of sixty characters.
# The sign-in instructions are four sentences and were arriving cut in
# half, so anything past that goes to the full-screen text viewer instead.
from lib.kodiutils import _needs_room                          # noqa: E402

check("a one-line error still uses the ok box",
      _needs_room("Could not load the guide: the request timed out"), False)
check("and four lines still fit it", _needs_room("a" * 58 * 4), False)
check("but five do not", _needs_room("a" * 58 * 5), True)
# Measured as wrapped lines, not raw length: a short message is still tall
# when it is written as paragraphs.
check("and paragraphs are counted as the lines they take",
      _needs_room("one\n\ntwo\n\nthree"), True)
check("an empty message asks for no room at all", _needs_room(""), False)

# -- a title's deferred shelves are folders, not one flat list -------------
# YouTube TV hangs a show's seasons off a selector: ten labels and ten
# content blocks, paired by position, each block holding only a
# continuation token. Family Feud answered with thirteen seasons and a
# shelf called "Extras", and fetching all fourteen and listing the result
# flat put 3,822 items in one folder (2026-08-30 03:50).
SEASONS = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"title": "Episodes", "content": {"sectionListRenderer": {
        "contents": [
            {"unpluggedGridVideoRenderer": {
                "primaryText": {"simpleText": "S26 E1 \u2022 Newest"},
                "navigationEndpoint": {"watchEndpoint": {"videoId": "new1"}}}},
            {"unpluggedSelectableSectionRenderer": {
                "selectors": [{"dropdownItemRenderer": {
                    "label": {"runs": [{"text": "Season 26"}]}}},
                    {"dropdownItemRenderer": {
                        "label": {"runs": [{"text": "Extras"}]}}}],
                "contents": [
                    {"sectionListRenderer": {"continuations": [
                        {"nextContinuationData": {"continuation": "tok26"}}]}},
                    {"sectionListRenderer": {"continuations": [
                        {"nextContinuationData": {"continuation": "tokX"}}]}},
                ]}},
        ]}}}},
]}}}

check("the selector's labels pair with its shelves by position",
      epg.section_continuations(SEASONS),
      [("Season 26", "tok26"), ("Extras", "tokX")])
# The label is YouTube TV's own and is used as given -- "Extras" is not
# renamed to anything this addon has decided it means. No capture has ever
# spent an Extras token, so what is behind it is not established.
check("and the labels are the page's own words",
      [label for label, _t in epg.section_continuations(SEASONS)],
      ["Season 26", "Extras"])

# A show with one shelf is not a menu: it lists its episodes as before.
# Several shelves and the folders are the listing, because the page's own
# episodes are the same episodes -- Family Feud carried 383 over thirteen
# seasons holding 4,192 between them.
ONE_SHELF = copy.deepcopy(SEASONS)
_sel = ONE_SHELF["contents"]["singleColumnBrowseResultsRenderer"]["tabs"][0][
    "tabRenderer"]["content"]["sectionListRenderer"]["contents"][1][
    "unpluggedSelectableSectionRenderer"]
_sel["selectors"] = _sel["selectors"][:1]
_sel["contents"] = _sel["contents"][:1]
check("one shelf is read as one, so the episodes still list",
      len(epg.section_continuations(ONE_SHELF)), 1)
check("and several are read as several, so the folders take over",
      len(epg.section_continuations(SEASONS)), 2)

# -- the order a listing is built in is the order it is shown in -----------
# Setting no sort method does not mean unsorted: Kodi applies whatever sort
# was last used for that content type. A show with forty seasons then reads
# "Season 1, Season 10, Season 11 ... Season 2", and a channel's schedule
# stops being chronological. The first method added is the default, so NONE
# has to be first.
xbmcplugin.SORTS[:] = []
default.finish("videos")
check("a listing asks for its own order first",
      xbmcplugin.SORTS[:1], [xbmcplugin.SORT_METHOD_NONE])
check("and offers alphabetical after it, not instead",
      xbmcplugin.SORT_METHOD_LABEL in xbmcplugin.SORTS, True)
xbmcplugin.SORTS[:] = []

# The folder keeps YouTube TV's own label and sorts on a padded copy, so a
# show with forty seasons is in order even if a view is switched to
# alphabetical. Renaming the folder "Season 01" to dodge that would be
# changing the service's words to work around Kodi's sort.
check("a season sorts on its number, not on its text",
      [default._sort_label(l) for l in ("Season 1", "Season 10", "Season 40")],
      ["Season 0001", "Season 0010", "Season 0040"])
check("a label with no number in it sorts as itself",
      default._sort_label("Extras"), "Extras")
check("so alphabetical puts them in season order",
      sorted(["Season 1", "Season 10", "Season 2", "Season 40"],
             key=default._sort_label),
      ["Season 1", "Season 2", "Season 10", "Season 40"])

# -- an episode's offers are not more episodes -----------------------------
# unpluggedCompactVideoVersionRenderer is where an episode says where it
# can be watched: one per service, each carrying the same videoId as the
# episode, and each with the service and the age in primaryText. Since
# primaryText is where a title comes from, every one listed itself as an
# episode called "Adult Swim * TV-14 * 13d ago", and Family Feud's Season
# 23 came back as 380 rows of episodes interleaved with their own offers.
EPISODE_WITH_OFFERS = {"unpluggedCompactVideoRenderer": {
    "primaryText": {"runs": [{"text": "S9 E10 \u2022 Field of Dreams"}]},
    "navigationEndpoint": {"watchEndpoint": {"videoId": "ep1"}},
    "videoVersionList": {"unpluggedVideoVersionListRenderer": {"contents": [
        {"unpluggedCompactVideoVersionRenderer": {
            "available": True,
            "primaryText": {"runs": [{"text": "Adult Swim \u2022 TV-14 \u2022 13d ago"}]},
            "secondaryText": {"runs": [{"text": "24 min \u2022 Expires Sep 3"}]},
            "navigationEndpoint": {"watchEndpoint": {"videoId": "ep1",
                                                     "params": "other"}}}},
        {"unpluggedCompactVideoVersionRenderer": {
            "available": False,
            "primaryText": {"runs": [{"text": "HBO Max \u2022 TV-14 \u2022 12d ago"}]},
            "navigationEndpoint": {"watchEndpoint": {"videoId": "ep1",
                                                     "params": "third"}}}},
    ]}}}}
check("an episode's offers do not list as episodes of their own",
      [i.title for i in epg.parse_items(EPISODE_WITH_OFFERS)],
      ["Field of Dreams"])

# -- rows whose titles do not tell them apart ------------------------------
# A syndicated show names every episode after itself: Family Feud's Season
# 24 came back as 186 rows reading "Family Feud", with no episode names and
# no numbers to draw one from. The same answer the guide already uses for
# two recordings of one show -- put the thing that differs in the label.
# Only the part that differs. Season 24 says "Game Show Network * TV-PG *
# Recorded 5 months ago" on every one of its 186 rows, and two thirds of
# that is the same on all of them, so appending the whole line would be 186
# rows of mostly identical text.
_same = [epg.Item(video_id="a", title="Family Feud",
                  subtitle="Game Show Network \u2022 TV-PG \u2022 Recorded 5 months ago"),
         epg.Item(video_id="b", title="Family Feud",
                  subtitle="Game Show Network \u2022 TV-PG \u2022 Recorded 3 months ago"),
         epg.Item(video_id="c", title="Celebrity Week", subtitle="1w ago")]
_apart = default._tell_apart(_same)
check("a colliding row says only what differs", _apart.get(0),
      "Family Feud  --  Recorded 5 months ago")
check("and so does the one it collides with", _apart.get(1),
      "Family Feud  --  Recorded 3 months ago")
# Only rows that actually collide are touched.
check("a row whose title is already its own is left alone",
      2 in _apart, False)
# And a collision with nothing to tell it apart by is left alone too,
# rather than gaining an empty suffix.
check("nor is anything added when there is nothing to add",
      default._tell_apart([epg.Item(video_id="a", title="X"),
                           epg.Item(video_id="b", title="X")]), {})
# Two rows saying exactly the same thing have nothing that differs, so
# they gain nothing rather than gaining the same suffix twice.
check("and identical subtitles add nothing either",
      default._tell_apart([epg.Item(video_id="a", title="X", subtitle="A \u2022 B"),
                           epg.Item(video_id="b", title="X", subtitle="A \u2022 B")]),
      {})

# -- a date parse that survives Kodi tearing its modules down --------------
# The player hides the n transform's array indices behind dates with
# fractional-hour offsets: new Date("1969-12-31T17:30:49.000-06:30")/1E3 is
# 49. Those parses went through unified_timestamp, which reaches for
# calendar and email.utils -- and Kodi blanks a plugin's module globals
# when the script ends, so on the next play every one of them came back
# "'NoneType' object is not callable" (Android, 2026-08-30). A wrong index
# reads a wrong slot, which throws, which the player answers with a
# constant, which googlevideo refuses with an empty-bodied 403.
from lib.jsinterp import _js_date                              # noqa: E402

check("the player's own date literals become the indices they encode",
      [_js_date(d) for d in ("1969-12-31T17:30:49.000-06:30",
                             "1970-01-01T09:31:06.000+09:30",
                             "1969-12-31T14:15:59.000-09:45",
                             "1970-01-01T06:30:24.000+06:30")],
      [49.0, 66.0, 59.0, 24.0])
check("a Z and a bare timestamp are read too",
      [_js_date("1970-01-01T00:00:05Z"), _js_date("1970-01-01T00:00:07")],
      [5.0, 7.0])
# The point of the whole thing: it calls nothing that a teardown can blank.
import calendar as _cal, email.utils as _eu                    # noqa: E402
_keep = (_cal.timegm, _eu.parsedate_tz)
_cal.timegm = None
_eu.parsedate_tz = None
try:
    check("and it still reads them once a teardown has blanked the helpers "
          "the general parser uses",
          _js_date("1969-12-31T17:30:49.000-06:30"), 49.0)
    check("which the probe reports by name rather than missing",
          bool(nsig_probe := __import__("lib.nsig", fromlist=["nsig"])
               ._emptied_modules()), True)
finally:
    _cal.timegm, _eu.parsedate_tz = _keep
check("and reports nothing once they are back",
      __import__("lib.nsig", fromlist=["nsig"])._emptied_modules(), [])

# -- an nsig answer that is not an answer ----------------------------------
# Player e937390a on a real Android box returned "BdHv3wGc_iIzKEBs97-_w8_"
# followed by the whole input on 4 of 6 solves, where every desktop log
# across the same player returned a proper 14-character value on all of
# them. googlevideo answers a url carrying that with an empty-bodied 403,
# and the value was being written to the on-disk cache, so one bad solve
# poisoned every play after it.
from lib import nsig                                          # noqa: E402

_N = "7tbMAbEhDYdV2D7bjM3"
check("a constant followed by the input is a bail, not a transform",
      bool(nsig.bailed("BdHv3wGc_iIzKEBs97-_w8_" + _N, _N)), True)
check("and the input handed back is the bail already known about",
      bool(nsig.bailed(_N, _N)), True)
# A real answer is shorter than its input and shares no tail with it.
check("a real answer is not called a bail",
      nsig.bailed("IO1grhszxl1e_Q", _N), "")
# Guard the test itself: something merely ending in the same letter is not
# a bail, or every answer would be one.
check("and neither is one that merely ends the same way",
      nsig.bailed("xmjn1z9smHwl93", _N), "")

# -- what a guide airing keeps in its info panel ---------------------------
# An epgAiringRenderer has eight keys and not one of them is a picture, a
# synopsis, a genre or a rating: 0 of the 989 airings in the 2026-08-30
# guide carry a "thumbnail" at all. Its epgInfoPanelRenderer carries all
# four, on 989 of the 989. Reading only the airing is why the guide was a
# column of channel logos with a title and nothing else.
def _panel(primary, secondary, tertiary):
    def badged(said):
        return {"unpluggedBadgedTextRenderer": {"items": [
            {"unpluggedTextRenderer": {"text": {"simpleText": said}}}]}}
    return {"epgAiringRenderer": {
        "beginTimeMs": "1000", "endTimeMs": "2000",
        "title": {"simpleText": "True Crime: 48 Hours"},
        "videoId": "vid1",
        "infoPanel": {"epgInfoPanelRenderer": {
            "thumbnail": {"thumbnails": [{"url": "//img/still",
                                          "width": 2560, "height": 1440}]},
            "primaryContainer": badged(primary),
            "secondaryContainer": badged(secondary),
            "tertiaryContainer": badged(tertiary)}}}}


aired = epg.parse_airing(_panel(
    "Sat, Aug 29, 11:00\u202fPM \u2022 KDKA+ \u2022 Aired Mar 1, 2025 "
    "\u2022 S38 E20 \u2022 The Hit-and-Run Homicide of Davis McClendon "
    "\u2022 TV-14",
    "A hit-and-run leaves one dead.",
    "Newsmagazine \u2022 Crime \u2022 Law")["epgAiringRenderer"])
check("a guide airing's picture is in its info panel, not on the airing",
      aired.art, "https://img/still")
check("and so is its synopsis", aired.description, "A hit-and-run leaves one dead.")
check("and its genres", aired.genres, ["Newsmagazine", "Crime", "Law"])
# Read part by part, not by position: the line comes in two to seven parts
# and which are present varies. A channel name must never be taken for a
# rating, nor a date for a runtime.
check("and its rating and episode numbers, whatever else is on the line",
      (aired.mpaa, aired.season, aired.episode, aired.episode_title),
      ("TV-14", 38, 20, "The Hit-and-Run Homicide of Davis McClendon"))
check("a date on that line is not read as a runtime", aired.duration, 0)

film = epg.parse_airing(_panel("KDKA+ \u2022 2 hr \u2022 R",
                               "Aimless friends drift.",
                               "Drama")["epgAiringRenderer"])
check("a written runtime is read as one", film.duration, 7200)
check("and the channel beside it is not mistaken for a rating",
      (film.mpaa, film.genres), ("R", ["Drama"]))

# An episode keeps its runtime in a COUNTER badge and its numbers in one
# line of text: Rick and Morty's S9 E10 is "24:03" and "S9 E10 * Field of
# Dreams". A show's header carries neither.
episodes = {"unpluggedCompactVideoRenderer": {
    "primaryText": {"runs": [{"text": "S9 E10 \u2022 Field of Dreams"}]},
    "secondaryText": {"runs": [{"text": "Adult Swim \u2022 TV-14 \u2022 13d ago"}]},
    "badge": {"unpluggedTextBadgeRenderer": {
        "label": {"runs": [{"text": "24:03"}]}, "type": "COUNTER"}},
    "navigationEndpoint": {"watchEndpoint": {"videoId": "ep1"}}}}
check("a show's rating and runtime come off its episodes, having no header",
      epg.episode_facts(episodes), ("TV-14", 1443))
# The VOD badge beside it is a version, not a runtime.
check("and a badge that is not a counter is not read as a runtime",
      epg._counter_badge({"badge": {"unpluggedTextBadgeRenderer": {
          "label": {"runs": [{"text": "VOD"}]}, "type": "VIDEO_VERSION"}}}), 0)
one = epg.parse_items(episodes)
check("an episode's numbers and name are three fields, not one string",
      [(i.season, i.episode, i.title, i.duration) for i in one],
      [(9, 10, "Field of Dreams", 1443)])

# "2013 - Present" and "1994 - 2004": a show gives years where a film gives
# a release date. 45 of them were reported unlabelled in one run.
ran = epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
    "attributes": [{"simpleText": "Animated, Comedy"},
                   {"simpleText": "2013 \u2013 Present"}]}})
ended = epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
    "attributes": [{"simpleText": "Comedy"},
                   {"simpleText": "1994 \u2013 2004"}]}})
check("a show's years say when it started and whether it is still running",
      ((ran["year"], ran["status"]), (ended["year"], ended["status"])),
      ((2013, "Continuing"), (1994, "Ended")))
check("and are not mistaken for the genres",
      (ran["genres"], ended["genres"]), (["Animated", "Comedy"], ["Comedy"]))

# A label this does not know is reported, not dropped: a title carrying one
# should name it in a log rather than go quietly missing.
check("and a label it does not know is reported rather than dropped",
      fields["unknown"], ["Composers: Michael Giacchino"])

# The shape this client actually sends, which the browser never does. John
# Wick: Chapter 4's About tab named it at 2026-08-30 02:04 --
# ".../attributes/simpleText" beside ".../attributes/runs/text" -- and only
# the runs were being looked at. A whole line in one string came back
# unlabelled, which is how the genres line is recognised, so each attribute
# in turn overwrote the genres with itself and a show reached Kodi with
# none: Rick and Morty's page says "Animated, Action, Adventure, Comedy".
flat = epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
    "attributes": [{"simpleText": "Animated, Action, Adventure, Comedy"},
                   {"simpleText": "On Adult Swim, Cartoon Network, HBO Max"},
                   {"simpleText": "Released 2013"},
                   {"simpleText": "Directors: Justin Roiland"}]}})
check("a whole About line in one string is read label and all",
      (flat["genres"], flat["network"], flat["year"], flat["directors"]),
      (["Animated", "Action", "Adventure", "Comedy"],
       ["Adult Swim", "Cartoon Network", "HBO Max"], 2013,
       ["Justin Roiland"]))
# A colon alone does not make a label. "Sports: the documentary" is a
# genres line, not a field called Sports.
check("and a colon this cannot name leaves the line unlabelled",
      epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
          "attributes": [{"simpleText": "Comedy: Stand-up, Variety"}]
      }})["genres"], ["Comedy: Stand-up", "Variety"])
# Only the first unlabelled line is the genres. A second one is reported
# rather than allowed to overwrite it.
check("a second unlabelled line does not overwrite the genres",
      epg.about_fields({"unpluggedContentDetailsAboutFieldsRenderer": {
          "attributes": [{"simpleText": "Animated, Comedy"},
                         {"simpleText": "Something else entirely"}]
      }})["genres"], ["Animated", "Comedy"])
# The photo is on the person renderer beside the name. Without it Kodi
# draws the silhouette it uses for an actor it has no picture of, which is
# what the cast row was: five names under five blank outlines.
check("the cast keeps the parts they played and their photographs",
      epg.cast_of(ABOUT),
      [("Felicity Jones", "Jyn Erso", "https://yt3.ggpht.com/jyn=p-ns-nd-df"),
       ("Diego Luna", "Cassian Andor", "")])
check("and a rating is told from a year by shape, not by position",
      [epg.rating_and_year({"simpleText": "PG-13 \u2022 2016"}),
       epg.rating_and_year({"simpleText": "2016 \u2022 TV-14"}),
       epg.rating_and_year({"simpleText": "TV-14"})],
      [("PG-13", 2016), ("TV-14", 2016), ("TV-14", 0)])
# And a rating has to look like one. Saturday Night Live's header reads
# "NBC", which was shown as "Rated: NBC" for want of asking what a rating
# actually is.
# A show gives a span where a film gives a year, and the year it started
# is still a year.
check("a span still yields the year it started",
      [epg.rating_and_year({"simpleText": "2013 \u2013 Present"}),
       epg.rating_and_year({"simpleText": "1975 \u2013 Present \u2022 NBC"})],
      [("", 2013), ("", 1975)])

# A show defers its cast where a film inlines it: Rick and Morty's LEAD
# CAST tab is empty on the page and holds seven people behind its token.
CAST_TABS = [epg.Section("RECENT", [epg.Item(video_id="V1", title="an episode")]),
             epg.Section("LEAD CAST", [], "CAST-TOKEN"),
             epg.Section("SUGGESTED", [], "SUGGESTED-TOKEN")]


class _CastClient(object):
    def __init__(self): self.asked = []

    def continuation(self, token):
        self.asked.append(token)
        return {"unpluggedPersonRenderer": {
            "name": {"simpleText": "Sarah Chalke"},
            "role": {"simpleText": "Beth Smith"}}}


cast_client = _CastClient()
check("a show's cast is fetched from the tab that defers it",
      (default._cast_behind(cast_client, CAST_TABS), cast_client.asked),
      ([("Sarah Chalke", "Beth Smith", "")], ["CAST-TOKEN"]))
check("and a page with no such tab asks for nothing",
      (default._cast_behind(_CastClient(), CAST_TABS[:1] + CAST_TABS[2:]), True),
      ([], True))

# A show leaves LEAD CAST empty and holds its cast behind a token, and so
# does a film sometimes -- The Bob's Burgers Movie has MOVIE and LEAD CAST
# where Rogue One inlined twenty-two people. So the tab is followed when a
# page carries no cast of its own, and rationed: it is a second request per
# title, and a row of shows would otherwise cost twice a row of films.
remembered.clear()
default._REMEMBERED_META.clear()
default._LOADED_META[0] = False
deferring = _FakeClient({}, defers_cast=True)
show_rows = _row(*[epg.Item(browse_id="UCS%d" % n, title="show %d" % n)
                   for n in range(5)])
default._type_results(deferring, show_rows, cast_limit=2)
check("a deferred cast is followed, and rationed",
      (len(deferring.asked), len(deferring.followed)), (5, 2))
check("and the two that were followed have their cast",
      sum(1 for i in show_rows[0].items
          if (default._meta_for(i.browse_id) or {}).get("cast")), 2)
remembered.clear()
default._REMEMBERED_META.clear()

check("a network is not a rating",
      [epg.rating_and_year({"simpleText": "NBC"})[0],
       epg.rating_and_year({"simpleText": "1975 \u2013 Present \u2022 NBC"})[0]],
      ["", ""])

# And that it reaches the item, which is the part that was silently missing
# the last time everything read correctly.
xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(video_id="V1", title="Rogue One", duration=8037),
                  plot=fields["description"],
                  meta=dict(fields, cast=epg.cast_of(ABOUT), mpaa="PG-13",
                            studios=fields["companies"]))
tag = xbmcplugin.ITEMS[0][3].info.set
check("the metadata reaches the list item",
      (tag.get("setGenres"), tag.get("setYear"), tag.get("setMpaa"),
       tag.get("setDirectors"), tag.get("setStudios"),
       tag.get("setDuration"), [(a.name, a.role) for a in tag["setCast"]]),
      (["Science fiction", "Adventure", "Action"], 2016, "PG-13",
       ["Gareth Edwards"], ["Lucasfilm", "Allison Shearmur Productions"], 8037,
       [("Felicity Jones", "Jyn Erso"), ("Diego Luna", "Cassian Andor")]))

# -- a film nobody has bought ----------------------------------------------
# It has no Watch now items at all, so that tab is dropped for holding
# nothing and Suggested takes its place. Reading the first tab as the one
# that plays then hid the only tab there was: The Blues Brothers came back
# "beside the one that plays -- nothing", with 26 suggestions in it.
BOUGHT = [epg.Section("Watch now", [epg.Item(video_id="V1", title="the film")]),
          epg.Section("Suggested", [epg.Item(browse_id="UC2", title="another")])]
UNBOUGHT = [epg.Section("Suggested",
                        [epg.Item(browse_id="UC2", title="another")])]

check("the tab that plays is found by what is in it, not by position",
      (default._plays(BOUGHT).title, default._plays(UNBOUGHT)),
      ("Watch now", None))
# And there is only one tab left to find. Watch now is dropped for holding
# nothing, About and Lead cast for naming nowhere to go, so asking for two
# tabs threw the survivor away as well.
UNBOUGHT_PAGE = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"title": "Watch now", "selected": True,
                     "content": {"sectionListRenderer": {"contents": []}}}},
    {"tabRenderer": {"title": "Suggested", "content": {"sectionListRenderer": {
        "contents": [{"unpluggedBrowseItemRenderer": {
            "primaryText": {"simpleText": "Another film"},
            "navigationEndpoint": {"browseEndpoint": {
                "browseId": "UC2"}}}}]}}}},
]}}}
check("one surviving tab is still a tab when a title is asked what else it has",
      ([t.title for t in epg.browse_tabs(UNBOUGHT_PAGE)],
       [t.title for t in epg.browse_tabs(UNBOUGHT_PAGE, least=1)]),
      ([], ["Suggested"]))

check("so a film nobody has bought still shows its suggestions",
      [t.title for t in UNBOUGHT if t is not default._plays(UNBOUGHT)],
      ["Suggested"])
check("and one that plays still keeps them apart",
      [t.title for t in BOUGHT if t is not default._plays(BOUGHT)],
      ["Suggested"])

# The details have to reach a *listing*, not only a title page. Films play
# on selection now, so that page is the one screen nobody opens -- which is
# why a build that read every field correctly still showed a year and a
# rating and nothing else.
default._REMEMBERED_META.clear()
default._LOADED_META[0] = False       # make it read the stub again
remembered["UCROGUE"] = {"genres": ["Science fiction"], "year": 2016,
                         "mpaa": "PG-13", "directors": ["Gareth Edwards"],
                         "studios": ["Disney"], "plot": "Jyn Erso.",
                         "cast": [["Felicity Jones", "Jyn Erso", "https://yt3.ggpht.com/jyn"]],
                         "art": "https://yt3.ggpht.com/banner"}
xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(browse_id="UCROGUE", title="Rogue One",
                           content_type="MOVIE"))
listed = xbmcplugin.ITEMS[0][3].info.set
check("a listed film carries what was learned when it was typed",
      (listed.get("setGenres"), listed.get("setYear"), listed.get("setMpaa"),
       listed.get("setStudios"), listed.get("setPlot"),
       [(a.name, a.role, a.thumbnail) for a in listed["setCast"]]),
      (["Science fiction"], 2016, "PG-13", ["Disney"], "Jyn Erso.",
       [("Felicity Jones", "Jyn Erso", "https://yt3.ggpht.com/jyn")]))
check("and the page's banner becomes its fanart",
      xbmcplugin.ITEMS[0][3].art.get("fanart"), "https://yt3.ggpht.com/banner")
# A cast remembered by a build that did not read photos is pairs, not
# triples, and must still list rather than raise.
xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(browse_id="UCOLD", title="Older",
                           content_type="MOVIE"),
                  meta={"cast": [["Keanu Reeves", "John Wick"]]})
check("a cast remembered before the photos were read still lists",
      [(a.name, a.role, a.thumbnail)
       for a in xbmcplugin.ITEMS[0][3].info.set["setCast"]],
      [("Keanu Reeves", "John Wick", "")])

xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(browse_id="UCUNKNOWN", title="Never opened",
                           content_type="MOVIE"))
# A show should say it is a show. The info dialog read "Type: video" on
# 60 Minutes, with no plot and no cast, because nothing had fetched its
# page and nothing had said what it was either.
xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(browse_id="UCSHOW", title="60 Minutes",
                           content_type="SHOW"))
check("a show is listed as a show, not as video",
      xbmcplugin.ITEMS[0][3].info.set.get("setMediaType"), "tvshow")

check("and one nothing is known about is listed all the same",
      "setGenres" in xbmcplugin.ITEMS[0][3].info.set, False)
remembered.clear()
default._REMEMBERED_META.clear()

# A film's tile is a poster -- 2560x3840 -- and belongs in the slot a skin
# keeps for one. A show's is a wide still, and putting that in the same slot
# is what made Marshals look stretched. Only the shape says which.
check("a tile knows whether it is a poster or a still",
      [epg.is_portrait({"thumbnails": [{"url": "//x", "width": 2560,
                                        "height": 3840}]}),
       epg.is_portrait({"thumbnails": [{"url": "//x", "width": 3840,
                                        "height": 2160}]}),
       epg.is_portrait({})],
      [True, False, False])

xbmcplugin.ITEMS[:] = []
default._add_item(epg.Item(video_id="V1", title="upright", art="poster.jpg",
                           upright=True))
default._add_item(epg.Item(video_id="V2", title="wide", art="still.jpg"))
check("only the upright one is offered as a poster",
      ("poster" in xbmcplugin.ITEMS[0][3].art,
       "poster" in xbmcplugin.ITEMS[1][3].art), (True, False))
check("and both are still a thumb",
      [i[3].art.get("thumb") for i in xbmcplugin.ITEMS],
      ["poster.jpg", "still.jpg"])
# A show's page carries no portrait image at all -- everything on it is
# 3840x2160 -- so a wide one is offered as what it is instead.
check("a wide image is offered as landscape",
      xbmcplugin.ITEMS[1][3].art.get("landscape"), "still.jpg")

# -- a cache that cannot say it is out of date -----------------------------
# Nothing refetches a title it already knows about, so a cache written
# before a field was read keeps that field missing for good. The cast
# photographs were read in .54 and stayed blank on a box that had been
# running .50 to .53: every title was already remembered, by builds that
# stored names alone.
_store = {}
api.remembered_meta, api.remember_meta = REAL_REMEMBERED_META, REAL_REMEMBER_META
_real_read, _real_write = kodiutils.read_json, kodiutils.write_json
kodiutils.read_json = lambda name, default=None: _store.get(name, default)
kodiutils.write_json = lambda name, data: (_store.__setitem__(name, data), True)[1]

api.remember_meta({"UC1": {"cast": [["Keanu Reeves", "John Wick", "photo"]]}})
check("what is remembered comes back",
      api.remembered_meta(),
      {"UC1": {"cast": [["Keanu Reeves", "John Wick", "photo"]]}})
_store["titles.json"] = {"UC1": {"cast": [["Keanu Reeves", "John Wick"]]}}
check("a cache from before the photographs is thrown away, not read",
      api.remembered_meta(), {})
_store["titles.json"] = {"version": 1, "titles": {"UC1": {"genres": ["x"]}}}
check("and so is one that names an older version",
      api.remembered_meta(), {})
kodiutils.read_json, kodiutils.write_json = _real_read, _real_write

# -- the search call itself ------------------------------------------------
# Scanning the request bodies of every capture, search was the one endpoint
# this addon called with a shape no real client uses: seven captured
# searches all send params beside the query and none sends the query alone.
# The value is copied from a plain search, not rebuilt, so it is pinned.
check("a search sends the params a real one does",
      api.SEARCH_PARAMS, "6gMOCgASABoAIgAqADIAQgA%3D")
check("and that is the query-independent form, not the one that tracks "
      "which suggestion was clicked",
      api.SEARCH_PARAMS.startswith("6gMO"), True)

print("failures:", len(failures))
sys.exit(1 if failures else 0)
