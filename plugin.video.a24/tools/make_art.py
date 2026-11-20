#!/usr/bin/env python3
"""Build resources/icon.png and resources/fanart.jpg from A24's official logo.

Usage:
    python3 make_art.py        (needs: pip install cairosvg pillow)

Logo: Wikimedia Commons "File:A24 logo.svg" -- public domain (too simple for
copyright) but a trademark of A24; used nominatively to identify the service
this unofficial add-on plays. White on black, as A24 uses it.
Fanart: A24's own share image (a24films.com/og.png) when it is large enough,
cover-cropped to 1920x1080; otherwise the logo on black.
"""

import io
import os
import sys
import urllib.request

import cairosvg
from PIL import Image

LOGO_URL = "https://upload.wikimedia.org/wikipedia/commons/b/b7/A24_logo.svg"
OG_URL = "https://a24films.com/og.png"
UA = "kodi-addon-art/1.0 (build script)"
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "resources")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def white_logo(svg, width):
    """The logo rendered at `width` px, recoloured white on transparent."""
    png = cairosvg.svg2png(bytestring=svg, output_width=width)
    logo = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = logo.getchannel("A")
    white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    white.putalpha(alpha)
    return white


def on_black(svg, size, logo_frac):
    w, h = size
    canvas = Image.new("RGB", size, (0, 0, 0))
    logo = white_logo(svg, int(w * logo_frac))
    if logo.height > h * 0.8:
        logo = logo.resize((int(logo.width * h * 0.8 / logo.height), int(h * 0.8)),
                           Image.LANCZOS)
    canvas.paste(logo, ((w - logo.width) // 2, (h - logo.height) // 2), logo)
    return canvas


def cover(img, size):
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def main():
    svg = get(LOGO_URL)
    on_black(svg, (512, 512), 0.78).save(os.path.join(RES, "icon.png"), optimize=True)
    fanart = None
    try:
        og = Image.open(io.BytesIO(get(OG_URL))).convert("RGB")
        print("og.png: %dx%d" % og.size)
        if og.width >= 1200:
            fanart = cover(og, (1920, 1080))
    except Exception as exc:
        print("og.png not used: %s" % exc)
    if fanart is None:
        fanart = on_black(svg, (1920, 1080), 0.42)
        print("fanart: logo on black")
    else:
        print("fanart: from og.png")
    fanart.save(os.path.join(RES, "fanart.jpg"), quality=90)
    print("wrote resources/icon.png and resources/fanart.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
