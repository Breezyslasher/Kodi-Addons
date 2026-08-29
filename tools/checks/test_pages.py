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

import copy
import os
import sys

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

print("failures:", len(failures))
sys.exit(1 if failures else 0)
