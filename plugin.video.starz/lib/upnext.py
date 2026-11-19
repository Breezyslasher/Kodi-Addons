"""Up Next integration (service.upnext), and what the fallback needs.

Up Next listens for a JSONRPC.NotifyAll from "<addon id>.SIGNAL" with the
message "upnext_data" and one base64-encoded JSON document describing the
current and next episode plus the URL that plays the next one. It then
shows its own "Up next" pop-up and plays play_url itself.

Starz gives every episode a nextContentId (also across seasons) and a
creditTimeIn: where the credits start, which is when the pop-up should
appear rather than a fixed time before the end.
"""

import base64
import json

import xbmc

ADDON_ID = "plugin.video.starz"
PLUGIN = "plugin://%s/" % ADDON_ID


def installed():
    return bool(xbmc.getCondVisibility("System.HasAddon(service.upnext)"))


def _episode(item, content):
    first_aired = (content.get("startDate") or "")[:10] or str(content.get("releaseYear") or "")
    return {
        "episodeid": int(item["id"]),
        "tvshowid": int(item.get("series_id") or 0),
        "title": item.get("title", ""),
        "art": item.get("art") or {},
        "season": int(item.get("season") or 0),
        "episode": int(item.get("episode") or 0),
        "showtitle": item.get("series_title") or content.get("seriesName") or "",
        "plot": item.get("plot", ""),
        "playcount": 0,
        "rating": None,
        "firstaired": first_aired,
        "runtime": int(item.get("duration") or 0),
    }


def build(current_item, current, next_item, nxt):
    data = {
        "current_episode": _episode(current_item, current),
        "next_episode": _episode(next_item, nxt),
        "play_url": PLUGIN + "?action=play&content_id=%s" % next_item["id"],
    }
    if current.get("creditTimeIn"):
        data["notification_time"] = int(current["creditTimeIn"])
    return data


def signal(data):
    payload = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "JSONRPC.NotifyAll",
        "params": {"sender": "%s.SIGNAL" % ADDON_ID, "message": "upnext_data",
                   "data": [payload]}}))
