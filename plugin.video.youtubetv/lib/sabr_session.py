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


def box_span(data):
    """(bytes accounted for by top-level boxes, count), walking the sizes.

    A fragment can list moof and mdat and still be wrong: box_tree reads
    headers, so a truncated or over-long mdat looks identical to a complete
    one. The declared sizes have to add up to the fragment, and if they do
    not then the bytes are being assembled wrongly -- which is the one
    remaining way to hold the right key, the right IVs and still get
    kDecryptError out of the CDM.
    """
    pos = count = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        # A size that runs past the buffer is itself the finding: stop and
        # report where it claimed to end, rather than walking into nonsense.
        if size < 8 or pos + size > len(data):
            return (pos + size if size >= 8 else pos), count
        pos += size
        count += 1
    return pos, count


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
                 client_version, post, live=True, po_token=b"",
                 max_height=1080):
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
        # The browser sends one in every request: streamerContext subfield 2
        # of a captured body holds 85 bytes that base64url back to the same
        # shape as the addon's po_token setting. Ours has always sent none,
        # which the endpoint tolerated until it did not -- twelve exchanges
        # in a row answered with an empty-bodied 403.
        self.po_token = po_token or b""

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
        self._reinit = {}
        # itag -> height, so the itag ISA fetches can be turned into the
        # height cap the request carries. Filled in by the bridge; empty
        # means nothing to steer with.
        self.heights = {}
        # What ClientAbrState field 59 asks for, and what it started as.
        self.ceiling = max_height
        self.wanted_height = max_height
        self.adaptive = False
        # ISA reads audio and video on separate threads and both drive this
        # one session. Two fetches at once interleave their MEDIA parts
        # through the same _open map and the same echo, so a segment can be
        # assembled from two responses or lost entirely -- which is what
        # "gave up waiting for 3 of 150" looked like from the outside.
        self._lock = threading.Lock()

    # -- the conversation ------------------------------------------------

    def want(self, itag):
        """Ask for the quality tier this rendition sits in, from here on.

        Not by narrowing what is offered. Fields 16 and 17 are what the
        client can play, and offering one video format is answered
        sabr.no_video_selected -- measured twice now, most recently with
        every key id in place and the manifest naming nine renditions, so
        it is the request the endpoint objects to and not the pick.

        The client says what it wants through ClientAbrState instead. Field
        59 is the height cap, and the browser sends 1080 in it; that is the
        knob a server-driven ABR gives the client, so this turns "ISA
        fetched itag 224" into "cap the state at 1080" and lets the server
        choose within it, which is the arrangement SABR is built around.

        Audio has no equivalent field, so an audio itag changes nothing.
        """
        if not self.adaptive:
            return False
        height = self.heights.get(itag) or 0
        if not height or height == self.wanted_height:
            return False
        kodiutils.log("sabr session: the player fetched itag %s (%dp), so "
                      "the abr state asks for %dp rather than %dp"
                      % (itag, height, height, self.wanted_height))
        self.wanted_height = height
        return True

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
        try:
            return self._exchange()
        except SabrError as exc:
            if self.wanted_height == self.ceiling:
                raise
            # The cap the player asked for was refused. Put it back where it
            # started rather than failing the segment, and stop moving it.
            kodiutils.log("sabr session: the endpoint refused an abr state "
                          "asking for %dp (%s) -- back to %dp and leaving it "
                          "there" % (self.wanted_height, str(exc).strip(),
                                     self.ceiling))
            self.wanted_height = self.ceiling
            self.adaptive = False
            return self._exchange()

    def _exchange(self):
        body = sabr.build_request(
            self.config, audio=self.audio, video=self.video,
            player_time_ms=self.position,
            max_height=self.wanted_height,
            buffered=self._buffered(),
            context=sabr.streamer_context(info=self.info,
                                          po_token=self.po_token,
                                          echo=self.echo),
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
            # A browser SABR capture of an audio track (itag 149) shows every
            # media payload opening with its own ftyp and moov, not just the
            # first. Keeping only the first one -- which setdefault does --
            # is right if they never change and silently wrong if the track
            # rotates its key, since the moov is where tenc lives and tenc is
            # where the KID and the IV size come from. So say when a payload
            # brings its own, and whether it agrees with the one being
            # served.
            known = self.initialisation.get(itag)
            if known is None:
                self.initialisation[itag] = head
                kodiutils.log("sabr session: itag %s took its initialisation "
                              "from the payload for sequence %s (%d bytes): %s"
                              % (itag, sequence, len(head),
                                 mp4.track_encryption(head)))
            elif head != known and self._reinit.get(itag, 0) < 3:
                self._reinit[itag] = self._reinit.get(itag, 0) + 1
                kodiutils.log("sabr session: itag %s sequence %s carries a "
                              "DIFFERENT initialisation (%d bytes vs %d): "
                              "%s vs %s"
                              % (itag, sequence, len(head), len(known),
                                 mp4.track_encryption(head),
                                 mp4.track_encryption(known)))
            elif self._reinit.get(itag, 0) < 3:
                self._reinit[itag] = self._reinit.get(itag, 0) + 1
                kodiutils.log("sabr session: itag %s sequence %s repeats the "
                              "same initialisation (%d bytes)"
                              % (itag, sequence, len(head)))
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
        # Four, not two: the first fragment of a track is a clear lead with
        # no saiz or saio, and two dumps only ever showed that one twice --
        # so nothing here has yet seen an encrypted fragment, which is the
        # only kind that can fail to decrypt.
        seen = self._boxed.setdefault(itag, 0)
        if seen < 4:
            self._boxed[itag] = seen + 1
            try:
                boxes = mp4.box_tree(body)
                names = {b.strip() for b in boxes}
                span, count = box_span(body)
                kodiutils.log("sabr session: itag %s fragment %s (%d bytes) "
                              "%s, boxes account for %d of %d bytes across "
                              "%d box(es)%s: %s"
                              % (itag, sequence, len(body),
                                 "ENCRYPTED (saiz/saio present)"
                                 if {"saiz", "saio"} & names else
                                 "clear (no saiz/saio)",
                                 span, len(body), count,
                                 "" if span == len(body) else
                                 "  << MISMATCH: %+d" % (span - len(body)),
                                 " ".join(boxes)))
                # An encrypted fragment is the only kind that can fail to
                # decrypt, and every parameter the CDM is given about it
                # comes from saiz/saio. Measure where that region is
                # instead of assuming the bridge served it whole.
                if {"saiz", "saio"} & names:
                    kodiutils.log("sabr session: itag %s fragment %s aux: %s"
                                  % (itag, sequence,
                                     mp4.aux_report(
                                         body,
                                         self.initialisation.get(itag, b""))))
            except Exception as exc:
                kodiutils.log("sabr session: could not walk itag %s: %s"
                              % (itag, exc))
        return (itag, sequence)

    # -- what the bridge asks for ----------------------------------------

    def initialisation_for(self, itag):
        """The init segment, fetching until one has been seen.

        Trimmed to ftyp and moov: what SABR calls the initialisation also
        carries the sidx, which the file counts as its index and the DASH
        path never puts in front of the media.
        """
        for _ in range(PUMP_LIMIT):
            if itag in self.initialisation:
                return mp4.movie_header(self.initialisation[itag])
            if not self.fetch():
                time.sleep(1)
        return mp4.movie_header(self.initialisation.get(itag, b""))

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

    def prime(self, minimum=2):
        """Fetch until every track holds `minimum` segments, or give up.

        A manifest needs a number to start from, and a freshly opened
        session has none: the service opens it on the first request, which
        is the manifest request.

        Two segments, not one, because the manifest also has to state how
        long a fragment is, and one segment gives nothing to measure -- the
        duration then fell back to a five second constant while the
        fragments were four times that, which is a timeline ISA cannot map
        onto the media it receives.
        """
        for attempt in range(PUMP_LIMIT):
            if self.segments and all(len(held) >= minimum
                                     for held in self.segments.values()):
                return True
            if not self.fetch() and attempt:
                time.sleep(1)
        counts = {itag: len(held) for itag, held in self.segments.items()}
        kodiutils.log("sabr session: primed %d times, holding %s"
                      % (PUMP_LIMIT, counts or "nothing"))
        return bool(self.segments)

    def first_sequence(self, itag):
        held = self.segments.get(itag) or {}
        return min(held) if held else 0
