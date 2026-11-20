#!/usr/bin/env python3
"""Run the addon's own lib/ code against the live Plex VOD provider with the
Kodi modules stubbed, so browse + stream resolution are exercised outside
Kodi (from GitHub Actions; plex hosts are blocked in the dev container).

Usage: python3 tools/smoke_test.py   (needs `requests`)
"""
import os
import re
import unicodedata
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SETTINGS = {}
PROPS = {}


class _ListItem(object):
    def __init__(self, label="", path=""):
        self.label, self.path, self.props, self.art, self.subs, self.mime = label, path, {}, {}, [], None
        self.tag = types.SimpleNamespace(**{n: (lambda *a, **k: None) for n in (
            "setMediaType", "setTitle", "setPlot", "setTagLine", "setYear", "setPremiered", "setDuration",
            "setMpaa", "setStudios", "setGenres", "setRating", "setTvShowTitle", "setSeason", "setEpisode",
            "setResumePoint", "setPlaycount")})

    def setProperty(self, k, v): self.props[k] = v
    def setArt(self, a): self.art.update(a)
    def setSubtitles(self, s): self.subs = s
    def setMimeType(self, m): self.mime = m
    def setContentLookup(self, v): pass
    def getVideoInfoTag(self): return self.tag


class _Addon(object):
    def __init__(self, *a): pass
    def getAddonInfo(self, k): return {"id": "plugin.video.plexondemand", "name": "Plex On Demand",
                                       "profile": "/tmp/pod", "version": "21.5.0"}.get(k, "")
    def getSetting(self, k): return SETTINGS.get(k, "")
    def setSetting(self, k, v): SETTINGS[k] = v
    def getLocalizedString(self, i): return str(i)


class _Window(object):
    def __init__(self, *a): pass
    def setProperty(self, k, v): PROPS[k] = v
    def getProperty(self, k): return PROPS.get(k, "")
    def clearProperty(self, k): PROPS.pop(k, None)


xbmc = types.ModuleType("xbmc")
xbmc.LOGINFO, xbmc.LOGERROR = 1, 4
xbmc.log = lambda m, l=1: print("  [log]", m)
xbmc.getCondVisibility = lambda c: False
xbmc.getLanguage = lambda *a: "en"
xbmc.ISO_639_1 = 0
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.ListItem, xbmcgui.Window = _ListItem, _Window
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = ""
xbmcgui.Dialog = lambda: types.SimpleNamespace(notification=lambda *a: None, ok=lambda *a: None)
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
for m in (xbmc, xbmcgui, xbmcaddon, xbmcvfs):
    sys.modules[m.__name__] = m

from lib.api import PlexVodApi, TYPE_MOVIE, TYPE_SHOW  # noqa: E402
from lib import playback  # noqa: E402

FAIL = []


def check(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (" - " + str(extra) if extra else ""))
    if not ok:
        FAIL.append(label)


api = PlexVodApi()
tok = api.auth.token()
print("::add-mask::" + tok)
check("anonymous token", bool(tok))
from lib.auth import base_headers as _bh
_h = _bh()
pin = api.auth.create_pin(_h)
check("PIN create", bool(pin.get("code")) and pin.get("clientIdentifier") == _h["X-Plex-Client-Identifier"],
      len(pin.get("code") or ""))
try:
    check("PIN poll (same client id)", api.auth.check_pin(pin["id"], _h) is None)
except Exception as exc:
    check("PIN poll (same client id)", False, exc)
SETTINGS.pop("client_id", None)  # a lost setting must not change the id
check("client id stable across lost setting", _bh()["X-Plex-Client-Identifier"] == _h["X-Plex-Client-Identifier"])
from lib import auth as _auth
_auth._save_account(plex_token="")
check("account store round-trip", _auth._account().get("anon_token") == tok and not api.auth.signed_in())
hubs = api.hubs()
check("hubs", len(hubs) > 10, len(hubs))
items, total = api.hub_items(hubs[0]["key"])
check("hub items", bool(items), "%d of %d" % (len(items), total))
mg, sg = api.genres(TYPE_MOVIE), api.genres(TYPE_SHOW)
check("genres", bool(mg) and bool(sg), "%d movie / %d show" % (len(mg), len(sg)))
movies, total = api.listing(mg[0]["key"])
check("genre listing page 1", len(movies) > 0, "%d of %d" % (len(movies), total))
page2, _ = api.listing(mg[0]["key"], start=len(movies))
from lib.api import is_store_item
check("genre listing has no rental titles (availabilityType=free)", not any(is_store_item(m) for m in movies),
      [m.get("title") for m in movies if is_store_item(m)][:3])
check("genre listing page 2 differs", page2 and page2[0].get("ratingKey") != movies[0].get("ratingKey"))
shows, _ = api.listing(sg[0]["key"])
show = next((s for s in shows if s.get("type") == "show"), None)
check("show in show genre", bool(show))
seasons, _ = api.children(show["ratingKey"])
eps, _ = api.children(seasons[0]["ratingKey"])
check("seasons/episodes", bool(seasons) and bool(eps), "%d seasons, %d eps" % (len(seasons), len(eps)))
res = api.search("star")
check("search free section", bool(res), [r.get("title") for r in res[:3]])

clear = drm = None
for h in hubs[:20]:
    for m in api.hub_items(h["key"])[0]:
        ms = m.get("Media") or []
        if any(x.get("drm") for x in ms):
            drm = drm or m
        elif ms:
            clear = clear or m
    if clear and drm:
        break

sys.modules["inputstreamhelper"] = None  # force the ImportError path
for label, m in (("clear", clear), ("drm", drm), ("episode", eps[0])):
    try:
        li = playback.build(api, m["ratingKey"])
        p = li.props
        check("play %s: %s" % (label, m.get("title")), li.path.startswith("https://"),
              "%s mime=%s drm=%s legacy_key=%s subs=%d" % (
                  li.path.split("?")[0].rsplit("/", 1)[-1], li.mime, "inputstream.adaptive.drm" in p,
                  "inputstream.adaptive.license_key" in p, len(li.subs)))
        check("  manifest headers carry token", "X-Plex-Token=" in p.get("inputstream.adaptive.manifest_headers", ""))
        if label == "drm":
            key = p.get("inputstream.adaptive.license_key", "")
            check("  licence has X-Plex-DRM", "X-Plex-DRM=widevine" in key, key.split("|")[0].rsplit("/", 2)[-2:])
        check("  timeline handoff", "plexondemand.playing" in PROPS)
    except playback.Blocked:
        print("SKIP play %s: blocked from this IP (451 VPN/proxy)" % label)
if drm:
    import requests
    from lib.auth import base_headers
    part = [x for x in drm["Media"] if x.get("protocol") == "dash"][0]["Part"][0]
    h = base_headers(); h.update({"X-Plex-Token": tok, "X-Plex-DRM": "widevine",
                                  "Content-Type": "application/octet-stream"})
    r = requests.post(api.base + part["license"], data=b"\x08\x04", headers=h, timeout=20)
    check("licence endpoint answers cert request", r.status_code == 200 and r.content[:1] == b"\x08", r.status_code)
r = api._get("/timeline", {"ratingKey": clear["ratingKey"], "key": "/library/metadata/%s" % clear["ratingKey"],
                           "state": "stopped", "time": 1000, "duration": clear.get("duration", 0)})
check("timeline report", "playbackState" in r, r.get("playbackState"))
# ---- watch state, trailers, DRM subtitles, up next
rk = clear["ratingKey"]
api._get("/timeline", {"ratingKey": rk, "key": "/library/metadata/%s" % rk, "state": "stopped",
                       "time": 240000, "duration": clear.get("duration", 0)})
st = api.user_states([rk, eps[0]["ratingKey"]])
check("batch userState has resume point", (st.get(rk) or {}).get("viewOffset") == 240000, st.get(rk))
cw = api.continue_watching()
check("continue watching lists it", any(m.get("ratingKey") == rk for m in cw), len(cw))
api.action("removeFromContinueWatching", rk)
check("remove from continue watching", not any(m.get("ratingKey") == rk for m in api.continue_watching()))
api.action("scrobble", rk)
check("scrobble marks watched", (api.user_state(rk) or {}).get("viewCount") == 1)
api.action("unscrobble", rk)
check("unscrobble", (api.user_state(rk) or {}).get("viewCount") == 0)
# Not every title has a trailer (the add-on then says so); test one that does.
with_trailer = None
for h in hubs[:15]:
    for m in api.hub_items(h["key"])[0]:
        if m.get("type") == "movie":
            full = api.metadata(m["ratingKey"])
            if full.get("primaryExtraKey"):
                with_trailer = full
                break
    if with_trailer:
        break
tr = api.trailer(with_trailer) if with_trailer else None
check("trailer resolves to open HLS", bool(tr) and tr[1].startswith("https://"),
      "%s -> %s" % ((with_trailer or {}).get("title"), tr and tr[1].split("/")[2]))
if drm:
    part = [x for x in api.metadata(drm["ratingKey"])["Media"] if x.get("protocol") == "dash"][0]["Part"][0]
    subs = playback._stream_subtitles(api, part)
    check("DRM subtitles resolve", bool(subs) and subs[0].startswith("https://"), subs[:1])
nxt = api.next_item(eps[0]["ratingKey"])
check("up next after episode 1", bool(nxt) and nxt.get("type") == "episode", nxt and nxt.get("index"))
playback.build(api, eps[0]["ratingKey"])
import json as _json
handoff = _json.loads(PROPS.get("plexondemand.playing", "{}"))
check("handoff carries next episode", handoff.get("next") == (nxt or {}).get("ratingKey"), handoff)

# ---- watchlist, related, images, sort, search size, region
from lib.api import image
api.action("addToWatchlist", rk)
wl, _ = api.watchlist()
check("watchlist add + list", any(m.get("ratingKey") == rk for m in wl), len(wl))
check("watchlist keys (Remove offered in any listing)", rk in api.watchlist_keys())
api.action("removeFromWatchlist", rk)
import time as _time
for _ in range(5):  # the watchlist list can lag a removal by a moment
    if not any(m.get("ratingKey") == rk for m in api.watchlist()[0]):
        break
    _time.sleep(1)
check("watchlist remove", not any(m.get("ratingKey") == rk for m in api.watchlist()[0]))
sim = api.related(rk)
cast = api.related(rk, "more-from-credits")
check("related: similar titles", len(sim) > 0, len(sim))
check("related: more from cast & crew", len(cast) > 0, len(cast))
thumb = next((m.get("thumb") for m in sim if m.get("thumb")), None)
import requests as _rq
r = _rq.get(image(thumb, 400, 600), timeout=20) if thumb else None
check("image transcoder resizes art", bool(r) and r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/")
      and len(r.content) < 200000, r is not None and len(r.content))
az, _ = api.listing(mg[0]["key"], sort="titleSort")
za, _ = api.listing(mg[0]["key"], sort="titleSort:desc")
check("genre sort A-Z vs Z-A differ", az and za and az[0].get("ratingKey") != za[0].get("ratingKey"),
      (az[0].get("title"), za[0].get("title")))
many = api.search("love")
check("search returns more than 30", len(many) > 30, len(many))
check("region lookup", api.region() == "US", api.region())
api.rate(rk, 8)
check("rate shows in userState", (api.user_state(rk) or {}).get("userRating") == 8, api.user_state(rk))
api.rate(rk, -1)
check("clear rating", (api.user_state(rk) or {}).get("userRating") is None)
api.action("addToDislikes", rk)
api.action("removeFromDislikes", rk)
check("dislike / undo accepted", True)

# ---- brands (Plex "channels") and curated categories
br = api.brands()
check("brands list", len(br) > 3 and all(b.get("key", "").startswith("/library/channels/") for b in br),
      [b.get("title") for b in br[:4]])
bi, btotal = api.listing(br[0]["key"])
check("a brand's titles", len(bi) > 0, "%s: %d of %d" % (br[0].get("title"), len(bi), btotal))
check("brand titles are free", not any(is_store_item(m) for m in bi))
cats = api.categories()
check("categories list", len(cats) > 3, [c.get("title") for c in cats[:4]])
ci, _ = api.listing(cats[0]["key"])
check("a category's titles", len(ci) > 0 and not any(is_store_item(m) for m in ci), len(ci))
missing = [d.get("title") for d in cats
           if not os.path.exists(os.path.join(ROOT, "resources", "media", "categories",
                                              re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", d.get("title") or "")
                                                     .encode("ascii", "ignore").decode().lower()).strip("-") + ".jpg"))]
print("INFO categories without bundled art (shown without; re-run tools/make_category_art.py): %s" % (missing or "none"))
import requests as _rq2
for label, row in (("brand", br[0]), ("category", cats[0])):
    t = row.get("thumb")
    r = _rq2.get(image(t, 400, 400), timeout=20) if t else None
    print("INFO %s artwork via transcoder -> %s" % (label, r.status_code if r is not None else "none"))

# ---- store (rentals): browse, and a title nobody rented reports NotRented
sh = api.store_hubs()
check("store hubs", any(h["key"].endswith("/myRentalsAndPurchases") for h in sh), len(sh))
mine, _ = api.listing("/hubs/sections/store/myRentalsAndPurchases")
check("my rentals (anonymous: empty)", mine == [], len(mine))
check("owns_any is False for an anonymous account", api.owns_any() is False)
rentals = []
for h in sh:
    if not h["key"].endswith("/myRentalsAndPurchases"):
        rentals = api.listing(h["key"])[0]
        if rentals:
            break
check("store row has priced titles", bool(rentals) and bool(rentals[0].get("rentalPrice")),
      rentals and (rentals[0].get("title"), rentals[0].get("rentalPrice")))
free_in_store = []
for h in sh:
    if h["key"].endswith("/myRentalsAndPurchases"):
        continue
    row = api.listing(h["key"])[0]
    free = [m.get("title") for m in row if not is_store_item(m)]
    print("INFO store row %r: %d titles, %d free (the addon hides those)" % (h.get("title") or h["key"], len(row), len(free)))
    free_in_store += free
    if free and len(free_in_store) == len(free):  # once: run the addon's own store listing on that row
        sys.modules.setdefault("xbmcplugin", types.ModuleType("xbmcplugin"))
        import default
        seen = []
        default.add_items = lambda its, *a, **k: seen.extend(its) or "movies"
        default.add_next = lambda *a, **k: None
        default.end = lambda *a, **k: None
        default.listing(h["key"], in_store=True)
        check("Rentals store row drops free titles", seen and all(is_store_item(m) for m in seen),
              (len(row), len(seen)))
try:
    playback.build(api, rentals[0]["ratingKey"])
    check("unrented title -> NotRented", False, "played without a rental?")
except playback.NotRented as nr:
    check("unrented title -> NotRented", nr.item.get("publicPagesURL", "").startswith("https://"),
          nr.item.get("publicPagesURL"))
except playback.Blocked:
    print("SKIP unrented title: blocked from this IP (451 VPN/proxy)")

# ---- Live TV (tv.plex.provider.epg) and IPTV Manager
import json as _json
import socket as _socket
import threading as _threading
import time as _time
from lib import live, iptv
lapi = live.LiveApi()
check("live provider base", lapi.base.startswith("https://") and "epg" in lapi.base, lapi.base)
filters = lapi.filters()
check("live filters (Featured + genres)", len(filters) > 3 and filters[0][0], [f[0] for f in filters[:5]])
allch = lapi.channels(live.ALL)
featured = lapi.channels(filters[0][1])
check("live channels", len(allch) > 100 and all(c.get("id") and c.get("gridKey") for c in allch),
      (len(allch), allch[0].get("title")))
check("featured channels", 0 < len(featured) < len(allch), len(featured))
genre_ch = lapi.channels(filters[1][1])
check("a genre's channels", 0 < len(genre_ch) < len(allch), (filters[1][0], len(genre_ch)))
ch = featured[0]
li = live.build(lapi, ch["id"])
check("live ListItem (ISA HLS, token header)",
      li.props.get("inputstream") == "inputstream.adaptive" and "X-Plex-Token=" in li.props.get(
          "inputstream.adaptive.manifest_headers", "") and li.mime == "application/vnd.apple.mpegurl", li.path)
r = lapi.session.get(li.path, headers=lapi.headers(), timeout=20)
check("live master playlist", r.status_code == 200 and r.text.startswith("#EXTM3U"), r.status_code)
variant = [l for l in r.text.splitlines() if l and not l.startswith("#")]
rv = lapi.session.get(variant[0], timeout=20) if variant else None
check("live variant plays without headers", rv is not None and rv.status_code == 200 and "#EXTINF" in rv.text,
      rv is not None and rv.status_code)
segs = [l for l in (rv.text if rv is not None else "").splitlines() if l and not l.startswith("#")]
if segs:
    seg = segs[0] if segs[0].startswith("http") else variant[0].rsplit("/", 1)[0] + "/" + segs[0]
    rs = lapi.session.get(seg, timeout=20, stream=True)
    check("live segment open", rs.status_code == 200, rs.status_code)
    rs.close()
t0 = _time.time()
nn = lapi.now_next(featured)
with_now = [gk for gk, (cur, nxt) in nn.items() if cur]
check("now/next for featured channels", len(with_now) >= len(featured) // 2,
      ("%d/%d in %.1fs" % (len(with_now), len(featured), _time.time() - t0)))
cur, nxt = nn[with_now[0]] if with_now else (None, None)
check("now covers the present", cur and cur["Media"][0]["beginsAt"] <= _time.time() < cur["Media"][0]["endsAt"],
      cur and live.programme_title(cur))
check("next follows now", nxt is None or nxt["Media"][0]["beginsAt"] >= cur["Media"][0]["beginsAt"],
      nxt and live.programme_title(nxt))

streams = iptv.channels(lapi, allch)["streams"]
check("IPTV channels: all, with stream/logo/group",
      len(streams) == len(allch) and all(s["stream"].startswith(iptv.PLUGIN) and s["name"] for s in streams),
      len(streams))
check("IPTV channel ids unique", len({s["id"] for s in streams}) == len(streams))
groups = sorted({s["group"] for s in streams})
check("IPTV channels grouped by genre", len(groups) > 3, groups[:8])
t0 = _time.time()
guide = iptv.epg(lapi, featured)["epg"]
progs = [e for v in guide.values() for e in v]
check("IPTV guide for featured channels", len(guide) == len(featured) and len(progs) > len(featured) * 5,
      "%d programmes, %.1fs" % (len(progs), _time.time() - t0))
check("IPTV guide entries well-formed", all(e["start"] < e["stop"] and e["title"] for e in progs), progs[:1])
first = sorted(guide[iptv.channel_id(featured[0])], key=lambda e: e["start"])
now_iso = iptv._iso(_time.time())
check("IPTV guide reaches ahead", first and first[0]["start"] <= now_iso and first[-1]["stop"] > iptv._iso(_time.time() + 12 * 3600),
      first and (first[0]["start"], first[-1]["stop"]))
# round trip over a socket the way IPTV Manager receives it
srv = _socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
got = []


def _recv():
    conn, _ = srv.accept()
    buf = b""
    while True:
        b = conn.recv(65536)
        if not b:
            break
        buf += b
    got.append(_json.loads(buf))
    conn.close()


th = _threading.Thread(target=_recv)
th.start()
iptv.send(srv.getsockname()[1], lambda: iptv.channels(lapi, featured))
th.join(10)
srv.close()
check("IPTV send over socket", got and got[0]["version"] == 1 and len(got[0]["streams"]) == len(featured))
check("IPTV routes", iptv.handle("plugin://plugin.video.plexondemand/", "1") is False)

print("\n%d failure(s)" % len(FAIL))
sys.exit(1 if FAIL else 0)
