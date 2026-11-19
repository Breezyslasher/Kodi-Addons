"""Plex Metadata -> Kodi ListItem art and info tags."""


def art(m):
    art = {}
    thumb = m.get("thumb")
    if thumb:
        art["poster"] = art["thumb"] = thumb
    if m.get("art"):
        art["fanart"] = m["art"]
    for img in m.get("Image") or []:
        kind, src = img.get("type"), img.get("url")
        if not src:
            continue
        if kind == "clearLogo":
            art["clearlogo"] = src
        elif kind == "coverPoster" and "poster" not in art:
            art["poster"] = src
        elif kind == "background" and "fanart" not in art:
            art["fanart"] = src
    if m.get("type") == "episode":
        art["thumb"] = thumb or m.get("parentThumb") or m.get("grandparentThumb") or ""
        art["poster"] = m.get("grandparentThumb") or art.get("thumb", "")
        art.setdefault("fanart", m.get("grandparentArt") or "")
    elif m.get("type") == "season":
        art.setdefault("fanart", m.get("parentArt") or "")
    return art


def info(item, m):
    tag = item.getVideoInfoTag()
    kind = m.get("type")
    tag.setMediaType({"movie": "movie", "show": "tvshow", "season": "season",
                      "episode": "episode"}.get(kind, "video"))
    tag.setTitle(m.get("title") or "")
    if m.get("summary"):
        tag.setPlot(m["summary"])
    if m.get("tagline"):
        tag.setTagLine(m["tagline"])
    if m.get("year"):
        tag.setYear(int(m["year"]))
    if m.get("originallyAvailableAt"):
        tag.setPremiered(m["originallyAvailableAt"])
    if m.get("duration"):
        tag.setDuration(int(m["duration"]) // 1000)
    if m.get("contentRating"):
        tag.setMpaa(m["contentRating"])
    if m.get("studio"):
        tag.setStudios([m["studio"]])
    genres = [g.get("tag") for g in m.get("Genre") or [] if g.get("tag")]
    if genres:
        tag.setGenres(genres)
    if m.get("audienceRating"):
        try:
            tag.setRating(float(m["audienceRating"]) * (2 if float(m["audienceRating"]) <= 5 else 1))
        except (TypeError, ValueError):
            pass
    if kind == "episode":
        tag.setTvShowTitle(m.get("grandparentTitle") or "")
        if m.get("parentIndex") is not None:
            tag.setSeason(int(m["parentIndex"]))
        if m.get("index") is not None:
            tag.setEpisode(int(m["index"]))
    elif kind == "season":
        tag.setTvShowTitle(m.get("parentTitle") or "")
        if m.get("index") is not None:
            tag.setSeason(int(m["index"]))
    if m.get("viewOffset") and m.get("duration"):
        tag.setResumePoint(int(m["viewOffset"]) / 1000.0, int(m["duration"]) / 1000.0)
    if m.get("viewCount"):
        tag.setPlaycount(int(m["viewCount"]))
