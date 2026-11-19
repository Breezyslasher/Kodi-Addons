"""Kodi info for a scheduled programme (an element of the linear schedule:
title, contentType, contentId, startMs, endMs, logLine, ratingName,
releaseYear, simplifiedGenres). Used for what plays from a channel, and by
the service to follow the live broadcast from one programme to the next."""

import xbmcgui

IMAGE = ("https://stz1.imgix.net/Web/contentId/%s/type/%s/dimension/%s/lang/en-US"
         "?fit=max&auto=format,compress&w=%d")


def apply(li, el, channel_name=""):
    tag = li.getVideoInfoTag()
    title = el.get("title") or ""
    tag.setTitle(title)
    plot = el.get("logLine") or ""
    if channel_name:
        plot = "[B]%s[/B]\n%s" % (channel_name, plot) if plot else "[B]%s[/B]" % channel_name
    tag.setPlot(plot)
    if el.get("startMs") and el.get("endMs"):
        tag.setDuration(int((el["endMs"] - el["startMs"]) // 1000))
    try:
        if el.get("releaseYear"):
            tag.setYear(int(el["releaseYear"]))
    except (TypeError, ValueError):
        pass
    if el.get("simplifiedGenres"):
        tag.setGenres(list(el["simplifiedGenres"]))
    if el.get("ratingName"):
        tag.setMpaa(el["ratingName"])
    tag.setMediaType("episode" if el.get("contentType") == "Episode" else "movie")
    cid = el.get("contentId")
    if cid:
        kind = "STUDIO" if el.get("contentType") == "Episode" else "KEY"
        li.setArt({"thumb": IMAGE % (cid, kind, "2560x1440", 1280),
                   "fanart": IMAGE % (cid, "KEY", "2560x1440", 1920),
                   "poster": IMAGE % (cid, "KEY", "1536x2048", 600)})
    li.setLabel(title)
    return li


def listitem(el, channel_name=""):
    return apply(xbmcgui.ListItem(el.get("title") or ""), el, channel_name)
