"""Does the manifest describe every rendition, and does want() narrow?

No network: a Session is built by hand and its post() answers with the
captured shapes, so this exercises the manifest and the narrowing logic
alone.
"""
import sys, os, xml.dom.minidom
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SP, "kodistub"))
sys.path.insert(0, "/home/user/Kodi-Addons/plugin.video.youtubetv")

from lib import sabr_bridge, sabr_session, kodiutils

CANDIDATES = [
    {"itag": 149, "mimeType": 'audio/mp4; codecs="mp4a.40.2"', "bitrate": 128000,
     "audioSampleRate": "44100", "xtags": "primary", "approxDurationMs": "1320000"},
    {"itag": 143, "mimeType": 'video/mp4; codecs="avc1.4d401e"', "bitrate": 500000,
     "width": 640, "height": 360, "fps": 30, "approxDurationMs": "1320000"},
    {"itag": 223, "mimeType": 'video/mp4; codecs="avc1.4d401f"', "bitrate": 1200000,
     "width": 854, "height": 480, "fps": 30, "approxDurationMs": "1320000"},
    {"itag": 224, "mimeType": 'video/mp4; codecs="avc1.640028"', "bitrate": 3000000,
     "width": 1920, "height": 1080, "fps": 30, "approxDurationMs": "1320000"},
    {"itag": 810, "mimeType": 'video/mp4; codecs="av01.0.08M.08"', "bitrate": 2500000,
     "width": 1920, "height": 1080, "fps": 30, "approxDurationMs": "1320000"},
]
BY = {f["itag"]: f for f in CANDIDATES}
entry = lambda itag: (itag, 0, "")

asked = []

def post(url, body):
    asked.append(body)
    return b""

def build():
    del asked[:]
    session = sabr_session.Session(
        "https://example/videoplayback", "", [entry(149)],
        [entry(143), entry(223), entry(224)],
        "WEB_UNPLUGGED", 41, "1.0", post, live=False)
    # What one primed exchange would have left behind.
    for itag, seq in ((149, 3), (149, 4), (223, 3), (223, 4)):
        session.segments.setdefault(itag, {})[seq] = b"\0" * 16
        session.held.setdefault(itag, {})[seq] = seq * 20000
    formats = {"audio": BY[149], "video": BY[223], "drm_params": "",
               "candidates": CANDIDATES, "max_height": 1080,
               "video_id": "x", "refused": [], "compared": True}
    sabr_bridge._sessions["k"] = (session, formats)
    return session, formats

base = {"url": "http://127.0.0.1:1", "secret": "s"}

# -- off: one Representation per track, as it plays today ----------------
session, formats = build()
mpd = sabr_bridge.manifest("k", base)
doc = xml.dom.minidom.parseString(mpd)
reps = doc.getElementsByTagName("Representation")
print("adaptive off: %d representation(s): %s"
      % (len(reps), [r.getAttribute("id") for r in reps]))
assert [r.getAttribute("id") for r in reps] == ["149", "223"], mpd

# -- the manifest names only what the session can serve ------------------
session, formats = build()
formats["drm_params"] = ""
mpd = sabr_bridge.manifest("k", base)
doc = xml.dom.minidom.parseString(mpd)
ids = sorted(int(r.getAttribute("id"))
             for r in doc.getElementsByTagName("Representation"))
print("with nine renditions offered, the manifest names: %s" % ids)
assert ids == [149, 223], ids
held = sorted(session.segments)
assert ids == held, ("the manifest must name exactly what is held", ids, held)
print("which is exactly what the session holds")

# -- the abr state carries the real ceiling, not a hardcoded 1080 --------
from lib import sabr
session, formats = build()
session.wanted_height = 2160
del asked[:]
session.fetch()
state = dict((n, v) for n, _w, v in sabr.fields(
    dict((n, v) for n, _w, v in sabr.fields(asked[0]))[1]))
print("the request asks for %sp" % state.get(59))
assert state.get(59) == 2160
print("PASS")
