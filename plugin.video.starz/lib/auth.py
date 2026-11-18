"""Starz sign-in and session handling.

Read from the sign-in HAR (docs/recon.md rows A, B):

* Every auth.starz.com call before sign-in carries an ``AuthTokenAuthorization``
  header: a ``PLAYAUTH1.0 ...`` string signed by Starz's own server and
  minted, fresh per call, by GET www.starz.com/sapi/header/v1/starz/us/{deviceId}.
  It needs no cookies, only a device id (32 hex chars) the client chooses.
* Sign-in: GET Activate/Code -> {"code"} (4 chars), then POST User/login with
  {"code", "emailAddress", "password", "captchaResponse"}. The session is bound
  to the device id; no cookie or token comes back, only {"subscriptionState"}.
* GET Token (with a fresh auth token) -> {"playSessionToken": "PLAYSESSION1.0
  x=..."}. Every signed-in call then sends it as ``SessionTokenAuthorization``.

The web login sends a reCAPTCHA Enterprise token Kodi cannot produce, and
Starz refused it without one (HTTP 400). Activation is the way in (activation
HAR): starz.com/activate checks a code with GET Activate/{code} and links it
with POST Activate/Login {code, emailAddress, password, preLoginEmailAddress}
-- no captcha. Until a device's code is linked, Token answers 412 "Not
registered". Kodi either links its own code with the user's credentials
(sign_in) or shows the code to be entered on another device (poll_activation).
"""

import time
import uuid

import requests

from lib import kodiutils

WEB = "https://www.starz.com"
AUTH_API = "https://auth.starz.com/api/v4/"
# Country segment of the header-minting path; the capture is from the US site.
COUNTRY = "us"

# The browser the captures came from. The minted token records platform fields
# Starz derives server-side, so keep the request looking like that browser.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) "
              "Gecko/20100101 Firefox/158.0")

SESSION_FILE = "session.json"
# Kept across sign-outs: the account tracks devices (the web client advertises
# DevicePlaybackLimitSupport), so a new id per sign-in would use up slots.
DEVICE_FILE = "device.json"

TIMEOUT = 20
REGISTER_WAIT_TRIES = 3
REGISTER_WAIT_SECS = 2


class NotReadyError(Exception):
    """A Starz-specific step that has not been built from a capture yet."""


class StarzAuthError(Exception):
    """Starz refused a sign-in step; the message is what Starz said."""


def normalise_username(username):
    """Trim and lowercase an email login (TV keyboards add capitals/spaces)."""
    username = (username or "").strip()
    if "@" in username:
        username = username.lower()
    return username


def _play_headers(resp):
    """Starz's X-Play-* response headers (correlation id, subscription state,
    user type...): the only detail some errors carry."""
    return {k: v for k, v in resp.headers.items() if k.lower().startswith("x-play-")}


def log_response(label, resp):
    """Log a failed call in full enough detail to diagnose from a Kodi log.
    Called only on responses, never with request bodies, so no password."""
    kodiutils.log_error("%s -> HTTP %d, content-type=%s, x-play=%s, body=%r" % (
        label, resp.status_code, resp.headers.get("Content-Type", ""),
        _play_headers(resp), (resp.text or "")[:800]))


def _error_text(resp):
    """The most useful human-readable error out of a Starz response."""
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or "").strip()[:300]
        if not text:
            text = "(empty response) %s" % (_play_headers(resp) or "")
        return "HTTP %d: %s" % (resp.status_code, text)
    if isinstance(data, dict):
        for key in ("message", "Message", "error_description", "error",
                    "errorMessage", "description", "title"):
            if data.get(key):
                return "HTTP %d: %s" % (resp.status_code, data[key])
    return "HTTP %d: %s" % (resp.status_code, str(data)[:300])


class StarzAuth(object):

    def __init__(self):
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        self._session = kodiutils.read_json(SESSION_FILE, default=None) or {}

    # -- device + tokens ---------------------------------------------------

    @property
    def device_id(self):
        device = kodiutils.read_json(DEVICE_FILE, default=None) or {}
        if not device.get("id"):
            device = {"id": uuid.uuid4().hex}
            kodiutils.write_json(DEVICE_FILE, device)
        return device["id"]

    def auth_token(self):
        """A freshly minted PLAYAUTH token. The web client mints one per call,
        so do the same rather than guess at its lifetime."""
        resp = self.http.get(
            "%s/sapi/header/v1/starz/%s/%s" % (WEB, COUNTRY, self.device_id),
            headers={"Accept": "text/html; charset=utf-8",
                     "Content-Type": "text/html; charset=utf-8",
                     "Referer": WEB + "/us/en/login"},
            timeout=TIMEOUT)
        resp.raise_for_status()
        token = resp.text.strip().strip('"')
        if not token.startswith("PLAYAUTH"):
            raise StarzAuthError("Unexpected auth token response: %s"
                                 % _error_text(resp))
        return token

    def _base_headers(self):
        return {"Accept": "application/json, text/plain, */*",
                "Origin": WEB, "Referer": WEB + "/",
                "X-Client-Features": "DevicePlaybackLimitSupport"}

    def app_headers(self):
        headers = self._base_headers()
        headers["AuthTokenAuthorization"] = self.auth_token()
        return headers

    def session_headers(self):
        token = self._session.get("session_token")
        if not token:
            raise StarzAuthError("Not signed in")
        headers = self._base_headers()
        headers["SessionTokenAuthorization"] = token
        return headers

    # -- state -------------------------------------------------------------

    @property
    def signed_in(self):
        return bool(self._session.get("session_token"))

    @property
    def username(self):
        return self._session.get("username", "")

    @property
    def profile_id(self):
        return self._session.get("profile_id", "")

    @property
    def profile_name(self):
        return self._session.get("profile_name", "")

    def _save(self, **changes):
        self._session.update(changes)
        kodiutils.write_json(SESSION_FILE, self._session)

    def clear(self):
        self._session = {}
        kodiutils.delete_file(SESSION_FILE)

    def sign_out(self):
        """End the session on Starz's side, then forget it locally.

        The web client logs out with DELETE Token under the session token
        (requiresSessionToken in its bundle; read from the logout HAR's JS,
        the request itself was not captured). The local session is cleared
        even if Starz cannot be reached, so Sign out always signs out.
        [VERIFY] whether this also frees the device's slot in the account's
        device limit.
        """
        if self.signed_in:
            try:
                resp = self.http.delete(AUTH_API + "Token",
                                        headers=self.session_headers(),
                                        timeout=TIMEOUT)
                kodiutils.log("DELETE Token -> HTTP %d" % resp.status_code)
                if resp.status_code >= 400:
                    log_response("DELETE Token", resp)
            except requests.RequestException as exc:
                kodiutils.log_error("DELETE Token failed: %s" % exc)
        self.clear()

    # -- sign-in -------------------------------------------------------------

    def activation_code(self):
        """GET Activate/Code -> (code, activation_url)."""
        resp = self.http.get(AUTH_API + "Activate/Code",
                             headers=self.app_headers(), timeout=TIMEOUT)
        if resp.status_code != 200:
            log_response("Activate/Code", resp)
            raise StarzAuthError(_error_text(resp))
        data = resp.json()
        kodiutils.log("Activation code issued, url=%s" % data.get("activationUrl"))
        return data.get("code"), data.get("activationUrl") or WEB + "/activate"

    def sign_in(self, username, password):
        """Email/password sign-in through the activation endpoint.

        The web login (User/login) needs a reCAPTCHA token Kodi cannot make;
        it returned HTTP 400 in the first Kodi test. starz.com/activate instead
        links a device's activation code to an account with POST Activate/Login
        {code, emailAddress, password, preLoginEmailAddress} and no captcha
        (activation HAR #331). Kodi does both halves itself: it asks for its
        own code, links it with the credentials, then fetches the session for
        its now-registered device.
        """
        email = normalise_username(username)
        code, _url = self.activation_code()
        body = {"code": code, "emailAddress": email, "password": password,
                "preLoginEmailAddress": email}
        resp = self.http.post(AUTH_API + "Activate/Login", json=body,
                              headers=self.app_headers(), timeout=TIMEOUT)
        kodiutils.log("Activate/Login -> HTTP %d" % resp.status_code)
        if resp.status_code != 200:
            log_response("Activate/Login", resp)
            raise StarzAuthError(_error_text(resp))
        try:
            state = resp.json().get("subscriptionState", "")
        except ValueError:
            state = ""
        kodiutils.log("Device linked, subscriptionState=%s" % state)
        # Token answered 412 "Not registered" before linking; allow the
        # registration a moment to land before giving up.
        for attempt in range(REGISTER_WAIT_TRIES):
            try:
                self.refresh()
                break
            except StarzAuthError:
                if attempt == REGISTER_WAIT_TRIES - 1:
                    raise
                time.sleep(REGISTER_WAIT_SECS)
        self._save(username=email, subscription_state=state)

    def poll_activation(self):
        """One poll of the activation-code flow: True once the device is linked.

        [VERIFY] Assumes GET Token starts returning a signed-in session for
        this device once its code has been entered at starz.com/activate.
        """
        try:
            self.refresh()
        except StarzAuthError as exc:
            kodiutils.log("Activation poll: %s" % exc)
            return False
        return True

    def refresh(self):
        """GET Token -> playSessionToken, then confirm it is a real account.

        Also the refresh path: the session lives on the device id, so a fresh
        auth token re-issues it. [VERIFY] the session token's lifetime.
        """
        resp = self.http.get(AUTH_API + "Token", headers=self.app_headers(),
                             timeout=TIMEOUT)
        if resp.status_code != 200:
            log_response("Token", resp)
            raise StarzAuthError(_error_text(resp))
        data = resp.json() or {}
        token = data.get("playSessionToken")
        # Status fields only, never the token itself.
        kodiutils.log("Token -> session=%s accountStatus=%s country=%s" % (
            "yes" if token else "no", data.get("accountStatus"),
            data.get("residencyCountry")))
        if not token:
            raise StarzAuthError("No session token: %s" % _error_text(resp))
        headers = self._base_headers()
        headers["SessionTokenAuthorization"] = token
        user = self.http.get(AUTH_API + "User", headers=headers, timeout=TIMEOUT)
        if user.status_code != 200:
            log_response("User", user)
            raise StarzAuthError(_error_text(user))
        account = (user.json() or {}).get("userAccount") or {}
        if not account.get("userID"):
            raise StarzAuthError("Session is not signed in to an account")
        self._save(session_token=token,
                   username=self.username or normalise_username(
                       account.get("emailAddress", "")))

    # -- profiles --------------------------------------------------------------

    def profiles(self):
        resp = self.request("GET", AUTH_API + "User/profiles")
        return resp.json() or []

    def select_profile(self, profile):
        self.request("POST", AUTH_API + "User/profiles/%s/select" % profile["id"])
        self._save(profile_id=profile["id"], profile_name=profile.get("name", ""))

    # -- signed-in requests -------------------------------------------------

    def request(self, method, url, **kwargs):
        """A SessionTokenAuthorization call, re-issuing the session once on 401."""
        resp = self.http.request(method, url, headers=self.session_headers(),
                                 timeout=TIMEOUT, **kwargs)
        if resp.status_code == 401:
            kodiutils.log("401 from %s, re-issuing session" % url)
            self.refresh()
            resp = self.http.request(method, url, headers=self.session_headers(),
                                     timeout=TIMEOUT, **kwargs)
        if resp.status_code >= 400:
            raise StarzAuthError(_error_text(resp))
        return resp
