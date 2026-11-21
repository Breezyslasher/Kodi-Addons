#!/usr/bin/env python3
"""Download the service's official artwork for the add-on icon and fanart.

Usage:
    python3 fetch_logo.py OUT_DIR

Reads the site's own pages for its share image (og:image) and its App Store /
Google Play links, then saves the share image and the store icons (from the
store pages' og:image, at a large size) to OUT_DIR as store-apple-icon.png,
store-play-icon.png and site-share.<ext>. tools/make_art.py builds the art
from them. Stdlib only.
"""
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


def site_urls():
    src = open(os.path.join(os.path.dirname(HERE), "lib", "api.py"), encoding="utf-8").read()
    site = re.search(r'^SITE = "([^"]+)"', src, re.M).group(1)
    player = re.search(r'^PLAYER = "([^"]+)"', src, re.M)
    return [site] + ([player.group(1)] if player and player.group(1) != site else [])


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.headers.get("Content-Type", ""), r.read(8 * 1024 * 1024)


def og_image(html):
    m = (re.search(rb'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html) or
         re.search(rb'content="([^"]+)"[^>]+property="og:image"', html))
    return m.group(1).decode().replace("&amp;", "&") if m else None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "art"
    os.makedirs(out, exist_ok=True)
    todo, stores = [], {}
    for page in site_urls():
        try:
            _, html = get(page)
        except Exception as exc:  # noqa: BLE001
            print("page failed %s: %s" % (page, exc))
            continue
        img = og_image(html)
        if img and "site-share" not in dict(todo):
            todo.append(("site-share", urllib.parse.urljoin(page, img)))
        for m in re.finditer(rb'href="(https://(?:apps\.apple\.com|play\.google\.com)/[^"]+)"', html):
            u = m.group(1).decode().replace("&amp;", "&")
            key = "store-apple-icon" if "apple.com" in u else "store-play-icon"
            stores.setdefault(key, u)
    print("store pages:", stores)
    for key, page in stores.items():
        try:
            _, body = get(page)
            img = og_image(body)
            if img:
                img = re.sub(r"=w\d+-h\d+.*$", "=s1024", img)
                img = re.sub(r"/\d+x\d+[a-z]*\.(png|jpg|webp)$", r"/1024x1024bb.png", img)
                todo.append((key, img))
        except Exception as exc:  # noqa: BLE001
            print("store page failed %s: %s" % (page, exc))
    for name, url in todo:
        try:
            ctype, body = get(url)
        except Exception as exc:  # noqa: BLE001
            print("FAIL %s %s: %s" % (name, url, exc))
            continue
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
            ctype.split(";")[0].strip(), ".png")
        with open(os.path.join(out, name + ext), "wb") as fh:
            fh.write(body)
        print("OK   %-18s %8d bytes %-12s %s" % (name + ext, len(body), ctype, url))


if __name__ == "__main__":
    main()
