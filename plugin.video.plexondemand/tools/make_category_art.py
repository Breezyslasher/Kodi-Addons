#!/usr/bin/env python3
"""Build the bundled category artwork (resources/media/categories/*.jpg).

Plex's category backgrounds are plain gradients; its apps draw the name over
them, which Kodi can't do for a folder. This rebuilds each background from the
16x9 colour grid tools/probe.py (round17) prints, and draws the name in white,
centred, like watch.plex.tv. Run it on a saved probe log:

    python3 tools/make_category_art.py probe-log.txt

Needs Pillow and a bold font (Inter Bold, else DejaVu Sans Bold). The addon
matches a category to its file by name (slug), so a category Plex adds later
simply shows without art until this is re-run.
"""
import ast
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "resources", "media", "categories")
W, H = 960, 540
FONTS = ["/usr/share/fonts/opentype/inter/Inter-Bold.otf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def slug(title):
    """Must match default.category_art()."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    raise SystemExit("no bold font found")


def render(title, hexes):
    cols = [tuple(int(hexes[i + j:i + j + 2], 16) for j in (0, 2, 4)) for i in range(0, 16 * 9 * 6, 6)]
    grid = Image.new("RGB", (16, 9))
    grid.putdata(cols)
    bg = grid.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(18))
    size = 92
    f = font(size)
    d = ImageDraw.Draw(bg)
    while d.textlength(title, font=f) > W * 0.86 and size > 40:
        size -= 4
        f = font(size)
    # soft shadow, then the name
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).text((W / 2, H / 2 + 4), title, font=f, fill=150, anchor="mm")
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    bg.paste((0, 0, 0), (0, 0), shadow)
    ImageDraw.Draw(bg).text((W / 2, H / 2), title, font=f, fill=(255, 255, 255), anchor="mm")
    return bg


def main(path):
    titles, grids = {}, {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"== CAT (\d+) title=('.*?'|\".*?\") ", line)
        if m:
            titles[m.group(1)] = ast.literal_eval(m.group(2))
        m = re.search(r"GRID (\d+) ([0-9a-f]{864})\s*$", line)
        if m:
            grids[m.group(1)] = m.group(2)
    os.makedirs(OUT, exist_ok=True)
    done = 0
    for i, title in sorted(titles.items(), key=lambda kv: int(kv[0])):
        if i not in grids:
            print("no grid for", title)
            continue
        out = os.path.join(OUT, slug(title) + ".jpg")
        render(title, grids[i]).save(out, "JPEG", quality=88, optimize=True)
        print("wrote", os.path.relpath(out, ROOT))
        done += 1
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
