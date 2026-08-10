"""Apple ID web sign-in for Kodi, including two-factor authentication.

This reproduces the exact browser sign-in flow used by the Apple TV web app
(tv.apple.com -> idmsa.apple.com/appleauth/auth), captured from a real session:

  1. GET  /authorize/signin      seeds scnt, X-Apple-Auth-Attributes, the
                                  X-Apple-HC hashcash challenge and a session id
  2. POST /signin/init           SRP-6a start (sends A, gets salt/B/challenge)
  3. POST /signin/complete       SRP proof + X-Apple-HC stamp; 409 => 2FA
  4. 2FA  /verify/trusteddevice  or /verify/phone (SMS) security code
  5. GET  /2sv/trust             trusts the session

The OAuth client id below is the real Apple TV web client. Apple documents none
of this and may change it; it is also exposed as the advanced "oauth_widget_key"
setting so it can be corrected without a code change.
"""

import base64
import json
import time
import uuid

import requests

from . import kodiutils
from . import hashcash
from .srp_client import SRPClient

AUTH_BASE = "https://idmsa.apple.com/appleauth/auth"

# Real Apple TV web OAuth client id (captured from tv.apple.com sign-in).
DEFAULT_CLIENT_ID = "06f8d74b71c73757a2f82158d5e948ae7bae11ec45fda9a58690f55e35945c51"
REDIRECT_URI = "https://tv.apple.com"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

SESSION_FILE = "session.json"

STATUS_OK = "ok"
STATUS_NEEDS_2FA = "2fa"
STATUS_ERROR = "error"


def _client_id():
    return kodiutils.get_setting("oauth_widget_key") or DEFAULT_CLIENT_ID


def _frame_id():
    return "auth-" + str(uuid.uuid4())


def _fd_client_info():
    # Fraud-detection blob. Apple's obfuscated JS fills "F" with a device
    # fingerprint; the SRP web flow is accepted with an empty F.
    return json.dumps({
        "U": USER_AGENT,
        "L": "en_US",
        "Z": "GMT+00:00",
        "V": "1.1",
        "F": "",
    })


class AppleAuth(object):
    """Owns the Apple ID session: sign-in, 2FA, persistence."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://idmsa.apple.com",
            "Referer": "https://idmsa.apple.com/",
            "X-Requested-With": "XMLHttpRequest",
        })
        # Dynamic per-flow state captured from responses.
        self._scnt = None
        self._session_id = None
        self._auth_attributes = None
        self._hc_bits = None
        self._hc_challenge = None
        self._frame = None
        self._load()

    # -- persistence -----------------------------------------------------

    def _load(self):
        data = kodiutils.read_json(SESSION_FILE, default={}) or {}
        self.tokens = data.get("tokens", {})
        cookies = data.get("cookies") or []
        if isinstance(cookies, dict):  # legacy name->value form (domain lost)
            cookies = [{"name": n, "value": v} for n, v in cookies.items()]
        for c in cookies:
            if isinstance(c, dict) and c.get("name"):
                self.session.cookies.set(
                    c["name"], c.get("value"),
                    domain=c.get("domain", ""), path=c.get("path", "/"))

    def save(self):
        # Preserve domain/path so cookies like myacinfo (domain apple.com) are
        # still sent to auth.tv.apple.com in a later plugin invocation.
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                   for c in self.session.cookies]
        kodiutils.write_json(SESSION_FILE, {
            "tokens": self.tokens,
            "cookies": cookies,
            "saved_at": int(time.time()),
        })

    def clear(self):
        # Keep the anonymous bootstrap (utsk + developer token): it is the app's
        # own browsing session, not account data, and the catalogue needs it.
        # Wiping it made every screen after a sign-out rebuild it from a fresh
        # tv.apple.com scrape -- so a single failed scrape took the whole
        # catalogue down, while a signed-in session coasted on the cache. Only
        # the account tokens and cookies are cleared; playback still needs a
        # sign-in because the media-user-token is gone.
        boot = self.tokens.get("boot")
        self.tokens = {"boot": boot} if boot else {}
        self.session.cookies.clear()
        self.save()

    def is_authenticated(self):
        # A pasted media-user-token (advanced/debug setting) counts as signed in
        # so the pipeline can be tested without the full Apple ID login.
        if kodiutils.get_setting("media_user_token"):
            return True
        return bool(self.tokens.get("authenticated"))

    # -- header helpers --------------------------------------------------

    def _oauth_headers(self, extra=None):
        cid = _client_id()
        frame = self._frame or _frame_id()
        headers = {
            "X-Apple-Widget-Key": cid,
            "X-Apple-OAuth-Client-Id": cid,
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": REDIRECT_URI,
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-State": frame,
            "X-Apple-Frame-Id": frame,
            "X-Apple-Auth-Context": "tv",
            "X-Apple-Domain-Id": "2",
            "X-Apple-Locale": "en_US",
            "X-Apple-I-FD-Client-Info": _fd_client_info(),
        }
        if self._scnt:
            headers["scnt"] = self._scnt
        if self._session_id:
            headers["X-Apple-ID-Session-Id"] = self._session_id
        if self._auth_attributes:
            headers["X-Apple-Auth-Attributes"] = self._auth_attributes
        if extra:
            headers.update(extra)
        return headers

    def _capture(self, response):
        h = response.headers
        if h.get("scnt"):
            self._scnt = h["scnt"]
        if h.get("X-Apple-ID-Session-Id"):
            self._session_id = h["X-Apple-ID-Session-Id"]
        if h.get("X-Apple-Auth-Attributes"):
            self._auth_attributes = h["X-Apple-Auth-Attributes"]
        if h.get("X-Apple-HC-Bits"):
            self._hc_bits = h["X-Apple-HC-Bits"]
        if h.get("X-Apple-HC-Challenge"):
            self._hc_challenge = h["X-Apple-HC-Challenge"]

    # -- sign-in ---------------------------------------------------------

    def login(self, account_name, password):
        """Full SRP sign-in. Returns a STATUS_* constant."""
        try:
            # Start clean: stale idmsa cookies/session state from a previous
            # attempt can make Apple reject the new SRP flow.
            self.session.cookies.clear()
            self._scnt = None
            self._session_id = None
            self._auth_attributes = None
            self._frame = _frame_id()
            self._bootstrap()

            srp = SRPClient(account_name)
            init = self.session.post(
                AUTH_BASE + "/signin/init",
                data=json.dumps({
                    "a": base64.b64encode(srp.public_a_bytes()).decode("ascii"),
                    "accountName": account_name,
                    "protocols": ["s2k", "s2k_fo"],
                }),
                headers=self._oauth_headers(),
                timeout=30,
            )
            self._capture(init)
            if init.status_code != 200:
                kodiutils.log_error("signin/init %s: %s" % (init.status_code, init.text[:300]))
                return STATUS_ERROR
            payload = init.json()

            m1 = srp.process_challenge(
                password,
                base64.b64decode(payload["salt"]),
                int(payload["iteration"]),
                payload.get("protocol", "s2k"),
                base64.b64decode(payload["b"]),
            )

            complete_headers = self._oauth_headers()
            if self._hc_bits and self._hc_challenge:
                complete_headers["X-Apple-HC"] = hashcash.make_stamp(
                    self._hc_bits, self._hc_challenge)

            complete = self.session.post(
                AUTH_BASE + "/signin/complete?isRememberMeEnabled=false",
                data=json.dumps({
                    "accountName": account_name,
                    "rememberMe": False,
                    "m1": base64.b64encode(m1).decode("ascii"),
                    "c": payload["c"],
                    "m2": base64.b64encode(srp.expected_server_proof()).decode("ascii"),
                }),
                headers=complete_headers,
                timeout=30,
            )
            self._capture(complete)

            if complete.status_code in (200, 302):
                self._finish()
                return STATUS_OK
            if complete.status_code == 409:
                return self._begin_2fa()

            kodiutils.log_error("signin/complete %s: %s" % (complete.status_code, complete.text[:300]))
            return STATUS_ERROR
        except KeyError as exc:
            kodiutils.log_error("Unexpected sign-in response (missing %s)" % exc)
            return STATUS_ERROR
        except Exception as exc:
            kodiutils.log_error("Sign-in error: %s" % exc)
            return STATUS_ERROR

    def _bootstrap(self):
        """GET /authorize/signin to seed scnt, auth-attributes and hashcash."""
        cid = _client_id()
        params = {
            "frame_id": self._frame,
            "language": "en_us",
            "skVersion": "7",
            "iframeId": self._frame,
            "client_id": cid,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "response_mode": "web_message",
            "state": self._frame,
            "authVersion": "latest",
        }
        resp = self.session.get(
            AUTH_BASE + "/authorize/signin",
            params=params,
            headers={"Accept": "text/html,application/xhtml+xml", "Referer": REDIRECT_URI + "/"},
            timeout=30,
        )
        self._capture(resp)

    # -- two-factor ------------------------------------------------------

    def _begin_2fa(self):
        """Inspect the 2FA state so submit_2fa_code() knows device vs SMS."""
        try:
            resp = self.session.get(AUTH_BASE, headers=self._oauth_headers(), timeout=30)
            self._capture(resp)
            info = resp.json() if resp.content else {}
        except Exception as exc:
            kodiutils.log_error("2FA state query failed: %s" % exc)
            info = {}

        device_count = info.get("trustedDeviceCount", 0)
        phones = info.get("trustedPhoneNumbers") or []
        if not device_count and phones:
            # SMS-only account: ask Apple to text a code to the first number.
            self.tokens["_2fa_phone_id"] = phones[0].get("id", 1)
            try:
                self.session.put(
                    AUTH_BASE + "/verify/phone",
                    data=json.dumps({
                        "phoneNumber": {"id": self.tokens["_2fa_phone_id"]},
                        "mode": "sms",
                    }),
                    headers=self._oauth_headers(),
                    timeout=30,
                )
            except Exception as exc:
                kodiutils.log_error("SMS code request failed: %s" % exc)
        else:
            self.tokens.pop("_2fa_phone_id", None)
        return STATUS_NEEDS_2FA

    def submit_2fa_code(self, code):
        """Submit the six-digit code (trusted-device or SMS). Returns STATUS_*."""
        try:
            phone_id = self.tokens.get("_2fa_phone_id")
            if phone_id:
                url = AUTH_BASE + "/verify/phone/securitycode"
                body = {
                    "phoneNumber": {"id": phone_id},
                    "securityCode": {"code": str(code)},
                    "mode": "sms",
                }
            else:
                url = AUTH_BASE + "/verify/trusteddevice/securitycode"
                body = {"securityCode": {"code": str(code)}}

            resp = self.session.post(
                url, data=json.dumps(body), headers=self._oauth_headers(), timeout=30)
            self._capture(resp)
            if resp.status_code not in (200, 204):
                kodiutils.log_error("2FA verify %s: %s" % (resp.status_code, resp.text[:300]))
                return STATUS_ERROR

            # Trust the session so Apple stops prompting for a while.
            try:
                trust = self.session.get(
                    AUTH_BASE + "/2sv/trust", headers=self._oauth_headers(), timeout=30)
                self._capture(trust)
            except Exception:
                pass

            self.tokens.pop("_2fa_phone_id", None)
            self._finish()
            return STATUS_OK
        except Exception as exc:
            kodiutils.log_error("2FA submit error: %s" % exc)
            return STATUS_ERROR

    def _finish(self):
        """Mark the idmsa session authenticated and persist it."""
        self.tokens["authenticated"] = True
        if self._session_id:
            self.tokens["session_id"] = self._session_id
        self.save()

    def authorize_media(self, developer_token):
        """Exchange the signed-in Apple ID session for a media-user-token.

        After Apple ID sign-in the session holds the myacinfo cookie; posting to
        auth.tv.apple.com/auth/v1/web with the web developer token makes Apple
        set the media-user-token cookie, which is the credential playback needs.
        Returns the token, or None.
        """
        try:
            self.session.post(
                "https://auth.tv.apple.com/auth/v1/web",
                data=json.dumps({"webAuthorizationFlowContext": "tv"}),
                headers={
                    "Authorization": "Bearer " + developer_token,
                    "Content-Type": "application/json",
                    "Origin": REDIRECT_URI,
                    "Referer": REDIRECT_URI + "/",
                },
                timeout=30,
            )
            # Read by iteration (cookies.get can raise if the name exists for
            # more than one domain).
            mut = None
            for cookie in self.session.cookies:
                if cookie.name == "media-user-token" and cookie.value:
                    mut = cookie.value
                    break
            if mut:
                self.tokens["media_user_token"] = mut
                self.save()
                return mut
            kodiutils.log_error("Store login did not return a media-user-token")
        except Exception as exc:
            kodiutils.log_error("Media authorization failed: %s" % exc)
        return None
