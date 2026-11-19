"""Build the Kodi ListItem that plays a free Plex title through InputStream Adaptive.

Capture-confirmed behaviour (tools/probe.py):
  * Every title lists two Media: protocol "hls" and protocol "dash".
  * Clear titles (Media.drm false): plain fMP4 HLS. Only the manifest on
    vod.provider.plex.tv needs X-Plex-Token; the variants and segments on
    vod-content.plexvideos.com answer without any header.
  * DRM titles (~half the catalogue): the HLS part is FairPlay (useless here),
    the DASH part is CENC with a Widevine PSSH, licence at
    Part.license = /library/parts/<id>-dash/license on the VOD host.
  * The licence POST needs header X-Plex-DRM: widevine (else 400 "Missing
    DRM"); with it the raw Widevine challenge/response pass straight through,
    so ISA calls it directly -- no local licence proxy.
  * HTTP 451 on a manifest: "Playback is unavailable while using a VPN or
    proxy" -- some licensors block datacenter/VPN addresses per title.
  * Subtitles: the HLS master carries a WebVTT rendition whose URI has the
    real .vtt on vod-static.plex.tv in its url= parameter.
"""

import json
import re
from urllib.parse import quote, urlparse, parse_qs, urljoin

import xbmc
import xbmcaddon
import xbmcgui

from . import kodiutils
from . import listing

MIME = {"hls": "application/vnd.apple.mpegurl", "dash": "application/dash+xml"}


class DrmUnavailable(Exception):
    pass


class Blocked(Exception):
    pass


def _pick(item):
    """(media, part, protocol, is_drm): prefer clear HLS, else Widevine DASH."""
    medias = item.get("Media") or []
    clear = [m for m in medias if not m.get("drm")]
    for proto in ("hls", "dash"):
        for m in clear:
            if m.get("protocol") == proto and m.get("Part"):
                return m, m["Part"][0], proto, False
    for m in medias:
        part = (m.get("Part") or [{}])[0]
        if m.get("protocol") == "dash" and part.get("drm") == "widevine":
            return m, part, "dash", True
    return None, None, None, None


def _abs(api, path):
    return path if path.startswith("http") else api.base + path


def _header_str(headers):
    return "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in headers.items())


def _subtitles(api, manifest_url, headers):
    """External .vtt URLs from the HLS master's subtitle renditions."""
    try:
        resp = api.session.get(manifest_url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return []
        subs = []
        for uri in re.findall(r'TYPE=SUBTITLES[^\n]*URI="([^"]+)"', resp.text):
            real = parse_qs(urlparse(uri).query).get("url")
            subs.append(real[0] if real else urljoin(manifest_url, uri))
        return subs
    except Exception as exc:
        kodiutils.log_error("subtitle lookup failed: %s" % exc)
        return []


def _stream_subtitles(api, part):
    """Subtitle files listed on the Part (DRM DASH parts included): each
    /library/streams/<id>-<lang>?format=srt redirects to an open file."""
    subs = []
    for st in part.get("Stream") or []:
        if st.get("streamType") != 3 or not st.get("key"):
            continue
        try:
            u = api.subtitle_url(st["key"], "srt" if "srt" in (st.get("formats") or "srt") else "vtt")
            if u and u not in subs:
                subs.append(u)
        except Exception as exc:
            kodiutils.log_error("subtitle %s failed: %s" % (st.get("key"), exc))
    return subs


def _check(api, manifest_url, headers):
    resp = api.session.get(manifest_url, headers=headers, timeout=20, stream=True)
    status = resp.status_code
    resp.close()
    if status == 451:
        raise Blocked("HTTP 451")
    if status >= 400:
        raise Exception("manifest HTTP %s" % status)


def build(api, rating_key):
    item_meta = api.metadata(rating_key, markers=True)
    media, part, proto, drm = _pick(item_meta)
    if not part:
        raise DrmUnavailable()
    manifest = _abs(api, part["key"])
    headers = api.headers()
    _check(api, manifest, headers)

    li = xbmcgui.ListItem(path=manifest)
    li.setProperty("inputstream", "inputstream.adaptive")
    li.setMimeType(MIME[proto])
    li.setContentLookup(False)
    # Token only matters on the manifest; segments are open, and sending Plex
    # headers to the CDN is harmless but unnecessary.
    li.setProperty("inputstream.adaptive.manifest_headers",
                   _header_str({"X-Plex-Token": headers["X-Plex-Token"],
                                "X-Plex-Client-Identifier": headers["X-Plex-Client-Identifier"]}))
    if kodiutils.get_setting_int("max_height", 0):
        li.setProperty("inputstream.adaptive.chooser_resolution_max",
                       "%dp" % kodiutils.get_setting_int("max_height"))

    if kodiutils.get_setting_bool("subtitles", True):
        subs = _stream_subtitles(api, part)
        if not subs and proto == "hls":
            subs = _subtitles(api, manifest, headers)
        if subs:
            li.setSubtitles(subs)

    if drm:
        _widevine(li, _abs(api, part["license"]), headers)

    li.setArt(listing.art(item_meta))
    listing.info(li, item_meta)
    nxt = None
    if item_meta.get("type") == "episode":
        try:
            nxt = api.next_item(item_meta["ratingKey"])
        except Exception as exc:
            kodiutils.log("up next lookup failed: %s" % exc)
    _remember(item_meta, nxt)
    return li


def _widevine(li, license_url, headers):
    try:
        import inputstreamhelper
        if not inputstreamhelper.Helper("mpd", drm="com.widevine.alpha").check_inputstream():
            raise DrmUnavailable()
    except ImportError:
        kodiutils.log("inputstreamhelper not installed; assuming Widevine is present")
    req = _header_str({"X-Plex-Token": headers["X-Plex-Token"],
                       "X-Plex-Client-Identifier": headers["X-Plex-Client-Identifier"],
                       "X-Plex-DRM": "widevine",
                       "Content-Type": "application/octet-stream"})
    android = xbmc.getCondVisibility("System.Platform.Android")
    if _isa_version() >= (22, 1, 5):
        cfg = {"priority": 1, "secure_decoder": bool(android),
               "license": {"server_url": license_url, "req_headers": req}}
        li.setProperty("inputstream.adaptive.drm", json.dumps({"com.widevine.alpha": cfg}))
    else:
        li.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        li.setProperty("inputstream.adaptive.license_key", "%s|%s|R{SSM}|" % (license_url, req))
        if android:
            li.setProperty("inputstream.adaptive.secure_decoder", "true")


def _isa_version():
    try:
        raw = xbmcaddon.Addon("inputstream.adaptive").getAddonInfo("version")
    except Exception:
        return (0, 0, 0)
    parts = [int("".join(c for c in t if c.isdigit()) or 0) for t in str(raw).split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def _remember(m, nxt=None):
    """Hand the playing title to service.py: /timeline progress reports,
    the credits marker and the next episode for the skip-buttons countdown."""
    credits = None
    for mk in m.get("Marker") or []:
        if mk.get("type") == "credits" and mk.get("startTimeOffset") is not None:
            credits = int(mk["startTimeOffset"]) / 1000.0
    xbmcgui.Window(10000).setProperty("plexondemand.playing", json.dumps({
        "ratingKey": m.get("ratingKey"), "key": m.get("key") or "/library/metadata/%s" % m.get("ratingKey"),
        "duration": m.get("duration") or 0, "credits": credits,
        "next": (nxt or {}).get("ratingKey")}))
