"""Building a playable Kodi item out of a player response."""

import xbmcgui

from . import api, auth, cipher, kodiutils, license_proxy, manifest as manifest_mod, sabr, widevine

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


# What each granted DRM track type is worth in picture height. YouTube offers
# 144p-1080p on a live channel regardless of what the account may decrypt.
TRACK_TYPE_HEIGHTS = {
    "DRM_TRACK_TYPE_SD": 480,
    "DRM_TRACK_TYPE_HD": 1080,
    "DRM_TRACK_TYPE_UHD1": 2160,
    "DRM_TRACK_TYPE_UHD2": 4320,
}


def _authorized_cap(streaming):
    """The tallest track this account is actually licensed to decrypt.

    The player response says which track types were granted -- commonly just
    AUDIO and SD -- while the manifest still advertises 720p and 1080p. Left
    alone, ISA picks by screen size, asks the CDM to decrypt a track whose key
    was never issued, and the result is not a clean error: it has taken Kodi
    down. Capping to what was granted keeps the chooser inside the keys we
    hold.

    Returns 0 when the response says nothing, rather than inventing a limit.
    """
    granted = streaming.get("initialAuthorizedDrmTrackTypes") or []
    heights = [TRACK_TYPE_HEIGHTS[t] for t in granted if t in TRACK_TYPE_HEIGHTS]
    return max(heights) if heights else 0


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
        # Ask the CDN for the first segment with exactly the headers ISA uses,
        # so a 403 from ISA can be attributed to the URL or to ISA.
        manifest_mod.probe_segments(response.content, {
            "User-Agent": api.UA,
            "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
        }, cookie_header=auth.cookie_header(auth.load()))
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

    # ISA reads the manifest through the local proxy, which repairs the
    # missing SegmentList attributes that otherwise crash it. See lib/manifest.
    item = xbmcgui.ListItem(label=label or details.get("title") or "",
                            path=license_proxy.manifest_url(manifest))
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

        # YouTube's manifests carry no PSSH ISA can open a session from, which
        # is what "Unhandled encrypted stream" means, on live and on-demand
        # alike. Hand it the one the web player builds for itself; the content
        # id comes out of drmParams and the manifest URL. See lib/widevine.py.
        content = widevine.content_id(streaming.get("drmParams", ""), manifest)
        if content:
            pssh = widevine.build_pssh(content, is_live=is_live)
            kodiutils.log("pssh content id: %s (live=%s)" % (content, is_live))
            item.setProperty("inputstream.adaptive.license_data", pssh)

            # ISA has been opening every session with an unknown KID, because
            # nothing it can read carries one: YouTube's ContentProtection has
            # no cenc:default_KID and a content-id PSSH has no key ids. The
            # licence response does name them, so a previous play of this title
            # leaves them behind for this one. pre_init_data is the property
            # that takes both halves.
            video_id = details.get("videoId") or ""
            known = license_proxy.key_ids_for(video_id)
            key_id = (known.get("DRM_TRACK_TYPE_SD")
                      or known.get("DRM_TRACK_TYPE_AUDIO")
                      or next(iter(known.values()), ""))
            if key_id:
                kodiutils.log("pre_init_data: supplying key id from the "
                              "previous licence (%d known for this title)"
                              % len(known))
                item.setProperty("inputstream.adaptive.pre_init_data",
                                 "%s|%s" % (pssh, key_id))
            else:
                kodiutils.log("no key id known for %s yet -- ISA will open "
                              "with an unknown KID; the licence this play "
                              "fetches will supply one for next time"
                              % (video_id or "this title"))
        else:
            kodiutils.log_error("no content id could be derived -- ISA will "
                                "have no PSSH and will refuse the stream")

    # The lower of what the user asked for and what the licence covers.
    caps = [c for c in (_quality_cap(), _authorized_cap(streaming)) if c]
    cap = min(caps) if caps else 0
    if cap:
        authorized = _authorized_cap(streaming)
        if authorized and cap == authorized:
            kodiutils.log("capping to %dp: the account is licensed for %s"
                          % (cap, ", ".join(streaming.get(
                              "initialAuthorizedDrmTrackTypes") or [])))
        # Both spellings: the chooser properties are what ISA 21+ reads, and
        # max_resolution is the older name still honoured by some builds.
        item.setProperty("inputstream.adaptive.max_resolution", str(cap))
        item.setProperty("inputstream.adaptive.chooser_resolution_max", "%dp" % cap)
        item.setProperty("inputstream.adaptive.chooser_resolution_secure_max",
                         "%dp" % cap)

    headers = "User-Agent=%s&Referer=%s/" % (api.UA, api.ORIGIN)
    item.setProperty("inputstream.adaptive.stream_headers", headers)
    item.setProperty("inputstream.adaptive.manifest_headers", headers)

    return item


def probe_clients(client, video_id):
    """Ask every YouTube TV client identity for the same video and compare.

    We have only ever used WEB_UNPLUGGED, which answers with SABR delivery and
    formats whose URLs are locked behind a signatureCipher. On ordinary YouTube
    the mobile and TV clients are commonly served plain URLs instead, which is
    the whole reason the regular Kodi YouTube addon still works. If any
    Unplugged client does the same here, that is a far shorter road than
    implementing SABR.

    Logs one line per client: whether formats carry a usable url, a
    signatureCipher, or nothing but a SABR endpoint.
    """
    for name in sorted(api.UNPLUGGED_CLIENTS):
        try:
            response = client.player(video_id, api.new_cpn(), client_name=name)
        except api.NotPlayable as exc:
            kodiutils.log("client %-22s unplayable: %s" % (name, exc))
            continue
        except Exception as exc:
            kodiutils.log("client %-22s failed: %s" % (name, exc))
            continue
        streaming = response.get("streamingData") or {}
        formats = streaming.get("adaptiveFormats") or []
        plain = sum(1 for f in formats if f.get("url"))
        ciphered = sum(1 for f in formats if f.get("signatureCipher"))
        kodiutils.log(
            "client %-22s formats=%-3d url=%-3d cipher=%-3d dash=%-5s sabr=%-5s drm=%s"
            % (name, len(formats), plain, ciphered,
               bool(streaming.get("dashManifestUrl")),
               bool(streaming.get("serverAbrStreamingUrl")),
               bool(streaming.get("licenseInfos"))))


def probe_cipher(client, video_id, response):
    """Descramble a format's signatureCipher and see if the CDN serves it.

    Every format comes as a signatureCipher rather than a URL, and we have only
    ever used the DASH manifest's pre-signed segment URLs, which are refused.
    These are a different family -- their sparams carry aitags and bui, matching
    the requests the browser gets 200s for -- so whether they are served is the
    open question this answers.
    """
    formats = (response.get("streamingData") or {}).get("adaptiveFormats") or []
    ciphered = [f for f in formats
                if f.get("signatureCipher") and "video/" in f.get("mimeType", "")]
    if not ciphered:
        kodiutils.log("cipher probe: no ciphered video formats to try")
        return

    headers = {"User-Agent": api.UA, "Cookie": auth.cookie_header(client.cookies)}
    watch = "%s/watch/%s" % (api.ORIGIN, video_id)
    try:
        _url, plan = cipher.fetch_plan(client.session, headers, watch)
    except cipher.CipherError as exc:
        kodiutils.log_error("cipher probe: %s" % exc)
        return
    except Exception as exc:
        kodiutils.log_error("cipher probe: could not read the player: %s" % exc)
        return

    chosen = min(ciphered, key=lambda f: f.get("height") or 9999)
    try:
        resolved = cipher.resolve(chosen["signatureCipher"], plan)
    except cipher.CipherError as exc:
        kodiutils.log_error("cipher probe: %s" % exc)
        return

    token = kodiutils.get_setting("po_token", "")
    attempts = [("descrambled", resolved)]
    if token:
        attempts.append(("descrambled + pot",
                         manifest_mod._add_param(resolved, "pot", token)))
    attempts.append(("descrambled, no n",
                     manifest_mod._strip_param(resolved, "n")))

    import requests
    for name, candidate in attempts:
        try:
            reply = requests.get(candidate, timeout=20, stream=True, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Range": "bytes=0-131071",
            })
            kodiutils.log("cipher probe [%-18s itag %s]: HTTP %d, %s bytes"
                          % (name, chosen.get("itag"), reply.status_code,
                             reply.headers.get("Content-Length", "?")))
            reply.close()
        except Exception as exc:
            kodiutils.log_error("cipher probe [%s] failed: %s" % (name, exc))


def probe_sabr(client, response):
    """POST to the SABR endpoint and report what the server says.

    This is where the media actually is. The DASH URLs are refused; the web
    player POSTs to serverAbrStreamingUrl, which the player response hands us
    verbatim, and gets 15 MB back. The request body is mostly the ustreamer
    config out of the same response, so it can be built rather than guessed.

    A probe, not a player: it asks for one format and reports the UMP parts
    that come back. A 200 with MEDIA parts means the endpoint is reachable and
    the remaining work is feeding those bytes to ISA. An error part means the
    server has said, in its own words, what is missing.
    """
    streaming = response.get("streamingData") or {}
    url = streaming.get("serverAbrStreamingUrl")
    if not url:
        kodiutils.log("sabr probe: no serverAbrStreamingUrl offered")
        return
    try:
        config = (response["playerConfig"]["mediaCommonConfig"]
                  ["mediaUstreamerRequestConfig"]["videoPlaybackUstreamerConfig"])
    except (KeyError, TypeError):
        kodiutils.log("sabr probe: no ustreamer config in the player response")
        return

    formats = streaming.get("adaptiveFormats") or []
    def pick(kind):
        matches = [f for f in formats if kind in (f.get("mimeType") or "")]
        if not matches:
            return None
        # The smallest, since only reachability is being tested.
        return min(matches, key=lambda f: f.get("bitrate") or 1 << 30)

    audio, video = pick("audio/"), pick("video/")
    if not audio and not video:
        kodiutils.log("sabr probe: no formats to ask for")
        return
    wanted = audio or video
    known = [(f["itag"], f.get("lastModified") or 0)
             for f in (video, audio) if f and f is not wanted and f.get("itag")]

    body = sabr.build_request(
        config,
        wanted=(wanted["itag"], wanted.get("lastModified") or 0),
        known=known)
    kodiutils.log("sabr probe: asking for itag %s (%s), %d byte request"
                  % (wanted.get("itag"), wanted.get("mimeType", "")[:24],
                     len(body)))

    try:
        import requests
        reply = requests.post(url, data=body, timeout=30, headers={
            "User-Agent": api.UA,
            "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
            "Content-Type": "application/x-protobuf",
            "Accept": "*/*",
        })
    except Exception as exc:
        kodiutils.log_error("sabr probe: request failed: %s" % exc)
        return

    kodiutils.log("sabr probe: HTTP %d, %d bytes returned"
                  % (reply.status_code, len(reply.content)))
    if reply.status_code != 200:
        kodiutils.log_error("sabr probe: %r" % reply.content[:200])
        return
    kodiutils.log(sabr.describe_response(reply.content))


def replay_captured_sabr():
    """POST a request the browser made, verbatim, and see if we are served.

    Every googlevideo URL our own session mints is refused -- DASH GET and SABR
    POST alike, always HTTP 403 with an empty body, which is a rejection at the
    edge rather than anything SABR said. The browser, from the same IP and
    account in the same minute, is served 15 MB from its own SABR URL.

    That leaves exactly two possibilities, and replaying the browser's own
    request separates them:

      200 -- the URL is fine and ours are not. Something about how our player
             call is made produces URLs that will not be served, and that is
             a difference worth hunting.
      403 -- the URL is irrelevant and we are refused as a client, whatever we
             present. Nothing in the addon can change that.

    A personal build supplies lib/baked_sabr.py with URL and BODY captured from
    a browser session; it is absent from the repository, and without it this is
    a no-op.
    """
    try:
        from . import baked_sabr
    except ImportError:
        return
    url = getattr(baked_sabr, "URL", "")
    body = getattr(baked_sabr, "BODY", b"")
    if not url or not body:
        return
    import time
    from urllib.parse import parse_qs, urlparse
    expires = (parse_qs(urlparse(url).query).get("expire") or ["0"])[0]
    try:
        remaining = int(expires) - int(time.time())
    except ValueError:
        remaining = 0
    if remaining <= 0:
        kodiutils.log("sabr replay: the captured url expired %d minutes ago; "
                      "capture a fresh browser play to retry"
                      % (-remaining // 60))
        return

    kodiutils.log("sabr replay: posting the browser's own request verbatim "
                  "(%d bytes, url valid for %d more minutes)"
                  % (len(body), remaining // 60))
    try:
        import requests
        reply = requests.post(url, data=body, timeout=30, headers={
            "User-Agent": getattr(baked_sabr, "USER_AGENT", api.UA),
            "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
            "Accept": "*/*",
        })
    except Exception as exc:
        kodiutils.log_error("sabr replay: %s" % exc)
        return
    kodiutils.log("sabr replay: HTTP %d, %d bytes"
                  % (reply.status_code, len(reply.content)))
    if reply.status_code == 200:
        kodiutils.log("sabr replay: SERVED -- the browser's url works from "
                      "here, so the refusal is about how our urls are minted, "
                      "not about us as a client")
        kodiutils.log(sabr.describe_response(reply.content))
    else:
        kodiutils.log("sabr replay: refused too -- we are rejected whatever "
                      "url we present, so nothing in the request can fix it")


def prepare(client, video_id, label=None, art=None):
    """Call player, arm the licence proxy, and return a ListItem."""
    cpn = api.new_cpn()
    if kodiutils.get_setting_bool("probe_clients", False):
        probe_clients(client, video_id)
    response = client.player(video_id, cpn)
    if kodiutils.get_setting_bool("probe_sabr", False):
        probe_sabr(client, response)
        replay_captured_sabr()

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
