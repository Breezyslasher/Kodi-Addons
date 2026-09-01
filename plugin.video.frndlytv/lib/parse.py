"""Turning Friendly TV's cards into something Kodi can draw.

The service describes everything -- a film, an episode, a live channel, a
recording -- with the same card shape, and says what a card *is* only through
``target.pageType`` and a bag of string-valued ``pageAttributes``. This module
is the one place that reads that shape, so the routing code can deal in plain
dictionaries.
"""

import time


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


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
        "start_ms": start_ms,
        "end_ms": end_ms,
        "badge": markers.get("badgeV2") or markers.get("badge") or "",
        "poster": api.image(display.get("imageUrl")),
        "channel_logo": api.image(display.get("parentIcon")),
        "id": raw.get("id"),
    }

    # A card whose only marker says "non_playable" is a heading in the
    # guide's channel strip, not something to open.
    if markers.get("special") == "non_playable" and not item["path"]:
        item["playable"] = False

    if not item["duration_ms"] and start_ms and end_ms > start_ms:
        item["duration_ms"] = end_ms - start_ms
    return item


def sections(response, api):
    """Every section on a page, as {name, code, path, cards, deferred}.

    ``deferred`` is true where the service described the section but sent no
    cards with it; the caller fills those in with ``Api.section`` only if it
    actually needs them, because a page can defer a dozen at once.
    """
    out = []
    for pane in (response.get("data") or []):
        section = pane.get("section")
        if not section:
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
    found = {"title": "", "plot": "", "poster": "", "fanart": "", "actions": []}
    for pane in (response.get("data") or []):
        content = pane.get("content")
        if not content:
            continue
        found["title"] = found["title"] or content.get("title") or ""
        found["poster"] = found["poster"] or api.image(content.get("posterImage"))
        found["fanart"] = (found["fanart"] or
                           api.image(content.get("backgroundImage")))
        blurb = []
        for row in (content.get("dataRows") or []):
            for element in (row.get("elements") or []):
                kind = element.get("elementType") or ""
                sub = element.get("elementSubtype") or ""
                data = element.get("data")
                target = str(element.get("target") or "").strip()
                if kind == "button":
                    if "/" in target:
                        found["actions"].append(
                            {"label": str(data or sub or "Play"),
                             "path": target})
                elif kind == "description" or sub == "description":
                    if data:
                        blurb.append(str(data))
                elif kind == "text":
                    if sub == "title" and not found["title"]:
                        found["title"] = str(data or "")
                    elif sub in ("subtitle1", "subtitle2") and data:
                        blurb.append(str(data))
        # dict.fromkeys keeps first-seen order while dropping repeats: an
        # episode subtitle is often the description over again.
        found["plot"] = "\n".join(dict.fromkeys(b for b in blurb if b))
    return found


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
    return {
        "title": display.get("title") or "",
        "id": raw.get("id"),
        "path": (raw.get("target") or {}).get("path") or "",
        "start_ms": _int(markers.get("startTime")),
        "end_ms": _int(markers.get("endTime")),
        "image": display.get("imageUrl") or "",
    }
