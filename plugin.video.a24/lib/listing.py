"""The add-on's item dict -> Kodi ListItem.

Item dict fields (all optional except ``id`` and ``title``):

    id, title, type ("movie" | "extra"), year, plot, duration (seconds),
    genres [str], directors [str], cast [{"name", "role", "order"}], mpaa,
    art {"poster", "fanart", "thumb", "clearlogo"},
    entitlement (lib.api OWNED / RENTED / MEMBER_FREE / RENTABLE / UNAVAILABLE),
    rental_expires (unix seconds), price (display string, e.g. "$5.99"),
    has_extras (bool)

The entitlement shows in the label so the user is never offered a title they
cannot play; rentable titles say "rent in the A24 app" and do not play.
"""

import time

import xbmc
import xbmcgui

from lib import kodiutils
from lib.api import OWNED, RENTED, MEMBER_FREE, RENTABLE, PLAYABLE

L = kodiutils.localize


def entitlement_label(item):
    state = item.get("entitlement")
    if state == OWNED:
        return L(32060)
    if state == RENTED:
        expires = item.get("rental_expires")
        if expires:
            return L(32061) % time.strftime("%d %b %H:%M", time.localtime(expires))
        return L(32062)
    if state == MEMBER_FREE:
        return L(32063)
    if state == RENTABLE:
        return L(32064) % item["price"] if item.get("price") else L(32065)
    return L(32066)


def playable(item):
    if item.get("entitlement") == RENTED and item.get("rental_expires"):
        return item["rental_expires"] > time.time()
    return item.get("entitlement") in PLAYABLE


def build(item, fanart=""):
    label = item.get("title", "")
    tag_text = entitlement_label(item)
    li = xbmcgui.ListItem("%s  [COLOR grey]%s[/COLOR]" % (label, tag_text))
    li.setLabel2(tag_text)
    tag = li.getVideoInfoTag()
    tag.setTitle(label)
    plot = item.get("plot", "")
    tag.setPlot("[B]%s[/B]\n%s" % (tag_text, plot) if plot else tag_text)
    tag.setMediaType("movie" if item.get("type", "movie") == "movie" else "video")
    try:
        if item.get("year"):
            tag.setYear(int(item["year"]))
        if item.get("duration"):
            tag.setDuration(int(item["duration"]))
    except (TypeError, ValueError):
        pass
    if item.get("genres"):
        tag.setGenres(item["genres"])
    if item.get("directors"):
        tag.setDirectors(item["directors"])
    if item.get("mpaa"):
        tag.setMpaa(item["mpaa"])
    if item.get("cast"):
        tag.setCast([xbmc.Actor(c.get("name", ""), c.get("role", ""), c.get("order", i))
                     for i, c in enumerate(item["cast"])])
    art = dict(item.get("art") or {})
    art.setdefault("fanart", fanart)
    if art.get("poster"):
        art.setdefault("thumb", art["poster"])
    li.setArt(art)
    li.setProperty("IsPlayable", "true" if playable(item) else "false")
    return li
