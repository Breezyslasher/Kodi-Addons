#!/usr/bin/env python3
"""Build a Kodi-installable zip for an add-on, carrying only what Kodi runs.

    tools/build_addon_zip.py plugin.video.youtubetv
    tools/build_addon_zip.py                       # every add-on with an addon.xml

The version comes out of addon.xml, so the name is always the one Kodi will
show, and the zip lands in zips/<addon>/<addon>-<version>.zip beside the
releases already there.

**What is left out, and why.** Protocol notes, development tools and compiled
caches are for the repository, not for a box: the youtubetv notes alone are
180 KB, which is most of that add-on's install. This was being done by hand --
the shipped plugin.video.appletv zip has no docs/ in it -- and doing it by
hand is the kind of thing that is right until the once it is not.

``baked_*.py`` is excluded for a different and more important reason. Those
files carry a personal build's own credentials -- an OAuth client secret, a
session, a cookie export -- and are gitignored so they never reach the
repository. A build script that swept them into a published zip would undo
that in one step, so the exclusion here is deliberate and the ``--personal``
flag is what a private build has to say out loud.
"""

import os
import sys
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Whole directories that never ship.
SKIP_DIRS = {"docs", "tools", "__pycache__", ".git", ".github", "tests"}

# Files that never ship, by suffix or by exact name.
SKIP_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".log")
SKIP_NAMES = {".DS_Store", ".gitignore", ".gitattributes"}


def _is_baked(name):
    """A personal build's credentials: baked_oauth.py and its siblings."""
    return name.startswith("baked_") and name.endswith(".py")


def version_of(folder):
    """The version Kodi will show, out of addon.xml."""
    tree = ET.parse(os.path.join(folder, "addon.xml"))
    return tree.getroot().get("version") or "0.0.0"


def files_in(folder, personal=False):
    """Every path that belongs in the zip, relative to the repository root."""
    kept = []
    for root, dirs, names in os.walk(folder):
        # Pruned in place so os.walk does not descend into them at all.
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            if _is_baked(name) and not personal:
                continue
            kept.append(os.path.join(root, name))
    return kept


def build(addon, personal=False, out_dir=None):
    folder = os.path.join(HERE, addon)
    if not os.path.isfile(os.path.join(folder, "addon.xml")):
        raise SystemExit("%s has no addon.xml" % addon)
    version = version_of(folder)
    where = out_dir or os.path.join(HERE, "zips", addon)
    if not os.path.isdir(where):
        os.makedirs(where)
    target = os.path.join(where, "%s-%s.zip" % (addon, version))

    kept = files_in(folder, personal)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for path in kept:
            # Kodi wants the add-on id as the single top-level directory.
            out.write(path, os.path.relpath(path, HERE))
    size = os.path.getsize(target)
    baked = [p for p in kept if _is_baked(os.path.basename(p))]
    print("%s -> %s  (%d files, %.0f KB)%s"
          % (addon, os.path.relpath(target, HERE), len(kept), size / 1024.0,
             "  [PERSONAL: carries %s]"
             % ", ".join(os.path.basename(p) for p in baked) if baked else ""))
    return target


def main(argv):
    personal = "--personal" in argv
    out_dir = None
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
    names = [a for a in argv[1:]
             if not a.startswith("--") and a != out_dir]
    if not names:
        names = sorted(d for d in os.listdir(HERE)
                       if os.path.isfile(os.path.join(HERE, d, "addon.xml")))
    for addon in names:
        build(addon, personal, out_dir)


if __name__ == "__main__":
    main(sys.argv)
