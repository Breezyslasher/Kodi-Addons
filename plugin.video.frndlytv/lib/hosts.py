"""Where Friendly TV's APIs live, asked rather than assumed.

The web player does not hardcode its hosts. Before anything else it fetches a
small file that names them, one block per client:

    GET https://paas-init.revlet.net/clients/frndlytv/init/live/frndlytv-live-v2.json
    {"default": {...}, "web": {...}, "roku": {...}, "android": {...}, ...}

and each block is the same shape:

    {"location": "https://frndlytv-api.revlet.net",
     "api":      "https://frndlytv-api.revlet.net",
     "search":   "https://frndlytv-api.revlet.net",
     "pgURL":    "https://frndlytv-api.revlet.net",
     "guideURL": "https://frndlytv-tvguideapi.revlet.net",
     "tivo": "...", "tivoClick": "...",
     "tenantCode": "frndlytv", "product": "frndlytv", "isSupported": true}

The blocks are not identical: Roku's search lives on ``frndlytv-rokuapi`` and
Android's whole API on ``frndlytv-androidapi``. This addon presents itself as
the web player everywhere else, so it reads ``web``, over ``default``.

The point is that a moved host is announced here. Hardcoding the two the
addon happens to use means it breaks on the day one changes, with no way to
find out from inside Kodi; this way it follows.

Nothing here can break the addon: a fetch that fails, a file that is not
JSON, a block that is missing, a value that is not an https url -- each falls
back to the captured value for that one key.
"""

import time

import requests

from . import kodiutils

INIT_URL = ("https://paas-init.revlet.net/clients/frndlytv/init/live/"
            "frndlytv-live-v2.json")

# The client whose block to read, and the one the addon impersonates.
CLIENT = "web"

CACHE_FILE = "hosts.json"
MAX_AGE = 24 * 60 * 60
TIMEOUT = 15

# The captured "web" block, used until the file is read and for any key it
# turns out not to carry.
DEFAULTS = {
    "api": "https://frndlytv-api.revlet.net",
    "search": "https://frndlytv-api.revlet.net",
    "guideURL": "https://frndlytv-tvguideapi.revlet.net",
    "tenantCode": "frndlytv",
    "product": "frndlytv",
}

_CACHED = [None]


def _usable(key, value):
    """Whether a value is worth preferring over the captured one."""
    if not isinstance(value, str) or not value.strip():
        return False
    if key in ("tenantCode", "product"):
        return True
    return value.startswith("https://")


def all_hosts():
    """The merged block: captured values, overlaid with what the file says."""
    if _CACHED[0] is not None:
        return _CACHED[0]

    cached = kodiutils.read_json(CACHE_FILE, default=None)
    if cached and time.time() - cached.get("fetched_at", 0) < MAX_AGE:
        _CACHED[0] = cached.get("hosts") or dict(DEFAULTS)
        return _CACHED[0]

    found = dict(DEFAULTS)
    try:
        reply = requests.get(INIT_URL, timeout=TIMEOUT,
                             headers={"User-Agent": kodiutils.USER_AGENT})
        body = reply.json()
        block = {}
        for name in ("default", CLIENT):
            part = body.get(name)
            if isinstance(part, dict):
                block.update(part)
        changed = []
        for key in DEFAULTS:
            value = block.get(key)
            if _usable(key, value) and value != found[key]:
                found[key] = value
                changed.append("%s -> %s" % (key, value))
        if changed:
            kodiutils.log("hosts moved since this addon was written: %s"
                          % "; ".join(changed))
        kodiutils.write_json(CACHE_FILE, {"fetched_at": time.time(),
                                          "hosts": found})
    except Exception as exc:
        # Never fatal: the captured hosts are still the right answer today.
        kodiutils.log("could not read the host list (%s); using the built-in "
                      "hosts" % exc)
        found = (cached or {}).get("hosts") or found
    _CACHED[0] = found
    return found


def api():
    return all_hosts()["api"]


def search():
    return all_hosts()["search"]


def guide():
    return all_hosts()["guideURL"]


def tenant():
    return all_hosts()["tenantCode"]


def product():
    return all_hosts()["product"]
