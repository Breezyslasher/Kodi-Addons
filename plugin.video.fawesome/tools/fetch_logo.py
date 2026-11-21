#!/usr/bin/env python3
"""Download Fawesome's official logo candidates for the add-on art.

Usage:
    python3 fetch_logo.py OUT_DIR

Saves each candidate as-is (binary) to OUT_DIR, so tools/make_art.py can be
pointed at the right one. The site's own logo files, the player's logo and
background, and the app-store icons (read from the store pages' og:image).
"""
import os
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
SITE = [
    ("site-logo.svg", "https://fawesome.tv/home/new/{b}/assets/img/logo.svg"),
    ("site-fawesome-W.png", "https://fawesome.tv/home/new/{b}/assets/img/fawesome-W.png"),
    ("player-logo.png", "https://ftmain.cachefly.net/files/a8wn4hw/im/iptv/7d/8e/6e49727fa74fef6c0733a06a128bb0f7.png"),
    ("player-background.png", "https://ftmain.cachefly.net/files/a8wn4hw/im/iptv/21/6a/0a574d6aac9840e2296686ef4c3c6719.png"),
]
STORES = [
    ("store-play-icon", "https://play.google.com/store/apps/details?id=com.future.moviesByFawesomeAndroidTV&hl=en_US&gl=US"),
    ("store-apple-icon", "https://apps.apple.com/us/app/fawesome/id1063891742"),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.headers.get("Content-Type", ""), r.read(8 * 1024 * 1024)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "art"
    os.makedirs(out, exist_ok=True)
    _, html = get("https://fawesome.tv/")
    m = re.search(rb"/home/new/(v\d+)/", html)
    build = m.group(1).decode() if m else "v469"
    todo = [(n, u.format(b=build)) for n, u in SITE]
    for name, page in STORES:
        try:
            _, body = get(page)
            m = re.search(rb'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', body) or \
                re.search(rb'content="([^"]+)"[^>]+property="og:image"', body)
            if m:
                img = m.group(1).decode().replace("&amp;", "&")
                # Ask the stores' image servers for a large rendition.
                img = re.sub(r"=w\d+-h\d+.*$", "=s1024", img)
                img = re.sub(r"/\d+x\d+[a-z]*\.(png|jpg|webp)$", r"/1024x1024bb.png", img)
                todo.append((name, img))
            else:
                print("no og:image on %s" % page)
        except Exception as exc:  # noqa: BLE001
            print("store page failed %s: %s" % (page, exc))
    for name, url in todo:
        try:
            ctype, body = get(url)
        except Exception as exc:  # noqa: BLE001
            print("FAIL %s %s: %s" % (name, url, exc))
            continue
        ext = {"image/png": ".png", "image/svg+xml": ".svg", "image/jpeg": ".jpg",
               "image/webp": ".webp"}.get(ctype.split(";")[0].strip(), "")
        fn = name if os.path.splitext(name)[1] else name + (ext or ".bin")
        with open(os.path.join(out, fn), "wb") as fh:
            fh.write(body)
        print("OK   %-26s %8d bytes %-14s %s" % (fn, len(body), ctype, url))


if __name__ == "__main__":
    main()
