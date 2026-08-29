"""Constructing the Widevine PSSH that YouTube's DASH manifests leave out.

InputStream Adaptive opens a CDM session from initialisation data -- normally a
``<cenc:pssh>`` element in the manifest. YouTube's manifests do not carry one
that ISA can use, which is what "InitializePeriod: Unhandled encrypted stream"
means: streams marked encrypted, and nothing to open a session with.

The browser does not need the manifest for this. Its licence challenge carries
the PSSH inline, and decoding one from a capture shows exactly what YouTube
expects (video z0sfuXTVx8g, 2026-08-27)::

    WidevinePsshData {
        content_id            = "YT_TV_BROADCAST:z0sfuXTVx8g.0"
        crypto_period_index   = 20693
        protection_scheme     = 1667591779       # 'cenc' as a fourcc
        crypto_period_seconds = 86400            # a one-day period
    }

Every field is reconstructible: the content id from the video id, the index
from the clock, and the last two are constants. Notably there are no key ids in
it -- this is a content-id PSSH, so no amount of reading the manifest would let
ISA build it. It has to be handed over as license_data.

The period fields also settle a question the addon previously guessed at: the
licence exchange's cryptoPeriodIndex is time / 86400, confirmed here by
YouTube's own crypto_period_seconds rather than by one lucky arithmetic match.
"""

import base64
import math
import struct
import time

# WidevinePsshData field numbers, from the published proto.
F_CONTENT_ID = 4
F_CRYPTO_PERIOD_INDEX = 7
F_PROTECTION_SCHEME = 9
F_CRYPTO_PERIOD_SECONDS = 10

CRYPTO_PERIOD_SECONDS = 86400
PROTECTION_SCHEME_CENC = 0x63656E63  # 'cenc'

WIDEVINE_SYSTEM_ID = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")

# YouTube TV live streams. On-demand uses a different content-id prefix that no
# capture has pinned down yet, so build_pssh() is only trusted for live.
LIVE_CONTENT_PREFIX = "YT_TV_BROADCAST:"


def decode_b64(value):
    """Decode base64 in whichever dialect YouTube happens to have used.

    The licence comes back URL-safe ("-" and "_") while the challenge we send
    is standard ("+" and "/"), and padding is not always right: one captured
    licence was 2608 characters of which the payload was 2577, which
    b64decode rejects outright. Accepting both alphabets and repadding costs
    nothing and removes a whole class of failure.
    """
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    value = "".join(value.split()).replace("-", "+").replace("_", "/")
    value = value.rstrip("=")
    return base64.b64decode(value + "=" * (-len(value) % 4))


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field, wire):
    return _varint((field << 3) | wire)


def _field_varint(field, value):
    return _tag(field, 0) + _varint(value)


def _field_bytes(field, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return _tag(field, 2) + _varint(len(value)) + value


def read_fields(data):
    """Yield ``(field_number, value)`` from a protobuf message.

    Only the two wire types YouTube uses here are decoded; anything else ends
    the walk rather than guessing at a length.
    """
    index = 0
    while index < len(data):
        key = 0
        shift = 0
        while True:
            if index >= len(data):
                return
            byte = data[index]
            index += 1
            key |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        field, wire = key >> 3, key & 7
        if wire == 0:
            value = 0
            shift = 0
            while True:
                if index >= len(data):
                    return
                byte = data[index]
                index += 1
                value |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            yield field, value
        elif wire == 2:
            length = 0
            shift = 0
            while True:
                if index >= len(data):
                    return
                byte = data[index]
                index += 1
                length |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            yield field, data[index:index + length]
            index += length
        else:
            return


def crypto_period_index(now=None):
    """The current key period: one per day, indexed by the day it ends."""
    return int(math.ceil((now or time.time()) / float(CRYPTO_PERIOD_SECONDS)))


def pssh_data(video_id, period_index=None):
    """The WidevinePsshData payload for a live YouTube TV stream."""
    content_id = "%s%s.0" % (LIVE_CONTENT_PREFIX, video_id)
    return (_field_bytes(F_CONTENT_ID, content_id)
            + _field_varint(F_CRYPTO_PERIOD_INDEX,
                            period_index if period_index is not None
                            else crypto_period_index())
            + _field_varint(F_PROTECTION_SCHEME, PROTECTION_SCHEME_CENC)
            + _field_varint(F_CRYPTO_PERIOD_SECONDS, CRYPTO_PERIOD_SECONDS))


def build_pssh(video_id, period_index=None):
    """A complete ``pssh`` box, base64 encoded, for license_data."""
    data = pssh_data(video_id, period_index)
    body = (WIDEVINE_SYSTEM_ID
            + struct.pack(">I", len(data))
            + data)
    # size, type, then version/flags as one zero word.
    box = struct.pack(">I", len(body) + 12) + b"pssh" + b"\x00\x00\x00\x00" + body
    return base64.b64encode(box).decode("ascii")


def session_id_from_drm_params(drm_params):
    """The DRM session id YouTube embedded in ``drmParams``.

    The licence exchange must quote the id the player response minted, not a
    fresh one: in the capture, drmParams field 5 and the request's sessionId
    were the same string. Generating our own looked right and would have been
    rejected.
    """
    if not drm_params:
        return ""
    try:
        raw = decode_b64(_unquote(drm_params))
    except Exception:
        return ""
    for field, value in read_fields(raw):
        if field == 5 and isinstance(value, (bytes, bytearray)):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return ""
    return ""


def _unquote(value):
    from urllib.parse import unquote
    return unquote(value)
