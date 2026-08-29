"""A SABR conversation, turned into numbered segments.

InputStream Adaptive cannot speak SABR. It fetches an initialisation
segment and then numbered media segments over HTTP, so something has to
stand between the two and turn "give me segment N of itag 148" into a SABR
exchange. This module is that half: the conversation, with no HTTP server
and no manifest, so it can be exercised against a captured response without
Kodi being involved.

What the conversation is, all of it measured rather than assumed:

* A request carries the ustreamer config (field 5), the chosen audio and
  video FormatIds (16 and 17), what we already hold (3, repeated), and the
  StreamerContext (19). See docs/youtube-tv-protocol.md.
* A response is UMP parts. MEDIA_HEADER (20) opens a track's segment and
  names its itag and sequence; MEDIA (21) carries the bytes, prefixed with
  the header id it belongs to; MEDIA_END (22) closes it.
* NEXT_REQUEST_POLICY (35) field 7 must be echoed as StreamerContext field
  3 in the next request, or the session never advances.
* The first media of a track opens with an `ftyp` box, so initialisation
  arrives inline and has to be split off rather than fetched.
* An empty response means "at the live edge, nothing yet" -- not the end.
"""

import time

from . import kodiutils, sabr

# One segment's worth, for the duration a BufferedRange claims.
SEGMENT_MS = 5000
# How long to keep asking when a wanted segment has not arrived yet.
PUMP_LIMIT = 12
# What the server sends when it has nothing: a short body with no media.
EMPTY_BYTES = 512


class SabrError(Exception):
    """The endpoint refused, and said why."""


def split_initialisation(data):
    """(initialisation, media) for an fMP4, split at the first moof.

    A track's first media arrives as ftyp + moov + moof + mdat: the
    initialisation and the first segment in one run of bytes. ISA wants them
    separately, so the boxes are walked rather than the bytes guessed at.
    """
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if kind in (b"moof", b"styp"):
            return data[:pos], data[pos:]
        if size < 8:
            break
        pos += size
    return b"", data


def find_pssh(data):
    """Every pssh box in an initialisation segment, as raw bytes.

    ISA needs init data to open a Widevine session -- given a
    ContentProtection with no pssh under it, ISA 22 refuses with "PSSH init
    data has unexpected size (0)". On the DASH path the manifest carries it
    because live streams there have no init segment at all. SABR does send
    one, so the question is whether its moov already holds a pssh, and that
    is answered by walking the boxes rather than by assuming either way.
    """
    found = []

    def walk(chunk, depth=0):
        pos = 0
        while pos + 8 <= len(chunk):
            size = int.from_bytes(chunk[pos:pos + 4], "big")
            kind = chunk[pos + 4:pos + 8]
            if size < 8 or pos + size > len(chunk):
                return
            if kind == b"pssh":
                found.append(chunk[pos:pos + size])
            elif kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl") and depth < 4:
                walk(chunk[pos + 8:pos + size], depth + 1)
            pos += size

    walk(data)
    return found


class Session(object):
    """One playback session against one serverAbrStreamingUrl."""

    def __init__(self, url, config, audio, video, client_name, client_id,
                 client_version, post):
        self.url = url
        self.config = config
        self.audio = audio
        self.video = video
        self.post = post
        self.info = sabr.client_info(client_id, client_version)
        self.client_name = client_name

        self.echo = b""
        self.position = sabr.LIVE_EDGE
        self.started = time.time()
        # itag -> {sequence: start time}, everything held, because the
        # server backfills N-1 after answering the edge with N and a claim
        # that only grows forwards discards it and stalls.
        self.held = {}
        self.initialisation = {}
        self.segments = {}
        # header id -> (itag, sequence, buffer)
        self._open = {}

    # -- the conversation ------------------------------------------------

    def _entries(self):
        audio = [self.audio] if self.audio else []
        video = [self.video] if self.video else []
        return audio, video

    def _buffered(self):
        claims = []
        for itag, seen in sorted(self.held.items()):
            first, last = min(seen), max(seen)
            entry = self.audio if self.audio and self.audio[0] == itag else self.video
            if not entry:
                continue
            claims.append(sabr.buffered_range(
                entry, seen[first], (last - first + 1) * SEGMENT_MS,
                first, last))
        return claims

    def fetch(self):
        """One exchange. Returns the sequences that completed."""
        audio, video = self._entries()
        body = sabr.build_request(
            self.config, audio=audio, video=video,
            player_time_ms=self.position,
            buffered=self._buffered(),
            context=sabr.streamer_context(info=self.info, echo=self.echo),
            elapsed_ms=int((time.time() - self.started) * 1000))
        data = self.post(self.url, body)
        if not data:
            return []
        return self._absorb(data)

    def _absorb(self, data):
        done = []
        for part_type, payload in sabr.parse_ump(data):
            if part_type == 44:
                raise SabrError(bytes(payload[:60]).decode("ascii", "replace"))
            if part_type == 20:
                self._open_header(payload)
            elif part_type == 21:
                self._append(payload)
            elif part_type == 22:
                finished = self._close(payload)
                if finished:
                    done.append(finished)
            elif part_type == 35:
                for number, _wire, value in sabr.fields(payload):
                    if number == 7 and isinstance(value, bytes):
                        self.echo = value
        if self.position == sabr.LIVE_EDGE and self.held:
            self.position = min(min(seen.values())
                                for seen in self.held.values())
        return done

    def _open_header(self, payload):
        got = dict((n, v) for n, _w, v in sabr.fields(payload))
        header_id, itag = got.get(1), got.get(3)
        sequence, start = got.get(9), got.get(11)
        if itag is None or sequence is None:
            return
        self._open[header_id] = [itag, sequence, bytearray()]
        if start is not None:
            self.held.setdefault(itag, {})[sequence] = start

    def _append(self, payload):
        header_id, pos = sabr._ump_varint(payload, 0)
        if header_id is None or header_id not in self._open:
            return
        self._open[header_id][2] += payload[pos:]

    def _close(self, payload):
        header_id, _pos = sabr._ump_varint(payload, 0)
        entry = self._open.pop(header_id, None)
        if not entry:
            return None
        itag, sequence, buffer_ = entry
        data = bytes(buffer_)
        head, media = split_initialisation(data)
        if head:
            self.initialisation.setdefault(itag, head)
        self.segments.setdefault(itag, {})[sequence] = media or data
        return (itag, sequence)

    # -- what the bridge asks for ----------------------------------------

    def initialisation_for(self, itag):
        """The init segment, fetching until one has been seen."""
        for _ in range(PUMP_LIMIT):
            if itag in self.initialisation:
                return self.initialisation[itag]
            if not self.fetch():
                time.sleep(1)
        return self.initialisation.get(itag, b"")

    def segment(self, itag, sequence):
        """Segment `sequence` of `itag`, pumping the session until it lands."""
        for attempt in range(PUMP_LIMIT):
            held = self.segments.get(itag) or {}
            if sequence in held:
                return held[sequence]
            if held and sequence < min(held):
                # Older than anything the session has seen. The window moved
                # on and this cannot be served; saying so beats an empty body
                # that ISA would read as a broken segment.
                raise SabrError("segment %d of %d is behind the session"
                                % (sequence, itag))
            if not self.fetch() and attempt:
                # Nothing came back: the live edge has not produced it yet.
                time.sleep(1)
        kodiutils.log("sabr session: gave up waiting for %d of %d"
                      % (sequence, itag))
        return b""

    def prime(self):
        """Fetch until the session holds a segment, or give up saying so.

        A manifest needs a number to start from and a freshly opened session
        has none: the service opens it on the first request, which is a
        manifest request, so without this the very first thing ISA asks for
        is answered 503 by a session that had simply not spoken yet.
        """
        for attempt in range(PUMP_LIMIT):
            if self.segments:
                return True
            if not self.fetch() and attempt:
                time.sleep(1)
        kodiutils.log("sabr session: primed %d times and hold nothing"
                      % PUMP_LIMIT)
        return bool(self.segments)

    def first_sequence(self, itag):
        held = self.segments.get(itag) or {}
        return min(held) if held else 0
