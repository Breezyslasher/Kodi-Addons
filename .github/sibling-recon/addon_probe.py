#!/usr/bin/env python3
"""Run an add-on's own lib/api.py against the live site and show what it sees.

Usage:
    python3 addon_probe.py ADDON_DIR SEARCH_TERM
"""
import json
import sys
import uuid

sys.path.insert(0, sys.argv[1] + "/lib")
import api  # noqa: E402

a = api.FtApi(str(uuid.uuid4()), log=print)
print("build", a.build(), "base", a.api_base())
raw = a._get("recipes.php", {"searchType": "search", "keys": sys.argv[2], "start-index": 0})
res = raw.get("results")
print("search raw: status=%s count=%s results_type=%s len=%s keys=%s" % (
    raw.get("status"), raw.get("count"), type(res).__name__,
    len(res) if hasattr(res, "__len__") else None, sorted(raw)[:12]))
items, more = a.search(sys.argv[2])
print("search(): %d items, more=%s" % (len(items), more))
for it in items[:4]:
    print("  kind=%s type=%s title=%s url=%s hls=%r vurl=%r" % (
        api.kind(it), it.get("type"), it.get("title"), str(it.get("url"))[:70],
        it.get("video_hls_url"), it.get("video_url")))
# A show -> its seasons -> a season feed -> episodes.
show = next((it for it in items if "shows.php" in str(it.get("url"))), None)
if show:
    det, seasons, sitems = a.series(show["url"])
    print("series(%s): seasons=%d items=%d" % (show.get("title"), len(seasons), len(sitems)))
    feed = (seasons[0].get("feed") or seasons[0].get("url")) if seasons else None
    if feed:
        print("season feed:", feed[:120])
        raw = a._get(feed, {"start-index": 0})
        res = raw.get("results")
        print("feed raw: status=%s count=%s results_type=%s len=%s data=%s" % (
            raw.get("status"), raw.get("count"), type(res).__name__,
            len(res) if hasattr(res, "__len__") else None, raw.get("data")))
        eps, more = a.feed(feed)
        print("feed(): %d items, more=%s" % (len(eps), more))
        for it in eps[:3]:
            print("  kind=%s type=%s title=%s hls=%r vurl=%r fmt=%s drm=%s" % (
                api.kind(it), it.get("type"), it.get("title"), str(it.get("video_hls_url"))[:90],
                str(it.get("video_url"))[:90], it.get("video_format"), it.get("drm")))
        if eps:
            print("  first episode keys:", json.dumps({k: str(v)[:60] for k, v in eps[0].items()
                                                         if v not in (None, "", "None", [], {})})[:1500])

# --- where does a HappyKids 'post' get its stream? -------------------------
import base64
import re
import urllib.parse


def xor_b64(s, key="ft-iptv-website-#$#$"):
    raw = base64.b64decode(s + "=" * (-len(s) % 4)).decode("latin-1")
    return "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(raw))


for it in items[:2]:
    nid = it.get("node_id")
    print("\n== post %s %s web_url=%s" % (nid, it.get("title"), it.get("web_url")))
    try:
        d = a.detail(nid)
        print("  detail stream-ish fields:", {k: str(v)[:120] for k, v in d.items()
              if re.search(r"video|stream|hls|mp4|m3u8|flv|url|embed|source", k, re.I)
              and v not in (None, "", "None")})
    except Exception as exc:  # noqa: BLE001
        print("  detail failed:", exc)
    for path in (it.get("web_url"), "videos/%s" % nid):
        if not path:
            continue
        page = urllib.parse.urljoin("https://happykids.tv/home/", str(path))
        r = a.s.get(page, timeout=20)
        html = r.text
        print("  page %s -> HTTP %s %d bytes" % (page, r.status_code, len(html)))
        for m in re.finditer(r"(dvvideo_url|video_url|video_hls_url|deeplinkMovieInfo)\s*[=:]\s*[\"']?([^\"';,\s]{0,200})", html):
            print("    %s = %s" % (m.group(1), m.group(2)[:160]))
        m = re.search(r"dvvideo_url\s*=\s*[\"']([^\"']+)", html)
        if m:
            try:
                print("    decoded dvvideo_url ->", xor_b64(m.group(1))[:200])
            except Exception as exc:  # noqa: BLE001
                print("    decode failed:", exc)
        for u in sorted(set(re.findall(r"https?://[^\"'\s<>]+\.(?:m3u8|mp4)[^\"'\s<>]*", html)))[:5]:
            print("    media url in page:", u[:160])
        break
