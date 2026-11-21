#!/usr/bin/env python3
"""Do any Fawesome titles carry more than one audio track?

Usage:
    python3 probe_audio.py OUT_DIR

Collects node ids from a wide spread of the catalogue -- the home/movies/shows
rows, the En Espanol section, and searches for foreign-language terms (the
titles most likely to be dubbed) -- resolves each title's HLS master manifest,
and reports how many carry an #EXT-X-MEDIA:TYPE=AUDIO rendition (i.e. a
selectable alternate audio track) versus a single muxed track. Writes
audio_report.txt and saves any multi-audio manifest in full.

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

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
DEV = str(uuid.uuid4())
IDS = "appId=9&siteId=236&auth-token=1217575&platform_id=1217575&version=sv9.0"
SEARCHES = ["amor", "la", "el", "noche", "vida", "le", "der", "guerra", "familia", "paris"]
MAX_MANIFESTS = 120


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


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("audio_"):
            os.remove(os.path.join(out_dir, f))
    _, html = http("https://fawesome.tv/")
    m = re.search(r"/home/new/(v\d+)/", html)
    api = "https://fawesome.tv/home/new/%s/api/" % (m.group(1) if m else "v469")
    common = "%s&deviceId=%s&apiEnv=production" % (IDS, DEV)
    token = (json.loads(http(api + "getSecurityToken.php?" + common)[1] or "{}") or {}).get("securityToken", "")
    hdr = {"token": token}

    def get(url):
        if not url.startswith("http"):
            url = api + url
        elif "rapi.ifood.tv" in url:
            sp = urllib.parse.urlsplit(url)
            url = api + sp.path.rsplit("/", 1)[-1] + "?" + sp.query
        url += ("&" if "?" in url else "?") + "deviceId=%s&apiEnv=production" % DEV
        try:
            return json.loads(http(url, hdr)[1] or "{}")
        except ValueError:
            return {}

    # Gather titles (node_id, hls, lang) from a wide spread.
    titles, seen = [], set()

    def collect(data):
        res = data.get("results") or []
        if isinstance(res, dict):
            res = list(res.values())
        for it in res:
            if isinstance(it, dict) and it.get("node_id") and it["node_id"] not in seen:
                hls = it.get("video_hls_url") or ""
                if hls.startswith("http"):
                    seen.add(it["node_id"])
                    titles.append((it["node_id"], hls, it.get("content_language_iso2") or "?",
                                   it.get("title") or ""))

    for parent in ("home", "movies", "shows"):
        for ch in (get("index.php?parent=%s&%s" % (parent, common)).get("channels") or []):
            pass
    # Rows under Movies / Shows / En Espanol.
    for parent in ("Movies", "Shows", "En%20Espa%C3%B1ol", "Collections"):
        for ch in (get("sub-categories.php?parent=%s&%s" % (parent, common)).get("channels") or [])[:8]:
            if ch.get("url"):
                collect(get(ch["url"]))
    for term in SEARCHES:
        collect(get("recipes.php?searchType=search&keys=%s&start-index=0&%s" % (term, common)))

    report = ["titles gathered: %d (checking up to %d manifests)" % (len(titles), MAX_MANIFESTS),
              "api base: %s" % api, ""]
    multi = single = errors = 0
    multi_rows = []
    for nid, hls, lang, title in titles[:MAX_MANIFESTS]:
        st, body = http(hls, {"Accept": "*/*"})
        if st != 200 or not body.startswith("#EXTM3U"):
            errors += 1
            continue
        audio_media = [ln for ln in body.splitlines() if ln.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in ln]
        if audio_media:
            multi += 1
            langs = re.findall(r'LANGUAGE="([^"]*)"', "\n".join(audio_media))
            multi_rows.append("MULTI  nid=%s lang=%s tracks=%d %s -- %s" % (
                nid, lang, len(audio_media), langs, title[:50]))
            with open(os.path.join(out_dir, "audio_multi_%s.m3u8" % nid), "w", encoding="utf-8") as fh:
                fh.write("# %s\n%s" % (hls, body[:20000]))
        else:
            single += 1
    report.append("single muxed audio: %d" % single)
    report.append("multiple audio tracks: %d" % multi)
    report.append("manifest errors/skipped: %d" % errors)
    report.append("")
    report += multi_rows or ["(no title with an alternate audio track was found)"]
    text = "\n".join(report) + "\n"
    with open(os.path.join(out_dir, "audio_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
