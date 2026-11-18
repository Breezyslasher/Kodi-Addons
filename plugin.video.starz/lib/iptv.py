"""IPTV Manager integration (same approach as plugin.video.tubitv).

https://github.com/add-ons/service.iptv.manager/wiki/Integration

IPTV Manager finds this addon through the iptv.* settings, then calls
plugin://plugin.video.starz/iptv/channels?port=N and .../iptv/epg?port=N.
Each call opens a TCP connection back to 127.0.0.1:N and writes a JSON
document: JSON-STREAMS for the channels, JSON-EPG for the guide.

Starz has no live streams: a channel's stream tunes in to the title it is
scheduled to show now, from the point the channel has reached (see
default.py action=live). Guide entries carry their own on-demand stream.
"""

import json
import socket
from datetime import datetime, timezone

from lib import kodiutils
from lib.channels import CHANNELS, logo

VERSION = 1
TIMEOUT = 30
CHANNELS_ROUTE = "/iptv/channels"
EPG_ROUTE = "/iptv/epg"
PLUGIN = "plugin://plugin.video.starz/"


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


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def channels(addon_path):
    return {"version": VERSION, "streams": [
        {"id": "starz.%s" % code, "name": name, "logo": logo(addon_path, code),
         "group": group, "stream": PLUGIN + "?action=live&channel=" + code}
        for code, name, group in CHANNELS]}


def epg(api):
    guide = {}
    for code, _name, _group in CHANNELS:
        seen, programmes = set(), []
        for day in (0, 1):
            try:
                elements = api.schedule(code, day)
            except Exception as exc:
                kodiutils.log_error("EPG %s day %d: %s" % (code, day, exc))
                continue
            for el in elements:
                key = (el.get("contentId"), el.get("startMs"))
                if key in seen or not el.get("startMs") or not el.get("endMs"):
                    continue
                seen.add(key)
                entry = {"start": _iso(el["startMs"]), "stop": _iso(el["endMs"]),
                         "title": el.get("title") or ""}
                if el.get("logLine"):
                    entry["description"] = el["logLine"]
                if el.get("simplifiedGenres"):
                    entry["genre"] = el["simplifiedGenres"][0]
                if el.get("releaseYear"):
                    entry["date"] = str(el["releaseYear"])
                if el.get("contentId"):
                    entry["image"] = ("https://stz1.imgix.net/Web/contentId/%s/type/KEY/"
                                      "dimension/2560x1440/lang/en-US?fit=max&auto=format,"
                                      "compress&w=640" % el["contentId"])
                    if el.get("availableOnOtt"):
                        entry["stream"] = PLUGIN + "?action=play&content_id=%s" % el["contentId"]
                programmes.append(entry)
        guide["starz.%s" % code] = sorted(programmes, key=lambda p: p["start"])
    return {"version": VERSION, "epg": guide}


def handle(argv0, port, api, addon_path):
    """Answer an IPTV Manager call. True when argv0 was one of its routes."""
    route = "/" + argv0.split("://", 1)[-1].split("/", 1)[-1].rstrip("/")
    if route not in (CHANNELS_ROUTE, EPG_ROUTE):
        return False
    if not port:
        kodiutils.log_error("IPTV Manager call without a port: %s" % argv0)
        return True
    if route == CHANNELS_ROUTE:
        send(port, lambda: channels(addon_path))
    else:
        send(port, lambda: epg(api))
    kodiutils.log("IPTV Manager: served %s" % route)
    return True
