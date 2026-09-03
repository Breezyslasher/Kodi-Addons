"""Turning Friendly TV's cards into something Kodi can draw.

The service describes everything -- a film, an episode, a live channel, a
recording -- with the same card shape, and says what a card *is* only through
``target.pageType`` and a bag of string-valued ``pageAttributes``. This module
is the one place that reads that shape, so the routing code can deal in plain
dictionaries.
"""

import json
import re
import time

# "S3 E33 | 30m", "S7 - Ep8 | Mon, Aug 31 | ...", "S1 E1 - Fallen Timbers".
# The service writes the season and episode three ways and always at the front
# of subtitle1, which is the only place it puts them as numbers at all.
_SXEY = re.compile(r"^\s*S(\d+)\s*-?\s*E[p]?(\d+)", re.IGNORECASE)


def content_id(path):
    """The numeric id in a title's path, which is what "more like this" keys on.

    ``movies/1058109`` and ``series/shows/521500`` both ask for it as
    ``morelikethis/<id>``, so it is the last segment either way.
    """
    tail = str(path or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


def is_genre(item):
    """True for a "Browse by Genre" card, which is a search term, not a page.

    Home carries a ``browse_by_genre`` section of fifteen of these. They are
    marked ``isThirdPartySection`` and each card has an empty ``pageType``,
    empty ``pageAttributes`` and a bare word for a path -- "Westerns",
    "Crime", "nostalgia". There is no page at that path; the web player hands
    the word to its own genre screen.

    Handing it to search instead is not a substitute made up here: the search
    endpoint matches on genre, so "action" answers with Gunsmoke, NCIS and
    Bonanza rather than with titles containing the word.
    """
    path = item.get("path") or ""
    return bool(path) and "/" not in path and not item.get("page_type")


def media_of(path):
    """What a page path holds, for a caller that has only the path."""
    return _media_kind(path or "", 0, 0)


def _media_kind(path, season, episode):
    """What Kodi should treat a card as.

    Kodi renders a listing by its content type and each row by its mediatype,
    and without them a show is an anonymous folder: no poster shelf, no season
    grouping, no episode ordering. The path says which kind a card is, because
    the service routes each kind to its own page prefix.
    """
    if path.startswith("channel/live/"):
        # A live channel's card is titled with whatever is on it and carries
        # that programme's S/E numbers, but what is being chosen is the
        # channel. Calling it an episode would retitle the row to the
        # programme and file it under a show.
        return "video"
    if path.startswith("movies/"):
        return "movie"
    if path.startswith("series/shows/"):
        return "tvshow"
    if season or episode:
        return "episode"
    return "video"



def _meta(raw, name):
    """One of a card's ``metadata`` values.

    Each entry is itself an object -- ``{"key": "comingSoon", "value":
    "true"}`` -- and the values are strings, "true" and "false" included.
    """
    entry = (raw.get("metadata") or {}).get(name)
    if isinstance(entry, dict):
        return str(entry.get("value") or "")
    return str(entry or "")

def _markers(display):
    """A card's badges as {type: value}.

    They arrive as a list of marker objects on a page card and as a dict
    keyed by marker type in the guide, so both are flattened to the same
    thing rather than being read differently at each call site.
    """
    raw = display.get("markers")
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                out[key] = value.get("value", "")
            else:
                out[key] = value
    elif isinstance(raw, list):
        for marker in raw:
            if isinstance(marker, dict) and marker.get("markerType"):
                out[marker["markerType"]] = marker.get("value", "")
    return out



def _redirects_to_payment(display):
    """Whether any of a card's badges says the title has to be paid for.

    Only the list form of ``markers`` carries the flag; the guide's dict form
    has no badge of this kind at all.
    """
    for marker in display.get("markers") or []:
        if isinstance(marker, dict) and marker.get("isRedirectToPayment"):
            return True
    return False

def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fraction(value):
    """A 0..1 progress fraction, or 0.0 for anything else.

    Clamped rather than trusted: a value at or past the end would have Kodi
    resume at the credits, and one below zero is meaningless.
    """
    try:
        got = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not 0.0 < got < 1.0:
        return 0.0
    return got


def card(raw, api):
    """One card, normalised.

    ``api`` is only used to resolve image references against the CDN profile
    table, which lives with the config rather than here.
    """
    display = raw.get("display") or {}
    target = raw.get("target") or {}
    attrs = target.get("pageAttributes") or {}
    markers = _markers(display)

    start_ms = _int(attrs.get("startTime")) or _int(markers.get("startTime"))
    end_ms = _int(attrs.get("endTime")) or _int(markers.get("endTime"))

    season = episode = 0
    numbered = _SXEY.match(display.get("subtitle1") or "")
    if numbered:
        season, episode = int(numbered.group(1)), int(numbered.group(2))

    item = {
        "title": display.get("title") or "",
        "subtitle": display.get("subtitle1") or "",
        "description": display.get("subtitle2") or "",
        "path": target.get("path") or "",
        "page_type": target.get("pageType") or "",
        "playable": (target.get("pageType") == "player"),
        "is_live": str(attrs.get("isLive", "")).lower() == "true",
        "content_type": attrs.get("contentType") or "",
        "asset_type": attrs.get("assetType") or "",
        "channel_name": attrs.get("channelName") or display.get("parentName")
        or "",
        "episode_title": attrs.get("episodeTitle") or "",
        "genres": [g.strip() for g in
                   (attrs.get("RokuGenreCode") or "").split(",") if g.strip()],
        "duration_ms": _int(attrs.get("duration")),
        "network_id": attrs.get("networkid") or "",
        # The service's own id for the thing behind the card, which is what
        # the Continue Watching removal keys on. Not the same as raw["id"],
        # which a card usually does not have at all.
        "content_id": attrs.get("id") or "",
        "is_favourite": str(attrs.get("isFavourite", "")).lower() == "true",
        # Cards name their own recording form -- "player_recording_form",
        # where a guide airing's overlay names "recording_form". Carried
        # rather than assumed, so the right one is asked for.
        "recording_form": attrs.get("recordingForm") or "",
        "can_record": (str(attrs.get("isRecordingAllowed", "")).lower()
                       == "true"
                       and str(attrs.get("isRecordingDisabled", "")).lower()
                       != "true"),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "badge": markers.get("badgeV2") or markers.get("badge") or "",
        # An episode the service lists but will not serve yet. Two signals,
        # captured on 501 cards and in agreement on every one: the metadata
        # flag, and a red "Coming Soon" badge. Selecting one is refused with
        # the service's own words -- "The content provider has restricted this
        # program from being available On Demand." -- so saying so up front
        # saves opening several before finding one that plays.
        "coming_soon": (_meta(raw, "comingSoon") == "true"
                        or (markers.get("badgeV2") or "").strip().lower()
                        == "coming soon"),
        # A title on one of the add-on channels sold on top of the base plan.
        # The card *does* say so, in the badge it carries: across 2112
        # captured badges, "+ Add-On" is the only value ever accompanied by
        # isRedirectToPayment, and it carries it on all 22 of its cards. The
        # flag is read rather than the wording, so a renamed badge still works.
        "needs_addon": _redirects_to_payment(display),
        "poster": api.image(display.get("imageUrl")),
        "channel_logo": api.image(display.get("parentIcon")),
        "id": raw.get("id"),
        "season": season,
        "episode": episode,
        # Continue Watching carries how far in the viewer got, as a fraction
        # of the running time ("0.01795995379283789"). It is the only
        # progress the service sends, and without it that row restarts
        # everything from the beginning.
        "resume": _fraction(markers.get("seek")),
    }
    item["media"] = _media_kind(item["path"], season, episode)

    # A card whose only marker says "non_playable" is a heading in the
    # guide's channel strip, not something to open.
    if markers.get("special") == "non_playable" and not item["path"]:
        item["playable"] = False

    if not item["duration_ms"] and start_ms and end_ms > start_ms:
        item["duration_ms"] = end_ms - start_ms
    return item


def section_data(response, api):
    """The rows ``section/data`` answers with.

    A different shape from a page's, and not a variation on it. That endpoint
    takes a comma-separated list of codes and answers with a **bare list**,
    one element per code, in which ``section`` is the code as a *string*
    rather than the nested object a page pane carries:

        [{"section": "epg_series", "data": [ <cards> ], "hasMoreData": false,
          "lastIndex": 5, "dataRequestDelay": 0, "showViewAll": false}, ...]

    Reading it with the page reader raised "'list' object has no attribute
    'get'" and took the whole listing with it, which is what opening a
    deferred section did.
    """
    out = []
    for row in response if isinstance(response, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "code": row.get("section") if isinstance(row.get("section"), str)
                    else "",
            "cards": [card(c, api) for c in (row.get("data") or [])
                      if isinstance(c, dict)],
            "has_more": bool(row.get("hasMoreData")),
            "last_index": row.get("lastIndex") or 0,
        })
    return out


def sections(response, api):
    """Every section on a page, as {name, code, path, cards, deferred}.

    ``deferred`` is true where the service described the section but sent no
    cards with it; the caller fills those in with ``Api.section`` only if it
    actually needs them, because a page can defer a dozen at once.
    """
    out = []
    if not isinstance(response, dict):
        # section/data answers with a bare list; that is section_data's shape,
        # not this one.
        return out
    for pane in (response.get("data") or []):
        section = pane.get("section")
        if not isinstance(section, dict):
            continue
        info = section.get("sectionInfo") or {}
        data = section.get("sectionData") or {}
        cards = [card(c, api) for c in (data.get("data") or [])]
        controls = section.get("sectionControls") or {}
        out.append({
            "name": info.get("name") or "",
            "code": info.get("code") or "",
            "data_type": info.get("dataType") or "",
            "cards": cards,
            "deferred": not cards,
            "view_all": controls.get("viewAllTargetPath") or "",
        })
    return out


def on_now(cards):
    """The card that is on the air, out of a channel's schedule."""
    now = time.time() * 1000
    for item in cards:
        if item["start_ms"] <= now < item["end_ms"]:
            return item
    return None


def detail(response, api):
    """The ``content`` pane of a details page: its buttons and its blurb.

    A film or series page is not built from sections. It has one pane of
    ``paneType: "content"`` holding ``dataRows`` of elements -- an image, a
    title, a cast list, a description, and **buttons whose ``target`` is the
    playable path**. A series then has section panes after it, one per season;
    a film has none at all, which is why reading only sections left a film's
    page empty.

    Buttons that do something local rather than open a path ("Record",
    "Favorite") carry a blank or whitespace target, and the add-on info button
    targets "settings". A target is taken as a path only when it looks like
    one, which is the same test that keeps those three out.
    """
    found = {"title": "", "plot": "", "poster": "", "fanart": "", "actions": [],
             "cast": [], "directors": [], "year": 0, "rating": "", "now": "",
             "airing": "", "expires": "", "is_favourite": None,
             "subscribe": None}

    # A title the subscription does not include says so on its own page, in
    # the row where the play button would be: an "addonsubscribe" button whose
    # properties carry the service's own wording. This is why a film can have
    # a page and nothing to play on it.
    info = (response.get("info") or {}).get("attributes") or {}
    upgrade_form = info.get("upgradeForm") or ""

    # The page does say whether this is already a favourite, in a block of its
    # own rather than on the card: {"isFavourite": false,
    # "showFavouriteButton": false}. Only isFavourite is read -- the button
    # flag is false even on pages whose own content pane draws a Favorite
    # button, so it does not mean what its name suggests.
    buttons = response.get("pageButtons")
    if isinstance(buttons, dict) and "isFavourite" in buttons:
        found["is_favourite"] = bool(buttons["isFavourite"])

    for pane in (response.get("data") or []):
        content = pane.get("content")
        if not content:
            continue
        found["title"] = found["title"] or content.get("title") or ""
        found["poster"] = found["poster"] or api.image(content.get("posterImage"))
        found["fanart"] = (found["fanart"] or
                           api.image(content.get("backgroundImage")))

        # Films and series describe themselves differently, and it matters:
        #
        #   a series has a "description" element -- the show's own synopsis --
        #     and its subtitle1/subtitle2 are the episode on the air right now
        #   a film has no "description" element at all: its synopsis is in
        #     subtitle2, and "subtitle" is when it airs
        #
        # So reading only "description" leaves every film with a blank plot,
        # and folding subtitle2 in regardless gives a series the wrong one.
        # The fields are gathered first and sorted out after.
        got = {}
        for row in (content.get("dataRows") or []):
            for element in (row.get("elements") or []):
                kind = element.get("elementType") or ""
                sub = element.get("elementSubtype") or ""
                data = element.get("data")
                target = str(element.get("target") or "").strip()
                if kind == "button":
                    if sub == "addonsubscribe":
                        found["subscribe"] = _subscribe(element, upgrade_form)
                    elif "/" in target:
                        found["actions"].append(
                            {"label": str(data or sub or "Play"),
                             "path": target})
                    elif ("favourite" in sub or "favorite" in sub) \
                            and found["is_favourite"] is None:
                        # Which button the page drew -- "add_to_favourites"
                        # against a removal wording -- implies the state, but
                        # only the "add" half has ever been captured, so this
                        # is an inference and pageButtons.isFavourite is a
                        # statement. It fills the gap and never overrides.
                        found["is_favourite"] = sub.startswith("remove")
                elif kind == "marker" and sub == "tag":
                    found["year"], found["rating"] = _year_and_rating(data)
                elif data and kind in ("description", "text", "tag"):
                    got[sub or kind] = str(data)

        found["plot"] = got.get("description") or got.get("subtitle2") or ""
        found["cast"] = _names(got.get("cast"))
        found["directors"] = _names(got.get("Director"))
        found["title"] = found["title"] or got.get("title") or ""
        # What is on right now, which only a series has *beside* its own
        # synopsis; on a film these same fields are the synopsis.
        if got.get("description"):
            found["now"] = " - ".join(p for p in (got.get("subtitle1"),
                                                  got.get("subtitle2")) if p)
        # "Sat, Aug 29 | 10:00 AM - 12:00 PM | 2h" and "Expires in 23 hours".
        found["airing"] = got.get("subtitle") or ""
        found["expires"] = got.get("expires") or ""
    return found



def _subscribe(element, upgrade_form=""):
    """What an "addonsubscribe" button says, for a title outside the plan.

    The wording is the service's own and is used verbatim rather than
    invented, because it is the only thing that knows what the offer is:

        {"buttonText": "Start 7-day Free Trial", "buttonColor": "#d1a128",
         "masterPackageId": 27, "goToSettings": true,
         "descriptionAddonSubscribe": "", "messageAddonSubscribe": "",
         "showPopupAddonSubscribe": false,
         "imageUrl": "network,network/images/xdzdpu.png", ...}

    ``properties.addOnInfo`` is a JSON **string**, not an object.
    """
    raw = (element.get("properties") or {}).get("addOnInfo")
    info = {}
    if isinstance(raw, dict):
        info = raw
    elif isinstance(raw, str):
        try:
            info = json.loads(raw)
        except ValueError:
            info = {}
    return {
        "text": info.get("buttonText") or str(element.get("data") or ""),
        "message": info.get("messageAddonSubscribe") or "",
        "description": info.get("descriptionAddonSubscribe") or "",
        "package_id": info.get("masterPackageId") or 0,
        "upgrade_form": upgrade_form,
    }

def _names(value):
    """A credit list as names, in either form the service writes it.

    Most pages give a bare list -- "Glenn Ford, Addison Richards, Paul Birch".
    Some give prose instead -- "Starring Marlon Brand, Karl Malden and Katy
    Jurado" -- and splitting that on commas alone yields a cast whose first
    member is "Starring Marlon Brand" and whose last is "and Katy Jurado".
    """
    text = str(value or "").strip()
    if not text:
        return []
    text = re.sub(r"^(starring|featuring|with)\s+", "", text, flags=re.I)
    names = []
    for part in re.split(r",|\band\b|&", text):
        part = part.strip(" .")
        if part:
            names.append(part)
    return names


def _year_and_rating(tag):
    """"1975 | TVG " -> (1975, "TVG").

    The one place a title's year and certificate appear is this marker, as a
    single pipe-separated string.
    """
    year, rating = 0, ""
    for part in str(tag or "").split("|"):
        part = part.strip()
        if not part:
            continue
        if part.isdigit() and len(part) == 4:
            year = int(part)
        elif not rating:
            rating = part
    return year, rating


def overlay(data, api):
    """A guide airing's own metadata, from the tvguide overlay.

    The schedule endpoint sends a title, an id and two times per airing and
    nothing else. Everything a viewer would want to read -- the synopsis, the
    cast, the artwork, which episode it is and its certificate -- is only in
    this overlay, which the web player opens when an airing is selected.
    """
    if not data:
        return {}
    season, episode, episode_title = 0, 0, ""
    # "S9 Ep2 | Dregg Of The Earth"
    numbered = str(data.get("subtitle3") or "")
    match = _SXEY.match(numbered)
    if match:
        season, episode = int(match.group(1)), int(match.group(2))
    if "|" in numbered:
        episode_title = numbered.split("|", 1)[1].strip()
    return {
        "title": data.get("name") or "",
        "plot": data.get("description") or "",
        "cast": _names(data.get("cast")),
        "image": api.image(data.get("image")),
        "channel_logo": api.image(data.get("channel_icon_url")),
        "channel": data.get("subtitle") or "",
        "when": data.get("subtitle1") or "",
        "repeat": (data.get("subtitle2") or "").strip(),
        "rating": (data.get("subtitle4") or "").strip(),
        "season": season,
        "episode": episode,
        "episode_title": episode_title,
        "watch_live": data.get("target_watchlive") or "",
        "series": data.get("target_browse_episodes") or "",
    }


def form_options(response):
    """The choices a form offers, as [{label, value, code}].

    Only the radio buttons are choices; the rest of a form's elements are a
    hidden heading, a submit and a cancel. The ``value`` is an opaque
    instruction string that is sent back exactly as it arrived.
    """
    out = []
    for element in (response.get("elements") or []):
        if element.get("fieldType") != "radio-button":
            continue
        if not element.get("value"):
            continue
        out.append({
            "label": element.get("data") or element.get("elementCode") or "",
            "value": element["value"],
            "code": element.get("elementCode") or "",
        })
    return out


def programme(raw):
    """One airing from the guide endpoint.

    The guide's own programme objects are thinner than a page card -- a title,
    an id and the two markers holding the times -- and carry no channel of
    their own, so the caller pairs them with the channel they came under.
    """
    display = raw.get("display") or {}
    markers = _markers(display)
    attrs = (raw.get("target") or {}).get("pageAttributes") or {}
    return {
        "title": display.get("title") or "",
        "id": raw.get("id"),
        "path": (raw.get("target") or {}).get("path") or "",
        "is_favourite": str(attrs.get("isFavourite", "")).lower() == "true",
        "start_ms": _int(markers.get("startTime")),
        "end_ms": _int(markers.get("endTime")),
        "image": display.get("imageUrl") or "",
    }
