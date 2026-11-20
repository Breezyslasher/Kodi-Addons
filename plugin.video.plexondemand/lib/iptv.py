"""IPTV Manager integration (same approach as plugin.video.starz / tubitv).

https://github.com/add-ons/service.iptv.manager/wiki/Integration

IPTV Manager finds this addon through the iptv.* settings, then calls
plugin://plugin.video.plexondemand/iptv/channels?port=N and .../iptv/epg?port=N.
Each call opens a TCP connection back to 127.0.0.1:N and writes a JSON
document: JSON-STREAMS for the channels, JSON-EPG for the guide.

Channels are Plex's free Live TV lineup (lib/live.py), grouped by Plex's own
genre filters; each streams through ?action=live_play. The guide covers from
now to about a day ahead (Plex's grid is one channel and one day per call,
so it is fetched in parallel).
"""

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import kodiutils
from .live import ALL, WORKERS, LiveApi, episode_label, programme_title

VERSION = 1
TIMEOUT = 30
CHANNELS_ROUTE = "/iptv/channels"
EPG_ROUTE = "/iptv/epg"
PLUGIN = "plugin://plugin.video.plexondemand/"
GUIDE_HOURS = 24


def send(port, produce):
    """Connect first, build second: IPTV Manager gives up if the addon has
    not connected within ten seconds, and the guide takes longer to build."""
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.settimeout(TIMEOUT)
    try:
        conn.connect(("127.0.0.1", int(port)))
        conn.settimeout(None)
        conn.sendall(json.dumps(produce()).encode("utf-8"))
    finally:
        conn.close()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def channel_id(c):
    return "plex.%s" % (c.get("slug") or c.get("gridKey"))


def channels(api, chans=None):
    """JSON-STREAMS: every channel, grouped by Plex's genre filter."""
    groups = {g: t for t, _k, g in api.filters() if g}
    out = []
    for c in chans if chans is not None else api.channels(ALL):
        group = next((groups[g] for g in c.get("genreRatingKeys") or [] if g in groups), "Plex")
        out.append({"id": channel_id(c), "name": c.get("title") or "?",
                    "logo": c.get("thumb") or "", "group": group,
                    "stream": PLUGIN + "?action=live_play&id=" + c["id"]})
    return {"version": VERSION, "streams": out}


def _entry(m):
    media = m["Media"][0]
    e = {"start": _iso(media["beginsAt"]), "stop": _iso(media["endsAt"]),
         "title": programme_title(m)}
    sub = episode_label(m)
    if sub:
        e["subtitle"] = sub
    if m.get("summary"):
        e["description"] = m["summary"]
    if m.get("thumb"):
        e["image"] = m["thumb"]
    if m.get("type") == "episode" and m.get("parentIndex") and m.get("index"):
        e["episode"] = "S%02dE%02d" % (int(m["parentIndex"]), int(m["index"]))
    genres = [g.get("tag") for g in m.get("Genre") or [] if g.get("tag")]
    if genres:
        e["genre"] = genres[0]
    if m.get("year"):
        e["date"] = str(m["year"])
    # No per-programme "stream": Kodi's guide would mark every programme as
    # playable (catch-up). What is on now plays through the channel stream.
    return e


def epg(api, chans=None, hours=GUIDE_HOURS):
    """JSON-EPG from now to `hours` ahead for every channel."""
    now = time.time()
    chans = chans if chans is not None else api.channels(ALL)

    def one(c):
        try:
            progs = api.schedule(c["gridKey"], now - 3600, now + hours * 3600) if c.get("gridKey") else []
        except Exception as exc:
            kodiutils.log("EPG %s: %s" % (c.get("title"), exc))
            progs = []
        return channel_id(c), [_entry(m) for m in progs]

    with ThreadPoolExecutor(WORKERS) as pool:
        return {"version": VERSION, "epg": dict(pool.map(one, chans))}


def handle(argv0, port):
    """Answer an IPTV Manager call. True when argv0 was one of its routes."""
    route = "/" + argv0.split("://", 1)[-1].split("/", 1)[-1].rstrip("/")
    if route not in (CHANNELS_ROUTE, EPG_ROUTE):
        return False
    if not port:
        kodiutils.log_error("IPTV Manager call without a port: %s" % argv0)
        return True
    api = LiveApi()
    if route == CHANNELS_ROUTE:
        send(port, lambda: channels(api))
    else:
        send(port, lambda: epg(api))
    kodiutils.log("IPTV Manager: served %s" % route)
    return True
