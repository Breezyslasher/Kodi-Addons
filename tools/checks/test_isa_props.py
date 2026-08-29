"""The ISA properties the addon sets, per InputStream Adaptive version.

ISA 20's Open() returns false when inputstream.adaptive.manifest_type is
absent, which Kodi reports only as "error opening". ISA 21 infers it and
deprecates the property, so it must not be set there.
"""
import os, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs"); sys.argv = ["x", "1", ""]
sys.path.insert(0, os.path.join(SP, "..", "..", "plugin.video.youtubetv"))
import xbmcaddon
from lib import playback

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok: fails += 1
    print("  %-46s -> %-16s %s" % (label, got, "" if ok else "EXPECTED %s" % (want,)))

def version_of(raw):
    class A:
        def __init__(self, *a): pass
        def getAddonInfo(self, k): return raw
    real = xbmcaddon.Addon; xbmcaddon.Addon = A
    try:
        return playback._isa_version()
    finally:
        xbmcaddon.Addon = real

for raw, want in (("20.3.18", (20, 3, 18)), ("21.5.22", (21, 5, 22)),
                  ("22.3.20-Piers", (22, 3, 20)), ("", ()), ("junk", ())):
    check("parse %r" % raw, version_of(raw), want)

def names_type(raw):
    return bool(version_of(raw)) and version_of(raw) < playback.ISA_INFERS_MANIFEST_TYPE

check("ISA 20.3.18 is told the manifest type", names_type("20.3.18"), True)
check("ISA 20.0.0 too", names_type("20.0.0"), True)
check("ISA 21.5.22 is not", names_type("21.5.22"), False)
check("ISA 22.3.20 is not", names_type("22.3.20"), False)
check("an unreadable version is left alone", names_type("junk"), False)
print("failures:", fails)
sys.exit(1 if fails else 0)
