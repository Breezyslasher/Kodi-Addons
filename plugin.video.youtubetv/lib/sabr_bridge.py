"""Serving a SABR session to InputStream Adaptive as DASH.

ISA fetches an initialisation segment and then numbered media segments over
HTTP. A SABR session has neither URLs nor numbers of its own -- it is a
conversation -- so this puts a manifest and two routes in front of one:

    /sabr/manifest?id=..&k=..            the MPD
    /sabr/init?id=..&itag=..&k=..        ftyp+moov, split off the first media
    /sabr/segment?id=..&itag=..&n=..&k=.. one segment, pumped until it lands

The sequence numbers SABR reports are used as-is for $Number$, so the
manifest's startNumber is whatever sequence the session opened on. That
keeps one numbering across the whole stack instead of translating between
two, which is a place bugs live.

Nothing here decrypts anything: the media is Widevine-encrypted and ISA
does that with the licence proxy, exactly as it does on the cookie path.
"""

import threading
import time

import requests

from . import (api, auth, kodiutils, license_proxy,
               manifest as manifest_mod, mp4, nsig, sabr_session,
               widevine)

# Where the plugin leaves the session for the service to pick up. The two
# are different processes -- the plugin builds the playback item and exits,
# the service owns the HTTP server -- so an in-memory registry written by
# one is invisible to the other, and the first version of this answered
# every manifest request with 404 for exactly that reason. Everything else
# that has to cross the same gap in this addon travels as a file in the
# profile, and so does this.
CONTEXT_FILE = "sabr_context.json"
TIMEOUT = 30

# Live segments are five seconds. Measured: consecutive MEDIA_HEADER start
# times differ by ~5000 in the media timeline, and captured BufferedRanges
# grow 5015 (audio) and 5005 (video) per segment.
SEGMENT_MS = 5000

_sessions = {}
_lock = threading.Lock()


def register(session, formats):
    """Keep a live session in this process, and hand back its id.

    Only useful within one process -- the probe uses it. Playback goes
    through set_context, which crosses to the service.
    """
    with _lock:
        key = "s%d" % (len(_sessions) + 1)
        _sessions[key] = (session, formats)
        return key


def forget(key):
    with _lock:
        _sessions.pop(key, None)
    if kodiutils.read_json(CONTEXT_FILE, default={}).get("key") == key:
        kodiutils.delete_file(CONTEXT_FILE)


def set_context(url, config, audio, video, client_name, drm_params="",
                candidates=None, max_height=1080, video_id="",
                is_live=True, audio_url=""):
    """Leave everything the service needs to open the session itself.

    The url must already have its n solved: the plugin has the player JS and
    the solver, the service has neither, and a url whose n was never
    rewritten is answered with an empty-bodied 403.
    """
    key = "s%d" % int(time.time())
    kodiutils.write_json(CONTEXT_FILE, {
        "key": key,
        "url": url,
        "config": config,
        "audio": audio,
        "video": video,
        "client_name": client_name,
        # Every candidate, not just the chosen pair: the server refuses a
        # selection it will not serve -- sabr.no_video_selected for a 1080p
        # AV1 pick that the same session served 161 for -- and a context
        # holding one pair leaves nothing to fall back to.
        "candidates": candidates or [],
        "max_height": max_height,
        # The manifest has to carry the PSSH. A SABR initialisation
        # segment is 1067 bytes of ftyp and moov with no pssh box in
        # it -- walked, not assumed -- and ISA 22 refuses a
        # ContentProtection with no init data under it. drmParams is
        # where the content id comes from, exactly as on the DASH path.
        "drm_params": drm_params,
        "video_id": video_id,
        "live": bool(is_live),
        # The same audio track as a plain file, with n already solved. The
        # service has no player JS and cannot solve it, and this is the one
        # thing that can still tell a bridge fault from a media fault: the
        # DASH path reads these very bytes with byte ranges and decrypts
        # them, so if what SABR delivers is identical then nothing the
        # bridge does to the media is the reason audio will not decrypt.
        "audio_url": audio_url,
    })
    return key


def _post(url, body):
    """One SABR exchange. Lives here so the service can drive a session."""
    try:
        reply = requests.post(url, data=body, timeout=TIMEOUT, headers={
            "User-Agent": api.UA,
            "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
            "Content-Type": "application/x-protobuf",
        })
    except Exception as exc:
        kodiutils.log_error("sabr bridge: %s" % exc)
        return b""
    if reply.status_code != 200:
        kodiutils.log("sabr bridge: HTTP %d from the SABR endpoint"
                      % reply.status_code)
        return b""
    return reply.content


def _entry(fmt):
    return (fmt["itag"], fmt.get("lastModified") or 0, fmt.get("xtags") or "")


def alternatives(candidates, kind, max_height=1080, exclude=()):
    """Every usable rendition of one kind, best first, refusals removed."""
    wanted = [f for f in candidates
              if kind in (f.get("mimeType") or "")
              and f.get("itag") not in exclude
              and (kind != "video/" or (f.get("height") or 0) <= max_height)]
    if kind == "audio/":
        primary = [f for f in wanted if "primary" in (f.get("xtags") or "")]
        wanted = primary or wanted
    return sorted(wanted, key=lambda f: f.get("bitrate") or 0, reverse=True)


def lookup(key):
    """The session for this id, opening it from the stored context if needed."""
    with _lock:
        found = _sessions.get(key)
    if found:
        return found

    stored = kodiutils.read_json(CONTEXT_FILE, default={}) or {}
    if not stored or stored.get("key") != key:
        return None
    audio, video = stored.get("audio"), stored.get("video")
    name = stored.get("client_name") or api.CLIENT_NAME
    candidates = stored.get("candidates") or []
    max_height = stored.get("max_height") or 1080
    spec = api.client_spec(name)
    # Every candidate, in both fields. The server chooses among them and
    # names its choice in each MEDIA_HEADER; offering a single video format
    # was answered sabr.no_video_selected for all twelve renditions in
    # turn, including ones the cookie path plays in HD.
    session = sabr_session.Session(
        stored["url"], stored.get("config") or "",
        [_entry(f) for f in alternatives(candidates, "audio/")],
        [_entry(f) for f in alternatives(candidates, "video/", max_height)],
        name, spec["id"], api.effective_version(name), _post,
        live=bool(stored.get("live", True)))
    formats = {"audio": audio, "video": video,
               "drm_params": stored.get("drm_params", ""),
               "candidates": candidates, "max_height": max_height,
               "video_id": stored.get("video_id", ""),
               "refused": []}
    with _lock:
        # Check again inside the lock. ISA opens the manifest from more than
        # one thread and both missed the empty cache, so two sessions were
        # built for one playback: two conversations, two sets of fragments,
        # and whichever lost the race kept fetching into a session nothing
        # would ever read. It also explains every duplicated "the server
        # chose" line since this was written.
        if key in _sessions:
            return _sessions[key]
        _sessions[key] = (session, formats)
    kodiutils.log("sabr bridge: opened session %s as %s" % (key, name))
    return session, formats


def split_boxes(data):
    """([(name, bytes)] before the first moof, [fragment bytes]).

    A run of top-level boxes read from the file: ftyp, moov and -- because
    the DASH url carries gir=yes -- a sidx that SABR never sends, then
    moof/mdat pairs. Grouping them this way makes the file comparable to
    what the bridge assembled, box for box, without assuming either side's
    lengths.
    """
    head, fragments, current = [], [], None
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if size < 8 or pos + size > len(data):
            break
        if kind == b"moof":
            current = bytearray()
            fragments.append(current)
        if current is None:
            head.append((kind, data[pos:pos + size]))
        else:
            current += data[pos:pos + size]
        pos += size
    return head, [bytes(f) for f in fragments]


def compare_against_file(session, url, itag):
    """Whether what SABR delivered is what the file holds, byte for byte.

    Everything measurable about the bridge's audio has come back correct --
    saiz counts the samples trun counts, saio points eight bytes past the
    moof where the mdat payload starts, trun's data_offset lands exactly
    where the IV table ends, the boxes account for every byte -- and the
    CDM still answers kDecryptError with a key it reports usable. The DASH
    path decrypts the same itag with the same KID and the same lastModified.
    So the question left is not whether the bytes are well formed but
    whether they are the same bytes, and that is answerable rather than
    arguable.
    """
    held = session.segments.get(itag) or {}
    order = sorted(held)
    if len(order) < 2:
        return
    init = session.initialisation.get(itag) or b""
    ours = [held[order[0]], held[order[1]]]
    want = len(init) + sum(len(part) for part in ours) + 65536
    try:
        reply = requests.get(url, timeout=TIMEOUT, headers={
            "User-Agent": api.UA,
            "Origin": api.ORIGIN,
            "Referer": api.ORIGIN + "/",
            "Range": "bytes=0-%d" % want,
        })
    except Exception as exc:
        kodiutils.log("sabr bridge: could not read itag %s as a file: %s"
                      % (itag, exc))
        return
    if reply.status_code not in (200, 206):
        kodiutils.log("sabr bridge: HTTP %d reading itag %s as a file"
                      % (reply.status_code, itag))
        return
    head, fragments = split_boxes(reply.content)
    kodiutils.log("sabr bridge: itag %s as a file: %d bytes, head %s, "
                  "fragments %s"
                  % (itag, len(reply.content),
                     [(k.decode("latin-1"), len(v)) for k, v in head],
                     [len(f) for f in fragments[:3]]))
    theirs = [b"".join(v for k, v in head if k != b"sidx")]
    theirs += [fragments[i] if i < len(fragments) else b"" for i in range(2)]
    mine = [init] + ours
    names = ["initialisation", "fragment %s" % order[0],
             "fragment %s" % order[1]]
    for name, a, b in zip(names, mine, theirs):
        if a == b:
            kodiutils.log("sabr bridge: %s matches the file (%d bytes)"
                          % (name, len(a)))
            continue
        shared = min(len(a), len(b))
        at = next((i for i in range(shared) if a[i] != b[i]), shared)
        kodiutils.log("sabr bridge: %s DIFFERS -- %d bytes from SABR, %d in "
                      "the file, first difference at %d: %s vs %s"
                      % (name, len(a), len(b), at,
                         a[at:at + 16].hex(), b[at:at + 16].hex()))


def segment_ms(session, itag):
    """How long one fragment of this track actually is.

    SEGMENT_MS was a five second constant and the fragments are nothing
    like it: one audio fragment measured 321,489 bytes and one video
    fragment 1,866,238, which at these bitrates is around twenty seconds
    each. A SegmentTemplate claiming five seconds describes a timeline four
    times denser than the media, so ISA asks for segment numbers that do
    not exist and maps the ones it gets to the wrong instants.

    The start times of the segments held give the real spacing; the
    constant is only the fallback for a track holding one.
    """
    seen = session.held.get(itag) or {}
    starts = sorted(seen.values())
    if len(starts) > 1:
        gaps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
        if gaps:
            return sum(gaps) // len(gaps)
    return SEGMENT_MS


def _representation(fmt, base, key, itag, start_number,
                    has_init=False, duration=SEGMENT_MS,
                    protection=""):
    """One Representation, pointing its template at our own routes."""
    mime = (fmt.get("mimeType") or "")
    codecs = ""
    if 'codecs="' in mime:
        codecs = mime.split('codecs="', 1)[1].rstrip('"')
    extra = ""
    if fmt.get("audioSampleRate"):
        extra += ' audioSamplingRate="%s"' % fmt["audioSampleRate"]
    if fmt.get("width"):
        extra += ' width="%d" height="%d"' % (fmt["width"], fmt["height"])
    if fmt.get("fps"):
        extra += ' frameRate="%d"' % fmt["fps"]
    return (
        '<Representation id="%(itag)d" codecs="%(codecs)s" '
        'bandwidth="%(bandwidth)d"%(extra)s>%(protection)s'
        '<SegmentTemplate timescale="1000" duration="%(duration)d" '
        'startNumber="%(start)d" '
        '%(init)s'
        'media="%(base)s/sabr/segment?id=%(key)s&amp;itag=%(itag)d&amp;n=$Number$&amp;k=%(secret)s"/>'
        '</Representation>') % {
            "itag": itag, "codecs": codecs, "extra": extra,
            "bandwidth": fmt.get("bitrate") or 500000,
            "duration": duration, "start": start_number,
            "base": base["url"], "key": key, "secret": base["secret"],
            # Only when one exists. Declaring an initialisation url the
            # bridge cannot fill had ISA fetch it three times, take 503
            # each time, and give up -- where omitting it lets ISA read the
            # moov out of the first media segment, which is what it already
            # does on the live DASH path.
            # Inside the Representation, which is where the path that
            # works puts it: manifest.set_key_ids emits
            # head + _protection(own_uuid, own_pssh) + inner for every
            # Representation, so each track probes with its own key. The
            # bridge had it on the AdaptationSet instead.
            "protection": protection,
            "init": ('initialization="%s/sabr/init?id=%s&amp;itag=%d&amp;'
                     'k=%s" ' % (base["url"], key, itag, base["secret"])
                     if has_init else "")}


def manifest(key, base):
    """An MPD describing the session, or "" if it has gone."""
    found = lookup(key)
    if not found:
        return ""
    session, formats = found
    # The session may have been opened by this very request, and a manifest
    # cannot name a startNumber for a session that has not spoken yet.
    #
    # Priming is also where a bad selection surfaces: the server answers
    # sabr.no_video_selected rather than serving something else, and the
    # exception used to travel all the way out of the handler, closing the
    # socket on ISA with no response at all -- which it reported as
    # "CURLOpen failed" and I would have read as a network problem.
    if not session.segments:
        try:
            session.prime()
        except sabr_session.SabrError as exc:
            # With every candidate offered there is no narrower selection to
            # retry -- a refusal here is about the request, not the pick.
            kodiutils.log_error("sabr bridge: the endpoint refused the whole "
                                "set: %s" % str(exc).strip())
            return ""

    if not formats.get("compared"):
        formats["compared"] = True
        stored = kodiutils.read_json(CONTEXT_FILE, default={}) or {}
        audio_url = stored.get("audio_url") or ""
        itag = (formats.get("audio") or {}).get("itag")
        if audio_url and itag:
            try:
                compare_against_file(session, audio_url, itag)
            except Exception as exc:
                kodiutils.log("sabr bridge: the file comparison failed: %s"
                              % exc)
        else:
            kodiutils.log("sabr bridge: no file url for itag %s, so the "
                          "bytes cannot be compared" % itag)

    # The PSSH, built the way the DASH path builds it: from drmParams'
    # content id, with no manifest url to read a source out of.
    #
    # And with the track's own key id in it. ISA parsed the manifest, took
    # the licence, and then said "No KID found in PSSH" and refused to open
    # a session -- a PSSH naming no key is init data ISA cannot act on. The
    # DASH path solves this by reading each Representation's tenc box; the
    # bridge already holds every init segment in memory, so it reads the
    # same box from the same bytes rather than guessing a track tier.
    content = ""
    drm_params = formats.get("drm_params") or ""
    if drm_params:
        try:
            content = widevine.content_id(drm_params, "")
        except Exception as exc:
            kodiutils.log_error("sabr bridge: no content id: %s" % exc)

    def protection_for(itag, kind):
        if not content:
            return ""
        # The init first, then the first media segment. Live has no init of
        # its own -- that is why the DASH path reads a moov out of the first
        # media segment, and SABR live behaves the same way for some
        # renditions: 317 arrived with no ftyp at all while 148 and 161
        # arrived with one.
        head = session.initialisation.get(itag) or b""
        if not head:
            held = session.segments.get(itag) or {}
            if held:
                head = held[min(held)]
        # default_kid answers in hex; build_pssh wants the raw sixteen
        # bytes, and handing it the hex string would name a key that does
        # not exist while looking entirely plausible in the manifest.
        raw = mp4.default_kid(head) if head else ""
        kid = bytes.fromhex(raw) if len(raw) == 32 else None
        uuid = ("%s-%s-%s-%s-%s" % (raw[0:8], raw[8:12], raw[12:16],
                                    raw[16:20], raw[20:32])) if kid else ""
        if not kid:
            # Last resort: what the licence granted for this track's tier.
            # A height-to-tier mapping is a guess the DASH path avoids by
            # reading tenc, so it is only used when the media carries none.
            known = license_proxy.key_ids_for(formats.get("video_id") or "")
            tier = ("DRM_TRACK_TYPE_AUDIO" if kind == "audio"
                    else "DRM_TRACK_TYPE_HD" if (fmt.get("height") or 0) >= 720
                    else "DRM_TRACK_TYPE_SD")
            raw = known.get(tier) or ""
            kid = bytes.fromhex(raw) if len(raw) == 32 else None
            uuid = ("%s-%s-%s-%s-%s" % (raw[0:8], raw[8:12], raw[12:16],
                                        raw[16:20], raw[20:32])) if kid else ""
            kodiutils.log("sabr bridge: itag %d carries no key id; using the "
                          "licence's %s key: %s"
                          % (itag, tier, raw[:8] + ".." if raw else "none"))
        return manifest_mod._protection(
            uuid, widevine.build_pssh(content, is_live=True, key_id=kid))

    # What the server actually served, rather than what was asked for.
    by_itag = {f.get("itag"): f for f in (formats.get("candidates") or [])}
    served = []
    for itag in sorted(session.segments):
        fmt = by_itag.get(itag)
        if not fmt:
            kodiutils.log("sabr bridge: served itag %d is not in the player "
                          "response, so it cannot be described" % itag)
            continue
        kind = "audio" if "audio/" in (fmt.get("mimeType") or "") else "video"
        served.append((kind, fmt))
    if not served:
        kodiutils.log("sabr bridge: the session holds nothing to describe")
        return ""
    # Each track's own key, printed: on the cookie path a Representation
    # carrying the wrong key id is not a decode error, it is ISA removing
    # the audio track outright, and the manifest is where that is visible.
    kodiutils.log("sabr bridge: key ids %s"
                  % {fmt.get("itag"): (mp4.default_kid(
                      session.initialisation.get(fmt["itag"])
                      or (session.segments.get(fmt["itag"]) or {}).get(
                          min(session.segments.get(fmt["itag"]) or {0}), b""))
                      or "none")[:16] for _kind, fmt in served})
    kodiutils.log("sabr bridge: initialisation held for %s"
                  % (sorted(session.initialisation) or "nothing -- ISA will "
                     "read a moov out of the first fragment"))
    lengths = {fmt.get("itag"): segment_ms(session, fmt["itag"])
               for _kind, fmt in served}
    guessed = [itag for itag, _kind in
               ((f.get("itag"), k) for k, f in served)
               if len(session.held.get(itag) or {}) < 2]
    kodiutils.log("sabr bridge: fragment length %s%s"
                  % (lengths,
                     "  (unmeasured for %s -- the constant is a guess)"
                     % guessed if guessed else "  (measured)"))
    kodiutils.log("sabr bridge: the server chose %s"
                  % ", ".join("%s %s" % (kind, fmt.get("itag"))
                              for kind, fmt in served))

    sets = []
    for kind, fmt in served:
        itag = fmt["itag"]
        start = session.first_sequence(itag)
        if not start:
            continue
        mime = (fmt.get("mimeType") or "").split(";")[0]
        sets.append(
            '<AdaptationSet id="%d" contentType="%s" mimeType="%s" '
            'segmentAlignment="true" startWithSAP="1">%s</AdaptationSet>'
            % (len(sets), kind, mime,
               _representation(fmt, base, key, itag, start,
                               bool(session.initialisation.get(itag)),
                               segment_ms(session, itag),
                               protection_for(itag, kind))))

    if not sets:
        return ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" '
        'profiles="urn:mpeg:dash:profile:isoff-live:2011" type="static" '
        'mediaPresentationDuration="PT4H" minBufferTime="PT10S">'
        '<Period id="0">%s</Period></MPD>' % "".join(sets))


_WRAP = "<BaseURL>%s</BaseURL>"


def solve_n(url):
    """The url with n put through the player's transform, or "".

    Not optional: the SABR endpoint answers a url carrying the n the player
    minted with an empty-bodied 403, and the same url with n solved with the
    media. rewrite_n only works inside a BaseURL element, so the url is
    wrapped and unwrapped -- reusing it rather than matching n here again,
    because it already knows both spellings, the query one and the path
    segment live uses.
    """
    if not manifest_mod.carries_n(url):
        return url
    import requests
    session = requests.Session()
    try:
        cookies = auth.load()
    except auth.AuthError:
        cookies = {}
    try:
        player_id, js = api.player_js(session, cookies)
    except Exception as exc:
        kodiutils.log_error("sabr bridge: no player js, n cannot be solved: %s"
                            % exc)
        return ""
    try:
        rewritten = manifest_mod.rewrite_n(
            _WRAP % url, lambda value: nsig.solve(js, value, player_id))
    except Exception as exc:
        kodiutils.log_error("sabr bridge: could not solve n: %s" % exc)
        return ""
    return rewritten[len("<BaseURL>"):-len("</BaseURL>")]


def _pick(formats, kind, max_height=1080):
    """One rendition per track.

    The bridge declares a single Representation each, so this chooses
    rather than leaving it to ISA: the best audio, and the best video that
    fits the height cap. Adaptive switching would mean re-selecting formats
    mid-session, which the SABR request supports and this does not do yet --
    worth saying plainly rather than letting a single-quality stream look
    like a bug.
    """
    wanted = [f for f in formats
              if kind in (f.get("mimeType") or "")
              and (kind != "video/" or (f.get("height") or 0) <= max_height)]
    if not wanted:
        return None
    if kind == "audio/":
        primary = [f for f in wanted if "primary" in (f.get("xtags") or "")]
        wanted = primary or wanted
    return max(wanted, key=lambda f: f.get("bitrate") or 0)


def playable_url(player_response, max_height=1080):
    """Open a session for this response and return the manifest url for ISA."""
    streaming = player_response.get("streamingData") or {}
    sabr_url = streaming.get("serverAbrStreamingUrl") or ""
    if not sabr_url:
        return ""
    try:
        config = (player_response["playerConfig"]["mediaCommonConfig"]
                  ["mediaUstreamerRequestConfig"]["videoPlaybackUstreamerConfig"])
    except (KeyError, TypeError):
        kodiutils.log_error("sabr bridge: the player response carries no "
                            "ustreamer config, so SABR would answer "
                            "sabr.malformed_config")
        return ""

    formats = streaming.get("adaptiveFormats") or []
    audio = _pick(formats, "audio/")
    video = _pick(formats, "video/", max_height)
    if not audio or not video:
        kodiutils.log_error("sabr bridge: no audio/video pair to ask for")
        return ""

    solved = solve_n(sabr_url)
    if not solved:
        return ""

    audio_url = ""
    if audio.get("url"):
        audio_url = solve_n(audio["url"]) or ""
    else:
        kodiutils.log("sabr bridge: itag %s came with no url, only %s"
                      % (audio.get("itag"), sorted(audio)))

    key = set_context(solved, config, audio, video,
                      _client_name(), streaming.get("drmParams", ""),
                      audio_url=audio_url,
                      candidates=formats, max_height=max_height,
                      video_id=(player_response.get("videoDetails") or {}
                                ).get("videoId", ""),
                      is_live=bool((player_response.get("videoDetails") or {}
                                    ).get("isLive")))
    kodiutils.log("sabr bridge: session %s, audio itag %s, video itag %s (%sp)"
                  % (key, audio.get("itag"), video.get("itag"),
                     video.get("height")))
    return license_proxy.sabr_manifest_url(key)


def _client_name():
    """Which identity this credential is accepted as.

    auth.load() alone is the wrong question: a box holding both credentials
    has a jar whether or not playback is using it, and asking that opened a
    SABR session as WEB_UNPLUGGED while every url in it came from a token.
    The setting decides, exactly as it does in Api.
    """
    from . import oauth
    if kodiutils.get_setting_bool("prefer_token"):
        if oauth.load().get("access_token"):
            return oauth.load().get("client_name") or api.OAUTH_CLIENT_NAME
    try:
        auth.load()
        return api.CLIENT_NAME
    except auth.AuthError:
        return oauth.load().get("client_name") or api.OAUTH_CLIENT_NAME
