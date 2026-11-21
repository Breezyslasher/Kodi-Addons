#!/usr/bin/env python3
"""Probe Fawesome's queue ("My List") endpoints so the add-on can implement it.

Usage:
    python3 probe_queue.py [OUT_DIR]

The web player keeps a per-device list via getQueuelist.php / addQueue.php /
removeQueue.php (no account -- keyed by the generated deviceId). This:
  1. prints the bundle snippets that build those calls (exact params), and
  2. exercises them live on a fresh random device id: list (empty), add a
     title, list (should contain it), remove it, list (empty again),
     trying a few id param names until one actually changes the list.

Everything is printed; nothing here is secret (no account). Stdlib only.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
DEV = str(uuid.uuid4())
IDS = "appId=9&siteId=236&auth-token=1217575&platform_id=1217575&version=sv9.0"


def http(url, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*", "Referer": "https://fawesome.tv/"}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
            return r.status, r.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(100000).decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # noqa: BLE001
        return None, "ERROR %s" % e


def js(t):
    try:
        return json.loads(t)
    except ValueError:
        return None


def snippets(bundle):
    print("== bundle call construction ==")
    for needle in ("apiCommonParams=", "getQueuelist.php", "addQueue.php",
                   "removeQueue.php", "queueManager.api", "parseQueueVideos"):
        i = bundle.find(needle)
        if i == -1:
            print("  %s: not found" % needle)
            continue
        print("  --- %s ---" % needle)
        print("  " + bundle[max(0, i - 120):i + 260].replace("\n", " "))


def count_list(data):
    """Best-effort: how many items a getQueuelist response holds."""
    if not isinstance(data, dict):
        return -1
    for key in ("results", "channels", "videos", "queue", "data"):
        v = data.get(key)
        if isinstance(v, list):
            return len(v)
        if isinstance(v, dict):
            return len(v)
    return 0


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    _, home = http("https://fawesome.tv/")
    build = (re.search(r"/home/new/(v\d+)/", home) or [None, "v469"])[1]
    api = "https://fawesome.tv/home/new/%s/api/" % build
    common = "%s&deviceId=%s&apiEnv=production" % (IDS, DEV)
    print("build %s, device (random) %s\n" % (build, DEV))

    mb = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', home)
    if mb:
        _, bundle = http(urllib.parse.urljoin("https://fawesome.tv/", mb.group(1)))
        snippets(bundle)
    print()

    tok = (js(http(api + "getSecurityToken.php?" + common)[1]) or {}).get("securityToken", "")
    hdr = {"token": tok}

    def call(name, extra=""):
        url = api + "%s?%s%s" % (name, common, extra)
        st, body = http(url, hdr)
        data = js(body)
        with open(os.path.join(out, "queue_%s.txt" % re.sub(r"\W+", "_", name + extra)[:50]),
                  "w", encoding="utf-8") as fh:
            fh.write("# %s %s\n%s" % (st, url, json.dumps(data, indent=1) if data else body[:4000]))
        return st, data

    # A real node id to add.
    s = js(http(api + "recipes.php?searchType=search&keys=love&start-index=0&" + common, hdr)[1]) or {}
    results = s.get("results") or []
    nid = next((it.get("node_id") for it in results if isinstance(it, dict) and it.get("node_id")), None)
    print("== live exercise (node_id=%s) ==" % nid)

    st, data = call("getQueuelist.php")
    base = count_list(data)
    print("getQueuelist (before): HTTP %s status=%s items=%s" % (st, (data or {}).get("status"), base))

    # Try the likely id param names until the list changes.
    added_param = None
    for param in ("nid", "node_id", "id", "keys", "videoId", "contentId"):
        st, data = call("addQueue.php", "&%s=%s" % (param, nid))
        after = None
        st2, listed = call("getQueuelist.php")
        after = count_list(listed)
        print("addQueue %-9s: HTTP %s status=%s -> list now %s" % (
            param, st, (data or {}).get("status"), after))
        if (data or {}).get("status") in ("ok", "true", True) or (after or 0) > (base or 0):
            added_param = param
            print("  >> %s is the id param (list grew / status ok)" % param)
            break

    st, listed = call("getQueuelist.php")
    print("getQueuelist (after add): items=%s" % count_list(listed))
    # Show one item's shape so we know what fields the list returns.
    for key in ("results", "channels", "videos", "queue"):
        v = (listed or {}).get(key)
        items = v if isinstance(v, list) else (list(v.values()) if isinstance(v, dict) else [])
        if items and isinstance(items[0], dict):
            print("  item keys:", sorted(items[0])[:40])
            print("  sample:", {k: str(items[0].get(k))[:60] for k in
                                 ("node_id", "title", "video_hls_url", "type", "url") if k in items[0]})
            break

    if added_param:
        st, data = call("removeQueue.php", "&%s=%s" % (added_param, nid))
        st2, listed = call("getQueuelist.php")
        print("removeQueue %s: HTTP %s status=%s -> list now %s" % (
            added_param, st, (data or {}).get("status"), count_list(listed)))
    print("\nDONE")


if __name__ == "__main__":
    main()
