"""Apple ID web sign-in for Kodi, including two-factor authentication.

This replicates the browser sign-in flow used by the Apple TV web app
(idmsa.apple.com/appleauth/auth), which authenticates with SRP-6a and, when
the account has two-factor enabled, a trusted-device security code.

Apple does not document any of this. The OAuth widget key below identifies the
Apple TV web client to Apple's auth service; if Apple rotates it, sign-in will
start failing and it must be refreshed (it can be captured from the network
requests a browser makes at https://tv.apple.com when signing in). It is also
exposed as an advanced setting so it can be corrected without a code change.
"""

import base64
import json
import time

import requests

from . import kodiutils
from .srp_client import SRPClient

AUTH_BASE = "https://idmsa.apple.com/appleauth/auth"

# Identifies the Apple TV web client to Apple's auth service. Overridable via
# the advanced "oauth_widget_key" setting when Apple rotates it.
DEFAULT_WIDGET_KEY = "83545bf919730e51dbfba24e7e8a78d2"
REDIRECT_URI = "https://tv.apple.com"

SESSION_FILE = "session.json"

# Login outcomes returned by AppleAuth.login().
STATUS_OK = "ok"
STATUS_NEEDS_2FA = "2fa"
STATUS_ERROR = "error"


def _widget_key():
    return kodiutils.get_setting("oauth_widget_key") or DEFAULT_WIDGET_KEY


class AppleAuth(object):
    """Owns the Apple ID session: sign-in, 2FA, persistence and refresh."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": REDIRECT_URI,
            "Referer": REDIRECT_URI + "/",
        })
        # Populated during a 2FA challenge so the follow-up call can reuse it.
        self._scnt = None
        self._session_id = None
        self._pending_account = None
        self._load()

    # -- persistence -----------------------------------------------------

    def _load(self):
        data = kodiutils.read_json(SESSION_FILE, default={}) or {}
        self.tokens = data.get("tokens", {})
        cookies = data.get("cookies", {})
        for name, value in cookies.items():
            self.session.cookies.set(name, value)

    def save(self):
        cookies = {c.name: c.value for c in self.session.cookies}
        kodiutils.write_json(SESSION_FILE, {
            "tokens": self.tokens,
            "cookies": cookies,
            "saved_at": int(time.time()),
        })

    def clear(self):
        self.tokens = {}
        self.session.cookies.clear()
        kodiutils.delete_file(SESSION_FILE)

    def is_authenticated(self):
        return bool(self.tokens.get("x_apple_session")) or bool(self.session.cookies)

    # -- headers ---------------------------------------------------------

    def _auth_headers(self, extra=None):
        key = _widget_key()
        headers = {
            "X-Apple-Widget-Key": key,
            "X-Apple-OAuth-Client-Id": key,
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": REDIRECT_URI,
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": "auth-" + str(int(time.time())),
        }
        if self._session_id:
            headers["X-Apple-ID-Session-Id"] = self._session_id
        if self._scnt:
            headers["scnt"] = self._scnt
        if extra:
            headers.update(extra)
        return headers

    def _capture_challenge_headers(self, response):
        session_id = response.headers.get("X-Apple-ID-Session-Id")
        scnt = response.headers.get("scnt")
        if session_id:
            self._session_id = session_id
        if scnt:
            self._scnt = scnt

    # -- sign-in ---------------------------------------------------------

    def login(self, account_name, password):
        """Perform SRP sign-in. Returns one of the STATUS_* constants."""
        try:
            srp = SRPClient(account_name)
            init_body = {
                "a": base64.b64encode(srp.public_a_bytes()).decode("ascii"),
                "accountName": account_name,
                "protocols": ["s2k", "s2k_fo"],
            }
            init = self.session.post(
                AUTH_BASE + "/signin/init",
                data=json.dumps(init_body),
                headers=self._auth_headers(),
                timeout=30,
            )
            self._capture_challenge_headers(init)
            if init.status_code != 200:
                kodiutils.log_error("signin/init failed: %s %s" % (init.status_code, init.text[:300]))
                return STATUS_ERROR
            payload = init.json()

            salt = base64.b64decode(payload["salt"])
            b_value = base64.b64decode(payload["b"])
            iterations = int(payload["iteration"])
            protocol = payload.get("protocol", "s2k")
            challenge = payload["c"]

            m1 = srp.process_challenge(password, salt, iterations, protocol, b_value)

            # Body shape mirrors known-good Apple web clients: the client public
            # value A is only sent in /init (the server tracks it via the
            # session), and trustTokens must be present (empty is fine). Sending
            # an extra "a" here or omitting trustTokens is a known cause of 400.
            complete_body = {
                "accountName": account_name,
                "c": challenge,
                "m1": base64.b64encode(m1).decode("ascii"),
                "m2": base64.b64encode(srp.expected_server_proof()).decode("ascii"),
                "rememberMe": True,
                "trustTokens": [],
            }
            complete = self.session.post(
                AUTH_BASE + "/signin/complete?isRememberMeEnabled=true",
                data=json.dumps(complete_body),
                headers=self._auth_headers(),
                timeout=30,
            )
            self._capture_challenge_headers(complete)
            self._pending_account = account_name

            if complete.status_code in (200, 302):
                self._store_tokens(complete)
                self.save()
                return STATUS_OK
            if complete.status_code == 409:
                # Two-factor authentication required.
                self._request_2fa_code()
                return STATUS_NEEDS_2FA

            kodiutils.log_error("signin/complete failed: %s %s" % (complete.status_code, complete.text[:300]))
            return STATUS_ERROR
        except KeyError as exc:
            kodiutils.log_error("Unexpected sign-in response (missing %s)" % exc)
            return STATUS_ERROR
        except Exception as exc:
            kodiutils.log_error("Sign-in error: %s" % exc)
            return STATUS_ERROR

    # -- two-factor ------------------------------------------------------

    def _request_2fa_code(self):
        """Ask Apple to send a security code to the account's trusted devices."""
        try:
            self.session.get(AUTH_BASE, headers=self._auth_headers(), timeout=30)
        except Exception as exc:
            kodiutils.log_error("2FA device query failed: %s" % exc)

    def submit_2fa_code(self, code):
        """Submit the six-digit trusted-device code. Returns a STATUS_* value."""
        try:
            body = {"securityCode": {"code": str(code)}}
            resp = self.session.post(
                AUTH_BASE + "/verify/trusteddevice/securitycode",
                data=json.dumps(body),
                headers=self._auth_headers(),
                timeout=30,
            )
            self._capture_challenge_headers(resp)
            if resp.status_code not in (200, 204):
                kodiutils.log_error("2FA verify failed: %s %s" % (resp.status_code, resp.text[:300]))
                return STATUS_ERROR

            # Trust this session so Apple stops prompting for a while.
            try:
                trust = self.session.get(
                    AUTH_BASE + "/2sv/trust",
                    headers=self._auth_headers(),
                    timeout=30,
                )
                self._store_tokens(trust)
            except Exception:
                pass

            self.save()
            return STATUS_OK
        except Exception as exc:
            kodiutils.log_error("2FA submit error: %s" % exc)
            return STATUS_ERROR

    # -- tokens ----------------------------------------------------------

    def _store_tokens(self, response):
        session_id = response.headers.get("X-Apple-ID-Session-Id") or self._session_id
        if session_id:
            self.tokens["x_apple_session"] = session_id
        scnt = response.headers.get("scnt") or self._scnt
        if scnt:
            self.tokens["scnt"] = scnt
        token = response.headers.get("X-Apple-Session-Token")
        if token:
            self.tokens["session_token"] = token
