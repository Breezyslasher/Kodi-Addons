"""A24 account sign-in and session handling.

From the sign-in HAR (docs/recon.md row A, a24films.com, 2026-11-19):

* A24 accounts live on ``account.a24films.com/api/v1/`` (Django, "nexus").
* ``GET account/exists/?email=`` -> ``{"exists", "has_usable_password"}``.
* ``POST auth/passwordless/request/ {"email", "type": "otp_code"}`` -> 200,
  empty body; A24 emails a 4-digit code.
* ``POST auth/passwordless/validate/ {"email", "otp_code"}`` -> the account
  JSON and ``Set-Cookie: nexussessionid`` (32 chars, HttpOnly,
  Domain=.a24films.com, Max-Age 14 days) plus ``nexus_session_exists=1``.
* Signed-in calls send that cookie; no CSRF header, no captcha, no bearer.
* ``GET account/`` -> ``{"current_plan", "is_email_confirmed",
  "user_profile": {"nexus_id", "email", "first_name", ...}}``.

So Kodi signs in with an emailed code and never sees a password. The
session cookie is the only thing stored (hidden ``a24_token``), with its
expiry. There is no refresh: when it lapses (14 days) the user signs in
again.
"""

import time

import requests

from lib import kodiutils

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) "
              "Gecko/20100101 Firefox/158.0")
TIMEOUT = 20

ACCOUNT_API = "https://account.a24films.com/api/v1/"
SESSION_COOKIE = "nexussessionid"
COOKIE_DOMAIN = ".a24films.com"
SESSION_SECS = 14 * 24 * 3600      # Max-Age in the capture
WEB_ORIGIN = "https://a24films.com"


class NotReadyError(Exception):
    """An A24-specific step that has not been built from a capture yet."""


class A24AuthError(Exception):
    """A24 refused a call; the message is what to tell the user."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


def not_ready(row, what):
    return NotReadyError("%s is not built yet: it waits on docs/recon.md row %s."
                         % (what, row))


class A24Auth(object):

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Origin": WEB_ORIGIN,
            "Referer": WEB_ORIGIN + "/",
        })
        if self.token:
            self.session.cookies.set(SESSION_COOKIE, self.token, domain=COOKIE_DOMAIN)
            self.session.cookies.set("nexus_session_exists", "1", domain=COOKIE_DOMAIN)

    # -- stored state --------------------------------------------------------

    @property
    def token(self):
        expires = kodiutils.get_setting_int("a24_expires", 0)
        if expires and expires < time.time():
            return ""
        return kodiutils.get_setting("a24_token")

    @property
    def email(self):
        return kodiutils.get_setting("account_email")

    @property
    def signed_in(self):
        return bool(self.token)

    def clear(self):
        for key in ("a24_token", "a24_expires"):
            kodiutils.set_setting(key, "")
        self.session.cookies.clear()

    # -- sign-in -------------------------------------------------------------

    def _post(self, path, payload):
        return self.session.post(ACCOUNT_API + path, json=payload, timeout=TIMEOUT)

    def request_code(self, email):
        """Ask A24 to email a sign-in code. Raises A24AuthError when the
        address has no A24 account."""
        resp = self.session.get(ACCOUNT_API + "account/exists/",
                                params={"email": email}, timeout=TIMEOUT)
        if resp.ok and not (resp.json() or {}).get("exists"):
            raise A24AuthError(kodiutils.localize(32024), resp.status_code)
        resp = self._post("auth/passwordless/request/",
                          {"email": email, "type": "otp_code"})
        if not resp.ok:
            raise A24AuthError("%s (HTTP %d)" % (kodiutils.localize(32025),
                                                 resp.status_code), resp.status_code)

    def validate_code(self, email, code):
        """Exchange the emailed code for the session cookie. Returns the
        account JSON."""
        resp = self._post("auth/passwordless/validate/",
                          {"email": email, "otp_code": code.strip()})
        token = resp.cookies.get(SESSION_COOKIE) or self.session.cookies.get(SESSION_COOKIE)
        if not resp.ok or not token:
            raise A24AuthError(kodiutils.localize(32026), resp.status_code)
        kodiutils.set_setting("a24_token", token)
        kodiutils.set_setting("a24_expires", int(time.time() + SESSION_SECS - 300))
        kodiutils.set_setting("account_email", email)
        try:
            return resp.json()
        except ValueError:
            return {}

    def sign_out(self):
        # The bundle names auth/logout/ but it is not captured yet (row B1);
        # forgetting the cookie locally is enough to sign Kodi out.
        self.clear()

    def account(self):
        """GET account/ -> the account JSON (row A3)."""
        resp = self.request("GET", ACCOUNT_API + "account/")
        resp.raise_for_status()
        return resp.json()

    # -- requests ------------------------------------------------------------

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code in (401, 403) and "account.a24films.com" in url:
            # Django answers 401/403 for a missing or lapsed session.
            self.clear()
            raise A24AuthError(kodiutils.localize(32031), resp.status_code)
        return resp
