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

import base64
import json
import time

import requests

from . import api, auth, kodiutils, manifest as manifest_mod, nsig, sabr, widevine

LICENSE_URL = api.BASE + "player/get_drm_license"
# What one segment is worth. The captured requests grow a buffered range by
# 5015 ms for audio and 5005 for video per segment; the server is told a
# duration, not a segment count, and 5000 is close enough to claim honestly.
SEGMENT_MS = 5000
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
    _cookie_as_tv(video_id)
    _mint_web_session()
    _mint_scope()
    _ask_google_why()
    _mint_browser_flow()


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


def _cookie_as_tv(video_id):
    """The one empty cell: a cookie jar asked as the TV client.

    Every measurement says delivery follows the identity -- the web client
    is served DASH and the TV client is not, under eight request shapes --
    but identity and credential are welded together, because each credential
    is refused by the other's client. So "cookies get DASH" and "the web
    client gets DASH" are the same sentence said twice, and there is no way
    to tell which half is doing the work.

    Unless the TV client can be made to accept a jar. It answers 400
    INVALID_ARGUMENT, which is a complaint about the request, and the
    probe's own request is thinner than the addon's real one in a way that
    matters here: it never sends X-Goog-Visitor-Id, which Api._headers
    always does, and the TV context carries no visitorData. A jar whose
    visitor session is missing is a plausible thing for InnerTube to
    dislike.

    If any shape is served, the cell fills in and the question is answered
    outright. If none is, that is worth knowing too: it means the pairing is
    enforced and no experiment on this box can separate the two.
    """
    try:
        cookies = auth.load()
    except auth.AuthError:
        cookies = auth._baked() or {}
    if not cookies:
        kodiutils.log("cookie-as-tv: no jar, nothing to try")
        return
    base = {"Authorization": auth.authorization(cookies),
            "Cookie": auth.cookie_header(cookies)}
    visitor = (kodiutils.get_setting("visitor_id", "")
               or api._bootstrap().get("visitor_data")
               or api._baked_visitor_id())
    name = api.OAUTH_CLIENT_NAME

    def with_visitor_header():
        headers = dict(base)
        if visitor:
            headers["X-Goog-Visitor-Id"] = visitor
        return headers

    def with_visitor_context():
        ctx = api.context(client_name=name)
        if visitor:
            ctx["client"]["visitorData"] = visitor
        return ctx

    def generous():
        ctx = with_visitor_context()
        ctx["client"].update({
            "utcOffsetMinutes": -int(time.timezone / 60),
            "timeZone": api._timezone_name(),
            "playerType": "UNIPLAYER",
        })
        ctx["request"] = {"useSsl": True, "internalExperimentFlags": [],
                          "consistencyTokenJars": []}
        ctx["user"] = {"lockedSafetyMode": False}
        return ctx

    trials = (
        ("as sent", base, None),
        ("+ visitor id header", with_visitor_header(), None),
        ("+ visitorData in context", base, with_visitor_context()),
        ("+ both", with_visitor_header(), with_visitor_context()),
        ("+ both, generous context", with_visitor_header(), generous()),
    )
    for label, headers, ctx in trials:
        kodiutils.log("cookie-as-tv [%-24s]: %s"
                      % (label, _ask_player(video_id, name, headers,
                                            context=ctx)))
    if not visitor:
        kodiutils.log("cookie-as-tv: no visitor id was available, so three of"
                      " those trials were the first one again")


UBER_URL = "https://accounts.google.com/OAuthLogin"
MERGE_URL = "https://accounts.google.com/MergeSession"


def _mint_web_session():
    """Can a browser session be minted from the token we already hold?

    This is the shortcut past the whole bridge. The addon already plays the
    web client's DASH; what makes that path need a manual cookie export is
    only that we have no way to *become* the web client. Google's own
    accounts service has a route that turns an OAuth token into browser
    cookies -- OAuthLogin issues an "uberauth" token, MergeSession trades it
    for a Set-Cookie -- which is how Android hands a signed-in session to a
    WebView.

    What is not known is whether our token is allowed near it. It carries
    one scope, youtube, and the accounts route wants
    https://www.google.com/accounts/OAuthLogin, which Google does not hand
    to ordinary OAuth clients. So ask, and read the refusal: an error naming
    the scope means "request that scope at sign-in and try again", while an
    error about the client means the door is shut to a device-code app and
    the bridge is the way.

    Nothing here signs anything in or stores anything. It asks two questions
    and prints what Google says.
    """
    try:
        from . import oauth
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        kodiutils.log("mint: no token stored, nothing to try")
        return
    kodiutils.log("mint: the stored token carries scope %s" % oauth.SCOPE)

    try:
        reply = requests.get(UBER_URL, timeout=TIMEOUT,
                             params={"source": "ChromiumBrowser",
                                     "issueuberauth": "1"},
                             headers={"Authorization": "Bearer " + token,
                                      "User-Agent": api.UA})
    except Exception as exc:
        kodiutils.log_error("mint: OAuthLogin failed: %s" % exc)
        return
    body = (reply.text or "").strip()
    kodiutils.log("mint: OAuthLogin -> HTTP %d, %d bytes: %s"
                  % (reply.status_code, len(body), body[:200].replace("\n", " ")))
    if reply.status_code != 200 or not body or " " in body[:40]:
        kodiutils.log("mint: no uberauth token came back, so MergeSession has "
                      "nothing to trade")
        return

    try:
        merged = requests.get(MERGE_URL, timeout=TIMEOUT, allow_redirects=False,
                              params={"source": "ChromiumBrowser",
                                      "uberauth": body,
                                      "continue": api.ORIGIN},
                              headers={"User-Agent": api.UA})
    except Exception as exc:
        kodiutils.log_error("mint: MergeSession failed: %s" % exc)
        return
    jar = sorted(merged.cookies.keys())
    kodiutils.log("mint: MergeSession -> HTTP %d, cookies %s"
                  % (merged.status_code, jar or "none"))
    wanted = [name for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")
              if name in jar]
    kodiutils.log("mint: of the names the addon needs, it set %s"
                  % (wanted or "none"))


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _mint_browser_flow():
    """Would a browser sign-in with our client reach the minting scope?

    Asked once with a localhost redirect and refused -- and the refusal was
    nothing to do with scopes. Google's authError blob decodes to
    `invalid_request: Localhost URI is not allowed for 'NATIVE_DEVICE'
    client type`, which is a complaint about the redirect, so the scope was
    never reached. A device client gets the out-of-band redirect instead, so
    ask again with each redirect it is allowed and decode what comes back
    rather than reading a verdict off a status code.
    """
    try:
        from . import oauth
    except Exception:
        return
    client_id, _secret = oauth.credentials()
    if not client_id:
        return
    both = oauth.SCOPE + " https://www.google.com/accounts/OAuthLogin"

    for label, redirect, scope in (
            ("oob, both scopes", "urn:ietf:wg:oauth:2.0:oob", both),
            ("oob, minting scope only", "urn:ietf:wg:oauth:2.0:oob",
             "https://www.google.com/accounts/OAuthLogin"),
            ("oob, youtube only (control)", "urn:ietf:wg:oauth:2.0:oob",
             oauth.SCOPE)):
        try:
            reply = requests.get(AUTH_URL, timeout=TIMEOUT,
                                 allow_redirects=False,
                                 params={"client_id": client_id,
                                         "redirect_uri": redirect,
                                         "response_type": "code",
                                         "scope": scope},
                                 headers={"User-Agent": api.UA})
        except Exception as exc:
            kodiutils.log_error("mint browser [%s]: %s" % (label, exc))
            continue
        where = reply.headers.get("Location", "")
        kodiutils.log("mint browser [%-27s]: HTTP %d -> %s"
                      % (label, reply.status_code,
                         _auth_error(where) or where[:90] or "(no redirect)"))


def _auth_error(location):
    """Google's authError blob, decoded, or "" if there is not one.

    The reason is base64 inside the redirect and reads plainly once
    decoded -- worth doing, since the last run reported "an error page" for
    a message that named the exact problem.
    """
    if "authError=" not in location:
        return ""
    blob = location.split("authError=", 1)[1].split("&")[0]
    try:
        raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    except Exception:
        return ""
    readable = "".join(chr(b) if 32 <= b < 127 else " " for b in raw)
    return readable.strip()


def _ask_google_why():
    """Make Google say more than "invalid argument" and "invalid_scope".

    Two refusals have been taken at face value for want of asking twice.

    The device endpoint refused the youtube and minting scopes together
    with `invalid_scope` and named neither, so each is now asked for on its
    own: if youtube alone is accepted and the minting scope alone is not,
    the refusal is about that scope rather than about the pair or the
    client.

    InnerTube answers 400 "Request contains an invalid argument" with an
    empty error.details. Google's APIs take $.xgafv=2, which selects the
    verbose error format, and prettyPrint -- worth one request to find out
    whether the same call explains itself when asked to.
    """
    try:
        from . import oauth
    except Exception:
        return
    client_id, _secret = oauth.credentials()
    minting = "https://www.google.com/accounts/OAuthLogin"

    if client_id:
        for label, scope in (("youtube only (control)", oauth.SCOPE),
                             ("minting scope only", minting),
                             ("both", oauth.SCOPE + " " + minting)):
            try:
                reply = requests.post(oauth.DEVICE_CODE_URL, timeout=TIMEOUT,
                                      data={"client_id": client_id,
                                            "scope": scope},
                                      headers={"User-Agent": api.UA})
            except Exception as exc:
                kodiutils.log_error("ask google [%s]: %s" % (label, exc))
                continue
            kodiutils.log("ask google: device code, %-22s -> HTTP %d: %s"
                          % (label, reply.status_code,
                             (reply.text or "")[:120].replace("\n", " ")))

    # And the InnerTube 400, asked to explain itself.
    try:
        token = oauth.access_token()
    except Exception:
        token = ""
    if not token:
        return
    payload = {"context": api.context(client_name=api.CLIENT_NAME),
               "browseId": "default"}
    spec = api.client_spec(api.CLIENT_NAME)
    for label, params in (("as sent", {"prettyPrint": "false"}),
                          ("with $.xgafv=2", {"$.xgafv": "2",
                                              "prettyPrint": "true"})):
        try:
            reply = requests.post(
                api.BASE + "browse", data=json.dumps(payload), params=params,
                timeout=TIMEOUT,
                headers={"Content-Type": "application/json",
                         "User-Agent": spec["context"].get("userAgent", api.UA),
                         "Origin": api.ORIGIN,
                         "X-YouTube-Client-Name": spec["id"],
                         "X-YouTube-Client-Version":
                             api.effective_version(api.CLIENT_NAME),
                         "Authorization": "Bearer " + token})
        except Exception as exc:
            kodiutils.log_error("ask google [%s]: %s" % (label, exc))
            continue
        kodiutils.log("ask google: browse as WEB with a token, %-14s -> "
                      "HTTP %d: %s"
                      % (label, reply.status_code,
                         (reply.text or "")[:400].replace("\n", " ")))


def _mint_scope():
    """Would Google issue our client a token that OAuthLogin accepts?

    OAuthLogin answered `Error=badauth`, which is the accounts service's
    terse refusal and names nothing -- not a scope, not a client. The token
    it was given carries the youtube scope alone, and the route wants
    https://www.google.com/accounts/OAuthLogin, so the obvious next question
    is whether our client may even ask for that scope.

    The device-code endpoint answers that on its own, without anyone signing
    in: hand it the combined scope and it either returns a user code, which
    means the scope is allowed and a sign-in would be worth doing, or it
    refuses and names invalid_scope, which closes the route for good and
    leaves the bridge as the way.
    """
    try:
        from . import oauth
    except Exception:
        return
    client_id, _secret = oauth.credentials()
    if not client_id:
        kodiutils.log("mint scope: no client id configured")
        return
    combined = oauth.SCOPE + " https://www.google.com/accounts/OAuthLogin"
    try:
        reply = requests.post(oauth.DEVICE_CODE_URL, timeout=TIMEOUT,
                              data={"client_id": client_id, "scope": combined},
                              headers={"User-Agent": api.UA})
    except Exception as exc:
        kodiutils.log_error("mint scope: %s" % exc)
        return
    body = (reply.text or "")[:300].replace("\n", " ")
    kodiutils.log("mint scope: asking for both scopes -> HTTP %d: %s"
                  % (reply.status_code, body))
    if reply.status_code == 200:
        kodiutils.log("mint scope: the scope is allowed -- a sign-in with it "
                      "is worth trying, and nothing has been signed in here")


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
        _drive_session(solved_url, config, wanted, picked_video,
                       client_name)
        _session_check(solved_url, config, wanted, picked_video,
                       client_name, streaming.get("drmParams", ""))


def _session_check(url, config, audio, video, client_name,
                   drm_params=""):
    """Does the session driver produce what ISA would need?

    The bridge stands or falls on this: an initialisation segment per track
    and numbered media segments that begin with a moof. Checked here against
    the live server rather than a capture, because a captured response is
    zero-padded past its last real part and cannot show a segment closing.
    """
    from . import sabr_session

    def post(target, body):
        try:
            reply = requests.post(target, data=body, timeout=TIMEOUT, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Content-Type": "application/x-protobuf",
            })
        except Exception as exc:
            kodiutils.log_error("session check: %s" % exc)
            return b""
        if reply.status_code != 200:
            kodiutils.log("session check: HTTP %d" % reply.status_code)
            return b""
        return reply.content

    spec = api.client_spec(client_name)
    # Lists, matching what playback opens a session with -- a probe that
    # constructs it differently from the real path tests something else.
    session = sabr_session.Session(
        url, config,
        [_format_entry(audio)] if audio else [],
        [_format_entry(video)] if video else [],
        client_name, spec["id"], api.effective_version(client_name), post)

    try:
        for _ in range(4):
            session.fetch()
    except sabr_session.SabrError as exc:
        kodiutils.log_error("session check: the endpoint refused: %s" % exc)
        return

    for itag in sorted(session.segments):
        head = session.initialisation.get(itag, b"")
        held = sorted(session.segments[itag])
        gaps = [b - a for a, b in zip(held, held[1:]) if b - a != 1]
        shapes = {}
        for sequence in held[:4]:
            body = session.segments[itag][sequence]
            shapes[sequence] = (len(body), bytes(body[4:8]))
        kodiutils.log("session check: itag %-4s init %d bytes (moov %s), "
                      "%d segments %s..%s%s"
                      % (itag, len(head), b"moov" in head, len(held),
                         held[0] if held else "-", held[-1] if held else "-",
                         ", GAPS %s" % gaps if gaps else ", contiguous"))
        kodiutils.log("session check: itag %-4s first segments %s"
                      % (itag, shapes))
    if not session.segments:
        kodiutils.log("session check: no segment closed in four exchanges")
        return
    _bridge_check(session, audio, video, drm_params)


def _start_number(manifest_text, itag):
    """startNumber for one representation, read back out of the manifest."""
    marker = '<Representation id="%d"' % itag
    at = manifest_text.find(marker)
    if at < 0:
        return 0
    at = manifest_text.find('startNumber="', at)
    if at < 0:
        return 0
    at += len('startNumber="')
    return int(manifest_text[at:manifest_text.find('"', at)] or 0)


def _bridge_check(session, audio, video, drm_params=""):
    """Fetch the manifest and a segment the way ISA would: over HTTP.

    The routes and the manifest are only worth anything if a real client can
    walk them, so this goes through the running proxy rather than calling
    the functions directly -- the secret, the query parsing, the blocking
    segment fetch and all.
    """
    from . import sabr_bridge
    # Deliberately through the file, not through register(): the plugin and
    # the service are different processes, and an in-process registry made
    # the manifest 404 while every function it called worked perfectly.
    key = sabr_bridge.set_context(session.url, session.config,
                                  audio, video, session.client_name,
                                  drm_params=drm_params)
    try:
        proxy = kodiutils.read_json("license_proxy.json", default={}) or {}
        port, secret = proxy.get("port"), proxy.get("secret")
        if not port or not secret:
            kodiutils.log("bridge check: the proxy has not published a port")
            return
        base = "http://127.0.0.1:%d" % port

        got = requests.get("%s/sabr/manifest" % base, timeout=TIMEOUT,
                           params={"id": key, "k": secret})
        kodiutils.log("bridge check: manifest -> HTTP %d, %d bytes"
                      % (got.status_code, len(got.content)))
        if got.status_code != 200:
            return
        text = got.text
        kodiutils.log("bridge check: manifest says %s"
                      % text[:400].replace("\n", " "))
        kodiutils.log("bridge check: ContentProtection elements %d, "
                      "cenc:pssh present %s"
                      % (text.count("<ContentProtection"), "<cenc:pssh>" in text))

        for fmt in (audio, video):
            if not fmt:
                continue
            itag = fmt["itag"]
            head = requests.get("%s/sabr/init" % base, timeout=TIMEOUT,
                                params={"id": key, "itag": itag, "k": secret})
            # The service opened its own session, so ask it for a number it
            # will have: the manifest's own startNumber.
            number = _start_number(text, itag)
            seg = requests.get("%s/sabr/segment" % base, timeout=TIMEOUT,
                               params={"id": key, "itag": itag, "n": number,
                                       "k": secret})
            kodiutils.log("bridge check: itag %-4s init HTTP %d (%d bytes, %s)"
                          "  segment %d HTTP %d (%d bytes, %s)"
                          % (itag, head.status_code, len(head.content),
                             head.content[4:8] if head.content else b"-",
                             number, seg.status_code, len(seg.content),
                             seg.content[4:8] if seg.content else b"-"))
    finally:
        sabr_bridge.forget(key)


def _drive_session(url, config, audio, video, client_name=None):
    """Drive a real SABR session: does it advance, segment by segment?

    Nothing advanced across every previous attempt -- not a six second
    wait, not a claimed buffer, not a position -- and every explanation I
    offered for that was a guess. The captured traffic answers it outright.
    A response's NEXT_REQUEST_POLICY carries a blob in field 7; the request
    that follows sends that same blob, byte for byte, as streamerContext
    field 3. Rebuilt from a capture and compared to the request that
    actually followed it, they are identical. That echo is what makes a
    SABR session a session; without it every request is request one.

    So run the loop the browser runs: ask for the live edge, then echo what
    comes back, claim what arrived, and ask again.
    """
    client_name = client_name or api.CLIENT_NAME
    spec = api.client_spec(client_name)
    info = sabr.client_info(spec["id"], api.effective_version(client_name),
                            os_name=spec["context"].get("osName", "X11"))

    tracks = {}
    for fmt in (audio, video):
        if fmt and fmt.get("itag"):
            tracks[fmt["itag"]] = _format_entry(fmt)

    echo = b""
    # itag -> {sequence: media start time}. Every sequence actually held,
    # rather than a running first/last pair: the server answered the live
    # edge with N and then handed back N-1, and a pair that only grew
    # forwards could not represent "I now hold both", so the claim stopped
    # changing and so did the response, for four rounds.
    state = {}
    position = sabr.LIVE_EDGE
    started = time.time()

    audio_entries = [tracks[audio["itag"]]] if audio and audio.get("itag") else []
    video_entries = [tracks[video["itag"]]] if video and video.get("itag") else []

    for round_number in range(1, 7):
        buffered = []
        for itag, seen_starts in sorted(state.items()):
            first, last = min(seen_starts), max(seen_starts)
            # startTimeMs belongs to the first sequence of the range and the
            # duration covers the whole run -- that is what the captured
            # ranges do when they extend: start held still at the first
            # segment while duration grew from 5015 to 10031.
            buffered.append(sabr.buffered_range(
                tracks[itag], seen_starts[first],
                (last - first + 1) * SEGMENT_MS, first, last))
        body = sabr.build_request(
            config, audio=audio_entries, video=video_entries,
            player_time_ms=position,
            buffered=buffered,
            context=sabr.streamer_context(info=info, echo=echo),
            elapsed_ms=int((time.time() - started) * 1000))
        try:
            reply = requests.post(url, data=body, timeout=TIMEOUT, headers={
                "User-Agent": api.UA,
                "Origin": api.ORIGIN,
                "Referer": api.ORIGIN + "/",
                "Content-Type": "application/x-protobuf",
            })
        except Exception as exc:
            kodiutils.log_error("sabr drive [round %d]: %s" % (round_number, exc))
            return
        if reply.status_code != 200 or not reply.content:
            kodiutils.log("sabr drive [round %d]: HTTP %d, %d bytes"
                          % (round_number, reply.status_code,
                             len(reply.content)))
            return

        parts = list(sabr.parse_ump(reply.content))
        media = sum(len(p) for kind, p in parts if kind == 21)
        if round_number == 1:
            # Does a segment arrive with its initialisation, or does ISA
            # have to be given one from somewhere else? An fMP4 says so in
            # its first box: ftyp/moov is initialisation, moof is media.
            # Worth reading rather than reasoning about -- the bridge has to
            # hand ISA an init segment either way.
            for kind, payload in parts:
                if kind != 21 or len(payload) < 16:
                    continue
                # A MEDIA payload opens with the id of its MEDIA_HEADER.
                body = payload[1:]
                box = body[4:8]
                kodiutils.log("sabr drive: first media part opens with %r "
                              "(%s)" % (box, body[:16].hex()))
                break
        errors = [p for kind, p in parts if kind == 44]
        seen = []
        for kind, payload in parts:
            if kind != 20:
                continue
            got = dict((n, v) for n, _w, v in sabr.fields(payload))
            itag, sequence, start = got.get(3), got.get(9), got.get(11)
            seen.append((itag, sequence))
            if itag in tracks and sequence is not None and start is not None:
                state.setdefault(itag, {})[sequence] = start
        # The playhead stays where the session started. The captured
        # requests hold field 28 still across rounds and let the buffered
        # ranges do the advancing; taking it from whichever header landed
        # last mixed the audio and video timelines together.
        if position == sabr.LIVE_EDGE and state:
            position = min(min(seen.values()) for seen in state.values())
        kodiutils.log("sabr drive [round %d]: %d bytes, %d media, (itag,seq) %s"
                      "  holding %s%s"
                      % (round_number, len(reply.content), media, seen,
                         {itag: "%d..%d" % (min(v), max(v))
                          for itag, v in sorted(state.items())},
                         "  ERROR %r" % errors[0][:40] if errors else ""))

        echo = sabr.next_request_echo(reply.content)
        if not echo:
            kodiutils.log("sabr drive [round %d]: no NEXT_REQUEST_POLICY to "
                          "echo -- the session cannot continue" % round_number)
            return


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
