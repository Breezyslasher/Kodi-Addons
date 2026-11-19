"""Plex account handling: anonymous token, optional PIN (plex.tv/link) sign-in.

Confirmed by capture (tools/probe.py, run from GitHub Actions):

  POST https://plex.tv/api/v2/users/anonymous   -> 201 {authToken, anonymous: true}
      An anonymous token is enough for the whole free catalogue: hubs,
      metadata, playQueues and the HLS manifests. No account needed.
  POST https://plex.tv/api/v2/pins              -> 201 {id, code (4 chars), authToken: null}
      (?strong=true gives a 25-char code for a QR/redirect flow instead.)
  GET  https://plex.tv/api/v2/pins/{id}         -> 200 {authToken: null | "<token>"}
      Same X-Plex-Client-Identifier as the create call. Codes live 15 min.

Signing in is optional: it only makes watch state / Continue Watching belong
to the user's real Plex account instead of a throwaway anonymous one.
"""

import time
import uuid

import requests

from . import kodiutils

PLEX_TV = "https://plex.tv/api/v2"
LINK_URL = "https://plex.tv/link"
# Plex only issues anonymous accounts to client products it knows: with a
# made-up X-Plex-Product, POST /users/anonymous answers 404 (seen in the smoke
# test). These are the Plex Web values every capture succeeded with.
PRODUCT = "Plex Web"
VERSION = "4.145.1"
TIMEOUT = 20


_CLIENT_ID = None
CLIENT_FILE = "client.json"


def client_id():
    """One stable X-Plex-Client-Identifier for this install.

    plex.tv only answers GET /pins/{id} for the client id that created the
    PIN (404 otherwise), and a Kodi setting written with setSetting is not
    always readable again within the same run, so a fresh uuid was minted per
    call and every poll 404'd. Keep it in memory and in a profile file (read
    back reliably), mirroring it into the setting only for display/debug.
    """
    global _CLIENT_ID
    if _CLIENT_ID:
        return _CLIENT_ID
    cid = (kodiutils.read_json(CLIENT_FILE, {}) or {}).get("id") or kodiutils.get_setting("client_id")
    if not cid:
        cid = str(uuid.uuid4())
    kodiutils.write_json(CLIENT_FILE, {"id": cid})
    kodiutils.set_setting("client_id", cid)
    _CLIENT_ID = cid
    return cid


def base_headers():
    return {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": client_id(),
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": VERSION,
        "X-Plex-Platform": "Chrome",
        "X-Plex-Platform-Version": "131.0",
        "X-Plex-Device": "Linux",
        "X-Plex-Device-Name": "Kodi",
        "X-Plex-Language": "en",
    }


ACCOUNT_FILE = "account.json"


def _account():
    """Tokens and account name live in the profile, not in settings.

    A plugin that calls setSetting while Kodi's Settings dialog is open (the
    Sign in / Sign out actions run from there) cannot read the value back,
    and the dialog writes its own stale copy over it when closed with OK,
    wiping the token. A profile file has neither problem.
    """
    data = kodiutils.read_json(ACCOUNT_FILE, None)
    if data is None:  # migrate from the settings an older build used
        data = {k: kodiutils.get_setting(k) for k in ("plex_token", "anon_token", "account_name")}
        kodiutils.write_json(ACCOUNT_FILE, data)
    return data


def _save_account(**changes):
    data = _account()
    data.update(changes)
    kodiutils.write_json(ACCOUNT_FILE, data)


def account_name():
    return _account().get("account_name") or ""


class PinExpired(Exception):
    pass


class PlexAuth(object):

    def signed_in(self):
        return bool(_account().get("plex_token"))

    def token(self):
        """The user's token if signed in, else a (cached) anonymous one."""
        acct = _account()
        tok = acct.get("plex_token")
        if tok:
            return tok
        tok = acct.get("anon_token")
        if tok:
            return tok
        return self.new_anonymous_token()

    def new_anonymous_token(self):
        resp = requests.post(PLEX_TV + "/users/anonymous", headers=base_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        tok = resp.json().get("authToken") or ""
        _save_account(anon_token=tok)
        return tok

    def invalidate(self):
        """Drop a token the provider rejected (401) so the next call re-mints."""
        if self.signed_in():
            return False
        _save_account(anon_token="")
        return True

    # ---- PIN link flow
    def create_pin(self, headers=None):
        resp = requests.post(PLEX_TV + "/pins", headers=headers or base_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def check_pin(self, pin_id, headers=None):
        """The PIN's authToken once linked, else None. Must be polled with the
        same X-Plex-Client-Identifier that created it."""
        resp = requests.get("%s/pins/%s" % (PLEX_TV, pin_id), headers=headers or base_headers(),
                            timeout=TIMEOUT)
        if resp.status_code == 404:
            raise PinExpired("PIN %s not found for this client (expired or other client id)" % pin_id)
        resp.raise_for_status()
        return resp.json().get("authToken")

    def account(self, token):
        h = base_headers()
        h["X-Plex-Token"] = token
        resp = requests.get(PLEX_TV + "/user", headers=h, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def link(self, progress):
        """Run the plex.tv/link flow. progress(code, percent) -> False to cancel."""
        headers = base_headers()
        pin = self.create_pin(headers)
        if pin.get("clientIdentifier") and pin["clientIdentifier"] != headers["X-Plex-Client-Identifier"]:
            kodiutils.log_error("PIN created for a different client id than sent")
        expires = time.time() + int(pin.get("expiresIn") or 900)
        started = time.time()
        while time.time() < expires:
            pct = int(100 * (time.time() - started) / max(1, expires - started))
            if progress(pin["code"], pct) is False:
                return None
            tok = self.check_pin(pin["id"], headers)
            if tok:
                _save_account(plex_token=tok)
                try:
                    user = self.account(tok)
                    _save_account(account_name=user.get("username") or user.get("title") or "")
                except Exception as exc:
                    kodiutils.log_error("account lookup failed: %s" % exc)
                return tok
            for _ in range(4):
                time.sleep(0.5)
                if progress(pin["code"], pct) is False:
                    return None
        return None

    def sign_out(self):
        global _CLIENT_ID
        kodiutils.delete_file(ACCOUNT_FILE)
        kodiutils.delete_file(CLIENT_FILE)
        for key in ("plex_token", "account_name", "anon_token", "client_id"):
            kodiutils.set_setting(key, "")
        _CLIENT_ID = None
