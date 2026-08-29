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

import threading
import time

from . import kodiutils, mp4, sabr

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
                 client_version, post, live=True):
        self.url = url
        self.config = config
        # Lists of (itag, lastModified, xtags), not one each. Fields 16 and
        # 17 are the formats the client can play and the server chooses
        # among them: the browser offers two audio and six video in its
        # first request and the response names the pick. A single video
        # format was answered sabr.no_video_selected for all twelve
        # renditions in turn, HD ones the cookie path plays included.
        self.audio = list(audio or [])
        self.video = list(video or [])
        self.entries = {entry[0]: entry
                        for entry in self.audio + self.video}
        self.post = post
        self.info = sabr.client_info(client_id, client_version)
        self.client_name = client_name

        self.echo = b""
        # MAX_SAFE_INTEGER means "the live edge", which is past the end of a
        # recording. Asked that way, an on-demand title answered with its
        # two initialisation headers and no media at all, over and over,
        # until the session gave up holding nothing -- correctly, since
        # there is nothing after the end.
        self.live = live
        self.position = sabr.LIVE_EDGE if live else 0
        self.started = time.time()
        # itag -> {sequence: start time}, everything held, because the
        # server backfills N-1 after answering the edge with N and a claim
        # that only grows forwards discards it and stalls.
        self.held = {}
        self.initialisation = {}
        self.segments = {}
        # header id -> (itag, sequence, buffer)
        self._open = {}
        self._announced = set()
        self._boxed = {}
        # ISA reads audio and video on separate threads and both drive this
        # one session. Two fetches at once interleave their MEDIA parts
        # through the same _open map and the same echo, so a segment can be
        # assembled from two responses or lost entirely -- which is what
        # "gave up waiting for 3 of 150" looked like from the outside.
        self._lock = threading.Lock()

    # -- the conversation ------------------------------------------------

    def _entries(self):
        return self.audio, self.video

    def _buffered(self):
        """What we hold, with the duration measured rather than assumed.

        A BufferedRange says how much time it covers, and the length was a
        flat five seconds per segment -- a constant taken from live video.
        Audio segments are not that length, so the claim overstated what we
        held, the server counted the next one as already delivered, and it
        never arrived: "gave up waiting for 3 of 150", over and over, while
        video ran fine.

        The start times of the segments actually held give the real spacing,
        so the claim is built from those and only falls back to the constant
        for a track holding a single segment.
        """
        claims = []
        for itag, seen in sorted(self.held.items()):
            entry = self.entries.get(itag)
            if not entry:
                continue
            first, last = min(seen), max(seen)
            starts = sorted(seen.values())
            if len(starts) > 1:
                span = starts[-1] - starts[0]
                one = span // (len(starts) - 1)
                covered = span + one
            else:
                covered = SEGMENT_MS
            claims.append(sabr.buffered_range(
                entry, seen[first], covered, first, last))
        return claims

    def fetch(self):
        """One exchange, one at a time. Returns the sequences that completed."""
        with self._lock:
            return self._fetch()

    def _fetch(self):
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
        # Where the player has got to. It was set once, from the first
        # response, and never moved -- so every later request asked the
        # same question and the server, having already answered it, sent
        # nothing more. ISA then took 404 after 404 for segment 3 and gave
        # up nine seconds in. The captured requests move it: field 28 reads
        # 16239642500, then 16286357588, across one session.
        newest = [max(seen.values()) for seen in self.held.values() if seen]
        if newest:
            self.position = min(newest)
        return done

    def _open_header(self, payload):
        """Open a track's part, which may be a segment or its initialisation.

        A header with no sequence number is not a broken header: it is the
        initialisation segment, sent on its own. Requiring a sequence
        dropped it silently, so /sabr/init had nothing to answer with and
        ISA reported "Download failed, no data" three times before giving
        up. Where the server prepends the initialisation to the first media
        instead -- which it also does -- split_initialisation still finds
        it, so both shapes are handled.
        """
        got = dict((n, v) for n, _w, v in sabr.fields(payload))
        header_id, itag = got.get(1), got.get(3)
        sequence, start = got.get(9), got.get(11)
        if itag is None:
            return
        if sequence is None:
            # Field 8 is 1 on these and field 9 absent, on both tracks of an
            # on-demand title:
            #   {1: 0, 2: 11, 3: 150, 4: ..., 6: 0, 8: 1, 10: 32512, ...}
            # Logged once per itag rather than per response, since asking
            # repeatedly used to print it thirty times in one playback.
            if itag not in self._announced:
                self._announced.add(itag)
                kodiutils.log("sabr session: initialisation header for itag "
                              "%s: %s"
                              % (itag, {n: (len(v) if isinstance(v, bytes)
                                            else v)
                                        for n, _w, v in sabr.fields(payload)}))
        # Whatever the server chose from the sets we offered -- the
        # manifest is built from what arrives, not from what we hoped for.
        self._open[header_id] = [itag, sequence, bytearray()]
        if start is not None and sequence is not None:
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
        if sequence is None:
            # An initialisation sent on its own: no moof to split at, so the
            # whole part is the initialisation.
            if not head and data:
                self.initialisation.setdefault(itag, data)
            return None
        body = media or data
        self.segments.setdefault(itag, {})[sequence] = body
        # The boxes of the first two fragments of each track, once. On the
        # DASH path fragment 0 of an audio track is a clear lead with no
        # saiz/saio and fragment 1 carries them; a fragment the bridge has
        # assembled wrongly would differ here, and "Decrypt Sample returns
        # failure" is otherwise indistinguishable from a key problem.
        seen = self._boxed.setdefault(itag, 0)
        if seen < 2:
            self._boxed[itag] = seen + 1
            try:
                kodiutils.log("sabr session: itag %s fragment %s (%d bytes): %s"
                              % (itag, sequence, len(body),
                                 " ".join(mp4.box_tree(body))))
            except Exception as exc:
                kodiutils.log("sabr session: could not walk itag %s: %s"
                              % (itag, exc))
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
        """Segment `sequence` of `itag`, pumping the session until it lands.

        Another thread may be pumping for its own track and land this one on
        the way, so the cache is checked before every exchange rather than
        only before the first.
        """
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
            if sequence in (self.segments.get(itag) or {}):
                return self.segments[itag][sequence]
        # What the session actually holds, so a stall says whether the
        # segment never arrived or was never asked for correctly.
        held = sorted(self.segments.get(itag) or {})
        kodiutils.log("sabr session: gave up waiting for %d of %d; holds %s"
                      % (sequence, itag,
                         "%s..%s (%d)" % (held[0], held[-1], len(held))
                         if held else "nothing"))
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
