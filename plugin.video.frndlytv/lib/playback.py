"""Resolving a path to a playable ListItem.

Friendly TV's streams are Widevine-protected DASH, and the licence server it
names takes a *raw* Widevine challenge and answers with *raw* licence bytes:
the capture shows a POST whose body begins with the protobuf challenge and a
response of ``application/octet-stream``, with the entitlement carried in a
JWT already baked into the licence url's query string.

That is exactly what InputStream Adaptive speaks natively, so this addon has
no licence proxy. Nothing here translates a challenge, mints a token, or sits
on localhost -- ISA posts straight to the url the service handed back. The
Apple TV+ style proxy in the reference playbook exists because those services
wrap the challenge in a JSON envelope; this one does not.
"""

import json

from urllib.parse import urlencode, urlparse

import xbmcgui

from . import auth, kodiutils

# Handed to the licence server and the CDN. Both are copied from the capture:
# the licence POST carried a browser User-Agent and the site's Origin and
# nothing else that identified the caller.
STREAM_HEADERS = {
    "User-Agent": auth.USER_AGENT,
    "Origin": auth.ORIGIN,
}

CONTEXT_FILE = "playing.json"

WIDEVINE = "com.widevine.alpha"


class PlaybackError(Exception):
    """The stream could not be resolved into something playable."""


def _manifest_type(url):
    """"mpd" or "hls", from the manifest's own filename.

    Every stream in the capture is DASH, but the field the service uses to
    say so (``streamType``) names the DRM system rather than the container,
    so the url is the only thing that actually distinguishes them.
    """
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    return "mpd"


def _pick(streams):
    """The stream to play, out of what the service offered.

    Entries without a url describe side-car assets (the capture's first entry
    is tagged ``eia608/1``, the caption track) and are not manifests.
    """
    for stream in streams or []:
        if stream.get("url"):
            return stream
    return None


def resolve(client, path, label=""):
    """Ask for a stream and build the ListItem that plays it."""
    response = client.stream(path)

    status = response.get("streamStatus") or {}
    if status.get("hasAccess") is False:
        raise PlaybackError(status.get("message") or
                            "Your subscription does not include this channel.")

    stream = _pick(response.get("streams"))
    if not stream:
        raise PlaybackError("Friendly TV returned no playable stream for "
                            "this title.")

    url = stream["url"]
    licence = (stream.get("keys") or {}).get("licenseKey") or ""
    manifest_type = _manifest_type(url)

    kodiutils.log("playing %s: %s manifest, %s licence"
                  % (path, manifest_type,
                     "widevine" if licence else "no"))

    item = xbmcgui.ListItem(label=label or path, path=url)
    item.setContentLookup(False)
    item.setProperty("inputstream", "inputstream.adaptive")
    item.setMimeType("application/dash+xml" if manifest_type == "mpd"
                     else "application/vnd.apple.mpegurl")

    _configure_isa(item, manifest_type, licence)

    if _is_live(response, path):
        # Live has no meaningful end, and a duration gives Kodi a progress
        # bar that runs out while the channel keeps playing.
        item.setProperty("IsLive", "true")

    _remember(response, path)
    return item


def _is_live(response, path):
    if path.startswith("channel/live/"):
        return True
    info = (response.get("analyticsInfo") or {})
    return info.get("contentType") == "live"


def _configure_isa(item, manifest_type, licence_url):
    """Point ISA at the manifest and, when there is one, the licence server.

    Two spellings, because they are read by different ISA generations. ISA 21
    introduced the ``inputstream.adaptive.drm`` JSON property and deprecated
    the ``license_type``/``license_key`` pair; builds before it only
    understand the pair. Only one is written, chosen by the installed
    version, since setting both leaves ISA reading whichever it prefers and
    makes a log ambiguous about which one was in force.
    """
    major = kodiutils.isa_major()

    # ISA 22 dropped manifest_type in favour of sniffing the mime type, which
    # is set on the item either way; on 21 and below it is still required.
    if major and major < 22:
        item.setProperty("inputstream.adaptive.manifest_type", manifest_type)

    headers = urlencode(STREAM_HEADERS)
    item.setProperty("inputstream.adaptive.manifest_headers", headers)
    item.setProperty("inputstream.adaptive.stream_headers", headers)

    if not licence_url:
        return

    if major >= 21:
        item.setProperty("inputstream.adaptive.drm", json.dumps({
            WIDEVINE: {
                "license": {
                    "server_url": licence_url,
                    "req_headers": headers,
                },
            },
        }))
    else:
        item.setProperty("inputstream.adaptive.license_type", WIDEVINE)
        # server|headers|challenge|response. "R{SSM}" is the raw challenge
        # and an empty response field means the body comes back as raw
        # licence bytes, which is what this server does.
        item.setProperty("inputstream.adaptive.license_key",
                         "%s|%s|R{SSM}|" % (licence_url, headers))


def _remember(response, path):
    """Write down the stream slot this play took.

    Friendly TV caps concurrent streams and only frees a slot when something
    posts its poll key back. The plugin process exits the moment it hands the
    url to Kodi, so the service reads this file and does it when playback
    stops.
    """
    info = response.get("sessionInfo") or {}
    kodiutils.write_json(CONTEXT_FILE, {
        "path": path,
        "poll_key": info.get("streamPollKey") or "",
        "poll_interval_ms": info.get("pollIntervalInMillis") or 0,
    })


def ensure_widevine():
    """Make sure a Widevine CDM is present before the first play.

    inputstreamhelper installs it on demand and explains itself when it
    cannot. It is an optional dependency: when it is absent, playback is
    still attempted, because a box that already has the CDM does not need it.
    """
    try:
        import inputstreamhelper
    except ImportError:
        return True
    try:
        helper = inputstreamhelper.Helper("mpd", drm=WIDEVINE)
        return bool(helper.check_inputstream())
    except Exception as exc:
        kodiutils.log("inputstreamhelper could not check Widevine: %s" % exc)
        return True
