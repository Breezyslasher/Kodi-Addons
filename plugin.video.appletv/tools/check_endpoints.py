#!/usr/bin/env python3
"""Check that the private endpoints the Apple TV addon relies on still work.

Usage:
    python3 check_endpoints.py

Apple documents none of this, so the addon's weak point is not a bug of its
own but a rename on Apple's side: the tokens are read out of the page markup
by name, and everything else is built on them. This exercises that chain
against the live site, anonymously, and reports which link broke.

It cannot repair anything. A renamed key needs a person to find the new name.
What it buys is knowing within a day instead of whenever someone complains.

Exit status is 0 when every check passes and 1 when any fails, so a scheduled
job can fail loudly.
"""

import json
import re
import sys
import urllib.request

WEB_HOME = "https://tv.apple.com"
UTS_BASE = "https://tv.apple.com/api/uts/v3"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Kept in step with lib/api.py: these are what the addon itself sends.
UTS_VERSION = "96"
UTS_CLIENT_FLAGS = "OjAAAAEAAAAAAAIAEAAAACMAKwAtAA~~"
STOREFRONT = "143441"
LOCALE = "en-US"
APPLE_TV_PLUS_CHANNEL = "tvs.sbd.4000"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%-4s %-34s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def fetch(url, headers=None, timeout=30):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def main():
    # 1. The page shell still serves, and still carries the two tokens under
    #    the names the addon looks for. This is the single point of failure.
    try:
        status, html = fetch(WEB_HOME, {"User-Agent": BROWSER_UA,
                                        "Accept": "text/html"})
    except Exception as exc:
        record("page shell reachable", False, str(exc)[:70])
        return finish()
    record("page shell reachable", status == 200, "HTTP %s, %d bytes"
           % (status, len(html)))

    dev = re.search(r'"developerToken"\s*:\s*"([A-Za-z0-9_.\-]{20,})"', html)
    utsk = re.search(r'"utsk"\s*:\s*"([^"]+)"', html)
    record("developerToken in shell", bool(dev),
           "" if dev else "key renamed or no longer embedded")
    record("utsk in shell", bool(utsk),
           "" if utsk else "key renamed; /configurations is the fallback")
    if not dev:
        # Nothing below can run without it.
        return finish()

    bearer = dev.group(1)
    auth = {"Authorization": "Bearer " + bearer,
            "Origin": WEB_HOME,
            "User-Agent": BROWSER_UA}

    # 2. A utsk can still be minted the sanctioned way, which is what the
    #    addon falls back to when the shell stops carrying one.
    try:
        request = urllib.request.Request(
            "%s/configurations?caller=web&locale=%s&pfm=web&sfh=%s&v=%s"
            % (UTS_BASE, LOCALE, STOREFRONT, UTS_VERSION),
            data=json.dumps({"featureFlags": {"clientFeatures": []}}).encode(),
            headers=dict(auth, **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
        minted = ((body.get("data") or {}).get("utskProps") or {}).get("utsk")
        record("/configurations mints utsk", bool(minted))
    except Exception as exc:
        record("/configurations mints utsk", False, str(exc)[:70])
        minted = None

    token = (utsk.group(1) if utsk else None) or minted
    if not token:
        return finish()

    common = ("caller=web&pfm=web&v=%s&locale=%s&sf=%s&utscf=%s&utsk=%s"
              % (UTS_VERSION, LOCALE, STOREFRONT, UTS_CLIENT_FLAGS, token))

    # 3. The catalogue endpoints the addon is built on.
    checks = [
        ("channel canvas",
         "%s/canvases/channels/%s?%s&includePlatter=true"
         % (UTS_BASE, APPLE_TV_PLUS_CHANNEL, common),
         lambda d: (d.get("data") or {}).get("canvas")),
        ("search",
         "%s/search?%s&searchTerm=ted%%20lasso&topResultsOnly=true"
         % (UTS_BASE, common),
         lambda d: d.get("data")),
        ("search landing",
         "%s/search/landing?%s" % (UTS_BASE, common),
         lambda d: (d.get("data") or {}).get("canvas")),
    ]
    canvas = None
    for name, url, extract in checks:
        try:
            status, text = fetch(url, auth)
            data = json.loads(text)
            ok = status == 200 and bool(extract(data))
            record(name, ok, "HTTP %s" % status)
            if name == "channel canvas" and ok:
                canvas = (data.get("data") or {}).get("canvas")
        except Exception as exc:
            record(name, False, str(exc)[:70])

    # 4. The canvas still pages, and its shelves still carry what the addon
    #    reads out of them rather than constructs.
    if canvas:
        shelves = canvas.get("shelves") or []
        record("canvas returns shelves", bool(shelves), "%d shelves" % len(shelves))
        record("canvas paging token", "nextToken" in canvas,
               "absent is fine only if the tab is one page")
        with_ctx = 0
        for shelf in shelves:
            url = shelf.get("url") or ""
            if "ctx_brand" in url and "ctx_shelf" in url:
                with_ctx += 1
        record("shelves carry ctx params", with_ctx > 0,
               "%d of %d" % (with_ctx, len(shelves)))
    return finish()


def finish():
    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print("%d of %d checks FAILED: %s" % (len(failed), len(results),
                                              ", ".join(failed)))
        return 1
    print("all %d checks passed" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
