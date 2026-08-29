"""The device-code sign-in the regular YouTube addon uses, tried here.

plugin.video.youtube signs in without a browser export: it asks Google for a
device code, shows "go to this url and type these letters", polls until the
account authorises it, and then sends ``Authorization: Bearer <token>`` on
every InnerTube call. The flow, read from its login_client.py rather than
recalled:

    POST https://accounts.google.com/o/oauth2/device/code
         client_id, scope=https://www.googleapis.com/auth/youtube
      -> device_code, user_code, verification_url, interval, expires_in

    POST https://www.googleapis.com/oauth2/v4/token
         client_id, client_secret, code=<device_code>,
         grant_type=http://oauth.net/grant_type/device/1.0
      -> access_token, refresh_token, expires_in     (or error=authorization_pending)

**YouTube TV accepts it.** That was an open question for as long as this
addon has existed -- every capture of the web player, all sixty-nine
authenticated requests, carries ``Authorization: SAPISIDHASH`` and not one
carries a bearer token -- and it is now settled by measurement rather than
argument: a device-code token returned a 150 channel lineup.

The identity matters as much as the token. Asked as ``WEB_UNPLUGGED``, the
same token draws HTTP 400 ``INVALID_ARGUMENT``; asked as
``TVHTML5_UNPLUGGED`` it works. That is consistent with what the token is --
minted for a limited-input client -- and with how the regular YouTube addon
uses one, pairing every bearer request with a TVHTML5 identity and a Cobalt
user agent. So the accepted identity is stored with the token and travels
with it (see api.Api.client_name); a token without it is a valid credential
that fails every call.

Sign-in still proves itself before the token is kept: it asks each identity
in turn for the account's own lineup and keeps the token only for one that
answers. A stored credential that silently fails is worse than none.

The client id and secret are the user's own Google API project, the same pair
plugin.video.youtube needs. Nothing is shipped with the addon.
"""

import time

import requests

from . import kodiutils

DEVICE_CODE_URL = "https://accounts.google.com/o/oauth2/device/code"
TOKEN_URL = "https://www.googleapis.com/oauth2/v4/token"
SCOPE = "https://www.googleapis.com/auth/youtube"
GRANT_TYPE = "http://oauth.net/grant_type/device/1.0"

TOKEN_FILE = "oauth.json"
TIMEOUT = 20

# The user agent the flow is documented against. Google has been known to
# answer a bare python-requests differently.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36")


class OAuthError(Exception):
    pass


def _from_settings():
    return ((kodiutils.get_setting("oauth_client_id", "") or "").strip(),
            (kodiutils.get_setting("oauth_client_secret", "") or "").strip())


def _from_youtube_addon():
    """The pair plugin.video.youtube already holds, if it is installed.

    Anyone who set that addon up has made exactly the Google API project this
    one needs, and asking them to make a second is asking twice for the same
    thing. The setting names are read off a real box rather than guessed --
    plugin.video.youtube logs them on every settings change:

        Get setting 'youtube.api.id': '406...qnfrg' (str, success)
        Get setting 'youtube.api.secret': 'ZxC...Phc' (str, success)

    It is the user's own project either way, and this addon spends none of
    its Data API quota: the InnerTube calls go to tv.youtube.com with a
    bearer token and never touch googleapis.com. The project is used to mint
    the token and for nothing else.
    """
    try:
        import xbmc
        import xbmcaddon
        # Ask before constructing one. xbmcaddon.Addon() on an addon that is
        # not installed raises, and Kodi logs "EXCEPTION: Unknown addon id"
        # at error level on the way past -- alarming, in a log, for something
        # that is only a look.
        if not xbmc.getCondVisibility("System.HasAddon(plugin.video.youtube)"):
            return ("", "")
        other = xbmcaddon.Addon("plugin.video.youtube")
        return ((other.getSetting("youtube.api.id") or "").strip(),
                (other.getSetting("youtube.api.secret") or "").strip())
    except Exception:
        # Not installed, which is the ordinary case and not worth a log line.
        return ("", "")


# Google's own "YouTube on TV" OAuth client, if it is filled in.
#
# Asked for deliberately, and it is the one source here that is not the user's
# own project: the device flow runs against Google's application rather than
# one they created, so a box needs no setup at all. The account holder still
# authorises the code on Google's consent screen and still gets a token for
# their own account and their own subscription -- nothing is bypassed -- but
# the client presented to Google's identity service is not ours.
#
# Two things to know before filling it in. Google has moved against other
# projects doing this, so expect it to stop working without notice; and it is
# the last source tried, so a real project in any of the three above it wins
# and this never runs.
#
# Left empty because the pair could not be obtained from where this was
# written -- every Google host is blocked here, it appears in none of the
# captures, and writing one from memory is the kind of guess that has cost
# this project whole days. Fill both halves in and sign-in needs nothing else.
GOOGLE_TV_CLIENT = ("", "")


def _google_tv():
    return (GOOGLE_TV_CLIENT[0].strip(), GOOGLE_TV_CLIENT[1].strip())


def _baked():
    """A project compiled into the build, if there is one.

    Personal builds can ship ``lib/baked_oauth.py`` with CLIENT_ID and
    CLIENT_SECRET, so a box needs no setup at all. The module is absent from
    the repository and gitignored, for the same reason the baked session is:
    published in a public repo, one project would front every install, and
    whoever owns it carries the abuse and the unverified-app user cap for
    everyone at once.
    """
    try:
        from . import baked_oauth
    except ImportError:
        return ("", "")
    return ((getattr(baked_oauth, "CLIENT_ID", "") or "").strip(),
            (getattr(baked_oauth, "CLIENT_SECRET", "") or "").strip())


def credentials():
    """A Google API project to run the device-code flow against.

    Four places, in order of whose it most clearly is: this addon's own
    settings, then plugin.video.youtube's if that is set up, then one baked
    into the build, then Google's own TV client if that constant is filled
    in. Both halves or nothing -- half a pair fails at Google with an error
    about the wrong one.

    Google's device flow has no anonymous grant, so a project has to exist
    somewhere; the order above is about whose it is, from most clearly the
    user's to least.
    """
    for source, (client_id, secret) in (
            ("this addon's settings", _from_settings()),
            ("plugin.video.youtube", _from_youtube_addon()),
            ("the build", _baked()),
            ("Google's own TV client", _google_tv())):
        if client_id and secret:
            if source != "this addon's settings":
                kodiutils.log("oauth: using the Google API project from %s"
                              % source)
            return client_id, secret
    return ("", "")


def request_code(client_id):
    """Ask Google for a code to show. Returns the whole response."""
    try:
        response = requests.post(
            DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": SCOPE},
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise OAuthError("could not reach Google: %s" % exc)
    if response.status_code != 200:
        raise OAuthError("Google refused the code request: HTTP %d %s"
                         % (response.status_code, (response.text or "")[:300]))
    data = response.json()
    if not data.get("device_code") or not data.get("user_code"):
        raise OAuthError("Google's answer carried no code: %s"
                         % str(data)[:300])
    return data


def poll_for_token(client_id, secret, device_code, interval, deadline,
                   cancelled=lambda: False):
    """Wait for the account to authorise this device.

    ``authorization_pending`` is the normal answer until someone types the
    code; ``slow_down`` means back off. Anything else is a real refusal and
    is raised rather than retried forever.
    """
    wait = max(float(interval or 5), 1.0)
    while time.time() < deadline:
        if cancelled():
            return None
        time.sleep(wait)
        try:
            response = requests.post(
                TOKEN_URL,
                data={"client_id": client_id, "client_secret": secret,
                      "code": device_code, "grant_type": GRANT_TYPE},
                headers={"User-Agent": UA},
                timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise OAuthError("could not reach Google: %s" % exc)
        try:
            data = response.json()
        except ValueError:
            raise OAuthError("Google answered HTTP %d with no JSON"
                             % response.status_code)
        error = data.get("error")
        if not error:
            if not data.get("access_token"):
                raise OAuthError("no access token in Google's answer")
            return data
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            wait += 2
            continue
        raise OAuthError("Google refused: %s (%s)"
                         % (error, data.get("error_description") or ""))
    raise OAuthError("the code expired before it was entered")


def save(token, client_name=None):
    """Keep the token in the addon profile.

    ``client_name`` is the identity YouTube TV accepted this token as. It is
    part of the credential, not a detail: the same token is refused as
    WEB_UNPLUGGED and works as TVHTML5_UNPLUGGED, so losing it would leave a
    valid token that fails every call.
    """
    stored = load()
    kodiutils.write_json(TOKEN_FILE, {
        "access_token": token.get("access_token", ""),
        "refresh_token": token.get("refresh_token", ""),
        "expires_at": int(time.time() + int(token.get("expires_in") or 0)),
        "client_name": client_name or stored.get("client_name", ""),
    })


def load():
    return kodiutils.read_json(TOKEN_FILE, default={}) or {}


def forget():
    kodiutils.write_json(TOKEN_FILE, {})


def refresh(client_id, secret, refresh_token):
    """Trade a refresh token for a fresh access token."""
    try:
        response = requests.post(
            TOKEN_URL,
            data={"client_id": client_id, "client_secret": secret,
                  "refresh_token": refresh_token,
                  "grant_type": "refresh_token"},
            headers={"User-Agent": UA}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise OAuthError("could not reach Google: %s" % exc)
    data = {}
    try:
        data = response.json()
    except ValueError:
        pass
    if not data.get("access_token"):
        raise OAuthError("refresh refused: %s"
                         % (data.get("error") or response.status_code))
    data.setdefault("refresh_token", refresh_token)
    return data


def access_token():
    """A usable access token, refreshed if it has gone stale, or ""."""
    stored = load()
    token = stored.get("access_token") or ""
    if not token:
        return ""
    # A minute of slack: a token that expires mid-request is a confusing 401.
    if stored.get("expires_at", 0) > time.time() + 60:
        return token
    client_id, secret = credentials()
    if not client_id or not stored.get("refresh_token"):
        return token
    try:
        fresh = refresh(client_id, secret, stored["refresh_token"])
    except OAuthError as exc:
        kodiutils.log_error("oauth refresh failed: %s" % exc)
        return token
    save(fresh)
    kodiutils.log("oauth: access token refreshed")
    return fresh.get("access_token") or token
