"""Sign-in and the session headers every other call carries.

Friendly TV's backend is Revlet (revlet.net), a white-label OTT platform, and
its session model is two steps:

1. ``GET /service/api/v1/get/token`` mints an anonymous session id for a
   device, identified by a ``box_id`` the client invents once and keeps.
2. ``POST /service/api/auth/v2/signin`` exchanges an email and password for a
   ``generatedId``, which *replaces* the anonymous session id on every
   subsequent call.

Both steps were read from a capture of the web player at watch.frndlytv.com;
see docs/frndlytv-protocol.md. Every authenticated request then carries three
headers -- ``box-id``, ``session-id`` and ``tenant-code`` -- and no bearer
token or cookie of any kind.
"""

import uuid

import requests

from . import kodiutils

API_BASE = "https://frndlytv-api.revlet.net"
TENANT_CODE = "frndlytv"
PRODUCT = "frndlytv"

# The web player identifies itself as device 61 and reports the browser it is
# running in. Both values are copied from the capture rather than invented:
# the backend keys stream limits and the "Mobile Browser stream has been
# closed" wording off this device class, and an unrecognised one has not been
# observed being accepted.
DEVICE_ID = "61"
DEVICE_SUB_TYPE = "Firefox,5,UNIX"
DISPLAY_LANG = "ENG"

# Sent as a plain browser. The API does not gate on this, but the CDN in
# front of it is likelier to answer a request that looks like the client the
# service actually ships.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 "
              "Firefox/154.0")
ORIGIN = "https://watch.frndlytv.com"

SESSION_FILE = "session.json"
TIMEOUT = 30


class AuthError(Exception):
    """Sign-in did not happen, and playback cannot proceed without it."""


def _box_id():
    """This installation's device id, invented once and kept.

    Friendly TV counts concurrent streams per device. A fresh uuid on every
    call would look like an unbounded fleet of devices to the backend and
    would orphan the stream sessions the previous one opened, so this is
    written to the profile the first time it is needed and read thereafter.
    """
    stored = kodiutils.read_json(SESSION_FILE, default={}) or {}
    box_id = stored.get("box_id")
    if not box_id:
        box_id = str(uuid.uuid4())
        stored["box_id"] = box_id
        kodiutils.write_json(SESSION_FILE, stored)
        kodiutils.log("minted a device id for this installation")
    return box_id


def _timezone():
    """An IANA zone name for the token request.

    The backend uses it to place the guide's day boundaries. Kodi does not
    expose the zone, so this reads the platform's, and falls back to the
    zone the capture used rather than sending nothing -- an empty value has
    not been observed being accepted.
    """
    try:
        import time as _time
        import datetime
        local = datetime.datetime.now().astimezone().tzinfo
        name = getattr(local, "key", None) or str(local)
        # A tzinfo that stringifies to an offset ("UTC-04:00") is not a zone
        # name; the abbreviation from time.tzname is no better. Neither is
        # something the API takes, so fall through to the known-good value.
        if name and "/" in name:
            return name
        del _time
    except Exception:
        pass
    return "America/New_York"


class Session(object):
    """The stored session, and the credentials that can rebuild it."""

    def __init__(self):
        self._stored = kodiutils.read_json(SESSION_FILE, default={}) or {}
        self.box_id = _box_id()
        self.session_id = self._stored.get("session_id") or ""
        self.user_id = self._stored.get("user_id") or ""
        self.email = self._stored.get("email") or ""

    # -- persistence -------------------------------------------------------

    def _save(self):
        self._stored.update({
            "box_id": self.box_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "email": self.email,
        })
        kodiutils.write_json(SESSION_FILE, self._stored)

    def clear(self):
        """Forget the signed-in session, keeping the device id.

        The device id deliberately survives a sign-out: it identifies this
        Kodi box to the stream counter, and throwing it away on every sign-out
        would leave the account looking like a new device each time.
        """
        self.session_id = ""
        self.user_id = ""
        self.email = ""
        self._stored = {"box_id": self.box_id}
        kodiutils.write_json(SESSION_FILE, self._stored)

    @property
    def signed_in(self):
        return bool(self.session_id and self.user_id)

    # -- headers -----------------------------------------------------------

    def headers(self):
        head = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "box-id": self.box_id,
            "tenant-code": TENANT_CODE,
        }
        if self.session_id:
            head["session-id"] = self.session_id
        return head

    # -- the two steps -----------------------------------------------------

    def anonymous_token(self):
        """Step one: an anonymous session id for this device."""
        params = {
            "tenant_code": TENANT_CODE,
            "box_id": self.box_id,
            "product": PRODUCT,
            "device_id": DEVICE_ID,
            "display_lang_code": DISPLAY_LANG,
            "device_sub_type": DEVICE_SUB_TYPE,
            "timezone": _timezone(),
        }
        try:
            reply = requests.get(API_BASE + "/service/api/v1/get/token",
                                 params=params,
                                 headers={"User-Agent": USER_AGENT,
                                          "Origin": ORIGIN},
                                 timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise AuthError("Could not reach Friendly TV: %s" % exc)
        body = _json(reply)
        session_id = ((body.get("response") or {}).get("sessionId") or "")
        if not session_id:
            raise AuthError("Friendly TV issued no session id: %s"
                            % _message(body, reply))
        self.session_id = session_id
        return session_id

    def sign_in(self, email, password):
        """Step two: swap the credentials for the signed-in session id."""
        if not self.session_id:
            self.anonymous_token()
        payload = {
            "login_id": email,
            "login_key": password,
            "manufacturer": "123",
            "login_mode": 1,
        }
        head = self.headers()
        head["Content-Type"] = "application/json"
        try:
            reply = requests.post(API_BASE + "/service/api/auth/v2/signin",
                                  json=payload, headers=head, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise AuthError("Could not reach Friendly TV: %s" % exc)
        body = _json(reply)
        response = body.get("response") or {}
        generated = response.get("generatedId")
        if not body.get("status") or not generated:
            raise AuthError(_message(body, reply) or
                            "Friendly TV refused the sign-in.")
        self.session_id = generated
        self.user_id = str(response.get("userId") or "")
        self.email = response.get("email") or email
        self._save()
        packages = ", ".join(str(p.get("id")) for p in
                             (response.get("packages") or [])) or "none"
        kodiutils.log("signed in; user %s, package(s) %s"
                      % (self.user_id or "?", packages))
        return response

    def refresh(self):
        """Rebuild the session from the stored credentials.

        Called when a request comes back unauthenticated. Raises rather than
        prompting: this runs on the service thread and during unattended
        guide refreshes, where a dialog would appear out of nowhere.
        """
        email = kodiutils.get_setting("username")
        password = kodiutils.get_setting("password")
        if not email or not password:
            raise AuthError("Not signed in. Add your Friendly TV email and "
                            "password in the addon's settings.")
        self.session_id = ""
        return self.sign_in(email, password)


def _json(reply):
    try:
        return reply.json() or {}
    except ValueError:
        return {}


def _message(body, reply):
    """The service's own words for a failure, or the HTTP status.

    Revlet reports a refusal with ``status`` false and HTTP 200, so the status
    line alone is usually not the reason. It puts the reason in one of two
    places depending on which surface answered: ``response.message`` for the
    main API, and an ``error`` object for the search API -- a search with no
    matches comes back as ``{"error": {"code": 404, "message": "We didn't find
    any matches..."}, "status": false}``.
    """
    response = body.get("response")
    if isinstance(response, dict):
        said = response.get("message") or response.get("displayMessage")
        if said:
            return said
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return error["message"]
    if body.get("message"):
        return body["message"]
    return "HTTP %s" % reply.status_code


def _code(body):
    """The service's own error code, or 0 when it gave none."""
    for holder in (body.get("error"), body.get("response")):
        if isinstance(holder, dict):
            try:
                return int(holder.get("code") or 0)
            except (TypeError, ValueError):
                pass
    return 0
