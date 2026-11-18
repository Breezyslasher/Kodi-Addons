"""Starz sign-in and token handling.

The generic parts are real: username normalisation, token storage in the
profile dir, expiry checks. The Starz-specific parts -- which endpoint, which
grant, which headers -- are deliberately unimplemented until they are read out
of a sign-in HAR (docs/recon.md rows A and B). Nothing here is guessed.
"""

import time

from lib import kodiutils

TOKEN_FILE = "token.json"

# Refresh this many seconds before the stated expiry, so a token never runs
# out halfway through a request or a long browse session.
EXPIRY_SKEW_SECS = 120


class NotReadyError(Exception):
    """A Starz-specific step that has not been built from a capture yet."""


def normalise_username(username):
    """Trim and lowercase an email login.

    TV on-screen keyboards readily add a capital first letter or a trailing
    space; an email address is case-insensitive in practice, so normalising
    costs nothing and avoids a confusing "wrong password". (The same fix was
    needed in the Apple TV addon.) Non-email usernames are only trimmed.
    """
    username = (username or "").strip()
    if "@" in username:
        username = username.lower()
    return username


class StarzAuth(object):

    def __init__(self):
        self._token = kodiutils.read_json(TOKEN_FILE, default=None) or {}

    # -- state -----------------------------------------------------------

    @property
    def signed_in(self):
        return bool(self._token.get("access_token") or
                    self._token.get("refresh_token"))

    @property
    def username(self):
        return self._token.get("username", "")

    def _expired(self):
        expires_at = self._token.get("expires_at")
        if not expires_at:
            # Unknown expiry: treat as valid and let a 401 trigger a refresh.
            return False
        return time.time() >= float(expires_at) - EXPIRY_SKEW_SECS

    def _store(self, access_token, refresh_token=None, expires_in=None,
               username=None, extra=None):
        token = {
            "access_token": access_token,
            "refresh_token": refresh_token or self._token.get("refresh_token"),
            "expires_at": (time.time() + float(expires_in)) if expires_in else None,
            "username": username or self._token.get("username", ""),
        }
        if extra:
            token["extra"] = extra
        self._token = token
        kodiutils.write_json(TOKEN_FILE, token)

    def clear(self):
        self._token = {}
        kodiutils.delete_file(TOKEN_FILE)

    # -- public ----------------------------------------------------------

    def access_token(self):
        """A usable access token, refreshing first if it has expired."""
        if not self.signed_in:
            return None
        if self._expired():
            self.refresh()
        return self._token.get("access_token")

    def sign_in(self, username, password):
        """Exchange credentials for tokens and persist them.

        TODO(recon A): from the sign-in HAR, record the token endpoint, the
        request body shape (form vs JSON, grant_type, client id), required
        headers, and the response fields. Then call self._store(...).
        Also check: standalone Starz account vs TV-provider/app-store SSO --
        the latter is a different flow entirely.
        """
        normalise_username(username)
        raise NotReadyError("Starz sign-in has not been built yet "
                            "(needs the sign-in HAR, recon row A).")

    def refresh(self):
        """Renew the access token from the stored refresh token.

        TODO(recon B): refresh endpoint/grant, lifetime, and whether the
        refresh token rotates.
        """
        raise NotReadyError("Token refresh has not been built yet "
                            "(recon row B).")
