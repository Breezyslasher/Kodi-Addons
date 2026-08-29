"""Minting a proof-of-origin token, rather than pasting one.

Every media url YouTube serves wants a `pot`, and the token is bound to the
`visitorData` that minted it and lives a matter of hours. Taking one out of a
browser capture worked exactly as long as the capture was fresh: a token five
minutes old still played, and one from half an hour earlier did not. That is
not a thing a person can keep up with by hand, and it took both playback
paths down when it lapsed.

So mint it. `botguard.js` does the whole exchange in the JavaScript runtime
the addon already finds for the n transform -- ask jnn-pa.googleapis.com for a
challenge, unscramble it, run BotGuard's own interpreter against a small
browser shim, and trade the snapshot for a token. Verified end to end: a
token from here plays where one lifted from a browser used to be required.

Cached against its binding until it expires, because the exchange costs a
couple of seconds and three network round trips, and the token is good for
twelve hours.
"""
import base64
import json
import os
import random
import subprocess
import threading
import time

from . import kodiutils, nsig

CACHE_FILE = "potoken_cache.json"
SCRIPT = "botguard.js"

# A minute of slack, so a token that expires mid-playback is not a mystery.
SLACK = 60

_lock = threading.Lock()
_memo = {}


class PoTokenError(Exception):
    """No token could be minted."""


def _script_path():
    """Where the runtime can read the minter from.

    The addon's own lib directory is inside the flatpak, and the host's node
    -- reached through flatpak-spawn -- cannot see it. The profile directory
    is a real host path under the same name on both sides, which is why nsig
    writes its programs there too.
    """
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), SCRIPT)
    with open(source, "r", encoding="utf-8") as handle:
        body = handle.read()
    try:
        directory = kodiutils.profile_dir()
    except Exception:
        import tempfile
        directory = tempfile.gettempdir()
    target = os.path.join(directory, SCRIPT)
    try:
        if not os.path.exists(target) or open(target, encoding="utf-8").read() != body:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(body)
    except OSError as exc:
        raise PoTokenError("could not write %s: %s" % (target, exc))
    return target


def _mint(binding):
    """Run the exchange once, and return (token, ttl)."""
    runtime, argv = nsig._runtime_on_path()
    if not runtime:
        raise PoTokenError("no JavaScript runtime, so no token can be minted "
                           "-- the same runtime the n transform needs")
    script = _script_path()
    try:
        result = subprocess.run(argv + [script, binding],
                                capture_output=True, timeout=180)
    except Exception as exc:
        raise PoTokenError("%s could not be run: %s" % (runtime, exc))
    if result.returncode != 0:
        raise PoTokenError("%s exited %d: %s"
                           % (runtime, result.returncode,
                              result.stderr.decode("utf-8", "replace").strip()[-300:]))
    try:
        answer = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception as exc:
        raise PoTokenError("could not read what %s printed: %s" % (runtime, exc))
    token = answer.get("token") or ""
    if not token:
        raise PoTokenError("the exchange produced no token")
    return token, int(answer.get("ttl") or 43200)


def cold_start(binding, client_state=1):
    """A token computed here, with no BotGuard and no JavaScript at all.

    YouTube accepts one of these while it reports StreamProtectionStatus 2,
    and refuses it once that becomes 3 -- so it is a fallback, not a
    replacement. It exists because a box may have no JavaScript runtime at
    all: LibreELEC ships Python and nothing else, and minting needs a runtime
    that BotGuard's interpreter will run in.

    The shape is arithmetic, not cryptography: a two byte prefix, two random
    keys, a client state, a four byte timestamp, the binding, and then the
    payload XORed against the keys that precede it.
    """
    body = binding.encode("utf-8")
    now = int(time.time())
    keys = [random.randrange(256), random.randrange(256)]
    header = keys + [0, client_state] + [
        (now >> 24) & 0xFF, (now >> 16) & 0xFF, (now >> 8) & 0xFF, now & 0xFF]
    packet = bytearray(2 + len(header) + len(body))
    packet[0] = 34
    packet[1] = len(header) + len(body)
    packet[2:2 + len(header)] = bytes(header)
    packet[2 + len(header):] = body
    # The payload is everything after the two byte prefix, XORed forward
    # against the two random keys at its head.
    for i in range(len(keys), len(packet) - 2):
        packet[2 + i] ^= packet[2 + (i % len(keys))]
    return base64.urlsafe_b64encode(bytes(packet)).decode().rstrip("=")


def token(binding, force=False):
    """A token for this binding, minted if there is not a live one already.

    ``binding`` is the visitorData for a session-bound token, or a video id
    for a content-bound one. Returns "" rather than raising: a missing token
    is a thing to log and carry on from, since a build may still carry a
    baked one that works.
    """
    if not binding:
        return ""
    now = time.time()
    with _lock:
        held = _memo.get(binding)
        if not held:
            stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
            held = stored.get(binding)
        if held and not force and held.get("expires_at", 0) > now + SLACK:
            _memo[binding] = held
            return held.get("token") or ""

        try:
            minted, ttl = _mint(binding)
        except PoTokenError as exc:
            kodiutils.log("po token: %s" % exc)
            # No runtime, or the exchange failed. A cold start token is
            # computed here and needs nothing, and YouTube takes one while it
            # reports StreamProtectionStatus 2. Short-lived on purpose: it
            # should be retried for a real one before long.
            minted, ttl = cold_start(binding), 1800
            kodiutils.log("po token: falling back to a cold start token, "
                          "%s... -- it plays while YouTube allows one, and "
                          "is not a substitute for minting" % minted[:16])

        held = {"token": minted, "expires_at": int(now + ttl)}
        _memo[binding] = held
        stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
        # Only this binding's, and only the ones still alive: the file should
        # not grow a row per video id ever played.
        stored = {k: v for k, v in stored.items()
                  if isinstance(v, dict) and v.get("expires_at", 0) > now}
        stored[binding] = held
        kodiutils.write_json(CACHE_FILE, stored)
        kodiutils.log("po token: minted %s... for %s..., good for %d hours"
                      % (minted[:16], binding[:12], ttl // 3600))
        return minted
