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


# The labels ISA's representation chooser accepts. 2160p is not among them --
# it answers "Resolution not valid" and falls back to the screen size -- so a
# ceiling above 1080p is expressed by setting no ceiling at all.
_CHOOSER_LABELS = (480, 576, 720, 1080)


def _chooser_label(cap):
    """The ISA chooser label for a height cap, or "" for no restriction."""
    usable = [h for h in _CHOOSER_LABELS if h >= cap]
    return "%dp" % min(usable) if usable else ""


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


def _authorized_cap(streaming, video_id=""):
    """The tallest track this account is actually licensed to decrypt.

    Two sources disagree, and the field name says which to believe:
    streamingData carries *initial*AuthorizedDrmTrackTypes, commonly just AUDIO
    and SD, while the licence that comes back a moment later grants AUDIO, SD,
    HD and UHD1 for the same title. The initial list is a hint from before the
    exchange; the licence is the answer.

    So prefer what the licence granted, which the proxy records per title on
    its way past. The first play of a title has none yet and falls back to the
    hint -- one play at a lower resolution, then the real ceiling.
    """
    granted = list(license_proxy.key_ids_for(video_id)) if video_id else []
    source = "the licence"
    if not granted:
        granted = streaming.get("initialAuthorizedDrmTrackTypes") or []
        source = "the player response (no licence seen for this title yet)"
    heights = [TRACK_TYPE_HEIGHTS[t] for t in granted if t in TRACK_TYPE_HEIGHTS]
    if not heights:
        return 0
    tallest = max(heights)
    kodiutils.log("licensed up to %dp according to %s: %s"
                  % (tallest, source, ", ".join(sorted(granted))))
    return tallest


def _dump_manifest(url):
    """Save the manifest ISA is about to fetch, when diagnostics are on.

    ISA reports "Unhandled encrypted stream" without saying what about the
    stream it could not handle, and the manifest is the only place the answer
    lives -- which ContentProtection schemes are declared, and whether they
    carry the PSSH init data ISA needs to open a CDM session.

    Fetched through the proxy, not from YouTube. The upstream copy is the one
    nobody plays: its n is still as minted, so every segment url in it is a
    403 by construction, and the probe below spent several builds reporting
    that as though it were news. What ISA reads is what the proxy returns.
    """
    if not kodiutils.get_setting_bool("dump_manifest", False):
        return
    try:
        import os

        import requests
        response = requests.get(license_proxy.manifest_url(url), timeout=60,
                                headers={
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


def probe_subsegments(manifest_url):
    """Ask what is actually at the second subsegment's offset.

    Not wired into playback any more: this answered its question -- the
    fragments and their crypto boxes were sound, the decrypter was not -- and
    it costs a handful of ranged GETs per play. Kept for the next time a
    fragment needs looking at; call it by hand from prepare().

    Playback dies one subsegment in: audio turns to invalid AAC and the CDM
    answers kNoKey for video, both at about 9.5 seconds, which is one audio
    subsegment. Every explanation tried so far -- the key id, the CDM session,
    the resolution -- has been ruled out by a measurement, so this measures the
    remaining one. If the bytes at that offset are not the start of a movie
    fragment, nothing downstream could have worked, and the question becomes
    why the range came back wrong rather than why decryption failed.

    Reads each track's SegmentIndex, then fetches subsegment 1 and subsegment 2
    and reports the box each one begins with. Subsegment 2 is fetched a second
    time without the pot, because that is the one parameter this addon adds to
    a URL YouTube signed and the browser never sends it on this path.
    """
    import requests

    from . import mp4
    proxied = license_proxy.manifest_url(manifest_url)
    headers = {"User-Agent": api.UA, "Origin": api.ORIGIN,
               "Referer": api.ORIGIN + "/"}
    body = requests.get(proxied, timeout=60, headers=dict(
        headers, Cookie=auth.cookie_header(auth.load()))).content

    def fetch(url, first, last):
        response = requests.get(url, timeout=30, headers=dict(
            headers, Range="bytes=%d-%d" % (first, last)))
        return response.status_code, response.content

    for ident, url, index_first, index_last in manifest_mod.index_targets(body)[:4]:
        try:
            # The init segment is everything before the index. Whether its
            # sample entry is enca/encv or mp4a/avc1 decides whether ISA
            # believes the track is encrypted at all -- and the fragments say
            # it is, from the second one on.
            status, moov = fetch(url, 0, index_first - 1)
            protected, iv_size, kid = mp4.track_encryption(moov)
            kodiutils.log("subsegment probe [%s init @0+%d]: HTTP %d, "
                          "tenc protected=%s iv_size=%s kid=%s"
                          % (ident, index_first, status, protected, iv_size,
                             kid[:8]))
            kodiutils.log("subsegment probe [%s init] boxes: %s"
                          % (ident, " ".join(b.strip() for b in
                                             mp4.box_tree(moov, limit=80))))
            status, sidx = fetch(url, index_first, index_last)
            subs = mp4.subsegments(sidx, index_first)
            kodiutils.log("subsegment probe [%s]: sidx HTTP %d, %d bytes, "
                          "%d subsegments" % (ident, status, len(sidx), len(subs)))
            for number in (0, 1):
                if number >= len(subs):
                    break
                offset, size, _ = subs[number]
                status, head = fetch(url, offset, offset + 8191)
                kodiutils.log("subsegment probe [%s #%d @%d+%d]: HTTP %d, "
                              "boxes: %s"
                              % (ident, number, offset, size, status,
                                 " ".join(b.strip() for b in
                                          mp4.box_tree(head))))
                note = mp4.crypto_info(head, offset) or "nothing"
                if "saiz default_size=" in note and iv_size is not None:
                    declared = int(note.split("saiz default_size=")[1]
                                   .split()[0])
                    note += ("; tenc iv_size=%d vs saiz %d -> %s"
                             % (iv_size, declared,
                                "AGREE" if iv_size == declared
                                else "DISAGREE"))
                kodiutils.log("subsegment probe [%s #%d] crypto: %s"
                              % (ident, number, note))
            if len(subs) > 1:
                offset = subs[1][0]
                bare = manifest_mod._strip_param(url, "pot")
                status, head = fetch(bare, offset, offset + 63)
                kodiutils.log("subsegment probe [%s #1 no pot]: HTTP %d, "
                              "starts with %r"
                              % (ident, status, mp4.first_box_type(head)))
        except Exception as exc:
            kodiutils.log_error("subsegment probe [%s] failed: %s" % (ident, exc))


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
            kodiutils.log("pssh content id: %s (live=%s)" % (content, is_live))

            # Deliberately NOT set as inputstream.adaptive.license_data. The
            # property looks like "an extra source of init data" and is in fact
            # a switch, and leaving it on is what silences the audio track.
            # Three steps, all of them in ISA's source rather than inferred:
            #
            # 1. src/parser/DASHTree.cpp
            #        m_isCustomInitPssh = !kodiProps.GetLicenseData().empty();
            #        ...
            #        if (m_isCustomInitPssh || GetProtectionData(...))
            #          InsertPsshSet(..., pssh, kid, licenseUrl);
            #    With the property set the || never evaluates its right side,
            #    so pssh and kid are inserted empty and every cenc:default_KID
            #    this addon computes is discarded before it is read. That is
            #    the "Cannot convert KID \"\"" line.
            #
            # 2. src/decrypters/widevine/WVCencSingleSampleDecrypter.cpp,
            #    GetCapabilities, which is handed that empty kid:
            #        m_fragmentPool[poolId].m_key =
            #            keyId.empty() ? m_keys.front().m_keyId : keyId;
            #    so the capability probe decrypts a test sample with whichever
            #    key the licence happened to list first.
            #
            # 3. When that probe fails, the same function does:
            #        if (media == SSD_MEDIA_VIDEO)
            #          caps.flags |= SSD_SECURE_PATH | SSD_ANNEXB_REQUIRED;
            #        else
            #          caps.flags = SSD_INVALID;
            #    and Session.cpp answers SSD_INVALID with RemovePSSHSet().
            #    Video falls back to the secure path and plays; audio is
            #    removed outright. Video with no sound, exactly as reported.
            #
            # Unset, GetProtectionData runs and reads the manifest the proxy
            # serves: a <cenc:pssh> and that track's own cenc:default_KID on
            # every Representation, so each track probes with its own key.
            # See manifest.set_key_ids and the licence proxy's manifest
            # handler.
            #
            # Note the cost this shares with the resolution ceiling: the key
            # ids come from a licence, so the first play of a title still has
            # an empty kid and may still lose audio. The second play has them.
            #
            # No pre_init_data either, for the reason it was removed: it pins
            # the CDM to one session for one key, and the licence grants four.
            video_id = details.get("videoId") or ""
            known = license_proxy.key_ids_for(video_id)
            if known:
                kodiutils.log("keys known for %s: %s -- the manifest names "
                              "them per track" % (video_id or "this title",
                                                  ", ".join(sorted(known))))
            else:
                kodiutils.log("no key ids known for %s yet: the licence this "
                              "play fetches supplies them, so the first play "
                              "of a title may not decrypt"
                              % (video_id or "this title"))
        else:
            kodiutils.log_error("no content id could be derived -- ISA will "
                                "have no PSSH and will refuse the stream")

    # The lower of what the user asked for and what the licence covers.
    licensed = _authorized_cap(streaming, details.get("videoId") or "")
    caps = [c for c in (_quality_cap(), licensed) if c]
    cap = min(caps) if caps else 0
    if cap:
        kodiutils.log("capping to %dp (%s)"
                      % (cap, "the licence's ceiling" if cap == licensed
                         else "your quality setting"))
        # Only labels ISA actually accepts. It answered "Resolution not valid"
        # for 2160p and then fell back to the screen size, so the cap was both
        # noisy and ineffective. A ceiling at or above the tallest label is not
        # a restriction anyway: leaving the properties unset lets ISA pick
        # freely, which is what "licensed to 2160p" should mean.
        label = _chooser_label(cap)
        if label:
            item.setProperty("inputstream.adaptive.max_resolution", str(cap))
            item.setProperty("inputstream.adaptive.chooser_resolution_max", label)
            item.setProperty("inputstream.adaptive.chooser_resolution_secure_max",
                             label)
        else:
            kodiutils.log("not restricting the chooser: %dp is at or above "
                          "everything ISA offers to cap" % cap)

    headers = "User-Agent=%s&Referer=%s/" % (api.UA, api.ORIGIN)
    item.setProperty("inputstream.adaptive.stream_headers", headers)
    item.setProperty("inputstream.adaptive.manifest_headers", headers)

    return item


SURVEY_FILE = "client_survey.json"
# Bumped when the survey changes, so a corrected request is retried
# rather than skipped because an older, broken one already ran.
SURVEY_REVISION = 3


def survey_clients_once(client, video_id):
    """Run the client survey the first time, without being asked.

    The question it answers is not a debugging curiosity any more: n is the
    gate, the player that computes n is behind an opcode VM we cannot read, and
    the only cheap way out is a client identity whose urls do not carry n. That
    is worth one automatic survey rather than a setting the user has to find.

    Recorded per player release, so it runs once and then stays quiet -- and
    runs again by itself when Google ships a new player, which is exactly when
    the answer could change.
    """
    boot = api._bootstrap()
    player_id = "%s@%d" % (boot.get("js_url") or boot.get("version")
                           or "unknown", SURVEY_REVISION)
    done = kodiutils.read_json(SURVEY_FILE, default={}) or {}
    if done.get("player") == player_id:
        return
    kodiutils.log("client survey: n cannot be computed from this player, so "
                  "asking every identity whether its urls need one. This runs "
                  "once per player release.")
    try:
        probe_clients(client, video_id)
    except Exception as exc:
        kodiutils.log_error("client survey failed: %s" % exc)
    kodiutils.write_json(SURVEY_FILE, {"player": player_id})


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

        # Counting formats is not the question any more. n is now known to be
        # the gate, and the player that computes it is behind an opcode VM we
        # cannot read, so what matters about a client is whether the urls it
        # hands out need n at all -- and whether googlevideo actually serves
        # one. A client answering with a url that needs no n, and being served,
        # ends this whole line of work.
        plain_url = next((f["url"] for f in formats if f.get("url")), None)
        if not plain_url:
            continue
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(plain_url).query)
        try:
            import requests
            reply = requests.get(plain_url, timeout=20, headers={
                "User-Agent": api.client_spec(name)["context"].get(
                    "userAgent", api.UA),
                "Range": "bytes=0-2047",
            })
            served = "HTTP %d, %d bytes" % (reply.status_code,
                                            len(reply.content))
        except Exception as exc:
            served = "fetch failed: %s" % exc
        kodiutils.log("client %-22s   first url: n=%s  sig=%s  -> %s"
                      % (name, (query.get("n") or ["absent"])[0],
                         "present" if query.get("sig") else "absent", served))


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


def probe_sabr(client, response, cpn):
    """POST to the SABR endpoint and report what the server says.

    This is where the media actually is. The DASH URLs are refused; the web
    player POSTs to serverAbrStreamingUrl, which the player response hands us
    verbatim, and gets 15 MB back. The request body is mostly the ustreamer
    config out of the same response, so it can be built rather than guessed.

    A probe, not a player: it asks for one format and reports the UMP parts
    that come back. A 200 with MEDIA parts means the endpoint is reachable and
    the remaining work is feeding those bytes to ISA. An error part means the
    server has said, in its own words, what is missing.
    
    Not wired into playback any more: the answer is known -- our body is
    accepted, our url is refused, and n is the parameter that differs -- so
    it only drew HTTP 403s on every play. Kept for the next refusal.
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

    # The browser rewrites n before it fetches: the player hands out a
    # scrambled value and the page's JS transforms it. n is not in sparams, so
    # changing it does not invalidate sig -- the edge checks it separately, and
    # refuses a value it did not expect. We have no JS engine, so measure what
    # the alternatives cost: dropping n entirely is how ordinary YouTube
    # degrades to throttled-but-served, and if that holds here there is nothing
    # left to solve.
    import requests
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    base = sabr.playback_url(url, cpn, api._client_version(), api.CLIENT_NAME)
    parts = urlparse(base)
    query = parse_qs(parts.query, keep_blank_values=True)
    kodiutils.log("sabr probe: n=%s (as the player minted it)"
                  % (query.get("n") or ["-"])[0])

    def variant(label, mutate):
        altered = {k: list(v) for k, v in query.items()}
        mutate(altered)
        target = urlunparse(parts._replace(query=urlencode(altered, doseq=True)))
        try:
            reply = requests.post(target, data=body, timeout=30, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Accept": "*/*",
            })
        except Exception as exc:
            kodiutils.log_error("sabr probe [%s]: %s" % (label, exc))
            return
        kodiutils.log("sabr probe [%-16s]: HTTP %d, %d bytes"
                      % (label, reply.status_code, len(reply.content)))
        if reply.status_code == 200 and len(reply.content) > 1000:
            kodiutils.log(sabr.describe_response(reply.content))

    variant("n as minted", lambda q: None)
    variant("n removed", lambda q: q.pop("n", None))
    variant("n and ns removed", lambda q: (q.pop("n", None), q.pop("ns", None)))


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
    
    Not wired into playback any more: the answer is known -- our body is
    accepted, our url is refused, and n is the parameter that differs -- so
    it only drew HTTP 403s on every play. Kept for the next refusal.
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

    # A url known to be served is worth more than another refused one: break it
    # in one place at a time and the refusal names its own cause. n is the only
    # difference between our url and theirs that is not simply session state,
    # and it is the expensive one to fix -- solving it means interpreting the
    # player's JavaScript. Damaging n on a url that otherwise works says
    # whether that expense buys anything, before it is spent.
    import requests
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    original = (query.get("n") or [""])[0]

    def attempt(label, params):
        target = urlunparse(parts._replace(query=urlencode(params, doseq=True)))
        try:
            reply = requests.post(target, data=body, timeout=30, headers={
                "User-Agent": getattr(baked_sabr, "USER_AGENT", api.UA),
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Accept": "*/*",
            })
        except Exception as exc:
            kodiutils.log_error("sabr replay [%s]: %s" % (label, exc))
            return None
        kodiutils.log("sabr replay [%-14s]: HTTP %d, %d bytes"
                      % (label, reply.status_code, len(reply.content)))
        return reply

    verbatim = attempt("verbatim", query)
    if not verbatim or verbatim.status_code != 200:
        kodiutils.log("sabr replay: the captured url is no longer served, so "
                      "the variants below prove nothing")
        return

    # Same length and alphabet, one character rotated: if the edge cared only
    # that n is present and well formed, this would still be served.
    damaged = dict(query)
    if original:
        swapped = ("b" if original[0] != "b" else "c") + original[1:]
        damaged["n"] = [swapped]
        attempt("n altered", damaged)
    attempt("n dropped", {k: v for k, v in query.items() if k != "n"})

    kodiutils.log("sabr replay: SERVED verbatim -- read the variants above: if "
                  "altering n alone loses the 200, n is the gate and computing "
                  "it from the player js is the remaining work")
    kodiutils.log(sabr.describe_response(verbatim.content))


def prepare(client, video_id, label=None, art=None):
    """Call player, arm the licence proxy, and return a ListItem."""
    cpn = api.new_cpn()
    if kodiutils.get_setting_bool("probe_clients", False):
        probe_clients(client, video_id)
    else:
        survey_clients_once(client, video_id)
    response = client.player(video_id, cpn)

    streaming = response.get("streamingData") or {}
    details = response.get("videoDetails") or {}
    is_live = bool(details.get("isLive"))
    try:
        duration = float(details.get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    license_proxy.set_context(
        video_id=video_id,
        cpn=cpn,
        drm_params=streaming.get("drmParams", ""),
        is_live=is_live,
        heartbeat=response.get("heartbeatParams") or {},
        tracking=(response.get("playbackTracking") or {}
                  if kodiutils.get_setting_bool("report_progress", True)
                  else {}),
        duration=duration,
    )

    authorized = streaming.get("initialAuthorizedDrmTrackTypes") or []
    kodiutils.log("play %s (%s): live=%s authorized=%s"
                  % (video_id, details.get("title"), is_live,
                     ",".join(authorized) or "none"))

    item = build_item(response, label=label, art=art)
    _resume(item, response, is_live)
    return response, item


def _resume(item, response, is_live):
    """Start where YouTube says this account left off.

    The position is not something we have to remember: the player response
    states it, in playerConfig.playbackStartConfig.startSeconds. The capture
    of an on-demand title opens with stats/playback at cmt=797.003 and the
    same response carries startSeconds 797, so this is the field the web
    player resumes from and it is already in a response the addon fetches.

    Live has no meaningful resume point and its "position" is a place in the
    DVR window, so it is left alone.

    StartOffset rather than the ResumeTime/TotalTime pair: Kodi 22 logs a
    deprecation warning for those and points at setResumePoint, but a resume
    point on a resolved item is not what starts playback at an offset --
    GetOptionsAndUpdateItem reads the resolved item's start offset into
    m_options.starttime (ApplicationPlay.cpp), and setProperty("StartOffset")
    is the un-deprecated way to set it. It is in seconds; ListItem converts.
    """
    if is_live or not kodiutils.get_setting_bool("resume", True):
        return
    config = ((response.get("playerConfig") or {})
              .get("playbackStartConfig") or {})
    try:
        start = float(config.get("startSeconds") or 0)
    except (TypeError, ValueError):
        return
    # Under a second is where YouTube says "from the beginning"; seeking there
    # costs a visible jump and buys nothing.
    if start < 1:
        return
    item.setProperty("StartOffset", "%.3f" % start)
    kodiutils.log("resuming at %.1fs, where YouTube says this account stopped"
                  % start)


def cross_sabr(client, response):
    """Cross the browser's request with ours and see which half is refused.

    The replay settled the question the two of them were built to settle: the
    browser's captured URL, POSTed from this machine, is served 15 MB. So we
    are not blocked as a client, and the difference is somewhere in what we
    send. There are only two halves to send.

        browser url + browser body  -- known 200, the replay
        browser url + our body      -- if this fails, our body is wrong
        our url     + browser body  -- if this fails, our url is wrong
        our url     + our body      -- known 403, the probe

    The bodies address the same title, so the cross is meaningful rather than
    two unrelated requests. Nothing here plays anything; it narrows the search
    to one half of one request.
    """
    try:
        from . import baked_sabr
    except ImportError:
        return
    their_url = getattr(baked_sabr, "URL", "")
    their_body = getattr(baked_sabr, "BODY", b"")
    if not their_url or not their_body:
        return

    streaming = response.get("streamingData") or {}
    our_url = streaming.get("serverAbrStreamingUrl") or ""
    try:
        config = (response["playerConfig"]["mediaCommonConfig"]
                  ["mediaUstreamerRequestConfig"]["videoPlaybackUstreamerConfig"])
    except (KeyError, TypeError):
        config = None
    formats = streaming.get("adaptiveFormats") or []
    audio = min((f for f in formats if "audio/" in (f.get("mimeType") or "")),
                key=lambda f: f.get("bitrate") or 1 << 30, default=None)
    our_body = b""
    if config and audio:
        our_body = sabr.build_request(
            config, wanted=(audio["itag"], audio.get("lastModified") or 0))

    # Which query parameters each side carries. The browser's working request
    # has no pot at all, and carries c/cver/cpn/rn/alr/sabr/svpuc; a missing
    # or extra parameter here is a far likelier cause of an empty-bodied 403
    # than anything in the protobuf.
    from urllib.parse import parse_qs, urlparse
    def keys(url):
        return set(parse_qs(urlparse(url).query))
    if our_url:
        theirs, ours = keys(their_url), keys(our_url)
        kodiutils.log("sabr cross: our url lacks %s; carries extra %s"
                      % (sorted(theirs - ours) or "nothing",
                         sorted(ours - theirs) or "nothing"))
    else:
        kodiutils.log("sabr cross: no serverAbrStreamingUrl to compare")

    import requests
    headers = {
        "User-Agent": getattr(baked_sabr, "USER_AGENT", api.UA),
        "Origin": api.ORIGIN,
        "Referer": api.ORIGIN + "/",
        "Accept": "*/*",
    }
    for label, url, body in (("their url + our body", their_url, our_body),
                             ("our url + their body", our_url, their_body)):
        if not url or not body:
            kodiutils.log("sabr cross: %s -- skipped, nothing to send" % label)
            continue
        try:
            reply = requests.post(url, data=body, timeout=30, headers=headers)
        except Exception as exc:
            kodiutils.log_error("sabr cross: %s -- %s" % (label, exc))
            continue
        kodiutils.log("sabr cross: %-20s HTTP %d, %d bytes"
                      % (label, reply.status_code, len(reply.content)))


def probe_dash_params(response, cpn):
    """Try a DASH url with the four parameters the SABR url was missing.

    Worth one measurement before committing to a SABR bridge. If googlevideo
    serves the DASH urls once they carry cpn/cver/alr/rn, InputStream Adaptive
    can play them as they stand and nothing else is needed. If it still
    refuses, the DASH urls are not a path at all -- the browser never fetches
    them, so there is no working example to match -- and the media has to come
    through SABR.
    """
    streaming = response.get("streamingData") or {}
    formats = streaming.get("adaptiveFormats") or []
    pick = next((f for f in formats if f.get("url")), None)
    if not pick:
        kodiutils.log("dash probe: every format is ciphered, no plain url to try")
        return
    import requests
    headers = {
        "User-Agent": api.UA,
        "Origin": api.ORIGIN,
        "Referer": api.ORIGIN + "/",
        "Range": "bytes=0-2047",
    }
    for label, url in (
            ("as minted", pick["url"]),
            ("with cpn/cver/alr/rn",
             sabr.playback_url(pick["url"], cpn, api._client_version(),
                               api.CLIENT_NAME))):
        try:
            reply = requests.get(url, timeout=30, headers=headers)
        except Exception as exc:
            kodiutils.log_error("dash probe: %s -- %s" % (label, exc))
            continue
        kodiutils.log("dash probe: %-22s HTTP %d, %d bytes"
                      % (label, reply.status_code, len(reply.content)))
