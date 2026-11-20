#!/usr/bin/env python3
"""One line per request in a HAR: the first pass of recon.

Usage:
    python3 har_summary.py capture.har [--hosts | --backend]

Prints  #  METHOD STATUS  MIME  host/path  [hints]  for every non-asset
request, in order. Hints flag the rows recon cares about (docs/recon.md):
token-ish responses, manifests, licence-looking POSTs, subtitle files.
--hosts prints a request count per host instead, to see which hosts are API,
CDN and licence before narrowing sanitize_har.py with --host.
--backend answers the M0 gate (docs/recon.md row 0): which video platform the
A24 Screening Room runs on, from the hosts and URL paths it talks to.

Works on raw or sanitized HARs; prints no header or body values.
"""

import base64
import collections
import json
import re
import sys

ASSET_RE = re.compile(r"\.(js|css|png|jpe?g|webp|gif|svg|ico|woff2?|ttf|m4s|"
                      r"mp4|ts|cmfv|cmfa|aac)$", re.I)


# Video-platform fingerprints: (vendor, host or URL substrings). A match is a
# lead for docs/recon.md row 0, not proof -- confirm the manifest and licence
# rows come from the same vendor.
BACKENDS = [
    ("Vimeo OTT (VHX)", ("vhx.tv", "vhx.com", "vimeocdn.com", "player.vimeo.com",
                         "vimeo.com/api")),
    ("Brightcove", ("brightcove", "bcove", "boltdns.net")),
    ("JW Player", ("jwplayer", "jwpcdn.com", "jwpsrv.com", "jwplatform.com")),
    ("Mux", ("mux.com", "litix.io")),
    ("Kaltura", ("kaltura",)),
    ("Bitmovin", ("bitmovin",)),
    ("Cloudflare Stream", ("videodelivery.net", "cloudflarestream.com")),
    ("AWS MediaPackage / MediaTailor", ("mediapackage", "mediatailor")),
    ("Axinom DRM", ("axinom",)),
    ("EZDRM", ("ezdrm",)),
    ("BuyDRM KeyOS", ("keyos",)),
    ("DRMtoday (castLabs)", ("drmtoday", "castlabs")),
    ("PallyCon / DoveRunner", ("pallycon", "doverunner")),
    ("Irdeto / Verimatrix", ("irdeto", "verimatrix")),
    ("NAGRA / NexGuard (forensic watermark)", ("nagra", "nexguard")),
    ("Google Widevine cloud licence", ("widevine.com", "proxy.uat.widevine")),
]


def backends(entries):
    """{vendor: Counter(host)} for every request that matches a fingerprint,
    plus the a24films.com hosts (a bespoke backend shows only those)."""
    found = collections.defaultdict(collections.Counter)
    for e in entries:
        url = e["request"].get("url", "")
        if not url.startswith("http"):
            continue
        host = url.split("/")[2].lower()
        low = url.lower().split("?", 1)[0]
        for vendor, needles in BACKENDS:
            if any(n in low for n in needles):
                found[vendor][host] += 1
        if host.endswith("a24films.com"):
            found["A24 own hosts (a24films.com)"][host] += 1
    return found


def hints(entry):
    req, resp = entry["request"], entry.get("response", {})
    url = req.get("url", "").lower()
    path = url.split("?", 1)[0]
    mime = ((resp.get("content") or {}).get("mimeType") or "").lower()
    content = resp.get("content") or {}
    body = content.get("text") or ""
    if body and content.get("encoding") == "base64":
        body = base64.b64decode(body + "=" * (-len(body) % 4)).decode(
            "utf-8", "replace")
    body = body[:20000].lower()
    reqmime = ((req.get("postData") or {}).get("mimeType") or "").lower()
    out = []
    if path.endswith(".mpd") or "dash+xml" in mime:
        out.append("MANIFEST-DASH")
    if path.endswith(".m3u8") or "mpegurl" in mime:
        out.append("MANIFEST-HLS")
    if req.get("method") == "POST" and ("octet-stream" in reqmime or
                                        "widevine" in url or "license" in url
                                        or "licence" in url or "drm" in url):
        out.append("LICENSE?")
    if any(k in body for k in ('"access_token"', '"refresh_token"',
                               '"id_token"', '"expires_in"')):
        out.append("TOKEN")
    if "grant_type" in ((req.get("postData") or {}).get("text") or "").lower():
        out.append("GRANT")
    if path.endswith((".vtt", ".ttml", ".dfxp", ".srt")) or "ttml" in mime:
        out.append("SUBS")
    if any(k in body for k in (".mpd", ".m3u8")) and "json" in mime:
        out.append("PLAYBACK-INFO?")
    names = {h.get("name", "").lower() for h in req.get("headers", [])}
    if "authorization" in names:
        out.append("auth-hdr")
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    with open(argv[0], "r", encoding="utf-8") as fh:
        entries = json.load(fh).get("log", {}).get("entries", [])
    if "--backend" in argv:
        found = backends(entries)
        if not found:
            print("No known video platform or a24films.com host in this HAR.")
        for vendor, hosts in sorted(found.items(), key=lambda kv: -sum(kv[1].values())):
            print("%s" % vendor)
            for host, n in hosts.most_common():
                print("  %6d  %s" % (n, host))
        return 0
    if "--hosts" in argv:
        counts = collections.Counter(
            e["request"]["url"].split("/")[2] for e in entries
            if e["request"].get("url", "").startswith("http"))
        for host, n in counts.most_common():
            print("%6d  %s" % (n, host))
        return 0
    for i, e in enumerate(entries):
        url = e["request"].get("url", "")
        if not url.startswith("http") or ASSET_RE.search(url.split("?", 1)[0]):
            continue
        mime = ((e.get("response", {}).get("content") or {}).get("mimeType")
                or "").split(";")[0]
        print("%4d  %-6s %3s  %-24s %s  %s" % (
            i, e["request"].get("method", ""),
            e.get("response", {}).get("status", ""), mime[:24],
            url.split("//", 1)[1].split("?", 1)[0][:110],
            " ".join(hints(e))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
