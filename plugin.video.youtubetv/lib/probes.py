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
import time

import requests

from . import api, auth, kodiutils, manifest as manifest_mod, nsig, sabr, widevine

LICENSE_URL = api.BASE + "player/get_drm_license"
TIMEOUT = 30


def run(client, video_id):
    """Answer the three, on every credential this box holds.

    One arm per credential, because the interesting one is no longer the
    default: the jar is preferred for playback, so running only the
    credential playback would pick meant the token path -- the one the whole
    exercise is for -- was never taken end to end.
    """
    kodiutils.log("sabr feasibility: starting on %s" % video_id)
    arms = _arms()
    if not arms:
        kodiutils.log_error("sabr feasibility: not signed in at all")
        return
    for how, credential, client_name in arms:
        _feasibility(video_id, how, credential, client_name)

    # -- 4. where the ustreamer config lives -------------------------------
    _config_matrix(video_id)
    _bearer_as_web(video_id)
    _tv_versions(video_id)
    _tv_dash(video_id)
    _tv_dash_more(video_id)


def _tv_dash(video_id):
    """Can the token session be given a DASH manifest instead of SABR?

    Worth asking before anything is built. The addon already plays DASH,
    and InputStream Adaptive cannot speak SABR -- so a token session served
    a dashManifestUrl needs no bridge at all, while one served only SABR
    needs a whole synthetic-DASH layer in front of ISA.

    dash=False on the TV client has been read as server policy, but it has
    only ever been asked one way. The web client, which is served dash=True,
    sends a *smaller* request than we do: its captured player body carries
    no `params` at all, and no mdxContext or captionParams. So vary the
    request rather than assume the policy.
    """
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        kodiutils.log("tv dash: no token stored, nothing to try")
        return
    credential = {"Authorization": "Bearer " + token}
    name = api.OAUTH_CLIENT_NAME

    def shaped(change):
        body = api.player_body(video_id, api.new_cpn())
        change(body)
        return body

    def drop_params(body):
        body.pop("params", None)

    def drop_html5(body):
        ctx = body["playbackContext"]["contentPlaybackContext"]
        ctx.pop("html5Preference", None)

    def as_browser(body):
        # What the captured web request actually carries, no more.
        body.pop("params", None)
        body.pop("captionParams", None)
        ctx = body["playbackContext"]["contentPlaybackContext"]
        for key in ("mdxContext", "autonavState", "autoCaptionsDefaultOn"):
            ctx.pop(key, None)

    def no_playback_context(body):
        body.pop("playbackContext", None)

    for label, change in (("as sent", lambda b: None),
                          ("without params", drop_params),
                          ("without html5Preference", drop_html5),
                          ("shaped like the browser's", as_browser),
                          ("no playbackContext", no_playback_context)):
        kodiutils.log("tv dash [%-26s]: %s"
                      % (label, _ask_player(video_id, name, credential,
                                            body=shaped(change))))


def _tv_dash_more(video_id):
    """The other direction: a TV request that sends MORE, not less.

    The first sweep only ever removed fields, so it tested "less than we
    send" five ways and never "more". Diffed against the browser's captured
    request, our TV context is the thin one -- it omits things that are not
    web-specific at all: utcOffsetMinutes, timeZone, playerType, the
    request block's useSsl, user.lockedSafetyMode, clientScreenNonce, and
    every device field. context() strips the web extras from non-web
    clients deliberately, because sending visitorData and friends to a
    mobile identity drew HTTP 400s, but that reasoning never covered the
    generic fields.

    So add them back in layers. The device values come from the client's
    own User-Agent string -- Cobalt 25, Starboard 16 -- rather than being
    invented, and are candidates like any other: the server answers.
    """
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        return
    credential = {"Authorization": "Bearer " + token}
    name = api.OAUTH_CLIENT_NAME

    def generic(ctx):
        ctx["client"].update({
            "utcOffsetMinutes": -int(time.timezone / 60),
            "timeZone": api._timezone_name(),
            "playerType": "UNIPLAYER",
        })
        ctx["request"] = {"useSsl": True, "internalExperimentFlags": [],
                          "consistencyTokenJars": []}
        ctx["user"] = {"lockedSafetyMode": False}
        ctx["clientScreenNonce"] = api.new_cpn()

    def device(ctx):
        ctx["client"].update({
            "deviceMake": "", "deviceModel": "",
            "osName": "Cobalt", "osVersion": "25.master",
            "clientFormFactor": "UNKNOWN_FORM_FACTOR",
            "clientScreen": "WATCH",
            "applicationState": "ACTIVE",
        })

    def both(ctx):
        generic(ctx)
        device(ctx)

    for label, change in (("generic InnerTube fields", generic),
                          ("TV device fields", device),
                          ("both", both)):
        ctx = api.context(client_name=name)
        change(ctx)
        kodiutils.log("tv dash+ [%-26s]: %s"
                      % (label, _ask_player(video_id, name, credential,
                                            context=ctx)))


def _arms():
    """(how, credential, client_name) for each credential stored.

    Each credential carries the identity it is accepted as: the jar is the
    web player's, and a device-code token is refused by every client but the
    TV one.
    """
    arms = []
    try:
        cookies = auth.load()
    except auth.AuthError:
        cookies = {}
    if cookies:
        arms.append(("cookie jar", {
            "Authorization": auth.authorization(cookies),
            "Cookie": auth.cookie_header(cookies)}, api.CLIENT_NAME))
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if token:
        arms.append(("bearer token", {"Authorization": "Bearer " + token},
                     oauth.load().get("client_name") or api.OAUTH_CLIENT_NAME))
    return arms


def _feasibility(video_id, how, credential, client_name):
    """The three questions, for one credential and the identity it works as."""
    kodiutils.log("sabr feasibility: --- %s, asking as %s v%s ---"
                  % (how, client_name, api.effective_version(client_name)))
    cpn = api.new_cpn()
    response = _raw_player(video_id, client_name, credential, cpn=cpn)
    if not response:
        kodiutils.log_error("sabr feasibility: no player response as %s"
                            % client_name)
        return
    streaming = response.get("streamingData") or {}

    sabr_url = streaming.get("serverAbrStreamingUrl") or ""
    kodiutils.log("sabr feasibility: dash=%s sabr=%s (%d formats) config=%s"
                  % (bool(streaming.get("dashManifestUrl")), bool(sabr_url),
                     len(streaming.get("adaptiveFormats") or []),
                     len(_find_ustreamer(response)) or "NONE"))

    _probe_license(response, video_id, cpn, credential, how)

    if not sabr_url:
        kodiutils.log("sabr feasibility: no serverAbrStreamingUrl, nothing to "
                      "POST to")
        return
    _probe_sabr(response, streaming, sabr_url, cpn, how, client_name)


def _tv_versions(video_id):
    """Is the config withheld from the TV client, or from a stale version?

    The client table still claims TVHTML5_UNPLUGGED is version 6.36, which
    is a value that was copied across three clients and is certainly not
    what a current TV app sends -- an earlier run read 7.20260826.15.00 off
    the TV shell page. The version travels in the context and in the
    X-YouTube-Client-Version header, so a server deciding what to serve has
    it in hand, and every measurement so far was taken at 6.36.

    So sweep. The versions are candidates, not claims; the server decides.
    """
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        kodiutils.log("tv versions: no token stored, nothing to try")
        return
    credential = {"Authorization": "Bearer " + token}
    name = api.OAUTH_CLIENT_NAME

    candidates = [
        (api.client_spec(name)["version"], "the table's value"),
        ("7.20260826.15.00", "read off the TV shell page in an earlier run"),
        (api.effective_version(api.CLIENT_NAME), "what the web client sends"),
    ]
    seen = set()
    for version, why in candidates:
        if not version or version in seen:
            continue
        seen.add(version)
        kodiutils.log("tv versions: %-20s (%s) -> %s"
                      % (version, why,
                         _ask_player(video_id, name, credential,
                                     version=version)))

    # And what the TV response actually carries, in case the config is there
    # under a name nothing has searched for.
    body = _raw_player(video_id, name, credential)
    if body:
        kodiutils.log("tv versions: TV response top level: %s" % sorted(body))
        interesting = []

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    low = key.lower()
                    if any(w in low for w in ("onesie", "abr", "sabr",
                                              "ustreamer", "streamer")):
                        interesting.append("%s.%s" % (path, key))
                    walk(value, path + "." + key)
            elif isinstance(node, list):
                for item in node[:3]:
                    walk(item, path + "[]")

        walk(body)
        kodiutils.log("tv versions: keys mentioning onesie/abr/sabr/streamer:"
                      " %s" % (sorted(set(interesting))[:25] or "none"))


def _raw_player(video_id, client_name, credential, cpn=None):
    """The parsed player response, or {} -- for surveying rather than judging."""
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
    payload = api.player_body(video_id, cpn or api.new_cpn())
    payload["context"] = api.context(client_name=client_name)
    try:
        reply = requests.post(api.BASE + "player", data=json.dumps(payload),
                              headers=headers, timeout=TIMEOUT,
                              params={"prettyPrint": "false"})
        return reply.json() if reply.status_code == 200 else {}
    except Exception:
        return {}


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

    verdict = {}
    for how, credential in credentials:
        for name in sorted(api.UNPLUGGED_CLIENTS):
            line = _ask_player(video_id, name, credential)
            verdict[(how, name)] = line
            kodiutils.log("ustreamer matrix: %-20s + %-12s -> %s"
                          % (name, how, line))

    # A dead jar answers every row identically and looks like a finding. Say
    # so instead: an arm that was never signed in has not measured anything.
    for how, _ in credentials:
        rows = [v for (h, _n), v in verdict.items() if h == how]
        served = [v for v in rows if v.startswith("OK")]
        if not served:
            kodiutils.log("ustreamer matrix: the %s was refused by every "
                          "client -- that arm measured nothing, not a finding"
                          % how)
        elif not any("config=NONE" not in v for v in served):
            kodiutils.log("ustreamer matrix: the %s was served by %d client(s)"
                          " and handed a config by none of them"
                          % (how, len(served)))


def _ask_player(video_id, client_name, credential, context=None,
                version=None, body=None):
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
        "X-YouTube-Client-Version": version or api.effective_version(client_name),
        "X-Goog-AuthUser": "0",
    }
    headers.update(credential)
    # The same body the working play path sends. An abbreviated one answered
    # UNPLAYABLE with no formats where this one is served 25, which would
    # have been read as the server withholding them.
    payload = body if body is not None else api.player_body(video_id,
                                                            api.new_cpn())
    payload["context"] = (context if context is not None
                          else api.context(client_name=client_name))
    if version:
        # Both places, or the request contradicts itself.
        payload["context"]["client"]["clientVersion"] = version
    try:
        reply = requests.post(api.BASE + "player", data=json.dumps(payload),
                              headers=headers, timeout=TIMEOUT,
                              params={"prettyPrint": "false"})
    except Exception as exc:
        return "request failed: %s" % exc
    if reply.status_code != 200:
        return "HTTP %d %s" % (reply.status_code, _complaint(reply))
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
    # useServerDrivenAbr travels with the config in every capture that has
    # one, and is absent from the TV response that has none, so it is worth
    # reporting beside it rather than inferring the link later.
    common = ((body.get("playerConfig") or {}).get("mediaCommonConfig") or {})
    return ("%s dash=%s sabr=%s formats=%d config=%s abr=%s"
            % (status, bool(streaming.get("dashManifestUrl")),
               bool(streaming.get("serverAbrStreamingUrl")),
               len(streaming.get("adaptiveFormats") or []),
               "%d chars" % len(config) if config else "NONE",
               common.get("useServerDrivenAbr", False)))


def _complaint(reply):
    """What InnerTube said, including which argument it disliked.

    "Request contains an invalid argument" names nothing, and four rows of
    the matrix say only that. error.details is where InnerTube names the
    field when it knows it, and it was being thrown away.
    """
    try:
        error = (reply.json().get("error") or {})
    except ValueError:
        return (reply.text or "")[:120]
    message = error.get("message") or ""
    details = error.get("details")
    if details:
        message = "%s | details=%s" % (message, json.dumps(details)[:400])
    return message[:500]


def _bearer_as_web(video_id):
    """Can the one identity that is given a config be asked with a token?

    The matrix cannot separate identity from credential, because the two
    are confounded: cookies are refused by TVHTML5_UNPLUGGED and a token is
    refused by WEB_UNPLUGGED, so each credential only ever reaches its own
    client. The refusal is HTTP 400 INVALID_ARGUMENT, which is a complaint
    about the request rather than about the sign-in -- and the web context
    carries three fields that describe a browser session the token does not
    have: visitorData, rolloutToken and configInfo.appInstallData.

    So ask three times, dropping them. If any shape is served, a token
    session can hold a ustreamer config and the whole cookie-free path
    opens; if all three are refused the same way, the identity is closed to
    the token and SABR frees nothing.
    """
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        kodiutils.log("bearer-as-web: no token stored, nothing to try")
        return
    credential = {"Authorization": "Bearer " + token}

    full = api.context(client_name=api.CLIENT_NAME)
    trimmed = json.loads(json.dumps(full))
    for key in ("visitorData", "rolloutToken", "configInfo"):
        trimmed["client"].pop(key, None)
    minimal = {"client": {k: v for k, v in full["client"].items()
                          if k in ("hl", "gl", "clientName", "clientVersion",
                                   "unpluggedAppInfo")}}

    for label, ctx in (("web context, as sent", full),
                       ("minus visitor/rollout/install", trimmed),
                       ("client name and version only", minimal)):
        kodiutils.log("bearer-as-web [%-29s]: %s"
                      % (label, _ask_player(video_id, api.CLIENT_NAME,
                                            credential, context=ctx)))


def _format_entry(fmt):
    """(itag, lastModified, xtags) as the captured requests carry them."""
    return (fmt.get("itag"), fmt.get("lastModified") or 0,
            fmt.get("xtags") or "")


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


def _probe_sabr(response, streaming, sabr_url, cpn, how,
                client_name=None):
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
    video = [f for f in formats if "video/" in (f.get("mimeType") or "")]
    picked_video = (min(video, key=lambda f: f.get("bitrate") or 1 << 30)
                    if video else None)
    body = sabr.build_request(
        config,
        audio=[_format_entry(wanted)],
        video=[_format_entry(picked_video)] if picked_video else [])
    kodiutils.log("sabr feasibility: asking for audio itag %s xtags=%s"
                  ", video itag %s"
                  % (wanted.get("itag"), wanted.get("xtags") or "none",
                     picked_video.get("itag") if picked_video else "none"))

    # c and cver must name the client the player call was made as. Sending
    # the web client's name and version on a TV session describes a session
    # that does not exist.
    client_name = client_name or api.CLIENT_NAME
    url = sabr.playback_url(sabr_url, cpn,
                            api.effective_version(client_name), client_name)
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

    if solved_url:
        _probe_seek(solved_url, config, wanted, picked_video)


def _probe_seek(url, config, audio, video):
    """Can a segment be asked for by time, or only streamed from wherever?

    This decides whether a bridge is buildable at all. InputStream Adaptive
    fetches segment N of a template, so the bridge has to turn "segment N"
    into a SABR request and get back that segment -- not whatever the server
    felt like sending. ClientAbrState field 28 is the player position, so
    ask twice at two positions and compare what comes back.

    If the two answers carry different sequence numbers, segments are
    addressable and a bridge can map ISA's requests onto them. If both
    return the same bytes, SABR is a stream that starts where it likes and
    the bridge would have to buffer rather than address.
    """
    entries = ([_format_entry(audio)] if audio else [],
               [_format_entry(video)] if video else [])
    for position in (0, 30000):
        body = sabr.build_request(config, audio=entries[0], video=entries[1],
                                  player_time_ms=position)
        try:
            reply = requests.post(url, data=body, timeout=TIMEOUT, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Content-Type": "application/x-protobuf",
            })
        except Exception as exc:
            kodiutils.log_error("sabr seek [%d ms]: %s" % (position, exc))
            continue
        if reply.status_code != 200 or not reply.content:
            kodiutils.log("sabr seek [%6d ms]: HTTP %d, %d bytes"
                          % (position, reply.status_code, len(reply.content)))
            continue
        headers = [payload for kind, payload in sabr.parse_ump(reply.content)
                   if kind == 20]
        media = sum(len(payload) for kind, payload
                    in sabr.parse_ump(reply.content) if kind == 21)
        kodiutils.log("sabr seek [%6d ms]: %d bytes, %d media, %d header(s)"
                      % (position, len(reply.content), media, len(headers)))
        for payload in headers:
            kodiutils.log("sabr seek [%6d ms]:   %s"
                          % (position, sabr.describe_media_header(payload)))


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
