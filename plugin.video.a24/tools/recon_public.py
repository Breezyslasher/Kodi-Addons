#!/usr/bin/env python3
"""Public, signed-out recon of the A24 Screening Room (docs/recon.md row 0).

Usage:
    python3 recon_public.py

Fetches the public landing pages and the JavaScript bundles they load, and
reports which video platform / DRM vendor names and which API-looking hosts
appear in them. No account, no credentials, nothing beyond what any browser
loads before signing in. A lead for row 0, not proof: the Play HAR confirms.

Standard library only (runs in CI, where the dev container's proxy does not
apply).
"""

import collections
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from har_summary import BACKENDS  # noqa: E402  same fingerprints as HAR recon

START = [
    "https://screeningroom.a24films.com/",
    "https://aaa24.a24films.com/",
    "https://aaa24.a24films.com/page/all-access-faqs/",
]
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0")
MAX_SCRIPTS = 40
MAX_BYTES = 8 * 1024 * 1024

SCRIPT_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)
# Next.js / Nuxt / Vite chunk names referenced from inside bundles.
CHUNK_RE = re.compile(r"""["']((?:/_next|/_nuxt|/assets|/static)/[^"'\s]+?\.js)["']""")
URL_RE = re.compile(r"""https?://[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s"'`<>\\)]*)?""", re.I)
API_HINT_RE = re.compile(r"""["'`](/(?:api|v\d|graphql|auth|oauth|playback|play|license|licence|drm|entitlements?|rentals?|library|me|user|session)[^"'`\s]{0,80})["'`]""", re.I)
KEYWORDS = ["widevine", "fairplay", "playready", "drm", "license", "licence",
            "hls", "m3u8", "dash", "mpd", "shaka", "hls.js", "video.js", "videojs",
            "bitmovin", "theoplayer", "watermark", "nexguard", "jwt", "graphql",
            "apollo", "next.js", "__NEXT_DATA__", "nuxt", "stripe", "shopify",
            "auth0", "cognito", "firebase", "okta", "clerk", "supabase"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(MAX_BYTES)
            return resp.status, resp.geturl(), dict(resp.headers), \
                body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, url, dict(exc.headers or {}), ""
    except Exception as exc:
        return None, url, {}, "ERROR %s" % exc


def main():
    vendors = collections.defaultdict(set)
    hosts = collections.Counter()
    api_paths = collections.Counter()
    keywords = collections.Counter()
    seen, queue = set(), list(START)
    scripts = 0
    print("== Pages and scripts fetched ==")
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
        server = headers.get("Server") or headers.get("server") or ""
        powered = headers.get("X-Powered-By") or headers.get("x-powered-by") or ""
        print("%s  %s  %d bytes  %s %s" % (status, final, len(text), server, powered))
        if not text:
            continue
        low = text.lower()
        for vendor, needles in BACKENDS:
            for n in needles:
                if n in low:
                    vendors[vendor].add("%s (in %s)" % (n, urllib.parse.urlsplit(final).path or "/"))
        for m in URL_RE.findall(text):
            hosts[urllib.parse.urlsplit(m).hostname or ""] += 1
        for m in API_HINT_RE.findall(text):
            api_paths[m] += 1
        for k in KEYWORDS:
            n = low.count(k.lower())
            if n:
                keywords[k] += n
        if not is_script:
            for src in SCRIPT_RE.findall(text):
                queue.append(urllib.parse.urljoin(final, src))
        for chunk in CHUNK_RE.findall(text):
            queue.append(urllib.parse.urljoin(final, chunk))

    print("\n== Video platform / DRM fingerprints (row 0 leads) ==")
    if not vendors:
        print("none found in public pages (bespoke backend, or loaded only after sign-in)")
    for vendor, where in sorted(vendors.items()):
        print("%s: %s" % (vendor, ", ".join(sorted(where)[:6])))
    print("\n== Hosts referenced (top 60) ==")
    for host, n in hosts.most_common(60):
        print("%6d  %s" % (n, host))
    print("\n== API-looking paths (top 60) ==")
    for path, n in api_paths.most_common(60):
        print("%6d  %s" % (n, path))
    print("\n== Keywords ==")
    for k, n in keywords.most_common():
        print("%6d  %s" % (n, k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
