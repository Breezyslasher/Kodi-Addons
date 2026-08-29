"""refresh_bootstrap against fake pages -- the path that broke playback."""
import os, sys, json
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs")
sys.argv = ["plugin://plugin.video.youtubetv/", "1", ""]
sys.path.insert(0, "/home/user/Kodi-Addons/plugin.video.youtubetv")
from lib import api, kodiutils

PROFILE = SP + "/profile"
os.makedirs(PROFILE, exist_ok=True)
kodiutils.profile_dir = lambda: PROFILE
store = {}
kodiutils.read_json = lambda name, default=None: store.get(name, default)
kodiutils.write_json = lambda name, value: store.__setitem__(name, value)
log = []
kodiutils.log = lambda m: log.append(m)
api.kodiutils = kodiutils

WELCOME = "<html>YouTube TV -- sign up today</html>"
TV_APP = ('{"INNERTUBE_CLIENT_VERSION":"1.20260826.04.00","STS":20690,'
          '"visitorData":"CgtUVi12aXNpdG9y","rolloutToken":"tv-rollout",'
          '"jsUrl":"\\/s\\/player\\/abc123\\/player_ias_tce.vflset\\/en_US\\/base.js"}')
WEB = ('{"INNERTUBE_CLIENT_VERSION":"2.20260826.01.00","STS":20111,'
       '"visitorData":"CgtXRUItdmlzaXRvcg","rolloutToken":"web-rollout",'
       '"jsUrl":"\\/s\\/player\\/def456\\/player_ias.vflset\\/en_US\\/base.js"}')

class Reply(object):
    def __init__(self, url, status, text):
        self.url, self.status_code, self.text = url, status, text

class Session(object):
    def __init__(self, pages): self.pages, self.asked = pages, []
    def get(self, url, **kw):
        self.asked.append(url)
        status, text = self.pages.get(url, (404, ""))
        return Reply(url, status, text)

def run(name, pages):
    store.clear(); log[:] = []
    session = Session(pages)
    boot = api.refresh_bootstrap(session)
    print("%-42s js_url=%-46s version=%s sts=%s"
          % (name, boot.get("js_url"), boot.get("version"), boot.get("sts")))
    return boot, session, list(log)

O = api.ORIGIN
boot, s1, _ = run("signed-in tv page (as it was)",
                  {O + "/": (200, TV_APP)})
assert boot["js_url"].endswith("base.js") and boot["version"] == "1.20260826.04.00"

boot, s2, lines = run("welcome page, then www.youtube.com",
                      {O + "/": (200, WELCOME), "https://www.youtube.com/": (200, WEB)})
assert boot.get("js_url") == "/s/player/def456/player_ias.vflset/en_US/base.js", boot
assert boot.get("version") is None, "web clientVersion must not be adopted: %r" % boot
assert boot.get("visitor_data") is None, "web visitorData must not be adopted"
print("   swept:", [u.split("//")[1] for u in s2.asked])

boot, s3, _ = run("nothing anywhere", {O + "/": (200, WELCOME)})
assert boot == {}, boot
print("   swept:", [u.split("//")[1] for u in s3.asked])

# The day cache must hold a js_url-only entry, or every call re-sweeps.
store.clear()
store["client_bootstrap.json"] = {"schema": api.BOOTSTRAP_SCHEMA,
                                  "fetched": 9e18, "js_url": "/s/player/x/base.js"}
s4 = Session({})
api.refresh_bootstrap(s4)
assert s4.asked == [], "a cached js_url should not trigger a sweep: %s" % s4.asked
print("cached js_url alone           -> no page fetched")
print("\nall bootstrap assertions passed")
