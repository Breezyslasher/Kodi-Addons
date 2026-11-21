#!/usr/bin/env python3
"""Discover the Future Today API config for Fawesome's sibling sites.

Usage:
    python3 discover.py

HappyKids and iFood.tv run the same Future Today jQuery player as Fawesome
(iptv-main.js: appConfig.appId / siteId / platform_id, API base
/home/new/<build>/api/). For each candidate site this finds the build and the
player bundle, extracts the client ids, then calls getSecurityToken.php and
index.php?parent=home to confirm the API answers and prints the home sections
and a sample title's stream fields. Read-only; prints everything. Stdlib only.
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
DEV = str(uuid.uuid4())
SITES = {
    "happykids": ["https://www.happykids.tv/", "https://happykids.tv/"],
    "ifoodtv": ["https://www.ifood.tv/", "https://ifood.tv/"],
}


def http(url, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
            return r.status, r.geturl(), r.read(6 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, (e.read(200000).decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # noqa: BLE001
        return None, url, "ERROR %s" % e


def js(t):
    try:
        return json.loads(t)
    except ValueError:
        return None


def find(bundle, name):
    m = re.search(r"appConfig\.%s\s*=\s*[\"']?([0-9A-Za-z._-]+)" % name, bundle)
    return m.group(1) if m else None


def probe(label, urls):
    print("\n################ %s ################" % label)
    home_url = home = None
    for u in urls:
        st, final, body = http(u)
        print("GET %-32s -> %s %s (%d bytes)" % (u, st, final, len(body)))
        if st == 200 and len(body) > 500:
            home_url, home = final, body
            break
    if not home:
        print("  no reachable home page")
        return
    m = re.search(r"/home/new/(v\d+)/", home)
    build = m.group(1) if m else None
    mb = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', home)
    print("  build=%s  iptv-main=%s" % (build, bool(mb)))
    if not mb:
        # Maybe a different structure; show script srcs for a lead.
        print("  scripts:", re.findall(r'<script[^>]+src="([^"]+)"', home)[:12])
        links = sorted(set(re.findall(r'href="([^"#]+)"', home)))
        print("  candidate app links:", [l for l in links if re.search(r"watch|home/new|app|play|tv\.|stream|kids", l, re.I)][:30])
        for extra in ("home/new/", "watch/", "app/", "home/", "robots.txt"):
            st2, fin2, b2 = http(urllib.parse.urljoin(home_url, extra))
            m2 = re.search(r"/home/new/(v\d+)/", b2 or "")
            print("  GET %-12s -> %s %s %d bytes build=%s iptv=%s" % (
                extra, st2, fin2, len(b2 or ""), m2.group(1) if m2 else None, "iptv-main" in (b2 or "")))
            if extra == "robots.txt":
                print("    ", (b2 or "")[:300].replace("\n", " | "))
        return
    bundle_url = urllib.parse.urljoin(home_url, mb.group(1))
    _, _, bundle = http(bundle_url)
    app_id, site_id, pid = find(bundle, "appId"), find(bundle, "siteId"), find(bundle, "platform_id")
    print("  bundle defaults: appId=%s siteId=%s platform_id=%s" % (app_id, site_id, pid))
    for blob, where in ((home, "page"), (bundle, "bundle")):
        sids = sorted(set(re.findall(r"siteId[\"'=:\s]+(\d+)", blob)))
        aids = sorted(set(re.findall(r"appId[\"'=:\s]+(\d+)", blob)))
        toks = sorted(set(re.findall(r"auth-token[\"'=:\s]+(\d+)", blob)))
        print("  %s literals: siteId=%s appId=%s auth-token=%s" % (where, sids[:12], aids[:12], toks[:12]))
    for sc in re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", home, re.S):
        if re.search(r"siteId|appId|platform|baseAppId", sc):
            print("  inline:", " ".join(sc.split())[:400])
    # Try the candidate combinations until index.php answers ok.
    cands = []
    page_sids = sorted(set(re.findall(r"siteId[\"'=:\s]+(\d+)", home + bundle)))
    page_aids = sorted(set(re.findall(r"appId[\"'=:\s]+(\d+)", home + bundle))) or [app_id]
    page_toks = sorted(set(re.findall(r"auth-token[\"'=:\s]+(\d+)", home + bundle))) or [pid]
    for a in page_aids[:4]:
        for si in page_sids[:8]:
            for t in page_toks[:4]:
                cands.append((a, si, t))

    base = re.search(r"https?://[^/]+", home_url).group(0)
    api = "%s/home/new/%s/api/" % (base, build or "v469")
    d, tok, common = {}, None, ""
    for a, si, t in cands or [(app_id, site_id, pid)]:
        common = "appId=%s&siteId=%s&auth-token=%s&platform_id=%s&deviceId=%s&apiEnv=production" % (
            a, si, t, t, DEV)
        st, _, tb = http(api + "getSecurityToken.php?" + common)
        tok = (js(tb) or {}).get("securityToken")
        if not tok:
            print("  try appId=%s siteId=%s tok=%s: no token (%s)" % (a, si, t, (tb or "")[:80]))
            continue
        st, _, ib = http(api + "index.php?parent=home&" + common, {"token": tok})
        d = js(ib) or {}
        print("  try appId=%s siteId=%s auth-token=%s: index status=%s %s" % (
            a, si, t, d.get("status"), "" if d else (ib or "")[:120].replace("\n", " ")))
        if d.get("status") == "ok":
            print("  >> WORKING IDS appId=%s siteId=%s auth-token=%s" % (a, si, t))
            break
    if not tok:
        return
    chans = d.get("channels") or []
    if isinstance(chans, dict):
        chans = list(chans.values())
    print("  index.php status=%s sections=%s" % (
        d.get("status"), [c.get("name") for c in chans if isinstance(c, dict)]))
    # A sample feed -> a title, to see the content/stream shape.
    feed = next((c.get("url") for c in chans if isinstance(c, dict) and c.get("url")), None)
    if feed:
        sp = urllib.parse.urlsplit(feed)
        furl = api + sp.path.rsplit("/", 1)[-1] + "?" + sp.query + "&deviceId=" + DEV
        _, _, fb = http(furl, {"token": tok})
        fd = js(fb) or {}
        res = fd.get("results") or []
        if isinstance(res, dict):
            res = list(res.values())
        print("  first feed '%s': %d results" % (sp.path, len(res)))
        if res and isinstance(res[0], dict):
            it = res[0]
            print("  sample item:", {k: str(it.get(k))[:70] for k in
                  ("title", "type", "feed_type", "video_hls_url", "video_url",
                   "video_format", "drm", "content_genre", "runtime") if k in it})


def main():
    for label, urls in SITES.items():
        probe(label, urls)
    print("\nDONE")


if __name__ == "__main__":
    main()
