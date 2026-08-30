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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/stubs")
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE))
                + "/plugin.video.youtubetv")

from lib import api, epg  # noqa: E402

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

print("failures:", len(failures))
sys.exit(1 if failures else 0)
