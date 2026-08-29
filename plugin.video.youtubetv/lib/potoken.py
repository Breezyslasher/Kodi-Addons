"""Minting a proof-of-origin token, rather than pasting one.

Every media url YouTube serves wants a `pot`, and the token is bound to the
`visitorData` that minted it and lives a matter of hours. Taking one out of a
browser capture worked exactly as long as the capture was fresh: a token five
minutes old still played, and one from half an hour earlier did not. That is
not a thing a person can keep up with by hand, and it took both playback
paths down when it lapsed.

So mint it. `botguard_py` does the whole exchange with nothing installed --
ask jnn-pa.googleapis.com for a challenge, unscramble it, run BotGuard's own
interpreter against a small browser shim inside a JavaScript engine written
in Python, and trade the snapshot for a token. Verified end to end: a token
from here plays where one lifted from a browser used to be required, and the
snapshot it produces is byte for byte the one V8 produces from the same
challenge.

Cached against its binding until it expires, because the exchange costs a
couple of seconds and three network round trips, and the token is good for
twelve hours.
"""
import base64
import json
import os
import random
import threading
import time

from . import kodiutils

CACHE_FILE = "potoken_cache.json"

# A minute of slack, so a token that expires mid-playback is not a mystery.
SLACK = 60

_lock = threading.Lock()
_memo = {}
# Bindings a mint is already running for, so two playbacks starting at once
# do not each run the VM.
_minting = set()
_mint_lock = threading.Lock()
# How the token in hand was come by, for anything that wants to say so.
last = {"source": ""}


class PoTokenError(Exception):
    """No token could be minted."""


def _mint(binding):
    """Run the exchange once, and return (token, ttl).

    Nothing here shells out and nothing here needs a runtime: the setting
    that says to behave as though none were installed only stops the mint
    so the cold start path can be exercised on purpose.
    """
    if kodiutils.get_setting_bool("no_js_runtime"):
        raise PoTokenError("minting is turned off, so a cold started token "
                           "it is")
    from . import botguard_py
    try:
        return botguard_py.mint(binding)
    except botguard_py.BotGuardError as exc:
        raise PoTokenError(str(exc))
    except Exception as exc:
        raise PoTokenError("%s: %s" % (type(exc).__name__, exc))


def short_visitor(value):
    """The visitor id inside a visitorData blob, or the value unchanged.

    visitorData is a base64url protobuf whose first field is the id itself:
    "CgtaLUZOelFqanVvbyjg..." begins 0a 0b and then eleven bytes of
    "Z-FNzQjjuoo". Anything that does not decode that way is handed back as
    it came, since a video id is a perfectly good binding too.
    """
    if not value or len(value) < 16:
        return value
    try:
        from urllib.parse import unquote
        raw = unquote(value)
        raw += "=" * (-len(raw) % 4)
        data = base64.urlsafe_b64decode(raw)
    except Exception:
        return value
    if len(data) > 2 and data[0] == 0x0A:
        length = data[1]
        if 0 < length <= 64 and len(data) >= 2 + length:
            try:
                return data[2:2 + length].decode("utf-8")
            except UnicodeDecodeError:
                return value
    return value


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
    # The packet carries one length byte, so the binding has to be the short
    # visitor id -- "Z-FNzQjjuoo" -- and not the five hundred character
    # visitorData it sits inside. Handing it the blob raises
    # "byte must be in range(0, 256)", which is what shipped.
    binding = short_visitor(binding)
    body = binding.encode("utf-8")
    if len(body) > 200:
        raise PoTokenError("a cold start binding must be short, not %d bytes"
                           % len(body))
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


def _start_mint(binding):
    """Run the exchange on a thread, and keep what it returns.

    Returns without doing anything if one is already running for this
    binding: ISA opens the manifest from more than one thread and both ask
    for a token.
    """
    with _mint_lock:
        if binding in _minting:
            return
        _minting.add(binding)

    def run():
        try:
            minted, ttl = _mint(binding)
        except PoTokenError as exc:
            kodiutils.log("po token: %s" % exc)
            return
        except Exception as exc:
            kodiutils.log_error("po token: minting fell over: %s: %s"
                                % (type(exc).__name__, exc))
            return
        finally:
            with _mint_lock:
                _minting.discard(binding)
        _remember(binding, minted, ttl, "minted")

    thread = threading.Thread(target=run, name="potoken-mint")
    thread.daemon = True
    thread.start()


def _remember(binding, minted, ttl, how):
    """Hold a token in memory and on disk, and say so."""
    now = time.time()
    held = {"token": minted, "expires_at": int(now + ttl), "source": how}
    with _lock:
        # Never downgrade. The cold start and the mint race by design --
        # the cold start answers the caller and the mint lands a few
        # seconds later -- and on a fast box the mint can land first.
        standing = _memo.get(binding)
        if how != "minted" and standing \
                and standing.get("source") == "minted" \
                and standing.get("expires_at", 0) > now + SLACK:
            last["source"] = "minted"
            return standing.get("token") or ""
        _memo[binding] = held
        stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
        # Only the ones still alive: the file should not grow a row per
        # video id ever played.
        stored = {k: v for k, v in stored.items()
                  if isinstance(v, dict) and v.get("expires_at", 0) > now}
        stored[binding] = held
        kodiutils.write_json(CACHE_FILE, stored)
    last["source"] = how
    kodiutils.log("po token: %s %s... for %s..., good for %s"
                  % (how, minted[:16], binding[:12],
                     ("%d hours" % (ttl // 3600)) if ttl >= 3600
                     else ("%d minutes" % (ttl // 60))))
    return minted


def prime(binding):
    """Have a minted token ready before anything asks for one.

    Called at service start. Playback then finds a twelve hour token in the
    cache instead of cold starting and waiting for the mint to catch up.
    """
    if not binding:
        return
    now = time.time()
    stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
    held = _memo.get(binding) or stored.get(binding)
    if held and held.get("source") == "minted" \
            and held.get("expires_at", 0) > now + SLACK:
        return
    _start_mint(binding)


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
            last["source"] = held.get("source") or "cached"
            return held.get("token") or ""

        # Cold start now, mint in the background. Running the VM costs
        # seconds -- four here, and this box is not a LibreELEC one --
        # and that would be four seconds of black screen on the first
        # play. The cold start token is arithmetic, costs nothing, and
        # YouTube takes one while it reports StreamProtectionStatus 2;
        # the minted one replaces it a few seconds later and is good for
        # twelve hours rather than thirty minutes.
        try:
            minted, ttl, how = cold_start(binding), 1800, "cold started"
        except PoTokenError as exc:
            kodiutils.log_error("po token: %s" % exc)
            minted, ttl, how = "", 0, ""
        _start_mint(binding)
        if not minted:
            return ""

    return _remember(binding, minted, ttl, how)
