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


def credentials():
    """The user's own Google API project, from settings. Both or nothing."""
    client_id = (kodiutils.get_setting("oauth_client_id", "") or "").strip()
    secret = (kodiutils.get_setting("oauth_client_secret", "") or "").strip()
    return (client_id, secret) if client_id and secret else ("", "")


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
