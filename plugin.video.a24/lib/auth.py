"""A24 account sign-in and session handling.

Two layers, both from captures (docs/recon.md rows A, A3, B, B2):

1. **A24 account session** (sign-in HAR, a24films.com):
   ``GET account/exists/?email=`` -> ``{"exists", ...}``;
   ``POST auth/passwordless/request/ {"email", "type": "otp_code"}`` -> A24
   emails a 4-digit code; ``POST auth/passwordless/validate/ {"email",
   "otp_code"}`` -> ``Set-Cookie: nexussessionid`` (HttpOnly,
   Domain=.a24films.com, 14 days). No CSRF header, no captcha.

2. **Screening Room app tokens** (app.a24films.com bundle, index-*.js): the
   app is an OAuth2 public client with PKCE against the same account service:
   ``GET {auth}/api/v1/auth/authorize/?response_type=code&client_id=..
   &code_challenge=..&code_challenge_method=S256&redirect_uri=..`` (signed in
   by the cookie) -> redirect to ``redirect_uri?code=..``; then
   ``POST {auth}/api/v1/auth/token/`` form ``grant_type=authorization_code,
   code, client_id, code_verifier, redirect_uri`` -> ``{access_token,
   refresh_token}``. Refresh: ``grant_type=refresh_token, refresh_token,
   client_id``. App API calls send ``Authorization: Bearer <access_token>``
   and ``PLATFORM: web``. The access token is an HS256 JWT with ``exp``.

Kodi does both without a browser: emailed code -> cookie -> authorize ->
token. No password is ever asked for or stored. Stored (hidden settings):
the cookie (``a24_session`` + ``a24_session_expires``), the app tokens
(``a24_token``, ``a24_refresh``, ``a24_expires``).
"""

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, parse_qs

import requests

from lib import kodiutils

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) "
              "Gecko/20100101 Firefox/158.0")
TIMEOUT = 20

AUTH_BASE = "https://account.a24films.com"
ACCOUNT_API = AUTH_BASE + "/api/v1/"
APP_ORIGIN = "https://app.a24films.com"
# Public OAuth client of the A24 web app (constant in its JS bundle).
CLIENT_ID = "nHodnPAsF9MSYmRkVlFH1EsD142RZ9Nk1xNg1f1e"
# authReturnUrl from the bundle. Real-box test 2026-11-20: the native
# build's return URL is the one registered for this client and issues a
# code; the web build's answers HTTP 400 "invalid_request: Mismatching
# redirect URI". The others stay as fallbacks in case A24 changes it.
REDIRECT_URIS = ("https://api.app.a24films.com/redirect/",
                 APP_ORIGIN + "/tabs/home/",
                 APP_ORIGIN + "/tabs/home")

SESSION_COOKIE = "nexussessionid"
COOKIE_DOMAIN = ".a24films.com"
SESSION_SECS = 14 * 24 * 3600      # cookie Max-Age in the capture
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


def _jwt_exp(token):
    """``exp`` of a JWT (unverified; only used to refresh before expiry)."""
    try:
        payload = token.split(".")[1]
        return int(json.loads(base64.urlsafe_b64decode(kodiutils.b64_padded(payload)))["exp"])
    except Exception:
        return 0


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


class A24Auth(object):

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "Accept": "application/json"})
        cookie = self._session_cookie
        if cookie:
            self.session.cookies.set(SESSION_COOKIE, cookie, domain=COOKIE_DOMAIN)
            self.session.cookies.set("nexus_session_exists", "1", domain=COOKIE_DOMAIN)

    # -- stored state --------------------------------------------------------

    @property
    def _session_cookie(self):
        expires = kodiutils.get_setting_int("a24_session_expires", 0)
        if expires and expires < time.time():
            return ""
        return kodiutils.get_setting("a24_session")

    @property
    def access_token(self):
        return kodiutils.get_setting("a24_token")

    @property
    def email(self):
        return kodiutils.get_setting("account_email")

    @property
    def signed_in(self):
        return bool(self.access_token or kodiutils.get_setting("a24_refresh")
                    or self._session_cookie)

    def clear(self):
        for key in ("a24_token", "a24_refresh", "a24_expires",
                    "a24_session", "a24_session_expires"):
            kodiutils.set_setting(key, "")
        self.session.cookies.clear()

    def _save_tokens(self, data):
        access = data.get("access_token") or ""
        kodiutils.set_setting("a24_token", access)
        if data.get("refresh_token"):
            kodiutils.set_setting("a24_refresh", data["refresh_token"])
        exp = _jwt_exp(access) or int(time.time() + int(data.get("expires_in") or 3600))
        kodiutils.set_setting("a24_expires", exp)

    # -- account session (emailed code) --------------------------------------

    def _account_headers(self):
        return {"Origin": WEB_ORIGIN, "Referer": WEB_ORIGIN + "/"}

    def request_code(self, email):
        """Ask A24 to email a sign-in code. Raises A24AuthError when the
        address has no A24 account."""
        resp = self.session.get(ACCOUNT_API + "account/exists/", params={"email": email},
                                headers=self._account_headers(), timeout=TIMEOUT)
        if resp.ok and not (resp.json() or {}).get("exists"):
            raise A24AuthError(kodiutils.localize(32024), resp.status_code)
        resp = self.session.post(ACCOUNT_API + "auth/passwordless/request/",
                                 json={"email": email, "type": "otp_code"},
                                 headers=self._account_headers(), timeout=TIMEOUT)
        if not resp.ok:
            raise A24AuthError("%s (HTTP %d)" % (kodiutils.localize(32025),
                                                 resp.status_code), resp.status_code)

    def validate_code(self, email, code):
        """Exchange the emailed code for the account session cookie, then
        for app tokens. Returns the account JSON."""
        resp = self.session.post(ACCOUNT_API + "auth/passwordless/validate/",
                                 json={"email": email, "otp_code": code.strip()},
                                 headers=self._account_headers(), timeout=TIMEOUT)
        cookie = resp.cookies.get(SESSION_COOKIE) or self.session.cookies.get(SESSION_COOKIE)
        if not resp.ok or not cookie:
            raise A24AuthError(kodiutils.localize(32026), resp.status_code)
        kodiutils.set_setting("a24_session", cookie)
        kodiutils.set_setting("a24_session_expires", int(time.time() + SESSION_SECS - 300))
        kodiutils.set_setting("account_email", email)
        self.authorize()
        try:
            return resp.json()
        except ValueError:
            return {}

    def account(self):
        """GET account/ with the cookie -> account JSON (row A3)."""
        resp = self.session.get(ACCOUNT_API + "account/", headers=self._account_headers(),
                                timeout=TIMEOUT)
        if resp.status_code in (401, 403):
            raise A24AuthError(kodiutils.localize(32031), resp.status_code)
        resp.raise_for_status()
        return resp.json()

    # -- app tokens (OAuth2 + PKCE) ------------------------------------------

    def authorize(self):
        """Cookie -> authorization code -> app tokens, as the web app's
        logIn() does, without a browser. Row B2."""
        if not self._session_cookie:
            raise A24AuthError(kodiutils.localize(32031), 401)
        status = None
        for redirect_uri in REDIRECT_URIS:
            verifier, challenge = _pkce_pair()
            code, status = self._authorize_code(challenge, redirect_uri)
            if code:
                kodiutils.log("authorize: code issued for redirect %s" % redirect_uri)
                self._token({"grant_type": "authorization_code", "code": code,
                             "client_id": CLIENT_ID, "code_verifier": verifier,
                             "redirect_uri": redirect_uri})
                return
        raise A24AuthError("%s (HTTP %s)" % (kodiutils.localize(32036), status), status)

    def _authorize_code(self, challenge, redirect_uri):
        """(code or None, last HTTP status) for one authorize attempt."""
        url = "%s/api/v1/auth/authorize/?%s" % (AUTH_BASE, urlencode({
            "response_type": "code", "client_id": CLIENT_ID,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "redirect_uri": redirect_uri, "back_to_site_title": "A24 App"}))
        resp = None
        for _ in range(6):
            resp = self.session.get(url, allow_redirects=False, timeout=TIMEOUT,
                                    headers={"Accept": "text/html,application/xhtml+xml,"
                                                       "application/xml;q=0.9,*/*;q=0.8",
                                             "Referer": APP_ORIGIN + "/"})
            location = resp.headers.get("Location", "")
            if not location:
                break
            query = parse_qs(urlsplit(location).query)
            if query.get("code"):
                return query["code"][0], resp.status_code
            if query.get("error"):
                break
            url = requests.compat.urljoin(url, location)
            if urlsplit(url).hostname not in ("account.a24films.com", "a24films.com"):
                break
        # The error text names the OAuth problem (invalid_request, mismatching
        # redirect URI...); it holds no secrets -- the verifier stays in Kodi.
        location = resp.headers.get("Location", "")
        loc = urlsplit(location)
        body = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", resp.text or "")
        body = re.sub(r"<[^>]+>", " ", body)
        kodiutils.log_error("authorize [%s]: no code (HTTP %s, %s, redirect %s%s?%s): %s" % (
            redirect_uri, resp.status_code, resp.headers.get("Content-Type"),
            loc.netloc, loc.path, ",".join("%s=%s" % (k, v[0][:80]) if k in ("error", "error_description") else k
                                           for k, v in parse_qs(loc.query).items()),
            " ".join(body.split())[:500]))
        return None, resp.status_code

    def _token(self, form):
        resp = self.session.post(ACCOUNT_API + "auth/token/", data=form, timeout=TIMEOUT,
                                 headers={"Content-Type":
                                          "application/x-www-form-urlencoded;charset=UTF-8"})
        if not resp.ok:
            kodiutils.log_error("auth/token %s: HTTP %d %s" % (
                form.get("grant_type"), resp.status_code, " ".join(resp.text.split())[:400]))
            raise A24AuthError("%s (HTTP %d)" % (kodiutils.localize(32036),
                                                 resp.status_code), resp.status_code)
        self._save_tokens(resp.json())

    def refresh(self):
        """New access token from the refresh token, else from the cookie.
        False when neither works (the user signs in again)."""
        refresh = kodiutils.get_setting("a24_refresh")
        if refresh:
            try:
                self._token({"grant_type": "refresh_token", "refresh_token": refresh,
                             "client_id": CLIENT_ID})
                return True
            except A24AuthError as exc:
                kodiutils.log("Token refresh failed: %s" % exc)
        if self._session_cookie:
            try:
                self.authorize()
                return True
            except A24AuthError as exc:
                kodiutils.log("Re-authorize failed: %s" % exc)
        return False

    def sign_out(self):
        # The bundle's sign-out is a browser redirect (auth/auto-logout/);
        # forgetting the cookie and tokens is enough to sign Kodi out.
        self.clear()

    # -- app API requests ----------------------------------------------------

    def app_headers(self):
        return {"Content-Type": "application/json", "PLATFORM": "web",
                "Origin": APP_ORIGIN, "Referer": APP_ORIGIN + "/",
                "Authorization": "Bearer %s" % self.access_token}

    def request(self, method, url, **kwargs):
        """An app API call with the Bearer token: refreshed ahead of expiry,
        and once more on a 401/403 -- never a loop."""
        kwargs.setdefault("timeout", TIMEOUT)
        if not self.access_token or kodiutils.get_setting_int("a24_expires", 0) < time.time() + 60:
            if not self.refresh():
                self.clear()
                raise A24AuthError(kodiutils.localize(32031), 401)
        extra = kwargs.pop("headers", None) or {}
        for attempt in (0, 1):
            headers = dict(self.app_headers(), **extra)
            resp = self.session.request(method, url, headers=headers, **kwargs)
            if resp.status_code in (401, 403) and attempt == 0 and self.refresh():
                continue
            break
        if resp.status_code == 401:
            self.clear()
            raise A24AuthError(kodiutils.localize(32031), 401)
        return resp
