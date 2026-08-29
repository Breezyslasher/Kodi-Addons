"""Where the Google API project comes from, and in what order."""
import os, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs")
sys.argv = ["plugin://plugin.video.youtubetv/", "1", ""]
sys.path.insert(0, os.path.join(SP, "..", "..", "plugin.video.youtubetv"))
import xbmcaddon
from lib import oauth, kodiutils

log = []
kodiutils.log = lambda m: log.append(m)
oauth.kodiutils = kodiutils

def setup(mine=None, youtube=None, baked=None):
    log[:] = []
    xbmcaddon._S.clear()
    if mine:
        xbmcaddon._S["oauth_client_id"], xbmcaddon._S["oauth_client_secret"] = mine
    xbmcaddon.OTHERS.clear()
    if youtube:
        xbmcaddon.OTHERS["plugin.video.youtube"] = {
            "youtube.api.id": youtube[0], "youtube.api.secret": youtube[1]}
    oauth._baked = (lambda: baked) if baked else (lambda: ("", ""))
    oauth.GOOGLE_TV_CLIENT = ("", "")

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok: fails += 1
    print("  %-46s -> %-28s %s" % (label, got, "" if ok else "EXPECTED %s" % (want,)))

setup(mine=("mine", "msec"), youtube=("yt", "ysec"), baked=("bk", "bsec"))
check("own settings win", oauth.credentials(), ("mine", "msec"))

setup(youtube=("yt", "ysec"), baked=("bk", "bsec"))
check("else plugin.video.youtube", oauth.credentials(), ("yt", "ysec"))
assert any("plugin.video.youtube" in m for m in log), "the borrow must be logged"

setup(baked=("bk", "bsec"))
check("else the build", oauth.credentials(), ("bk", "bsec"))

setup()
check("nothing anywhere", oauth.credentials(), ("", ""))

setup(mine=("mine", ""), youtube=("yt", "ysec"))
check("half a pair is skipped, not sent", oauth.credentials(), ("yt", "ysec"))

setup(youtube=("yt", ""))
check("half a borrowed pair is skipped too", oauth.credentials(), ("", ""))

# plugin.video.youtube absent must not raise.
setup(mine=("mine", "msec"))
xbmcaddon.OTHERS.clear()
check("no YouTube addon installed", oauth.credentials(), ("mine", "msec"))

# Google's own TV client is last, and only when it is filled in at all.
setup()
oauth.GOOGLE_TV_CLIENT = ("tv-client", "tv-secret")
check("Google's TV client is the last resort", oauth.credentials(),
      ("tv-client", "tv-secret"))
setup(baked=("bk", "bsec"))
oauth.GOOGLE_TV_CLIENT = ("tv-client", "tv-secret")
check("a real project still wins over it", oauth.credentials(), ("bk", "bsec"))
setup()
oauth.GOOGLE_TV_CLIENT = ("tv-client", "")
check("half of it is skipped, not sent", oauth.credentials(), ("", ""))

print("failures:", fails)
sys.exit(1 if fails else 0)
