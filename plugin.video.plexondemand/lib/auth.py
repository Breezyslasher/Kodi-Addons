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
PRODUCT = "Plex On Demand for Kodi"
VERSION = "1.0"
TIMEOUT = 20


def client_id():
    cid = kodiutils.get_setting("client_id")
    if not cid:
        cid = str(uuid.uuid4())
        kodiutils.set_setting("client_id", cid)
    return cid


def base_headers():
    return {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": client_id(),
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": VERSION,
        "X-Plex-Platform": "Kodi",
        "X-Plex-Device": "Kodi",
        "X-Plex-Device-Name": "Kodi",
        "X-Plex-Language": "en",
    }


class PlexAuth(object):

    def signed_in(self):
        return bool(kodiutils.get_setting("plex_token"))

    def token(self):
        """The user's token if signed in, else a (cached) anonymous one."""
        tok = kodiutils.get_setting("plex_token")
        if tok:
            return tok
        tok = kodiutils.get_setting("anon_token")
        if tok:
            return tok
        return self.new_anonymous_token()

    def new_anonymous_token(self):
        resp = requests.post(PLEX_TV + "/users/anonymous", headers=base_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        tok = resp.json().get("authToken") or ""
        kodiutils.set_setting("anon_token", tok)
        return tok

    def invalidate(self):
        """Drop a token the provider rejected (401) so the next call re-mints."""
        if self.signed_in():
            return False
        kodiutils.set_setting("anon_token", "")
        return True

    # ---- PIN link flow
    def create_pin(self):
        resp = requests.post(PLEX_TV + "/pins", headers=base_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def check_pin(self, pin_id):
        resp = requests.get("%s/pins/%s" % (PLEX_TV, pin_id), headers=base_headers(), timeout=TIMEOUT)
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
        pin = self.create_pin()
        expires = time.time() + int(pin.get("expiresIn") or 900)
        started = time.time()
        while time.time() < expires:
            pct = int(100 * (time.time() - started) / max(1, expires - started))
            if progress(pin["code"], pct) is False:
                return None
            tok = self.check_pin(pin["id"])
            if tok:
                kodiutils.set_setting("plex_token", tok)
                try:
                    user = self.account(tok)
                    kodiutils.set_setting("account_name", user.get("username") or user.get("title") or "")
                except Exception as exc:
                    kodiutils.log_error("account lookup failed: %s" % exc)
                return tok
            for _ in range(4):
                time.sleep(0.5)
                if progress(pin["code"], pct) is False:
                    return None
        return None

    def sign_out(self):
        for key in ("plex_token", "account_name", "anon_token", "client_id"):
            kodiutils.set_setting(key, "")
