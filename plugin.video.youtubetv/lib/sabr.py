"""Talking to YouTube's SABR endpoint.

Everything YouTube TV actually serves comes through SABR. The DASH URLs in a
player response are refused; the SABR endpoint is where the media is, and the
player response hands us its address in `serverAbrStreamingUrl` -- byte for
byte the URL the web player POSTs to, opaque per-session id and all.

What was missing is the conversation. A SABR request is a protobuf body, and
decoding one the browser sent shows it is mostly things we already hold:

    field 1   ClientAbrState -- player position, buffer state, capabilities
    field 5   the ustreamer config, which is
              playerConfig.mediaCommonConfig.mediaUstreamerRequestConfig
              .videoPlaybackUstreamerConfig from the player response,
              base64url-decoded. Verified byte-identical to the captured body.
    field 16  the format being requested, as {itag, lastModified}
    field 17  repeated, the other formats known to the session

The response is UMP: a stream of (type, length, payload) parts using YouTube's
own varint encoding, carrying media headers, media bytes, and control messages
such as redirects.

This is a probe, not a player. It builds the smallest plausible request and
reports what comes back, so the question "is the SABR endpoint reachable at
all" gets an answer from the server rather than from reasoning.
"""

import itertools
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import base64
import struct

from . import kodiutils

# UMP part types worth naming in a log.
PART_TYPES = {
    10: "ONESIE_HEADER",
    11: "ONESIE_DATA",
    20: "MEDIA_HEADER",
    21: "MEDIA",
    22: "MEDIA_END",
    31: "LIVE_METADATA",
    35: "NEXT_REQUEST_POLICY",
    43: "SABR_REDIRECT",
    44: "SABR_ERROR",
    45: "SABR_SEEK",
    46: "RELOAD_PLAYER_RESPONSE",
    47: "PLAYBACK_START_POLICY",
    58: "STREAM_PROTECTION_STATUS",
    60: "SABR_CONTEXT_UPDATE",
}


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


def _v(field, value):
    return _tag(field, 0) + _varint(value)


def _b(field, value):
    return _tag(field, 2) + _varint(len(value)) + value


def format_id(itag, last_modified):
    """A FormatId submessage: {itag, lastModified}."""
    body = _v(1, int(itag))
    if last_modified:
        body += _v(2, int(last_modified))
    return body


def client_abr_state(player_time_ms=0, max_height=1080):
    """The smallest ClientAbrState that still describes a real player.

    The captured one carries eighteen fields; most look like buffer health and
    telemetry that a server has no reason to require. These are the ones whose
    meaning is legible: where the player is, and what it can display.
    """
    return (_v(21, 0)                    # start / seek position marker
            + _v(28, int(player_time_ms))
            + _v(29, 3)                  # media type: audio+video
            + _v(59, int(max_height))
            + _v(71, 1)
            + _v(80, 1)
            + _v(85, 1))


def build_request(ustreamer_config, wanted, known=(), player_time_ms=0,
                  max_height=1080):
    """A VideoPlaybackAbrRequest body.

    ``ustreamer_config`` is the base64url string from the player response;
    ``wanted`` and ``known`` are (itag, lastModified) pairs.
    """
    config = base64.urlsafe_b64decode(
        ustreamer_config + "=" * (-len(ustreamer_config) % 4))
    body = _b(1, client_abr_state(player_time_ms, max_height))
    body += _b(5, config)
    for itag, lmt in ([wanted] if wanted else []):
        body += _b(16, format_id(itag, lmt))
    for itag, lmt in known:
        body += _b(17, format_id(itag, lmt))
    return body


# -- UMP response ---------------------------------------------------------

def _ump_varint(data, pos):
    """YouTube's UMP varint: the first byte's high bits give the length."""
    if pos >= len(data):
        return None, pos
    first = data[pos]
    if first < 0x80:
        return first, pos + 1
    if first < 0xC0:
        size, value, shift = 2, first & 0x3F, 6
    elif first < 0xE0:
        size, value, shift = 3, first & 0x1F, 5
    elif first < 0xF0:
        size, value, shift = 4, first & 0x0F, 4
    else:
        size, value, shift = 5, 0, 0
    if pos + size > len(data):
        return None, len(data)
    if size == 5:
        return struct.unpack("<I", data[pos + 1:pos + 5])[0], pos + 5
    for index in range(1, size):
        value |= data[pos + index] << (shift + 8 * (index - 1))
    return value, pos + size


def parse_ump(data):
    """Yield ``(part_type, payload)`` from a UMP response."""
    pos = 0
    while pos < len(data):
        part_type, pos = _ump_varint(data, pos)
        if part_type is None:
            return
        size, pos = _ump_varint(data, pos)
        if size is None:
            return
        payload = data[pos:pos + size]
        pos += size
        yield part_type, payload


def describe_response(data):
    """A one-line-per-part summary of what the endpoint returned."""
    lines = ["sabr response: %d bytes" % len(data)]
    media = 0
    for part_type, payload in parse_ump(data):
        name = PART_TYPES.get(part_type, "type %d" % part_type)
        if part_type == 21:
            media += len(payload)
            continue
        detail = ""
        if part_type in (44, 43) and payload:
            detail = " %r" % payload[:80]
        lines.append("  %-26s %d bytes%s" % (name, len(payload), detail))
    if media:
        lines.append("  MEDIA (total)              %d bytes" % media)
    return "\n".join(lines)


_request_number = itertools.count(1)


def playback_url(url, cpn, client_version, client_name=None):
    """Add the parameters the player appends before it POSTs.

    serverAbrStreamingUrl arrives signed but incomplete: the browser appends
    cpn, cver, alr and rn itself, and googlevideo refuses the bare url with an
    empty-bodied 403. Crossing the two requests is what showed this -- the
    browser's url carrying our body was served, ours carrying the browser's
    body was refused, so the difference was never in the protobuf.

      cpn   the playback nonce, the same one the player call was made with,
            which is what binds this fetch to that playback session
      cver  the client version, matching the one the player call claimed
      alr   allow redirects, which the edge uses to hand off to another host
      rn    request number, counting up across a playback session
    """
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["cpn"] = [cpn]
    query["cver"] = [client_version]
    query["alr"] = ["yes"]
    query["rn"] = [str(next(_request_number))]
    if client_name and "c" not in query:
        query["c"] = [client_name]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))
