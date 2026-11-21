#!/usr/bin/env python3
"""Discover the Future Today API config for Fawesome's sibling sites.

Usage:
    python3 discover.py

For each site's player page: read the bundle's own appInfo.buildVersion and
apiBaseUrl construction, the hardcoded rapi.ifood.tv URLs (which carry the real
appId / siteId / auth-token), then call getSecurityToken.php, index.php and
sub-categories.php with every candidate id set, printing raw response heads.
Read-only. Stdlib only.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
DEV = str(uuid.uuid4())
PLAYERS = {
    "happykids": "https://happykids.tv/home/",
    "ifoodtv": "https://ifood.tv/",
    "fawesome (reference)": "https://fawesome.tv/",
}


REF = {"v": ""}


def http(url, headers=None):
    # The API answers an empty body without the site's Referer/Origin.
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if REF["v"]:
        h.update({"Referer": REF["v"] + "/", "Origin": REF["v"]})
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


def ctx(blob, needle, before=150, after=300, limit=3):
    out, i = [], blob.find(needle)
    while i != -1 and len(out) < limit:
        out.append(" ".join(blob[max(0, i - before):i + after].split()))
        i = blob.find(needle, i + 1)
    return out


def probe(label, page):
    print("\n################ %s  %s ################" % (label, page))
    REF["v"] = re.search(r"https?://[^/]+", page).group(0)
    st, final, home = http(page)
    print("page: HTTP %s -> %s (%d bytes)" % (st, final, len(home)))
    mb = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', home)
    if not mb:
        print("  no iptv-main bundle; scripts:", re.findall(r'<script[^>]+src="([^"]+)"', home)[:15])
        return
    bundle_url = urllib.parse.urljoin(final, mb.group(1))
    _, _, bundle = http(bundle_url)
    print("  bundle:", bundle_url, len(bundle), "bytes")
    for s in ctx(bundle, "buildVersion:", 40, 120, 2):
        print("  appInfo:", s)
    for s in ctx(bundle, "apiBaseUrl=window.location", 80, 260, 3):
        print("  apiBaseUrl:", s)
    for s in ctx(bundle, 'appConfig.siteId=', 60, 160, 3):
        print("  siteId set:", s)
    # Inline config on the page.
    for sc in re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", home, re.S):
        if re.search(r"siteId|appId|platform|buildVersion|apiBase", sc):
            print("  inline:", " ".join(sc.split())[:500])
    for s2 in ctx(bundle, "initApp(", 300, 200, 4):
        print("  initApp call:", s2)
    for s2 in ctx(bundle, "site_id", 200, 120, 4):
        print("  site_id:", s2)
    rapi = sorted(set(re.findall(r"https?://rapi\.ifood\.tv/[^\"'\s]+", bundle)))
    print("  rapi URLs in bundle:", rapi[:8])

    m = re.search(r'buildVersion:"(v\d+)"', bundle)
    build = m.group(1) if m else (re.search(r"/home/new/(v\d+)/", home) or [None, None])[1]
    origin = re.search(r"https?://[^/]+", final).group(0)
    bases = ["%s/home/new/%s/api/" % (origin, build)] if build else []
    bases += ["%s/home/api/" % origin, "%s/api/" % origin]

    cands = set()
    for u in rapi:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))
        if q.get("appId") and q.get("siteId") and q.get("auth-token"):
            cands.add((q["appId"], q["siteId"], q["auth-token"]))
    cands.add(("9", "236", "1217575"))
    print("  id candidates:", sorted(cands))

    for base in bases:
        for a, si, t in sorted(cands):
            common = "appId=%s&siteId=%s&auth-token=%s&platform_id=%s&deviceId=%s&apiEnv=production" % (
                a, si, t, t, DEV)
            st, _, tb = http(base + "getSecurityToken.php?" + common)
            tok = (js(tb) or {}).get("securityToken")
            if not tok:
                print("  [%s] ids %s/%s/%s: token HTTP %s %r" % (base, a, si, t, st, (tb or "")[:80]))
                continue
            for call in ("index.php?parent=home", "sub-categories.php?parent=Home"):
                st, _, ib = http(base + call + "&" + common, {"token": tok})
                d = js(ib) or {}
                ch = d.get("channels") or []
                if isinstance(ch, dict):
                    ch = list(ch.values())
                names = [c.get("name") for c in ch if isinstance(c, dict)][:12]
                print("  [%s] ids %s/%s/%s %s: HTTP %s status=%s names=%s raw=%r" % (
                    base, a, si, t, call.split("?")[0], st, d.get("status"), names,
                    "" if d else (ib or "")[:160]))


def main():
    for label, page in PLAYERS.items():
        probe(label, page)
    print("\nDONE")


if __name__ == "__main__":
    main()
