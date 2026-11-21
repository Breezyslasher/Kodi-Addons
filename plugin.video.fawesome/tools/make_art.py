#!/usr/bin/env python3
"""Build resources/icon.png (512x512) and fanart.jpg (1920x1080).

Self-made, deliberately not Fawesome's logo: a plain play mark and the name
set in DejaVu Sans. Needs Pillow and the DejaVu fonts.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TOP, BOTTOM, ACCENT = (24, 18, 48), (70, 22, 74), (255, 176, 59)


def gradient(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        c = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return img


def play(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ACCENT, width=max(4, r // 9))
    s = r * 0.5
    d.polygon([(cx - s * 0.7, cy - s), (cx - s * 0.7, cy + s), (cx + s, cy)], fill=ACCENT)


def text(d, xy, s, size, anchor="mm"):
    d.text(xy, s, font=ImageFont.truetype(FONT, size), fill=(255, 255, 255), anchor=anchor)


icon = gradient(512, 512)
d = ImageDraw.Draw(icon)
play(d, 256, 210, 120)
text(d, (256, 415), "FAWESOME", 66)
icon.save(os.path.join(OUT, "icon.png"), optimize=True)

fan = gradient(1920, 1080)
d = ImageDraw.Draw(fan)
play(d, 960, 430, 220)
text(d, (960, 780), "FAWESOME", 150)
text(d, (960, 900), "free movies & TV", 54)
fan.save(os.path.join(OUT, "fanart.jpg"), quality=88, optimize=True)
print("wrote icon.png and fanart.jpg")
