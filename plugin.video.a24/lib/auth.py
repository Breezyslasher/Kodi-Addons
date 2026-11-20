"""A24 Screening Room / AAA24 sign-in and session handling.

Nothing here is confirmed yet. The backend (docs/recon.md row 0) decides the
sign-in shape -- a direct credential POST returning a token, a vendor OAuth
exchange (Vimeo OTT / VHX), or a device-code link -- so every step that would
talk to A24 raises NotReadyError until its recon row is filled from a capture.

What is settled regardless of backend:

* The token lives in the hidden ``a24_token`` setting (plus ``a24_refresh``
  and ``a24_expires`` if the flow has them). The password is never stored.
* ``request`` sends the token on every call and turns a 401 into one refresh
  attempt, then A24AuthError -- never a retry loop.
"""

import time

import requests

from lib import kodiutils

# The browser the captures will come from; replace with the capture's UA.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) "
              "Gecko/20100101 Firefox/158.0")
TIMEOUT = 20

# docs/recon.md row A. None until a capture proves the URL.
SIGN_IN_URL = None      # VERIFY: credential POST / token exchange
REFRESH_URL = None      # VERIFY: refresh-token exchange, if any
SIGN_OUT_URL = None     # VERIFY: server-side session revoke, if any
ACCOUNT_URL = None      # VERIFY: account / membership status


class NotReadyError(Exception):
    """An A24-specific step that has not been built from a capture yet."""


class A24AuthError(Exception):
    """A24 refused a call; the message is what A24 said."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


def not_ready(row, what):
    return NotReadyError("%s is not built yet: it waits on docs/recon.md row %s."
                         % (what, row))


class A24Auth(object):

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "Accept": "application/json"})

    # -- stored state --------------------------------------------------------

    @property
    def token(self):
        return kodiutils.get_setting("a24_token")

    @property
    def email(self):
        return kodiutils.get_setting("account_email")

    @property
    def signed_in(self):
        return bool(self.token)

    def _save(self, token, refresh="", expires_in=0, email=None):
        kodiutils.set_setting("a24_token", token)
        kodiutils.set_setting("a24_refresh", refresh or "")
        kodiutils.set_setting("a24_expires",
                              int(time.time() + expires_in) if expires_in else 0)
        if email is not None:
            kodiutils.set_setting("account_email", email)

    def clear(self):
        for key in ("a24_token", "a24_refresh", "a24_expires"):
            kodiutils.set_setting(key, "")

    # -- flows ---------------------------------------------------------------

    def sign_in(self, email, password):
        """Exchange the user's own credentials for a token (row A)."""
        # Built from the capture: POST SIGN_IN_URL with the captured body shape,
        # then self._save(token, refresh, expires_in, email).
        raise not_ready("A", "Sign-in")

    def refresh(self):
        """Renew the token (row B). False when there is nothing to renew with."""
        if not REFRESH_URL or not kodiutils.get_setting("a24_refresh"):
            return False
        raise not_ready("B", "Token refresh")

    def sign_out(self):
        """Revoke server-side when the backend has a call for it, then forget
        the token locally either way."""
        if SIGN_OUT_URL and self.signed_in:
            try:
                self.request("POST", SIGN_OUT_URL)
            except Exception as exc:
                kodiutils.log("Sign-out call failed (ignored): %s" % exc)
        self.clear()

    def account_status(self):
        """{"email", "membership", "country"} for the settings action (row A3)."""
        raise not_ready("A3", "Account status")

    # -- requests ------------------------------------------------------------

    def auth_headers(self):
        # VERIFY row B: Bearer header or cookie. Bearer is the common case.
        return {"Authorization": "Bearer %s" % self.token} if self.token else {}

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        kwargs.setdefault("timeout", TIMEOUT)
        for attempt in (0, 1):
            resp = self.session.request(method, url,
                                        headers=dict(self.auth_headers(), **headers),
                                        **kwargs)
            if resp.status_code == 401 and attempt == 0 and self.refresh():
                continue
            break
        if resp.status_code == 401:
            self.clear()
            raise A24AuthError(kodiutils.localize(32031), 401)
        return resp
