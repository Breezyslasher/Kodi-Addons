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


def resolve(client, path, label="", from_start=False):
    """Ask for a stream and build the ListItem that plays it.

    ``from_start`` is a "start over": the viewer asked for a programme from
    its beginning rather than from where the channel happens to be.
    """
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

    # What the service says this stream is and where it wants playback to
    # begin. Worth logging every time: it is the only way to tell from a log
    # whether a "start over" really started over, since the answer to
    # epg/play/<id> is a VOD asset for a finished programme but a live
    # manifest for one still on the air.
    seek_ms = _int(status.get("seekPositionInMillis"))
    total_ms = _int(status.get("totalDurationInMillis"))
    kodiutils.log("playing %s: %s manifest, %s licence, %s, starts at %s of %s"
                  % (path, manifest_type, "widevine" if licence else "no",
                     (response.get("analyticsInfo") or {}).get("contentType")
                     or "unknown type",
                     _hms(seek_ms), _hms(total_ms) if total_ms else "unknown"))

    item = xbmcgui.ListItem(label=label or path, path=url)
    item.setContentLookup(False)
    item.setProperty("inputstream", "inputstream.adaptive")
    item.setMimeType("application/dash+xml" if manifest_type == "mpd"
                     else "application/vnd.apple.mpegurl")

    _configure_isa(item, manifest_type, licence, from_start)

    if _is_live(response, path) and not from_start:
        # Live has no meaningful end, and a duration gives Kodi a progress
        # bar that runs out while the channel keeps playing. Not set for a
        # start-over: that is a finite programme being watched from its
        # beginning, and flagging it live would deny it a seek bar.
        item.setProperty("IsLive", "true")

    if seek_ms > 0 and total_ms > seek_ms:
        # The service asked for playback to begin partway in, and this is
        # how Continue Watching actually resumes: opening an episode from
        # that row answers with seekPositionInMillis set, ISA seeks there,
        # and playback starts where the viewer left off. The card's own
        # "seek" marker is the same progress expressed as a fraction, but it
        # is this that does the work.
        try:
            item.getVideoInfoTag().setResumePoint(seek_ms / 1000.0,
                                                  total_ms / 1000.0)
        except (AttributeError, TypeError):
            item.setProperty("ResumeTime", "%.0f" % (seek_ms / 1000.0))
            item.setProperty("TotalTime", "%.0f" % (total_ms / 1000.0))
        kodiutils.log("the service asked to start at %s" % _hms(seek_ms))

    _remember(response, path, from_start)
    return item


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hms(milliseconds):
    seconds = int((milliseconds or 0) / 1000)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def _is_live(response, path):
    if path.startswith("channel/live/"):
        return True
    info = (response.get("analyticsInfo") or {})
    return info.get("contentType") == "live"


def _configure_isa(item, manifest_type, licence_url, from_start=False):
    """Point ISA at the manifest and, when there is one, the licence server.

    Two spellings, because they are read by different ISA generations, and the
    boundary between them is **ISA 22.1.5** -- not 21, which is what this
    addon first shipped and why every protected stream died on Kodi 21 with
    "InitializePeriod: Unhandled encrypted stream". ISA 21 does not read the
    JSON property at all; it does not complain about it either, it simply
    reaches the encrypted stream with no key system configured.

    Only one spelling is written, so a log is never ambiguous about which was
    in force.
    """
    json_drm = kodiutils.isa_has_json_drm()

    # ISA 22 sniffs the container from the mime type, which is set on the item
    # either way, and warns that this property is deprecated. Below that it is
    # still what tells ISA which parser to use.
    if not json_drm:
        item.setProperty("inputstream.adaptive.manifest_type", manifest_type)

    headers = urlencode(STREAM_HEADERS)
    item.setProperty("inputstream.adaptive.manifest_headers", headers)
    item.setProperty("inputstream.adaptive.stream_headers", headers)

    if from_start:
        # Asked for a programme still on the air, the service answers with a
        # *live* manifest -- not the VOD asset it returns once the programme
        # has finished -- and ISA opens a live manifest at the live edge. So
        # "start over" played from wherever the channel was.
        #
        # This tells ISA to begin at the start of the manifest's timeshift
        # window instead. The window is what the service chose to publish for
        # this programme's path (five periods, against one or two for the
        # plain channel), so its beginning is the closest thing to the
        # programme's start that the manifest offers.
        item.setProperty("inputstream.adaptive.play_timeshift_buffer", "true")
        kodiutils.log("start over: opening at the beginning of the "
                      "timeshift window rather than the live edge")

    if not licence_url:
        kodiutils.log("no licence url on this stream; playing unencrypted")
        return

    if json_drm:
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
    kodiutils.log("DRM configured for %s (%s)"
                  % (kodiutils.isa_version() or "unknown ISA",
                     "json drm" if json_drm else "legacy properties"))


def _remember(response, path, from_start=False):
    """Write down the stream slot this play took.

    Friendly TV caps concurrent streams and only frees a slot when something
    posts its poll key back. The plugin process exits the moment it hands the
    url to Kodi, so the service reads this file and does it when playback
    stops.

    ``from_start`` rides along for the log: the service reports where playback
    actually began, and that is only interesting against what was asked for.
    """
    info = response.get("sessionInfo") or {}
    kodiutils.write_json(CONTEXT_FILE, {
        "path": path,
        "poll_key": info.get("streamPollKey") or "",
        "poll_interval_ms": info.get("pollIntervalInMillis") or 0,
        "from_start": bool(from_start),
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
