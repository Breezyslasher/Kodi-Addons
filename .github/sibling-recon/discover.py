#!/usr/bin/env python3
"""Confirm HappyKids and iFood.tv end to end on the shared Future Today API.

Usage:
    python3 discover.py

Findings so far (runs 1-4): one backend, the service chosen by siteId; the API
answers an empty body without the site's Referer/Origin. HappyKids is siteId 40
on happykids.tv/home/new/<build>/api/. iFood.tv's player picks siteId from the
hostname (ifood -> 1) and loads assets/data/config_<siteId>.json for the
platform id. This pass reads that config, then for each service walks
token -> home -> section rows -> a row -> a title's detail -> its HLS manifest
(checking for EXT-X-KEY), and a search. Read-only. Stdlib only.
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
REF = {"v": ""}


def http(url, headers=None):
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


def lst(v):
    if isinstance(v, dict):
        return list(v.values())
    return v if isinstance(v, list) else []


def site(label, page):
    print("\n################ %s ################" % label)
    origin = re.search(r"https?://[^/]+", page).group(0)
    REF["v"] = origin
    st, final, home = http(page)
    mb = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', home)
    bundle_url = urllib.parse.urljoin(final, mb.group(1))
    _, _, bundle = http(bundle_url)
    build = re.search(r'buildVersion:"(v\d+)"', bundle).group(1)
    assets = "%s/home/new/%s/" % (origin, build)
    api = assets + "api/"
    print("page %s -> build %s, api %s" % (final, build, api))

    # Hostname -> siteId map + config_<id>.json, as the player does.
    m = re.search(r"const e=window\.location\.hostname;let t=1;(.{0,400}?)loadAppConfig", bundle)
    if m:
        print("  hostname map:", m.group(1)[:300])
    return origin, assets, api


def walk(label, api, app_id, site_id, pid, search_term):
    common = "appId=%s&siteId=%s&auth-token=%s&platform_id=%s&deviceId=%s&apiEnv=production" % (
        app_id, site_id, pid, pid, DEV)
    print("  ids appId=%s siteId=%s auth-token=%s" % (app_id, site_id, pid))
    tok = (js(http(api + "getSecurityToken.php?" + common)[2]) or {}).get("securityToken")
    print("  token:", bool(tok))
    if not tok:
        return
    H = {"token": tok}

    def get(path):
        if path.startswith("http"):
            sp = urllib.parse.urlsplit(path)
            path = sp.path.rsplit("/", 1)[-1] + "?" + sp.query
        url = api + path + ("&" if "?" in path else "?") + common
        return js(http(url, H)[2]) or {}

    d = get("index.php?parent=home")
    secs = [c for c in lst(d.get("channels")) if isinstance(c, dict)]
    print("  sections:", [(c.get("name"), c.get("feed_type")) for c in secs])
    first_video = None
    for sec in secs[:3]:
        rows = [c for c in lst(get("sub-categories.php?parent=%s" % urllib.parse.quote(sec["name"])).get("channels"))
                if isinstance(c, dict)]
        print("  section %-12s rows=%d e.g. %s" % (sec["name"], len(rows), [r.get("name") for r in rows[:5]]))
        for row in rows[:4]:
            u = row.get("url") or ""
            if not re.search(r"recipes\.php|shows\.php", u):
                continue
            fd = get(u)
            res = [r for r in lst(fd.get("results")) if isinstance(r, dict)]
            vids = [r for r in res if r.get("video_hls_url") or r.get("video_url")]
            print("    row %-30s results=%d videos=%d count=%s" % (row.get("name"), len(res), len(vids), fd.get("count")))
            if vids and not first_video:
                first_video = vids[0]
        if first_video:
            break
    if first_video:
        v = first_video
        print("  sample title:", {k: str(v.get(k))[:80] for k in (
            "node_id", "title", "type", "video_format", "drm", "video_hls_url", "video_url",
            "runtime", "content_genre", "cc_path", "series_name", "season", "episode")})
        det = get("recipes.php?searchType=nodeid&nid=%s&gridstyle=flat-movie&max-results=1&dltype=1&start-index=0"
                  % v.get("node_id"))
        dv = (lst(det.get("results")) or [v])[0]
        hls = dv.get("video_hls_url") or ""
        if hls.startswith("http"):
            st, _, body = http(hls, {"Accept": "*/*"})
            print("  manifest HTTP %s head=%r" % (st, body[:24]))
            var = re.search(r"^(?!#)(\S+\.m3u8\S*)$", body, re.M)
            if var:
                vurl = urllib.parse.urljoin(hls, var.group(1))
                st2, _, vb = http(vurl, {"Accept": "*/*"})
                print("  variant HTTP %s EXT-X-KEY=%s segments=%d" % (
                    st2, "EXT-X-KEY" in vb, vb.count("#EXTINF")))
        else:
            print("  no video_hls_url; video_url=%s fmt=%s drm=%s" % (
                str(dv.get("video_url"))[:80], dv.get("video_format"), dv.get("drm")))
    sr = get("recipes.php?searchType=search&keys=%s&start-index=0" % search_term)
    print("  search '%s': status=%s count=%s first=%s" % (
        search_term, sr.get("status"), sr.get("count"),
        [r.get("title") for r in lst(sr.get("results"))[:4] if isinstance(r, dict)]))
    queue = get("getQueuelist.php")
    print("  queue (My List) endpoint: status=%s" % queue.get("status"))


def dump_happykids():
    """Print every non-empty field of a few HappyKids titles (search + a season)."""
    origin, assets, api = site("HappyKids items", "https://happykids.tv/home/")
    common = "appId=9&siteId=40&auth-token=1217575&platform_id=1217575&deviceId=%s&apiEnv=production" % DEV
    tok = (js(http(api + "getSecurityToken.php?" + common)[2]) or {}).get("securityToken")
    H = {"token": tok}
    sr = js(http(api + "recipes.php?searchType=search&keys=blippi&start-index=0&" + common, H)[2]) or {}
    res = [r for r in lst(sr.get("results")) if isinstance(r, dict)]
    print("  search results:", len(res), "types:", sorted({str(r.get("type")) for r in res}))
    for it in res[:3]:
        print("  --- item %s" % it.get("node_id"))
        for k in sorted(it):
            v = it[k]
            if v not in (None, "", [], {}, "None", "0", 0) and not k.startswith(("meta_", "description")):
                print("     %-24s %s" % (k, str(v)[:150]))
        nid = it.get("node_id")
        if nid:
            d = js(http(api + "recipes.php?searchType=nodeid&nid=%s&gridstyle=flat-movie&max-results=1&dltype=1&start-index=0&%s"
                        % (nid, common), H)[2]) or {}
            dv = (lst(d.get("results")) or [{}])[0]
            print("     detail stream fields:", {k: str(dv.get(k))[:150] for k in dv
                  if re.search(r"video|url|stream|youtube|yt|hls|mp4|m3u8|embed|source|play", k, re.I)
                  and dv.get(k) not in (None, "", "None")})
    # The web player's play path for such items.
    _, _, home = http("https://happykids.tv/home/")
    mb = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', home)
    _, _, bundle = http(urllib.parse.urljoin("https://happykids.tv/home/", mb.group(1)))
    for needle in ("youtube", "ytId", "youtube_id", "isLicencedForWeb", "video_flv_url"):
        i = bundle.find(needle)
        print("  bundle %-16s %s" % (needle, " ".join(bundle[max(0, i-150):i+250].split()) if i >= 0 else "not found"))


def main():
    dump_happykids()
    return
    # HappyKids: siteId 40 (confirmed run 4).
    origin, assets, api = site("HappyKids", "https://happykids.tv/home/")
    walk("HappyKids", api, "9", "40", "1217575", "peppa")

    # iFood.tv: siteId from hostname (ifood -> 1), platform from config_1.json.
    origin, assets, api = site("iFood.tv", "https://ifood.tv/")
    st, _, cfg_txt = http(assets + "assets/data/config_1.json")
    cfg = js(cfg_txt) or {}
    print("  config_1.json HTTP %s keys=%s" % (st, sorted(cfg)[:40]))
    print("  config:", {k: cfg.get(k) for k in ("site_id", "platform_id", "app_id", "appId", "site_name",
                                                  "siteName", "name", "title", "logo", "apiEnv") if k in cfg})
    pid = str(cfg.get("platform_id") or "1217575")
    sid = str(cfg.get("site_id") or "1")
    aid = str(cfg.get("app_id") or cfg.get("appId") or "9")
    walk("iFood.tv", api, aid, sid, pid, "chicken")
    print("\nDONE")


if __name__ == "__main__":
    main()
