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


def format_id(itag, last_modified=0, xtags=""):
    """A FormatId submessage: {itag, lastModified, xtags}.

    xtags is not decoration. YouTube TV lists the same audio itag twice --
    148 and 149 each appear as both primary and secondary -- and the only
    thing telling them apart is the xtags string, which the player response
    carries per format and the captured request sends verbatim, still
    base64, rather than decoded. An audio FormatId without it names a track
    the server cannot resolve, and the answer to a request built that way
    was `sabr.no_audio_selected`.
    """
    # All three fields, always, even when two of them are empty. The
    # captured video selections are seven bytes -- 08 e7 02, 10 00, 1a 00 --
    # a lastModified of zero and an xtags of length zero, both present. Ours
    # emitted three bytes by leaving them out, and every video rendition
    # offered was answered sabr.no_video_selected: twelve itags in a row,
    # which looked like the renditions being refused and was one missing
    # pair of empty fields. Audio never showed it, because audio xtags are
    # never empty.
    body = _v(1, int(itag))
    body += _v(2, int(last_modified or 0))
    body += _b(3, xtags.encode("ascii") if xtags else b"")
    return body


# What the browser sends for "play from the live edge": Number.MAX_SAFE_INTEGER.
LIVE_EDGE = 9007199254740991


# The two ClientAbrState fields that decide whether HD is served at all.
#
# Field 79 is a repeated {1: n, 2: 0} for n = 3, 4, 2, 1 -- a capability
# list -- and field 72 carries the height twice. Sending the height in 16
# and 21 and stopping there is answered sabr.no_video_selected for every
# codec at 1080p; adding these two is answered with the media, measured
# three ways in one playback:
#
#   avc1 at 1080p, height named                     REFUSED
#   avc1 at 1080p, + these two fields               SERVED itag 146
#   av01 at 1080p, + these two fields               SERVED itag 814
#   vp9  at 1080p, + these two fields               SERVED itag 360
#
# A bandwidth in field 23 changed nothing: byte for byte the same answer.
CAPABILITIES = bytes.fromhex("0a04080310000a04080410000a04080210000a0408011000")


def viewport(height):
    """ClientAbrState field 72, which carries the height in two slots.

    Read out of a capture as {1:0, 2:1080, 3:0, 4:0, 5:1080, 6:0} for a
    1080p request. Both filled slots are the height -- a 1920x1080
    rendition would put 1920 in one of them if either were the width.
    """
    height = int(height or 0)
    return (_v(1, 0) + _v(2, height) + _v(3, 0)
            + _v(4, 0) + _v(5, height) + _v(6, 0))


def client_abr_state(player_time_ms=0, max_height=1080, elapsed_ms=0,
                     target_height=0, bandwidth=0, extras=b""):
    """ClientAbrState, with the fields four captured requests agree on.

    Field 29 is not a media-type enum. It was annotated as one here, first
    as 3 and then as 2 after a capture was read too quickly; across four
    consecutive requests from one playback session it reads 2, 3, 490,
    1579, which is a counter, not a constant. It is passed in now.

    Field 28 is the position: MAX_SAFE_INTEGER on the first request of a
    live session -- the browser's way of saying "the edge" -- and an
    absolute media timestamp thereafter.

    Fields 18 and 19 are 2140 and 1204 in every captured request of every
    session, so they are sent as the constants they appear to be.

    Fields 16 and 21 are the height being asked for, and they are what
    makes the endpoint serve it. A capture of the browser's own quality
    selector has them tracking the offered renditions exactly:

        16 / 21     video itags offered
        720 / 720   [812, 811, 552]   all 1280x720, HD tier
        480 / 480   [810, 809, 551]   all  854x480
        360 / 360   [550]             one format, and accepted
        1080 / 1080 [814, 813, 553]   all 1920x1080, HD tier

    This addon sent neither -- 21 went out as a hardcoded 0 -- and every
    HD offer it ever made was answered sabr.no_video_selected, including
    offers of three renditions and of one. The browser makes both of those
    shapes and is served, so the refusals were never about the tier or the
    size of the offer. They were a request that named no height.

    Field 59 stays the ceiling: the browser holds it at 1080 while 16 and
    21 move between 360 and 1080.
    """
    body = (_v(18, 2140)
            + _v(19, 1204)
            + _v(21, int(target_height))
            + _v(28, int(player_time_ms))
            + _v(29, int(elapsed_ms))
            + _v(59, int(max_height))
            + _v(71, 1)
            + _v(80, 1)
            + _v(85, 1))
    if target_height:
        body = _v(16, int(target_height)) + body
    if bandwidth:
        body += _v(23, int(bandwidth))
    # Not optional, and not only when a height is named: without these the
    # endpoint serves 480p and refuses anything taller.
    body += _b(72, viewport(target_height or max_height))
    body += _b(79, CAPABILITIES)
    return body + extras


def client_info(client_id, client_version, locale="en_US", os_name="X11"):
    """StreamerContext.ClientInfo, as the captured requests carry it."""
    return (_b(1, locale.encode("ascii"))
            + _v(16, int(client_id))
            + _b(17, client_version.encode("ascii"))
            + _b(18, os_name.encode("ascii")))


def streamer_context(info=b"", po_token=b"", echo=b""):
    """Top-level field 19.

    Subfield 3 is the continuation: whatever the previous response's
    NEXT_REQUEST_POLICY carried in its field 7, echoed back verbatim.
    Rebuilt from a captured response and compared against the request that
    followed it, the two are byte-identical -- which is what makes a SABR
    session a session rather than the same answer over and over.
    """
    body = b""
    if info:
        body += _b(1, info)
    if po_token:
        body += _b(2, po_token)
    if echo:
        body += _b(3, echo)
    return body


def next_request_echo(data):
    """The blob to echo, taken from a response's NEXT_REQUEST_POLICY."""
    for part_type, payload in parse_ump(data):
        if part_type != 35:
            continue
        for number, _wire, value in fields(payload):
            if number == 7 and isinstance(value, bytes):
                return value
    return b""


def buffered_range(entry, start_ms, duration_ms, first_sequence,
                   last_sequence):
    """One BufferedRange: what we already hold for a format.

    Top-level field 3, repeated once per track. Decoded from two captured
    bodies six seconds apart, it is how the client tells the server where it
    has got to:

        3 { 1: FormatId  2: startTimeMs  3: durationMs
            4: firstSequence  5: lastSequence }

    and between those two captures field 5 went 2834103 -> 2834104 while
    field 3 grew by one segment. Without it every request says "I have
    nothing", which is why the same request repeated six seconds apart came
    back byte-identical, same sequence numbers and all: the server was
    answering the question we kept asking.
    """
    body = _b(1, format_id(*entry))
    body += _v(2, int(start_ms))
    body += _v(3, int(duration_ms))
    body += _v(4, int(first_sequence))
    body += _v(5, int(last_sequence))
    return body


def build_request(ustreamer_config, audio=(), video=(), player_time_ms=0,
                  max_height=1080, buffered=(), context=b"", elapsed_ms=0,
                  target_height=0, bandwidth=0, extras=b""):
    """A VideoPlaybackAbrRequest body.

    ``ustreamer_config`` is the base64url string from the player response.
    ``audio`` and ``video`` are sequences of (itag, lastModified, xtags).

    Fields 16 and 17 are the audio and the video selection, one repeated
    entry per chosen track -- not "the one I want" and "the others I know
    about", which is what they were built as and what produced a request
    selecting no audio at all. Every captured body sends audio in 16 and
    video in 17, and the browser sends two 16s, primary and secondary.

    The config is omitted when there is none. That is not hypothetical: a
    TVHTML5_UNPLUGGED response carries serverAbrStreamingUrl and no
    mediaUstreamerRequestConfig at all -- searched for, not assumed, with no
    key anywhere in the response even mentioning "ustreamer" -- while a
    WEB_UNPLUGGED one carries both. Whether the endpoint will serve a request
    without field 5 is the thing to find out, and it cannot be found out by a
    builder that refuses to make one.
    """
    body = _b(1, client_abr_state(player_time_ms, max_height, elapsed_ms,
                                  target_height, bandwidth, extras))
    for held in buffered:
        body += _b(3, held)
    if ustreamer_config:
        config = base64.urlsafe_b64decode(
            ustreamer_config + "=" * (-len(ustreamer_config) % 4))
        body += _b(5, config)
    for entry in audio:
        body += _b(16, format_id(*entry))
    for entry in video:
        body += _b(17, format_id(*entry))
    if context:
        body += _b(19, context)
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
    """Yield ``(part_type, payload)`` from a UMP response.

    Stops at a zero-typed, zero-length part. A response can be followed by
    padding -- a captured 1,114,824-byte body has its last real part end at
    1,016,501 and 98 KB of nulls after it -- and a reader with no guard
    turns that tail into 49,162 parts of "type 0", which is noise that
    reads like structure.
    """
    pos = 0
    while pos < len(data):
        part_type, pos = _ump_varint(data, pos)
        if part_type is None:
            return
        size, pos = _ump_varint(data, pos)
        if size is None:
            return
        if not part_type and not size:
            return
        payload = data[pos:pos + size]
        pos += size
        yield part_type, payload


def fields(data):
    """Every top-level protobuf field as (number, wire type, value).

    Generic on purpose. The field numbering of MEDIA_HEADER is not something
    to look up and half-remember: dumped across two requests at different
    player times, the field that counts up by one is the sequence number and
    the one near a segment length is the duration. The data says which is
    which.
    """
    out = []
    pos = 0
    while pos < len(data):
        try:
            key, pos = _read_varint(data, pos)
        except (IndexError, ValueError):
            break
        number, wire = key >> 3, key & 7
        try:
            if wire == 2:
                length, pos = _read_varint(data, pos)
                value = data[pos:pos + length]
                pos += length
            elif wire == 0:
                value, pos = _read_varint(data, pos)
            elif wire == 5:
                value, pos = data[pos:pos + 4], pos + 4
            elif wire == 1:
                value, pos = data[pos:pos + 8], pos + 8
            else:
                break
        except (IndexError, ValueError):
            break
        out.append((number, wire, value))
    return out


def describe_media_header(payload):
    """One line naming every field a MEDIA_HEADER carries, and its value."""
    parts = []
    for number, wire, value in fields(payload):
        if isinstance(value, bytes):
            shown = value.decode("ascii", "replace") if len(value) < 24 else \
                "%d bytes" % len(value)
            parts.append("%d=%r" % (number, shown))
        else:
            parts.append("%d=%d" % (number, value))
    return " ".join(parts)


def _read_varint(data, pos):
    """Read a plain protobuf varint.

    Named for reading. The first version of this was called _varint, which
    is the name of the *encoder* twenty lines up -- so every _v() in the
    request builder started calling a decoder, and the whole SABR probe died
    with "missing 1 required positional argument: 'pos'".
    """
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7f) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def _dump(data, limit=96):
    """Hex and printable ascii, for a response too small to be media."""
    head = data[:limit]
    hexed = " ".join("%02x" % b for b in head)
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    return "    hex %s\n    txt %s" % (hexed, text)


def describe_response(data):
    """A one-line-per-part summary of what the endpoint returned.

    A short body carrying no media is the interesting case rather than an
    error: the first SABR request the endpoint ever served came back 200 with
    31 bytes and no media, and a summary saying only "31 bytes" could not say
    whether that was a redirect, a refusal or a header with nothing behind it.
    Thirty-one arbitrary bytes will also parse as a couple of nonsense parts
    -- one of them even claiming to be media -- so no reading of the parts is
    a safe trigger. Any body too small to be a real media segment gets dumped
    whole, and the parse above is left as the guess it is.
    """
    lines = ["sabr response: %d bytes" % len(data)]
    media = 0
    headers = []
    for part_type, payload in parse_ump(data):
        if part_type == 20:
            headers.append(payload)
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
    for payload in headers:
        lines.append("  MEDIA_HEADER fields: %s" % describe_media_header(payload))
    if data and len(data) < 1024:
        lines.append("  too small to be media -- the whole body:")
        lines.append(_dump(data, limit=len(data)))
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
