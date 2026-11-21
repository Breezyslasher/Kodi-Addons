#!/usr/bin/env python3
"""Public recon of fawesome.tv (kickoff step zero: find the content API).

Usage:
    python3 recon.py OUT_DIR

Two passes, both signed out (Fawesome has no accounts at all):

1. Crawl: fetch the landing pages, robots/sitemaps and the JavaScript bundles
   they load; report the hosts, API-looking paths, keys and keywords found.
2. Probe: fetch every URL listed in probe_urls.txt (next to this script) and
   save the response bodies, so a lead from pass 1 can be followed up by
   adding a line there and pushing.

Everything lands in OUT_DIR: report.txt plus one file per probed body. The
workflow commits OUT_DIR back to the branch, because the dev container's
network cannot reach fawesome.tv and GitHub's runners can.

Standard library only.
"""

import collections
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
START = [
    "https://fawesome.tv/",
    "https://www.fawesome.tv/",
    "https://fawesome.tv/robots.txt",
    "https://fawesome.tv/sitemap.xml",
    "https://fawesome.tv/movies",
    "https://fawesome.tv/tv-shows",
]
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
MAX_SCRIPTS = 60
MAX_BYTES = 12 * 1024 * 1024

SCRIPT_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)
CHUNK_RE = re.compile(r"""["']((?:/_next|/_nuxt|/assets|/static|/js)/[^"'\s]+?\.js)["']""")
URL_RE = re.compile(r"""https?://[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?(?:/[^\s"'`<>\\)]*)?""", re.I)
API_HINT_RE = re.compile(
    r"""["'`](/(?:api|v\d|feed|feeds|catalog|content|home|rows?|category|categories|"""
    r"""genres?|movies?|shows?|series|episodes?|search|play|playback|stream|video|"""
    r"""vod|channels?|epg|config|ads?|vmap|vast)[^"'`\s]{0,100})["'`]""", re.I)
# Things that look like client keys / app ids baked into a bundle.
KEY_RE = re.compile(
    r"""((?:api[_-]?key|apikey|app[_-]?id|appid|app[_-]?key|brand[_-]?id|client[_-]?id|"""
    r"""x-api-key|secret|token|partner[_-]?id|channel[_-]?id)["']?\s*[:=]\s*["'][^"']{3,80}["'])""",
    re.I)
KEYWORDS = ["widevine", "fairplay", "playready", "drm", "license", "m3u8", ".mpd",
            "hls.js", "shaka", "video.js", "videojs", "jwplayer", "bitmovin",
            "theoplayer", "vmap", "vast", "ima3", "imasdk", "freewheel", "spotx",
            "springserve", "ssai", "yospace", "mediatailor", "__NEXT_DATA__",
            "nuxt", "react", "angular", "vue", "graphql", "futuretoday",
            "future today", "mrss", "roku", "device_id", "deviceid", "uuid"]


def fetch(url, headers=None):
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(MAX_BYTES)
            status, final, rh = resp.status, resp.geturl(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_BYTES) if exc.fp else b""
        status, final, rh = exc.code, url, dict(exc.headers or {})
    except Exception as exc:  # noqa: BLE001  recon: report, don't stop
        return None, url, {}, "ERROR %s" % exc
    enc = (rh.get("Content-Encoding") or rh.get("content-encoding") or "").lower()
    try:
        if enc == "gzip":
            body = gzip.decompress(body)
        elif enc == "deflate":
            body = zlib.decompress(body)
    except Exception:  # noqa: BLE001
        pass
    return status, final, rh, body.decode("utf-8", "replace")


def safe_name(url, n):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])[:120]
    return "%03d_%s.txt" % (n, s)


def crawl(out):
    hosts = collections.Counter()
    api_paths = collections.Counter()
    keys = collections.Counter()
    keywords = collections.Counter()
    urls = collections.Counter()
    seen, queue = set(), list(START)
    scripts = 0
    out.append("== Pages and scripts fetched ==")
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        is_script = url.split("?", 1)[0].endswith(".js")
        if is_script:
            scripts += 1
            if scripts > MAX_SCRIPTS:
                continue
        status, final, headers, text = fetch(url)
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
        out.append("%s  %s  %d bytes  %s" % (status, final, len(text), ctype))
        if not text or text.startswith("ERROR"):
            if text:
                out.append("    " + text)
            continue
        low = text.lower()
        for m in URL_RE.findall(text):
            hosts[urllib.parse.urlsplit(m).hostname or ""] += 1
            if not m.endswith((".png", ".jpg", ".svg", ".woff", ".woff2", ".css", ".webp")):
                urls[m[:200]] += 1
        for m in API_HINT_RE.findall(text):
            api_paths[m] += 1
        for m in KEY_RE.findall(text):
            keys[m] += 1
        for k in KEYWORDS:
            n = low.count(k.lower())
            if n:
                keywords[k] += n
        if not is_script:
            for src in SCRIPT_RE.findall(text):
                queue.append(urllib.parse.urljoin(final, src))
            # Sitemaps: follow nested sitemaps, report a sample of page URLs.
            if "<urlset" in text or "<sitemapindex" in text:
                locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
                out.append("    sitemap: %d <loc> entries; first 15:" % len(locs))
                out.extend("      " + l for l in locs[:15])
                for l in locs:
                    if l.endswith(".xml") and len(seen) < 40:
                        queue.append(l)
            # Inline JSON state blobs (Next/Nuxt/etc.).
            for m in re.finditer(r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
                                 text, re.S):
                out.append("    __NEXT_DATA__ (%d bytes): %s" % (len(m.group(1)), m.group(1)[:3000]))
            inline = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", text, re.S)
            for blob in inline:
                if len(blob) > 40:
                    out.append("    inline script (%d bytes): %s" % (len(blob), blob[:800].replace("\n", " ")))
            if not is_script and len(text) < 200000 and final.rstrip("/").count("/") <= 3:
                out.append("    --- page head ---")
                out.append("    " + text[:2500].replace("\n", "\n    "))
        for chunk in CHUNK_RE.findall(text):
            queue.append(urllib.parse.urljoin(final, chunk))

    out.append("\n== Hosts referenced (top 80) ==")
    out.extend("%6d  %s" % (n, h) for h, n in hosts.most_common(80))
    out.append("\n== Absolute URLs referenced (non-asset, top 150) ==")
    out.extend("%6d  %s" % (n, u) for u, n in urls.most_common(150))
    out.append("\n== API-looking paths (top 120) ==")
    out.extend("%6d  %s" % (n, p) for p, n in api_paths.most_common(120))
    out.append("\n== Key / id assignments (top 60) ==")
    out.extend("%6d  %s" % (n, k) for k, n in keys.most_common(60))
    out.append("\n== Keywords ==")
    out.extend("%6d  %s" % (n, k) for k, n in keywords.most_common())


def probe(out, out_dir):
    path = os.path.join(HERE, "probe_urls.txt")
    if not os.path.exists(path):
        return
    out.append("\n== Probes (probe_urls.txt) ==")
    n = 0
    with open(path, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    for line in lines:
        # Optional request headers: URL  |  Header: value  |  Header: value
        parts = [p.strip() for p in line.split(" | ")]
        url, hdrs = parts[0], {}
        for p in parts[1:]:
            if ":" in p:
                k, v = p.split(":", 1)
                hdrs[k.strip()] = v.strip()
        n += 1
        status, final, headers, text = fetch(url, hdrs)
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
        name = safe_name(url, n)
        body = text
        if "json" in ctype or text[:1] in "[{":
            try:
                body = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
            except ValueError:
                pass
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write("# %s %s\n# final: %s\n# %s\n" % (status, url, final,
                     json.dumps({k: v for k, v in headers.items()
                                 if k.lower() not in ("set-cookie",)})))
            fh.write(body[:3 * 1024 * 1024])
        out.append("%s  %-9s %7d bytes  %s  -> %s" % (status, ctype.split(";")[0][:9],
                                                     len(text), url, name))


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "recon-out"
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    out = []
    crawl(out)
    probe(out, out_dir)
    report = "\n".join(out) + "\n"
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report[:60000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
