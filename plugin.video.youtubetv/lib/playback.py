"""Building a playable Kodi item out of a player response."""

import xbmcgui

from . import api, auth, kodiutils, license_proxy

ISA_ADDON = "inputstream.adaptive"

# Widevine's DASH ContentProtection scheme, used to spot a protected manifest.
WIDEVINE_SCHEME = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"


def _ensure_widevine():
    """Ask inputstreamhelper to install Widevine if it is missing.

    Optional dependency: on a platform where it is unavailable, carry on and
    let ISA report the problem itself rather than refusing to try.
    """
    try:
        import inputstreamhelper
    except ImportError:
        return True
    try:
        helper = inputstreamhelper.Helper("mpd", drm="widevine")
        return bool(helper.check_inputstream())
    except Exception as exc:
        kodiutils.log("inputstreamhelper declined: %s" % exc)
        return True


def _quality_cap():
    """Maximum height from settings, 0 meaning unlimited."""
    return kodiutils.get_setting_int("max_height", 0)


def _dump_manifest(url):
    """Save the manifest ISA is about to fetch, when diagnostics are on.

    ISA reports "Unhandled encrypted stream" without saying what about the
    stream it could not handle, and the manifest is the only place the answer
    lives -- which ContentProtection schemes are declared, and whether they
    carry the PSSH init data ISA needs to open a CDM session.
    """
    if not kodiutils.get_setting_bool("dump_manifest", False):
        return
    try:
        import os

        import requests
        response = requests.get(url, timeout=30, headers={
            "User-Agent": api.UA, "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
            "Cookie": auth.cookie_header(auth.load()),
        })
        path = os.path.join(kodiutils.profile_dir(), "last-manifest.mpd")
        with open(path, "wb") as handle:
            handle.write(response.content)
        kodiutils.log("manifest saved to %s (HTTP %d, %d bytes)"
                      % (path, response.status_code, len(response.content)))
        kodiutils.notify("Manifest saved to the addon's profile folder")
    except Exception as exc:
        kodiutils.log_error("could not save the manifest: %s" % exc)


def build_item(player_response, label=None, art=None):
    """A ListItem wired to InputStream Adaptive, or None if unplayable."""
    streaming = player_response.get("streamingData") or {}
    details = player_response.get("videoDetails") or {}

    manifest = streaming.get("dashManifestUrl")
    if not manifest:
        # Only SABR was offered. Nothing in Kodi can play that today, and
        # saying so plainly beats a silent failure in the player.
        kodiutils.log_error("player response has no dashManifestUrl -- SABR "
                            "only, which InputStream Adaptive cannot play")
        return None

    if not _ensure_widevine():
        return None

    _dump_manifest(manifest)

    item = xbmcgui.ListItem(label=label or details.get("title") or "", path=manifest)
    item.setMimeType("application/dash+xml")
    item.setContentLookup(False)

    info = item.getVideoInfoTag()
    info.setTitle(label or details.get("title") or "")
    if details.get("shortDescription"):
        info.setPlot(details["shortDescription"])
    if details.get("author"):
        info.setStudios([details["author"]])
    try:
        length = int(details.get("lengthSeconds") or 0)
        if length:
            info.setDuration(length)
    except (TypeError, ValueError):
        pass
    if art:
        item.setArt({"thumb": art, "icon": art, "fanart": art})

    item.setProperty("inputstream", ISA_ADDON)
    # No manifest_type: ISA detects it from the response and warns that the
    # property is going away. No manifest_update_parameter either -- ISA 22
    # rejects the "full" value outright ("no longer supported") and refreshes
    # live manifests on its own.

    is_live = bool(details.get("isLive"))
    if is_live:
        item.setProperty("isPlayable", "true")
        info.setMediaType("video")

    licence = license_proxy.license_url()
    if streaming.get("licenseInfos"):
        if not licence:
            kodiutils.log_error("the licence proxy is not running -- is the "
                                "addon's service enabled?")
            return None
        item.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        # ISA posts the raw challenge (R{SSM}) to the proxy, which wraps it for
        # YouTube and returns raw licence bytes, so no response templating.
        item.setProperty("inputstream.adaptive.license_key",
                         "%s|Content-Type=application/octet-stream|R{SSM}|" % licence)

    cap = _quality_cap()
    if cap:
        item.setProperty("inputstream.adaptive.max_resolution", str(cap))
        item.setProperty("inputstream.adaptive.max_bandwidth", "0")

    headers = "User-Agent=%s&Referer=%s/" % (api.UA, api.ORIGIN)
    item.setProperty("inputstream.adaptive.stream_headers", headers)
    item.setProperty("inputstream.adaptive.manifest_headers", headers)

    return item


def prepare(client, video_id, label=None, art=None):
    """Call player, arm the licence proxy, and return a ListItem."""
    cpn = api.new_cpn()
    response = client.player(video_id, cpn)

    streaming = response.get("streamingData") or {}
    details = response.get("videoDetails") or {}
    license_proxy.set_context(
        video_id=video_id,
        cpn=cpn,
        drm_params=streaming.get("drmParams", ""),
        is_live=bool(details.get("isLive")),
    )

    authorized = streaming.get("initialAuthorizedDrmTrackTypes") or []
    kodiutils.log("play %s (%s): live=%s authorized=%s"
                  % (video_id, details.get("title"), details.get("isLive"),
                     ",".join(authorized) or "none"))

    return response, build_item(response, label=label, art=art)
