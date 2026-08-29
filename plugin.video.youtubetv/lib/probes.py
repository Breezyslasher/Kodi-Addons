"""Three questions to answer before anyone builds a SABR player.

The prize is a playback path that needs no cookie jar. An OAuth token is
accepted by YouTube TV, browses fine, and is never offered a DASH manifest --
only SABR -- so SABR support is what would make a code sign-in playable. But
that is a substantial build (a UMP parser, a ClientAbrState loop, and a bridge
serving synthetic DASH to InputStream Adaptive, which cannot speak SABR), and
three things should be measured first because any one of them sinks it.

1. **Does the Widevine licence exchange work on a bearer token?**
   SABR media is still encrypted. _post_license is hardcoded to SAPISIDHASH
   and a Cookie header, and _fetch_license opens with auth.load(), which
   raises without a jar. If the licence needs cookies then SABR frees nothing:
   the media would arrive and fail to decrypt.

   What this can answer standalone is the credential, not the whole exchange:
   without ISA there is no real challenge to send. A 401 or 403 is the
   credential being refused. A 400 means the credential was accepted and the
   placeholder challenge was rejected, which is the answer we want.

2. **Is serverAbrStreamingUrl actually there on a token session?**
   "SABR only" is currently the addon's own wording for "no dashManifestUrl",
   not something measured. Worth one line rather than an assumption.

3. **Is a SABR POST served when n is solved?**
   This is the real gap. The old SABR probe logged "n=... (as the player
   minted it)" and its comment says "We have no JS engine" -- it ran before
   the nsig solver existed and got 403. Meanwhile the browser's own SABR url,
   replayed verbatim from the same machine, returned 200 and 15 MB, and
   altering n lost the 200. The browser's url carries an n its JS had already
   transformed. So solved n was served and unsolved n was refused, and SABR
   has never once been tried with the solver we now run on every play.

Nothing here plays anything or changes any setting.
"""

import json

import requests

from . import api, auth, kodiutils, manifest as manifest_mod, nsig, sabr, widevine

LICENSE_URL = api.BASE + "player/get_drm_license"
TIMEOUT = 30


def _credential():
    """(headers, how) for whichever credential this session holds."""
    try:
        cookies = auth.load()
    except auth.AuthError:
        try:
            from . import oauth
            token = oauth.access_token()
        except Exception:
            token = ""
        if not token:
            return None, "nothing"
        return {"Authorization": "Bearer " + token}, "bearer token"
    return ({"Authorization": auth.authorization(cookies),
             "Cookie": auth.cookie_header(cookies)}, "cookie jar")


def run(client, video_id):
    """Answer the three, in the order that fails cheapest."""
    kodiutils.log("sabr feasibility: starting on %s" % video_id)
    credential, how = _credential()
    if credential is None:
        kodiutils.log_error("sabr feasibility: not signed in at all")
        return
    kodiutils.log("sabr feasibility: signed in with a %s, asking as %s"
                  % (how, client.client_name if hasattr(client, "client_name")
                     else api.CLIENT_NAME))

    cpn = api.new_cpn()
    response = client.player(video_id, cpn)
    streaming = response.get("streamingData") or {}

    # -- 2. what delivery is actually offered ------------------------------
    sabr_url = streaming.get("serverAbrStreamingUrl") or ""
    kodiutils.log("sabr feasibility: dash=%s sabr=%s (%d formats)"
                  % (bool(streaming.get("dashManifestUrl")), bool(sabr_url),
                     len(streaming.get("adaptiveFormats") or [])))

    # -- 1. the licence, which decides whether SABR would be worth anything -
    _probe_license(response, video_id, cpn, credential, how)

    # -- 3. SABR with a solved n -------------------------------------------
    if not sabr_url:
        kodiutils.log("sabr feasibility: no serverAbrStreamingUrl, nothing to "
                      "POST to")
        return
    _probe_sabr(response, streaming, sabr_url, cpn, how)

    # -- 4. where the ustreamer config lives -------------------------------
    _config_matrix(video_id)


def _config_matrix(video_id):
    """Which identity, under which credential, is handed a ustreamer config.

    The SABR endpoint answered `sabr.malformed_config` to a request sent
    without field 5, so the config is required rather than optional, and the
    TVHTML5_UNPLUGGED player response does not carry one. That is two facts
    and one open question: is the config withheld from the *identity* or
    from the *credential*? Six clients times the credentials this box holds
    answers it in one run, and the answer decides whether a cookie-free SABR
    player is buildable at all.
    """
    credentials = []
    # auth.load() refuses a jar once the user has signed out of it, and a
    # code sign-in does exactly that -- so the run that most needs the cookie
    # arm as a control is the one that silently loses it. This is a probe, so
    # it reads the baked jar directly and says which jar it used.
    try:
        cookies = auth.load()
    except auth.AuthError:
        cookies = auth._baked() or {}
        if cookies:
            kodiutils.log("ustreamer matrix: the live jar is signed out; "
                          "using the baked one as the control arm")
    if cookies:
        credentials.append(("cookie jar", {
            "Authorization": auth.authorization(cookies),
            "Cookie": auth.cookie_header(cookies)}))
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if token:
        credentials.append(("bearer token",
                            {"Authorization": "Bearer " + token}))
    if not credentials:
        return
    kodiutils.log("ustreamer matrix: %d credential(s) x %d clients"
                  % (len(credentials), len(api.UNPLUGGED_CLIENTS)))

    for how, credential in credentials:
        for name in sorted(api.UNPLUGGED_CLIENTS):
            kodiutils.log("ustreamer matrix: %-20s + %-12s -> %s"
                          % (name, how, _ask_player(video_id, name, credential)))


def _ask_player(video_id, client_name, credential):
    """One player call, described in a line: delivery, config, or refusal."""
    spec = api.client_spec(client_name)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": spec["context"].get("userAgent", api.UA),
        "Accept": "*/*",
        "Origin": api.ORIGIN,
        "Referer": api.ORIGIN + "/",
        "X-Origin": api.ORIGIN,
        "X-YouTube-Client-Name": spec["id"],
        "X-YouTube-Client-Version": api.effective_version(client_name),
        "X-Goog-AuthUser": "0",
    }
    headers.update(credential)
    # The same body the working play path sends. An abbreviated one answered
    # UNPLAYABLE with no formats where this one is served 25, which would
    # have been read as the server withholding them.
    payload = api.player_body(video_id, api.new_cpn())
    payload["context"] = api.context(client_name=client_name)
    try:
        reply = requests.post(api.BASE + "player", data=json.dumps(payload),
                              headers=headers, timeout=TIMEOUT,
                              params={"prettyPrint": "false"})
    except Exception as exc:
        return "request failed: %s" % exc
    if reply.status_code != 200:
        detail = ""
        try:
            detail = ((reply.json().get("error") or {}).get("message") or "")
        except ValueError:
            detail = (reply.text or "")[:120]
        return "HTTP %d %s" % (reply.status_code, detail[:90])
    try:
        body = reply.json()
    except ValueError:
        return "HTTP 200 but not JSON"
    streaming = body.get("streamingData") or {}
    playability = body.get("playabilityStatus") or {}
    status = playability.get("status") or "?"
    if status != "OK":
        # "UNPLAYABLE" alone cannot be acted on; the reason names whether it
        # is the account, the market, the client or the request. It arrives
        # as plain text or as a runs/simpleText blob depending on client.
        reason = playability.get("reason")
        if isinstance(reason, dict):
            reason = (reason.get("simpleText")
                      or "".join(r.get("text", "")
                                 for r in reason.get("runs") or []))
        status = "%s(%s)" % (status, (reason or "no reason given")[:70])
    config = _find_ustreamer(body)
    return ("%s dash=%s sabr=%s formats=%d config=%s"
            % (status, bool(streaming.get("dashManifestUrl")),
               bool(streaming.get("serverAbrStreamingUrl")),
               len(streaming.get("adaptiveFormats") or []),
               "%d chars" % len(config) if config else "NONE"))


def _find_ustreamer(body):
    """The ustreamer config wherever it is, not only where it was last seen.

    The documented home is playerConfig.mediaCommonConfig
    .mediaUstreamerRequestConfig.videoPlaybackUstreamerConfig, and a client
    that puts it somewhere else would otherwise read as not having one.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if "ustreamerconfig" in key.lower() and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return found[0] if found else ""


def _probe_license(response, video_id, cpn, credential, how):
    """Does get_drm_license accept this credential at all?"""
    streaming = response.get("streamingData") or {}
    drm_params = streaming.get("drmParams", "")
    payload = {
        "context": api.context(location=False),
        "drmSystem": "DRM_SYSTEM_WIDEVINE",
        "videoId": video_id,
        "cpn": cpn,
        "sessionId": widevine.session_id_from_drm_params(drm_params),
        # A placeholder, not a real Widevine challenge: without ISA there is
        # none to send. The point is which door it is turned away at.
        "licenseRequest": "",
        "drmParams": drm_params,
        "drmVideoFeature": "DRM_VIDEO_FEATURE_SDR",
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": api.UA,
        "Origin": api.ORIGIN,
        "Referer": "%s/watch/%s" % (api.ORIGIN, video_id),
        "X-Origin": api.ORIGIN,
        "X-Goog-AuthUser": "0",
    }
    headers.update(credential)
    try:
        import json as _json
        reply = requests.post(LICENSE_URL, params={"alt": "json"},
                              data=_json.dumps(payload), headers=headers,
                              timeout=TIMEOUT)
    except Exception as exc:
        kodiutils.log_error("sabr feasibility: licence call failed: %s" % exc)
        return
    verdict = ("the credential was REFUSED"
               if reply.status_code in (401, 403) else
               "the credential was ACCEPTED (this status is about the "
               "placeholder challenge, not the sign-in)")
    kodiutils.log("sabr feasibility: get_drm_license with a %s -> HTTP %d -- %s"
                  % (how, reply.status_code, verdict))
    kodiutils.log("sabr feasibility: licence said: %s"
                  % (reply.text or "")[:300].replace("\n", " "))


def _probe_sabr(response, streaming, sabr_url, cpn, how):
    """POST to SABR with n solved, which has never been tried."""
    try:
        config = (response["playerConfig"]["mediaCommonConfig"]
                  ["mediaUstreamerRequestConfig"]["videoPlaybackUstreamerConfig"])
    except (KeyError, TypeError):
        # Absent, or somewhere else. The guide's station metadata was a
        # renamed key rather than a missing one, and reporting "not there"
        # cost a round trip that naming what *was* there would have saved.
        # So say what the response actually carries at each level.
        player_config = response.get("playerConfig") or {}
        common = player_config.get("mediaCommonConfig") or {}
        ustreamer = common.get("mediaUstreamerRequestConfig") or {}
        kodiutils.log("sabr feasibility: no ustreamer config. playerConfig=%s"
                      % sorted(player_config))
        kodiutils.log("sabr feasibility:   mediaCommonConfig=%s  "
                      "mediaUstreamerRequestConfig=%s"
                      % (sorted(common) or "absent",
                         sorted(ustreamer) or "absent"))
        kodiutils.log("sabr feasibility:   streamingData=%s"
                      % sorted(streaming))
        # And anywhere else in the response it might have moved to.
        hits = sorted(_find_keys(response, "ustreamer"))
        kodiutils.log("sabr feasibility:   keys mentioning 'ustreamer' "
                      "anywhere: %s" % (hits or "none"))
        # Absent is not the same as required. The endpoint has never been
        # asked without field 5, so ask -- a builder that refuses to make the
        # request cannot find out whether the request would be served.
        kodiutils.log("sabr feasibility: asking anyway, without field 5")
        config = ""

    formats = streaming.get("adaptiveFormats") or []
    audio = [f for f in formats if "audio/" in (f.get("mimeType") or "")]
    if not audio:
        kodiutils.log("sabr feasibility: no audio format to ask for")
        return
    wanted = min(audio, key=lambda f: f.get("bitrate") or 1 << 30)
    body = sabr.build_request(config, (wanted.get("itag"),
                                       wanted.get("lastModified") or 0))

    url = sabr.playback_url(sabr_url, cpn, api._client_version(),
                            api.CLIENT_NAME)
    solved_url, minted, solved = _with_solved_n(url)
    kodiutils.log("sabr feasibility: n %s -> %s"
                  % (minted or "(none in the url)", solved or "(unsolved)"))

    for label, target in (("n as minted", url), ("n solved", solved_url)):
        if not target:
            continue
        try:
            reply = requests.post(target, data=body, timeout=TIMEOUT, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Content-Type": "application/x-protobuf",
            })
        except Exception as exc:
            kodiutils.log_error("sabr feasibility [%s]: %s" % (label, exc))
            continue
        kodiutils.log("sabr feasibility [%-11s]: HTTP %d, %d bytes"
                      % (label, reply.status_code, len(reply.content)))
        # A 200 carrying 31 bytes is not media, and the headers say more
        # about why than the body does: a content-type of text/plain, or a
        # location, or a googlevideo error header, each mean something
        # different. So print the ones that carry a verdict.
        told = {k: v for k, v in reply.headers.items()
                if k.lower() in ("content-type", "location", "x-restrict",
                                 "x-bandwidth-est", "x-walltime-ms",
                                 "x-sequence-num", "content-length")}
        kodiutils.log("sabr feasibility [%s]: headers %s" % (label, told))
        if reply.status_code == 200 and reply.content:
            kodiutils.log("sabr feasibility [%s]: %s"
                          % (label, sabr.describe_response(reply.content)))


def _find_keys(node, needle, path="", found=None):
    """Every key path whose name mentions ``needle``, case-insensitively."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            here = "%s.%s" % (path, key) if path else key
            if needle.lower() in key.lower():
                found.add(here)
            _find_keys(value, needle, here, found)
    elif isinstance(node, list) and node:
        _find_keys(node[0], needle, "%s[]" % path, found)
    return found


_WRAP = "<BaseURL>%s</BaseURL>"


def _with_solved_n(url):
    """The same url with n put through the player's transform.

    rewrite_n only rewrites inside a BaseURL tag, which is what a manifest is
    made of, so the url is wrapped in one and unwrapped after. Reusing it
    rather than re-deriving the regex matters: it already handles both
    spellings, the query ``n=`` of on demand and the ``/n/.../`` path segment
    live uses, and getting that wrong is how live segments kept the value the
    player minted.
    """
    if not manifest_mod.carries_n(url):
        return "", "", ""
    session = requests.Session()
    try:
        cookies = auth.load()
    except auth.AuthError:
        cookies = {}
    try:
        player_id, js = api.player_js(session, cookies)
    except Exception as exc:
        kodiutils.log_error("sabr feasibility: no player js: %s" % exc)
        return "", "", ""
    seen = {}

    def transform(value):
        seen["minted"] = value
        answer = nsig.solve(js, value, player_id)
        seen["solved"] = answer
        return answer

    try:
        rewritten = manifest_mod.rewrite_n(_WRAP % url, transform)
    except Exception as exc:
        kodiutils.log_error("sabr feasibility: could not solve n: %s" % exc)
        return "", seen.get("minted", ""), ""
    inner = rewritten[len("<BaseURL>"):-len("</BaseURL>")]
    return inner, seen.get("minted", ""), seen.get("solved", "")
