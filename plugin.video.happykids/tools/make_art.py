#!/usr/bin/env python3
"""Build resources/icon.png (512x512) and fanart.jpg (1920x1080) from the
service's official artwork, downloaded by tools/fetch_logo.py into ART_DIR:

* icon   -- the App Store icon (else the Google Play icon), resized to 512.
* fanart -- the site's share image (og:image) fitted into 1920x1080 over a
            blurred, darkened copy of itself, so any aspect ratio fills the
            frame without stretching.

Usage:
    python3 make_art.py ART_DIR
Needs Pillow.
"""
import glob
import os
import sys

from PIL import Image, ImageFilter, ImageEnhance

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ADDON, "resources")


def pick(art, *names):
    for n in names:
        hits = sorted(glob.glob(os.path.join(art, n + ".*")))
        if hits:
            return hits[0]
    return None


def main():
    art = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ADDON, "docs", "recon", "art")
    icon_src = pick(art, "store-apple-icon", "store-play-icon")
    share_src = pick(art, "site-share")
    if icon_src:
        Image.open(icon_src).convert("RGBA").resize((512, 512), Image.LANCZOS).save(
            os.path.join(OUT, "icon.png"), optimize=True)
        print("icon from", os.path.basename(icon_src))
    src = share_src or icon_src
    if src:
        img = Image.open(src).convert("RGB")
        bg = img.resize((1920, 1080), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
        scale = min(1920 / img.width, 1080 / img.height)
        fg = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        bg.paste(fg, ((1920 - fg.width) // 2, (1080 - fg.height) // 2))
        bg.save(os.path.join(OUT, "fanart.jpg"), quality=90, optimize=True)
        print("fanart from", os.path.basename(src))


if __name__ == "__main__":
    main()
