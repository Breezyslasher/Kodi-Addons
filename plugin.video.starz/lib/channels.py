"""Starz's linear channels.

Codes and names are the website's own list (its JS: {code, name, product});
the logos are the website's channel-logos/*.svg rendered to PNG, since Kodi
cannot draw SVG. Order as on the website.
"""

CHANNELS = [
    ("STZ1", "STARZ", "STARZ"),
    ("STZ8", "STARZ Edge", "STARZ"),
    ("STZ3", "STARZ In Black", "STARZ"),
    ("STZ7", "STARZ Comedy", "STARZ"),
    ("STZ5", "STARZ Cinema", "STARZ"),
    ("STZ4", "STARZ Kids & Family", "STARZ"),
    ("ENC1", "STARZ Encore", "STARZ Encore"),
    ("ACT1", "STARZ Encore Action", "STARZ Encore"),
    ("LST1", "STARZ Encore Classic", "STARZ Encore"),
    ("TST1", "STARZ Encore Black", "STARZ Encore"),
    ("WAM1", "STARZ Encore Family", "STARZ Encore"),
    ("MYS1", "STARZ Encore Suspense", "STARZ Encore"),
    ("WST1", "STARZ Encore Westerns", "STARZ Encore"),
    ("E2SP", "STARZ Encore Español", "STARZ Encore"),
    ("PLX2", "Movie Plex", "Plex"),
    ("IND1", "Indie Plex", "Plex"),
    ("CLA1", "Retro Plex", "Plex"),
]
NAMES = {code: name for code, name, _ in CHANNELS}


def logo(addon_path, code):
    return "%s/resources/channels/%s.png" % (addon_path, code.upper())
