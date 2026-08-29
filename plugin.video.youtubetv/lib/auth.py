"""Google session auth for the YouTube TV private API.

Sign-in is a cookie jar exported from a browser that is already signed in;
every request is signed with the SAPISIDHASH scheme the web player uses.

An earlier version of this note claimed flatly that no OAuth path exists. That
was an assertion, not a finding, and it was wrong: the OAuth device-code token
plugin.video.youtube uses is accepted here too, as TVHTML5_UNPLUGGED, and
returned a full lineup. See lib/oauth.py. The web player itself authenticates
with SAPISIDHASH in every capture and never with a bearer token, which says
what the web player does and not what the surface allows.

So there are two ways in, and a cookie jar is still the one that needs no
Google API project of your own.

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
WANTED = (
    "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "SID", "__Secure-1PSID", "__Secure-3PSID",
    "HSID", "SSID", "APISID",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "LOGIN_INFO", "VISITOR_INFO1_LIVE", "VISITOR_PRIVACY_METADATA",
    "PREF", "YSC", "__Secure-YNID", "__Secure-ROLLOUT_TOKEN",
)

# A note for the next person tempted to prune this list. SIDCC and the SIDTS
# pair rotate every few minutes, and dropping them looked like an obvious way
# to stop an imported jar going stale. It is not: a jar captured from a request
# that provably authenticated -- a 200 on browse or player -- contains them and
# works, and the 401s that prompted the pruning came from somewhere else
# entirely. Carry what the working request carried.

REQUIRED = ("SAPISID", "SID")


class AuthError(Exception):
    """Sign-in is missing or no longer accepted."""


def _filter(cookies):
    """Keep the jar as the browser sent it.

    There used to be an allow-list here, and every round of trimming it cost a
    day: first the session-integrity cookies were pruned on a hunch, then a
    capture turned out to carry names the list had never heard of --
    __Secure-BUCKET, YTV_CLC, NID, ST-* -- which were silently dropped from a
    jar that had provably authenticated seconds earlier. Whether Google needs
    each one is not knowable from here, and guessing wrong looks exactly like
    an expired session. Send what worked. WANTED survives only to describe the
    names that matter for REQUIRED and for the docs.
    """
    return dict(cookies)


def parse_cookie_header(header):
    """Split a raw ``Cookie:`` header into a dict."""
    cookies = {}
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name] = value
    return _filter(cookies)


HOST = "tv.youtube.com"


def domain_matches(domain, host=HOST):
    """The cookie domain-match rule, as a browser applies it.

    A cookies.txt export holds every domain the browser knows, and youtube.com
    alone spans www, music, studio, m and tv -- each with its own host-scoped
    cookies. Sending the lot to tv.youtube.com is not "what the browser sent";
    it is several times what the browser sent, and Google answers 413.

    So match the way a browser does: a cookie scoped to youtube.com travels to
    any subdomain, one scoped to www.youtube.com travels only there.
    """
    domain = domain.lstrip(".")
    return host == domain or host.endswith("." + domain)


def expired(expiry, now=None):
    """Whether a cookies.txt row is one a browser would no longer send.

    An export is a dump of the cookie store, not of what gets sent: it keeps
    rows long past their expiry. That is not academic here. A real export
    carried 113 ST-* cookies totalling 101 KB, nearly all of them expired two
    weeks earlier, and sending them produced HTTP 413 from Google -- a failure
    with no obvious connection to the cookie the request actually needed.

    Expiry 0 means a session cookie, which the browser does still send.
    """
    try:
        stamp = int(float(expiry))
    except (TypeError, ValueError):
        return False
    return stamp != 0 and stamp < (now if now is not None else time.time())


def parse_cookies_txt(path):
    """Read a Netscape cookies.txt and keep what tv.youtube.com would receive.

    Hand-rolled rather than http.cookiejar because the exports in the wild are
    not always well formed -- extensions emit a ``#HttpOnly_`` prefix that
    MozillaCookieJar rejects outright, and a strict parser fails the whole file
    over one bad line.

    Google keeps a copy of the session under .google.com as well, which no
    browser shows tv.youtube.com. It is used only to fill in an authenticating
    name the youtube.com jar lacks -- a browser signed in on one domain and not
    the other -- and never to add cookies of its own.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_cookies_txt_text(handle.read())


def parse_cookies_txt_text(text):
    """The same, for an export pasted rather than saved to disk.

    Split out so the sign-in page can take a cookies.txt someone pasted into
    a textarea without first asking them to put a file on the Kodi box, which
    is the whole difficulty being removed.
    """
    youtube, google = {}, {}
    now = time.time()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        domain, expiry, name, value = (fields[0], fields[4],
                                       fields[5], fields[6])
        if expired(expiry, now):
            continue
        if domain_matches(domain):
            # Two entries can carry one name -- .youtube.com and a
            # host-scoped tv.youtube.com copy. The specific one is the one
            # that was set for us, so let it win.
            if name not in youtube or domain.lstrip(".") == HOST:
                youtube[name] = value
        elif domain_matches(domain, "www.google.com"):
            google[name] = value

    jar = dict(youtube)
    for name in WANTED:
        if name not in jar and name in google:
            jar[name] = google[name]
    return jar


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
