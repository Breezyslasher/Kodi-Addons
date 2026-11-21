#!/usr/bin/env python3
"""Drive the add-on's own router against the live Fawesome API, without Kodi.

Usage:
    python3 smoke_test.py

Stubs the xbmc* modules, then walks what a user would: the root menu, a
section, a row, a show's seasons and episodes, search, and play -- checking
each step returns entries and that play resolves to a stream that answers
#EXTM3U (or a video file). Needs network access to fawesome.tv, so it runs in
the fawesome-recon workflow (and the endpoint check), not the dev container.
Exits non-zero on the first broken step.
"""

import os
import sys
import tempfile
import types
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
PROFILE = tempfile.mkdtemp(prefix="fawesome-profile-")
SETTINGS = {"use_isa": "true", "subtitles": "true", "debug_log": "true"}
LOG = []


# -- Kodi stubs ---------------------------------------------------------------

def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


xbmc = _mod("xbmc")
xbmc.LOGINFO, xbmc.LOGERROR, xbmc.LOGDEBUG, xbmc.ISO_639_1 = 1, 3, 0, 0
xbmc.log = lambda msg, level=1: LOG.append(msg)
xbmc.getLanguage = lambda *a: "en"
xbmc.executebuiltin = lambda cmd, *a: LOG.append("BUILTIN %s" % cmd)


class Actor(object):
    def __init__(self, name, *a, **k):
        self.name = name


xbmc.Actor = Actor


class Addon(object):
    def __init__(self, *a):
        pass

    def getAddonInfo(self, key):
        return {"id": "plugin.video.fawesome", "name": "Fawesome", "profile": PROFILE}[key]

    def getSetting(self, key):
        return SETTINGS.get(key, "")

    def setSetting(self, key, value):
        SETTINGS[key] = value

    def getLocalizedString(self, sid):
        return "#%d" % sid


_mod("xbmcaddon").Addon = Addon
_mod("xbmcvfs").translatePath = lambda p: p


class InfoTag(object):
    def __getattr__(self, name):
        def setter(*a):
            self.__dict__.setdefault("_set", {})[name] = a[0] if a else None
        return setter


class ListItem(object):
    def __init__(self, label="", path="", offscreen=False):
        self.label, self.path, self.props, self.art = label, path, {}, {}
        self.tag, self.subs, self.mime = InfoTag(), [], ""

    def setArt(self, art):
        self.art.update(art)

    def getVideoInfoTag(self):
        return self.tag

    def setProperty(self, k, v):
        self.props[k] = v

    def setPath(self, p):
        self.path = p

    def setMimeType(self, m):
        self.mime = m

    def setContentLookup(self, v):
        pass

    def setSubtitles(self, s):
        self.subs = s

    def addStreamInfo(self, kind, info):
        self.__dict__.setdefault("streams", []).append((kind, info))

    def addContextMenuItems(self, items, replaceItems=False):
        self.__dict__.setdefault("context", []).extend(items)


class Dialog(object):
    def notification(self, *a, **k):
        LOG.append("NOTIFY %r" % (a,))

    def ok(self, *a):
        LOG.append("OK %r" % (a,))

    def input(self, *a, **k):
        return "love"


xbmcgui = _mod("xbmcgui")
xbmcgui.ListItem, xbmcgui.Dialog = ListItem, Dialog
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_ERROR = 0
xbmcgui.INPUT_ALPHANUM = xbmcgui.INPUT_NUMERIC = xbmcgui.ALPHANUM_HIDE_INPUT = 0

RESULT = {}
xbmcplugin = _mod("xbmcplugin")
xbmcplugin.addDirectoryItem = lambda h, url, li, folder=False: RESULT["items"].append((url, li, folder))
xbmcplugin.setContent = lambda h, c: RESULT.__setitem__("content", c)
xbmcplugin.endOfDirectory = lambda h, succeeded=True, cacheToDisc=True: RESULT.__setitem__("ok", succeeded)
xbmcplugin.setResolvedUrl = lambda h, ok, li: RESULT.update(resolved=ok, li=li)

sys.path.insert(0, ADDON)
import default  # noqa: E402


def run(query):
    RESULT.clear()
    RESULT["items"] = []
    sys.argv = ["plugin://plugin.video.fawesome/", "1", "?" + query]
    default.HANDLE, default.BASE = 1, sys.argv[0]
    default.main()
    return RESULT


def q(url):
    return url.split("?", 1)[1]


failures = []


def step(name, res, need_items=True):
    items = res.get("items") or []
    folders = sum(1 for i in items if i[2])
    ok = res.get("ok", True) and (items or not need_items)
    print("%s  %-28s %3d entries (%d folders) content=%s  e.g. %s" % (
        "OK  " if ok else "FAIL", name, len(items), folders, res.get("content"),
        ", ".join(i[1].label for i in items[:4])))
    if not ok:
        failures.append(name)
    return items


def first(items, folder):
    for url, li, is_folder in items:
        if is_folder == folder and "page=" not in url:
            return url, li
    return None, None


def check_play(name, url):
    res = run(q(url))
    li = res.get("li")
    ok = bool(res.get("resolved")) and li is not None and li.path.startswith("http")
    detail = ""
    if ok:
        import requests
        stream = li.path.split("|", 1)[0]
        r = requests.get(stream, headers={"User-Agent": default.FawesomeApi("x").s.headers["User-Agent"]},
                         timeout=20, stream=True)
        head = next(r.iter_content(256), b"")
        ok = r.status_code == 200 and (head.startswith(b"#EXTM3U") or b"ftyp" in head[:16])
        # Subtitles are local <lang>.vtt files (so Kodi labels them), preferred
        # language first; each a real WEBVTT. Audio track labelled too.
        sub_langs = [os.path.splitext(os.path.basename(p))[0] for p in li.subs]
        for p in li.subs:
            if not (os.path.isfile(p) and open(p, encoding="utf-8").read(16).lstrip().startswith("WEBVTT")):
                ok = False
        if "en" in sub_langs and sub_langs[0] != "en":
            ok = False
        audio = [i.get("language") for k, i in getattr(li, "streams", []) if k == "audio"]
        detail = "%s %s isa=%s subs=%s audio=%s %s" % (
            r.status_code, head[:7], li.props.get("inputstream"),
            sub_langs or "none", audio or "none", stream[:70])
    print("%s  %-28s %s" % ("OK  " if ok else "FAIL", name, detail or "not resolved"))
    if not ok:
        failures.append(name)


root = step("root menu", run(""))
names = [li.label for _, li, _ in root]
for wanted in ("Movies", "Shows"):
    sec = [u for u, li, f in root if li.label == wanted]
    if not sec:
        print("FAIL  section %s missing from root (%s)" % (wanted, names))
        failures.append("section " + wanted)
        continue
    rows = step("section " + wanted, run(q(sec[0])))
    # Open rows until one yields a playable title (some rows are lists of lists).
    played = False
    for url, li, _ in rows[:6]:
        items = step("  row %s" % li.label[:22], run(q(url)))
        vurl, vli = first(items, folder=False)
        if not vurl:
            furl, fli = first(items, folder=True)
            if furl:
                sub = step("    open %s" % fli.label[:20], run(q(furl)))
                vurl, vli = first(sub, folder=False)
                if not vurl:
                    s2, sli = first(sub, folder=True)
                    if s2:
                        eps = step("      open %s" % sli.label[:18], run(q(s2)))
                        vurl, vli = first(eps, folder=False)
        if vurl:
            check_play("  play %s" % vli.label[:22], vurl)
            played = True
            break
    if not played:
        print("FAIL  no playable title reached in %s" % wanted)
        failures.append("play " + wanted)

# A known show (keys from docs/recon.md). It has one season, so opening it
# should descend straight to the episodes (no pointless single-season folder).
show = step("show Love (one season)", run("action=open&" + urllib.parse.urlencode(
    {"url": "https://rapi.ifood.tv/shows.php?searchType=listoflist&keys=116587"})))
if any(f for u, _, f in show if "page=" not in u):  # ignore the Next-page item
    print("FAIL  one-season show should list episodes, not a season folder")
    failures.append("single-season descend")
eurl, eli = first(show, folder=False)
if eurl:
    check_play("  play %s" % eli.label[:22], eurl)

found = step("search 'love'", run("action=search&q=love"))
page2 = [u for u, li, f in found if "page=" in u]
if page2:
    step("search page 2", run(q(page2[0])))

# My List: add a title, confirm it lists, remove it, confirm it's gone.
nid = None
for u, _, _ in found:
    m = re.search(r"nid=(\d+)", u)
    if m:
        nid = m.group(1)
        break
if nid:
    run("action=queue_add&nid=%s" % nid)
    ml = step("My List (after add)", run("action=mylist"))
    if not any("nid=%s" % nid in u for u, _, _ in ml):
        print("FAIL  added title did not appear in My List")
        failures.append("mylist add")
    run("action=queue_remove&nid=%s" % nid)
    ml2 = run("action=mylist").get("items") or []
    if any("nid=%s" % nid in u for u, _, _ in ml2):
        print("FAIL  removed title still in My List")
        failures.append("mylist remove")
    else:
        print("OK    My List remove                cleared nid=%s" % nid)
else:
    print("FAIL  no node id from search to test My List")
    failures.append("mylist nid")

print("\nlog tail:")
for line in LOG[-12:]:
    print("  " + line[:200])
print("%d step(s) failed" % len(failures) if failures else "All steps passed")
sys.exit(1 if failures else 0)
