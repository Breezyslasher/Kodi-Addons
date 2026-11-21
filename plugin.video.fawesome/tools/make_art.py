#!/usr/bin/env python3
"""Build resources/icon.png (512x512) and fanart.jpg (1920x1080) from
Fawesome's official artwork, downloaded by tools/fetch_logo.py (the
fawesome-recon workflow) into docs/recon/art/:

* icon   -- the App Store app icon (current W-mark brand), 1024 -> 512.
* fanart -- the site's share card (fawesome-W.png, 1200x630: W mark and
            wordmark on the brand's navy gradient), scaled to 1080 high and
            centre-cropped to 1920 (the card's sides are plain gradient).

Needs Pillow.
"""
import os
from PIL import Image

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ADDON, "docs", "recon", "art")
OUT = os.path.join(ADDON, "resources")

icon = Image.open(os.path.join(ART, "store-apple-icon.png")).convert("RGBA")
icon.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "icon.png"), optimize=True)

card = Image.open(os.path.join(ART, "site-fawesome-W.png")).convert("RGB")
w = round(card.width * 1080 / card.height)
card = card.resize((w, 1080), Image.LANCZOS)
left = (w - 1920) // 2
card.crop((left, 0, left + 1920, 1080)).save(os.path.join(OUT, "fanart.jpg"), quality=90, optimize=True)
print("wrote icon.png and fanart.jpg from the official artwork")
