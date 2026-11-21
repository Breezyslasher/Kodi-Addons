#!/usr/bin/env python3
"""Walk the Fawesome web API the way the web player (iptv-main.js) does.

Usage:
    python3 probe_api.py OUT_DIR

From iptv-main.js (docs/recon): the API base is
https://fawesome.tv/home/new/<buildVersion>/api/, every call carries
appId / siteId / auth-token (= platform_id) / deviceId / apiEnv, and every
call but getSecurityToken.php sends the token from getSecurityToken.php in a
'token' header. This fetches the token, the home index, a few of the feeds it
links to, one title's detail and its stream manifests (heads only), and saves
each response to OUT_DIR as api_NN_*.json for reading in the dev container.

Standard library only.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_PAGE = "https://fawesome.tv/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
APP_ID, SITE_ID, PLATFORM_ID = "9", "236", "1217575"
DEVICE_ID = str(uuid.uuid4())
MAX_FEEDS = 10

n = 0
out_dir = "."
log = []


def http(url, headers=None, limit=2 * 1024 * 1024):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Referer": BASE_PAGE, "Origin": "https://fawesome.tv"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read(limit).decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # noqa: BLE001
        return None, {}, "ERROR %s" % e


def save(tag, url, status, headers, text):
    global n
    n += 1
    name = "api_%02d_%s.txt" % (n, re.sub(r"[^A-Za-z0-9_-]+", "_", tag)[:60])
    try:
        body = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
    except ValueError:
        body = text
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        fh.write("# %s %s\n# %s\n" % (status, url, json.dumps(
            {k: v for k, v in headers.items() if k.lower() != "set-cookie"})))
        fh.write(body[:3 * 1024 * 1024])
    line = "%s  %7d  %-40s %s -> %s" % (status, len(text), tag[:40], url[:160], name)
    log.append(line)
    print(line)
    try:
        return json.loads(text)
    except ValueError:
        return None


def build_version():
    status, _, html = http(BASE_PAGE)
    m = re.search(r"/home/new/(v\d+)/", html)
    return m.group(1) if m else "v469"


def common():
    return "appId=%s&siteId=%s&auth-token=%s&platform_id=%s&deviceId=%s&apiEnv=production" % (
        APP_ID, SITE_ID, PLATFORM_ID, PLATFORM_ID, DEVICE_ID)


def walk_urls(obj, found):
    """Collect every value that looks like a feed URL, with its sibling title."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in ("url", "feed", "feed_url") and v.startswith("http"):
                found.append((obj.get("title") or obj.get("name") or k, v))
            else:
                walk_urls(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_urls(v, found)


def walk_videos(obj, found):
    if isinstance(obj, dict):
        if any(k in obj for k in ("video_url", "video_hls_url")):
            found.append(obj)
        for v in obj.values():
            walk_videos(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_videos(v, found)


def main():
    global out_dir
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("api_"):
            os.remove(os.path.join(out_dir, f))
    bv = build_version()
    api = "https://fawesome.tv/home/new/%s/api/" % bv
    log.append("build version %s, api base %s, device id (random) %s" % (bv, api, DEVICE_ID))
    print(log[-1])

    url = api + "getSecurityToken.php?&" + common()
    tok = save("getSecurityToken", url, *http(url)) or {}
    token = ""
    for path in (("securityToken",), ("data", "securityToken"), ("results", "securityToken"), ("token",)):
        cur = tok
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur:
            token = cur
            break
    hdr = {"token": token} if token else {}
    log.append("token found: %s (len %d)" % (bool(token), len(token)))

    feeds, videos = [], []
    for parent in ("home", "movies", "shows"):
        url = api + "index.php?parent=%s&%s" % (parent, common())
        data = save("index_" + parent, url, *http(url, hdr))
        walk_urls(data, feeds)
        walk_videos(data, videos)
    # Same call without the token, to learn whether it is enforced.
    url = api + "index.php?parent=home&" + common()
    save("index_home_no_token", url, *http(url))

    for path in ("sub-categories.php?parent=movies", "sub-categories.php?parent=shows",
                 "l1menu.php?"):
        url = api + path + "&" + common()
        data = save(path.split("?")[0] + "_" + path.split("=")[-1], url, *http(url, hdr))
        walk_urls(data, feeds)

    seen = set()
    for title, furl in feeds:
        if len(seen) >= MAX_FEEDS:
            break
        if furl in seen or not re.search(r"ifood\.tv|fawesome\.tv", furl):
            continue
        seen.add(furl)
        data = save("feed_" + str(title), furl, *http(furl, hdr))
        walk_videos(data, videos)

    url = api + "recipes.php?searchType=search&keys=love&start-index=0&max-results=10&" + common()
    data = save("search_love", url, *http(url, hdr))
    walk_videos(data, videos)

    log.append("feeds seen %d, videos seen %d" % (len(feeds), len(videos)))
    fields = {}
    for v in videos:
        for k in v:
            fields[k] = fields.get(k, 0) + 1
    log.append("video fields: " + json.dumps(fields))
    formats = {}
    for v in videos:
        key = "fmt=%s drm=%s hls=%s" % (v.get("video_format"), v.get("drm"), bool(v.get("video_hls_url")))
        formats[key] = formats.get(key, 0) + 1
    log.append("video stream kinds: " + json.dumps(formats))

    # One clear title and one DRM title, if seen: detail + manifest heads.
    picks = []
    for want_drm in ("0", "1"):
        for v in videos:
            if str(v.get("drm", "0")) == want_drm and v.get("node_id"):
                picks.append(v)
                break
    for v in picks:
        nid = v.get("node_id")
        url = api + ("recipes.php?searchType=nodeid&start-index=0&gridstyle=flat-movie&max-results=1"
                     "&dltype=1&nid=%s&%s" % (nid, common()))
        detail = save("detail_%s_drm%s" % (nid, v.get("drm")), url, *http(url, hdr))
        dv = []
        walk_videos(detail, dv)
        src = (dv[0] if dv else v)
        for key in ("video_hls_url", "video_url"):
            murl = src.get(key)
            if murl and murl.startswith("http"):
                st, h, body = http(murl, {"Accept": "*/*"}, limit=64 * 1024)
                save("manifest_%s_%s" % (nid, key), murl, st, h, body[:20000])
                m = re.search(r"^(?!#)(\S+\.m3u8\S*)$", body, re.M)
                if m:
                    vurl = urllib.parse.urljoin(murl, m.group(1))
                    st, h, body = http(vurl, {"Accept": "*/*"}, limit=64 * 1024)
                    save("variant_%s" % nid, vurl, st, h, body[:8000])
        if str(v.get("drm")) == "1":
            url = api + "drm.php?" + common()
            save("drm_php", url, *http(url, hdr))
    with open(os.path.join(out_dir, "api_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    print("\n".join(log[-4:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
