#!/usr/bin/env python3
"""Look for Fawesome's linear (FAST) channels.

Usage:
    python3 probe_live.py OUT_DIR

The website shows no live channels, but its player (iptv-main.js) is Future
Today's shared CTV codebase, keyed by platform_id (PLATFORM_SAMSUNG, _LG,
_VIZIO ...). This asks the API for the home menu and the Live section as each
platform -- on the fawesome.tv API base with the website's token, and on
rapi.ifood.tv with and without it -- and follows anything that looks like a
live channel list. Writes live_report.txt and the interesting bodies to OUT_DIR.
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
PLATFORMS = {
    "website": 1217575, "samsung": 1217555, "lg": 1217553, "vizio": 1217562,
    "xbox": 1217569, "windows": 1217556, "comcast": 1217563, "cox": 1217568,
    "panasonic": 1217564, "zeasn": 1217566, "foxxum": 1217567, "netrange": 1217565,
    "netgem": 1218181, "opera": 1217554,
}
LIVE_WORDS = re.compile(r"live|channel|linear|epg|fast|tv guide|guide", re.I)
out_dir, log, n = ".", [], [0]


def http(url, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json, */*",
         "Referer": "https://fawesome.tv/", "Origin": "https://fawesome.tv"}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
            return r.status, r.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(200000).decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # noqa: BLE001
        return None, "ERROR %s" % e


def js(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def save(tag, url, status, text):
    n[0] += 1
    name = "live_%02d_%s.txt" % (n[0], re.sub(r"[^A-Za-z0-9_-]+", "_", tag)[:60])
    d = js(text)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        fh.write("# %s %s\n" % (status, url))
        fh.write(json.dumps(d, indent=1, ensure_ascii=False) if d is not None else text[:200000])
    return name


def say(s):
    log.append(s)
    print(s)


def names(d):
    ch = (d or {}).get("channels") or []
    if isinstance(ch, dict):
        ch = list(ch.values())
    return [(c.get("name") or c.get("title") or "?", c.get("url") or c.get("feed") or "",
             c.get("feed_type") or "") for c in ch if isinstance(c, dict)]


def keys_like_live(obj, found, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if re.search(r"live|epg|linear|schedule|program", k, re.I) and v not in ("", None, [], {}, "false", False, 0, "0"):
                found.append("%s.%s=%s" % (path, k, str(v)[:120]))
            keys_like_live(v, found, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:30]):
            keys_like_live(v, found, "%s[%d]" % (path, i))


def main():
    global out_dir
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("live_"):
            os.remove(os.path.join(out_dir, f))
    st, html = http("https://fawesome.tv/")
    m = re.search(r"/home/new/(v\d+)/", html)
    api = "https://fawesome.tv/home/new/%s/api/" % (m.group(1) if m else "v469")
    base_q = "appId=9&siteId=236&deviceId=%s&apiEnv=production&version=sv9.0" % DEV

    def token(pid):
        st, t = http(api + "getSecurityToken.php?%s&auth-token=%d&platform_id=%d" % (base_q, pid, pid))
        return ((js(t) or {}).get("securityToken") or "")

    candidates = []
    for pname, pid in PLATFORMS.items():
        q = "%s&auth-token=%d&platform_id=%d" % (base_q, pid, pid)
        tok = token(pid)
        for host, hdr in (("api", {"token": tok}), ("rapi", {}), ("rapi+tok", {"token": tok})):
            root = api if host == "api" else "https://rapi.ifood.tv/"
            url = root + "index.php?parent=home&" + q
            st, t = http(url, hdr)
            d = js(t)
            secs = names(d)
            status = (d or {}).get("status")
            code = ((d or {}).get("data") or {}).get("code") if isinstance((d or {}).get("data"), dict) else ""
            say("%-9s %-8s %s status=%s code=%s sections=%s" % (
                pname, host, st, status, code, [s[0] for s in secs]))
            if secs and pname in ("website", "samsung", "lg", "vizio", "xbox") and host != "rapi+tok":
                save("index_%s_%s" % (pname, host), url, st, t)
            for nm, u, ft in secs:
                if LIVE_WORDS.search(nm):
                    candidates.append((pname, host, hdr, nm, u, q))
            if secs:
                break  # one working host per platform is enough

    say("\n== Live-looking sections ==")
    seen = set()
    for pname, host, hdr, nm, u, q in candidates:
        root = api if host == "api" else "https://rapi.ifood.tv/"
        url = root + "sub-categories.php?parent=%s&%s" % (urllib.parse.quote(nm), q)
        if (nm, host) in seen and pname not in ("samsung", "vizio"):
            continue
        seen.add((nm, host))
        st, t = http(url, hdr)
        d = js(t)
        rows = names(d)
        f = save("sec_%s_%s_%s" % (pname, host, nm), url, st, t)
        say("%-9s %-8s section %-12s rows=%s -> %s" % (pname, host, nm, [r[0] for r in rows][:12], f))
        for rn, ru, rft in rows[:4]:
            if not ru or "springserve" in ru:
                continue
            if host == "api" and "rapi.ifood.tv" in ru:
                sp = urllib.parse.urlsplit(ru)
                ru = api + sp.path.rsplit("/", 1)[-1] + "?" + sp.query
            st, t = http(ru, hdr)
            d = js(t)
            found = []
            keys_like_live(d, found)
            res = (d or {}).get("results") or []
            sample = res[0] if isinstance(res, list) and res and isinstance(res[0], dict) else {}
            f = save("row_%s_%s_%s" % (pname, nm, rn), ru, st, t)
            say("    row %-30s %s results=%s live-keys=%s sample=%s -> %s" % (
                rn[:30], st, len(res) if isinstance(res, list) else "?", found[:6],
                {k: str(sample.get(k))[:70] for k in ("title", "type", "feed_type", "video_hls_url", "video_url", "is_live", "epg")}, f))
    with open(os.path.join(out_dir, "live_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
