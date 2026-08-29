#!/usr/bin/env python3
"""Settle whether YouTube TV's DASH manifest is usable by InputStream Adaptive.

Five browser captures established the shape of the YouTube TV private API
(see docs/youtube-tv-protocol.md), but they cannot answer the one question a
Kodi addon depends on. Every `player` response offers a `dashManifestUrl`
alongside a `serverAbrStreamingUrl`, and the web player always takes the SABR
path -- so no HAR will ever show the DASH manifest being fetched. It has to be
requested directly, which is what this script does.

    python3 youtube_tv_check_dash.py cookies.txt
    python3 youtube_tv_check_dash.py cookies.txt --video-id z0sfuXTVx8g

With no --video-id it reads the EPG, picks the first station's current airing
and tests that.

Export cookies.txt for tv.youtube.com from a signed-in browser, in Netscape
format ("Get cookies.txt" or similar). The file is read, never written or
transmitted anywhere except to Google.

Exit status is 0 only if the manifest serves a DASH MPD whose segment URLs are
ordinary `videoplayback` requests -- the condition under which an addon is
worth building.
"""

import argparse
import hashlib
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ORIGIN = "https://tv.youtube.com"
CLIENT_VERSION = "1.20260825.04.00"
# Bumps whenever YouTube ships a new player. A stale value is the first thing
# to suspect if `player` starts returning LOGIN_REQUIRED against good cookies.
SIGNATURE_TIMESTAMP = 20689
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:154.0) "
      "Gecko/20100101 Firefox/154.0")


def load_cookies(path):
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(path, ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        sys.exit("could not read %s as a Netscape cookies.txt: %s" % (path, exc))
    cookies = {c.name: c.value for c in jar}
    missing = [n for n in ("SAPISID", "SID") if n not in cookies]
    if missing:
        sys.exit("cookies.txt is missing %s -- export it from a signed-in "
                 "tv.youtube.com session, with all domains included"
                 % " and ".join(missing))
    return cookies


def sapisid_hash(sapisid, origin=ORIGIN):
    """Google's SAPISIDHASH: SHA1 over "<ts> <SAPISID> <origin>"."""
    ts = int(time.time())
    digest = hashlib.sha1(("%d %s %s" % (ts, sapisid, origin)).encode()).hexdigest()
    return "%d_%s" % (ts, digest)


def auth_header(cookies):
    sapisid = cookies["SAPISID"]
    # The 1P/3P variants hash the same way off their own cookies; where those
    # are absent Google accepts the plain SAPISID value for all three.
    plain = sapisid_hash(sapisid)
    one_p = sapisid_hash(cookies.get("__Secure-1PAPISID", sapisid))
    three_p = sapisid_hash(cookies.get("__Secure-3PAPISID", sapisid))
    return ("SAPISIDHASH %s SAPISID1PHASH %s SAPISID3PHASH %s"
            % (plain, one_p, three_p))


def innertube(path, body, cookies, timeout=30):
    url = "%s/youtubei/v1/%s" % (ORIGIN, path)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    req.add_header("Origin", ORIGIN)
    req.add_header("Referer", ORIGIN + "/")
    req.add_header("X-Origin", ORIGIN)
    req.add_header("X-YouTube-Client-Name", "41")          # WEB_UNPLUGGED
    req.add_header("X-YouTube-Client-Version", CLIENT_VERSION)
    req.add_header("X-Goog-AuthUser", "0")
    req.add_header("Authorization", auth_header(cookies))
    req.add_header("Cookie", "; ".join("%s=%s" % kv for kv in cookies.items()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        sys.exit("%s returned HTTP %s\n%s"
                 % (path, exc.code, exc.read()[:600].decode(errors="replace")))
    except urllib.error.URLError as exc:
        sys.exit("could not reach %s: %s" % (url, exc.reason))


def context():
    return {
        "client": {
            "hl": "en",
            "gl": "US",
            "clientName": "WEB_UNPLUGGED",
            "clientVersion": CLIENT_VERSION,
            "platform": "DESKTOP",
            "userAgent": UA,
            "unpluggedAppInfo": {"filterModeType": "UNPLUGGED_FILTER_MODE_TYPE_NONE"},
        }
    }


def walk(node, key):
    """Yield every value stored under `key`, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, key)


def first_live_video_id(cookies):
    print("reading the guide (browse FEunplugged_epg) ...")
    now_ms = int(time.time() * 1000)
    epg = innertube("browse?alt=json", {
        "context": context(),
        "browseId": "FEunplugged_epg",
        "unpluggedBrowseOptions": {"epgOptions": {
            "maxAiringsPerStation": 2,
            "initialEpgFetchStartTimeMs": str(now_ms),
            "initialEpgFetchDurationMs": 3600000,
            "paginationDurationMs": 3600000,
            "maxDurationMs": "604800000",
        }},
    }, cookies)

    stations = list(walk(epg, "epgStationRenderer"))
    airings = list(walk(epg, "epgAiringRenderer"))
    print("  %d stations, %d airings" % (len(stations), len(airings)))
    if not stations:
        sys.exit("the guide came back empty -- is this account subscribed, and "
                 "are the cookies from a signed-in session?")

    for airing in airings:
        for endpoint in walk(airing, "watchEndpoint"):
            if endpoint.get("videoId"):
                name = "?"
                if stations:
                    runs = stations[0].get("name", {}).get("runs", [])
                    name = runs[0]["text"] if runs else "?"
                print("  testing against %s -> %s" % (name, endpoint["videoId"]))
                return endpoint["videoId"]
    sys.exit("no airing in the guide carried a watchEndpoint videoId")


def get_player(video_id, cookies):
    print("calling player for %s ..." % video_id)
    resp = innertube("player?prettyPrint=false", {
        "context": context(),
        "videoId": video_id,
        "playbackContext": {
            "contentPlaybackContext": {
                "html5Preference": "HTML5_PREF_WANTS",
                "signatureTimestamp": SIGNATURE_TIMESTAMP,
                "referer": "%s/watch/%s" % (ORIGIN, video_id),
            },
            "devicePlaybackCapabilities": {"supportsVp9Encoding": True,
                                           "supportXhr": True},
        },
        "cpn": hashlib.sha1(str(time.time()).encode()).hexdigest()[:16],
        "racyCheckOk": True,
        "captionParams": {},
    }, cookies)

    status = resp.get("playabilityStatus", {})
    if status.get("status") != "OK":
        sys.exit("playabilityStatus is %s: %s"
                 % (status.get("status"), status.get("reason")))
    return resp


def fetch_manifest(url, cookies):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Origin", ORIGIN)
    req.add_header("Referer", ORIGIN + "/")
    req.add_header("Cookie", "; ".join("%s=%s" % kv for kv in cookies.items()))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cookies", help="Netscape cookies.txt from a signed-in session")
    ap.add_argument("--video-id", help="test this video id instead of the guide's first airing")
    ap.add_argument("--save-mpd", metavar="PATH", help="write the manifest out for inspection")
    args = ap.parse_args()

    cookies = load_cookies(args.cookies)
    video_id = args.video_id or first_live_video_id(cookies)
    player = get_player(video_id, cookies)

    streaming = player.get("streamingData", {})
    details = player.get("videoDetails", {})
    print("  %s -- %s (live=%s)" % (details.get("title"), details.get("author"),
                                    details.get("isLive")))
    print("  authorized track types: %s"
          % streaming.get("initialAuthorizedDrmTrackTypes"))
    print("  license families: %s"
          % [l.get("drmFamily") for l in streaming.get("licenseInfos", [])])
    print("  serverAbrStreamingUrl offered: %s"
          % bool(streaming.get("serverAbrStreamingUrl")))

    manifest_url = streaming.get("dashManifestUrl")
    if not manifest_url:
        print("\nFAIL: no dashManifestUrl in this player response. SABR only.")
        return 1

    print("\nfetching dashManifestUrl ...")
    try:
        status, body = fetch_manifest(manifest_url, cookies)
    except urllib.error.HTTPError as exc:
        print("FAIL: manifest returned HTTP %s" % exc.code)
        print(exc.read()[:600].decode(errors="replace"))
        return 1
    except Exception as exc:
        print("FAIL: could not fetch the manifest: %s" % exc)
        return 1

    print("  HTTP %s, %d bytes" % (status, len(body)))
    if args.save_mpd:
        with open(args.save_mpd, "wb") as fh:
            fh.write(body)
        print("  written to %s" % args.save_mpd)

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        print("FAIL: response is not parseable XML: %s" % exc)
        print(body[:400])
        return 1

    if not root.tag.endswith("MPD"):
        print("FAIL: root element is <%s>, expected <MPD>" % root.tag)
        return 1

    ns = {"m": "urn:mpeg:dash:schema:mpd:2011"}
    reps = root.findall(".//m:Representation", ns)
    protections = root.findall(".//m:ContentProtection", ns)
    templates = root.findall(".//m:SegmentTemplate", ns)
    print("  MPD type=%s, %d representations, %d ContentProtection, %d SegmentTemplate"
          % (root.get("type"), len(reps), len(protections), len(templates)))

    schemes = {p.get("schemeIdUri", "").lower() for p in protections}
    if any("edef8ba9" in s for s in schemes):
        print("  Widevine ContentProtection present")

    urls = []
    for tmpl in templates:
        for attr in ("media", "initialization"):
            if tmpl.get(attr):
                urls.append(tmpl.get(attr))
    for base in root.findall(".//m:BaseURL", ns):
        if base.text:
            urls.append(base.text.strip())

    if not urls:
        print("\nFAIL: no SegmentTemplate or BaseURL -- nothing for ISA to fetch.")
        return 1

    print("  sample segment URL: %s" % urls[0][:160])
    if any(re.search(r"/videoplayback", u) for u in urls):
        print("\nPASS: the manifest serves and its segments are ordinary "
              "videoplayback URLs.")
        print("InputStream Adaptive can consume this. Remaining work is the "
              "Widevine license proxy (rotating keys) -- build the addon.")
        return 0

    print("\nINCONCLUSIVE: the manifest parsed but its segment URLs do not look "
          "like plain videoplayback requests:")
    for u in urls[:3]:
        print("  %s" % u[:200])
    print("Inspect the saved MPD before committing to a build.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
