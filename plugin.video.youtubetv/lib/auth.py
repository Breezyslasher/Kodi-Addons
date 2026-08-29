"""Google session auth for the YouTube TV private API.

There is no OAuth path here. YouTube TV grants no device-code scopes, and
Google stopped accepting scripted password login years ago, so the only way in
is the cookie jar of a browser that is already signed in. The user exports it
once; we sign every request with the SAPISIDHASH scheme the web player uses.

Two import routes, because typing a 3 KB cookie header on a remote is cruel:

* a Netscape ``cookies.txt`` path (what every browser extension exports), or
* the raw ``Cookie:`` header pasted from devtools.

Either way we keep only the names that matter and store them in the addon
profile, never in the settings file -- settings.xml is world-readable inside
the userdata directory and gets copied around in backups and bug reports.
"""

import hashlib
import time

from . import kodiutils

COOKIE_FILE = "cookies.json"
# Written by sign_out() so an explicit sign-out sticks even when the build
# carries a preloaded session -- see _baked().
SIGNED_OUT = {"signed_out": True}
ORIGIN = "https://tv.youtube.com"

# The jar the API actually needs. SAPISID and SID do the authenticating; the
# 1P/3P variants are hashed alongside them; LOGIN_INFO and the VISITOR_* pair
# keep YouTube from treating the session as a brand new anonymous client.
# Everything else in a browser jar is analytics and consent state.
WANTED = (
    "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "SID", "__Secure-1PSID", "__Secure-3PSID",
    "HSID", "SSID", "APISID",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "LOGIN_INFO", "VISITOR_INFO1_LIVE", "VISITOR_PRIVACY_METADATA",
    "PREF", "YSC", "__Secure-YNID", "__Secure-ROLLOUT_TOKEN",
)

REQUIRED = ("SAPISID", "SID")


class AuthError(Exception):
    """Sign-in is missing or no longer accepted."""


def _filter(cookies):
    return {name: value for name, value in cookies.items() if name in WANTED}


def parse_cookie_header(header):
    """Split a raw ``Cookie:`` header into a dict."""
    cookies = {}
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name] = value
    return _filter(cookies)


def parse_cookies_txt(path):
    """Read a Netscape cookies.txt, keeping only Google/YouTube domains.

    Hand-rolled rather than http.cookiejar because the exports in the wild are
    not always well formed -- extensions emit a ``#HttpOnly_`` prefix that
    MozillaCookieJar rejects outright, and a strict parser fails the whole file
    over one bad line.

    Google keeps a copy of the session under both .google.com and .youtube.com.
    The values normally agree, but they can diverge -- on a browser signed in
    to several accounts, or after one domain has been re-authed and the other
    has not. The API we call lives on youtube.com, so where both carry the same
    cookie the youtube.com copy wins.
    """
    google, youtube = {}, {}
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
                continue
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            fields = line.split("\t")
            if len(fields) < 7:
                continue
            domain, name, value = fields[0], fields[5], fields[6]
            if "youtube.com" in domain:
                youtube[name] = value
            elif "google.com" in domain:
                google[name] = value
    google.update(youtube)
    return _filter(google)


def save(cookies):
    missing = [name for name in REQUIRED if name not in cookies]
    if missing:
        raise AuthError("the cookie jar is missing %s -- export it from a "
                        "browser signed in to tv.youtube.com, with all "
                        "domains included" % " and ".join(missing))
    kodiutils.write_json(COOKIE_FILE, cookies)


def _baked():
    """A session compiled into the build, if there is one.

    Personal builds can ship ``lib/baked_cookies.py`` holding a ``COOKIES``
    dict, so the addon works on first run with no import step -- useful when
    Kodi is on a TV box with no browser and no keyboard. The module is absent
    from the repository and gitignored, because it holds live credentials; the
    published addon simply has no baked session and asks the user to sign in.

    A jar imported through the UI always wins, and an explicit sign-out
    suppresses this too.
    """
    try:
        from . import baked_cookies
    except ImportError:
        return None
    cookies = getattr(baked_cookies, "COOKIES", None)
    if not isinstance(cookies, dict):
        return None
    return _filter(cookies) or None


def load():
    stored = kodiutils.read_json(COOKIE_FILE, default=None)
    if isinstance(stored, dict) and stored.get("signed_out"):
        raise AuthError("signed out")
    if stored and all(name in stored for name in REQUIRED):
        return stored

    baked = _baked()
    if baked and all(name in baked for name in REQUIRED):
        return baked

    raise AuthError("not signed in")


def signed_in():
    try:
        load()
        return True
    except AuthError:
        return False


def sign_out():
    """Forget the session.

    Deleting the stored jar is not enough on a build with a baked session --
    load() would fall straight back to it -- so record the sign-out instead.
    """
    kodiutils.write_json(COOKIE_FILE, SIGNED_OUT)


def _hash(value, origin, now):
    digest = hashlib.sha1(("%d %s %s" % (now, value, origin)).encode()).hexdigest()
    return "%d_%s" % (now, digest)


def authorization(cookies, origin=ORIGIN):
    """The ``Authorization`` header the web player sends.

    SHA1 over "<unix seconds> <SAPISID> <origin>", repeated for the first- and
    third-party variants. Where a variant cookie is absent Google accepts the
    plain SAPISID value hashed in its place, which is what the web player does
    on accounts that predate the split.
    """
    now = int(time.time())
    sapisid = cookies["SAPISID"]
    return "SAPISIDHASH %s SAPISID1PHASH %s SAPISID3PHASH %s" % (
        _hash(sapisid, origin, now),
        _hash(cookies.get("__Secure-1PAPISID", sapisid), origin, now),
        _hash(cookies.get("__Secure-3PAPISID", sapisid), origin, now),
    )


def cookie_header(cookies):
    return "; ".join("%s=%s" % item for item in cookies.items())
