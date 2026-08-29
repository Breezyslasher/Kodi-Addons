"""Just enough ISO-BMFF to read a track's key id out of its init segment.

The key id we need per Representation is in the media, not only in the licence.
Every encrypted track's init segment carries a TrackEncryptionBox naming the
default_KID its samples are encrypted with -- the same value ISA's sample
reader takes for decryption. Reading it makes the manifest correct on a
title's first play, where waiting for a licence to come back cannot.
"""

# TrackEncryptionBox, from ISO/IEC 23001-7:
#
#     aligned(8) class TrackEncryptionBox extends FullBox('tenc', version, 0) {
#       unsigned int(8) reserved = 0;
#       if (version == 0) { unsigned int(8) reserved = 0; }
#       else { unsigned int(4) default_crypt_byte_block;
#              unsigned int(4) default_skip_byte_block; }
#       unsigned int(8) default_isProtected;
#       unsigned int(8) default_Per_Sample_IV_Size;
#       unsigned int(8)[16] default_KID;
#
# So from the start of the box: 4 size + 4 type + 4 version/flags + 4 of the
# fields above = 16, then the key id. Both versions put it at the same offset.
_KID_OFFSET = 16
_KID_LEN = 16

# A tenc holding only the key id is 32 bytes; version 1 with a pattern and a
# constant IV runs longer. Anything outside this range is a coincidence in
# encrypted payload rather than a box header, and the length check is what
# stops us reading one.
_MIN_BOX = 32
_MAX_BOX = 64


def _boxes_named(data, name):
    start = 0
    while True:
        found = data.find(name, start)
        if found < 0:
            return
        start = found + 1
        if found >= 4:
            yield found - 4


def default_kid(data):
    """The track's default_KID as lowercase hex, or "" if there is none.

    An all-zero key id is treated as absent. Apple ships those and they are a
    documented trap -- ISA's "no key id" fallback does not trigger on zeros, so
    it would decrypt with a key that was never licensed. Better to have none
    and let the licence supply one than to name a key that cannot work.
    """
    if not data:
        return ""
    for box in _boxes_named(data, b"tenc"):
        size = int.from_bytes(data[box:box + 4], "big")
        if not _MIN_BOX <= size <= _MAX_BOX:
            continue
        if box + size > len(data):
            continue
        kid = data[box + _KID_OFFSET:box + _KID_OFFSET + _KID_LEN]
        if len(kid) == _KID_LEN and any(kid):
            return kid.hex()
    return ""


def _box_at(data, offset):
    """(size, type) of the box starting at ``offset``, or (0, b"") if none."""
    if offset + 8 > len(data):
        return 0, b""
    return int.from_bytes(data[offset:offset + 4], "big"), data[offset + 4:offset + 8]


def first_box_type(data):
    """The four-character type of the box this buffer starts with."""
    _, kind = _box_at(data, 0)
    try:
        return kind.decode("ascii")
    except Exception:
        return repr(kind)


def subsegments(sidx, sidx_start):
    """Byte ranges of each subsegment described by a SegmentIndex box.

    ``sidx_start`` is the offset of the sidx box within the file, because the
    references are relative to the first byte after it plus ``first_offset``.
    Returns [(offset, size, duration_ticks)] in file order, and [] if the
    buffer is not a sidx this understands.

    This exists to answer one question with bytes rather than inference: when
    playback dies one subsegment in, is the data at the *second* subsegment's
    offset actually the start of a movie fragment?
    """
    size, kind = _box_at(sidx, 0)
    if kind != b"sidx" or size < 12 or size > len(sidx):
        return []
    version = sidx[8]
    pos = 12 + 8  # version/flags, reference_ID, timescale
    if version == 0:
        pos += 8   # earliest_presentation_time + first_offset, 32 bit
        first_offset = int.from_bytes(sidx[20:24], "big")
    else:
        pos += 16  # both 64 bit
        first_offset = int.from_bytes(sidx[24:32], "big")
    if pos + 4 > len(sidx):
        return []
    count = int.from_bytes(sidx[pos + 2:pos + 4], "big")
    pos += 4

    out = []
    offset = sidx_start + size + first_offset
    for _ in range(count):
        if pos + 12 > len(sidx):
            break
        word = int.from_bytes(sidx[pos:pos + 4], "big")
        # The top bit distinguishes a reference to another sidx from one to
        # media; only media carries samples, and a stream mixing the two would
        # make these offsets mean something else.
        if word >> 31:
            return []
        ref_size = word & 0x7FFFFFFF
        duration = int.from_bytes(sidx[pos + 4:pos + 8], "big")
        out.append((offset, ref_size, duration))
        offset += ref_size
        pos += 12
    return out


_CONTAINERS = (b"moof", b"traf", b"moov", b"trak", b"mdia", b"minf",
               b"stbl", b"sinf", b"schi", b"edts", b"mvex")

# stsd is a FullBox whose entries are sample descriptions, not plain boxes: a
# four byte version/flags and a four byte count come first, and a visual or
# audio entry carries a fixed header before its own children. Walking it as an
# ordinary container would read the count as a box size, so it gets its own
# offsets. The entry type is the thing worth seeing -- enca and encv mean the
# track declares itself encrypted, mp4a and avc1 mean it does not.
_STSD_ENTRY_HEADER = {b"enca": 36, b"mp4a": 36, b"encv": 86, b"avc1": 86,
                      b"avc3": 86, b"hev1": 86, b"hvc1": 86}


def box_tree(data, limit=64):
    """The box types in this buffer, containers descended into.

    Fragment 0 of a track decodes and fragment 1 does not, from bytes that are
    provably a movie fragment at the right offset, with the right key, on a
    private CDM session. That leaves how the samples are described, and the
    boxes present in each fragment are the cheapest way to see it: a traf
    carrying senc/saiz/saio is telling the decrypter where the per-sample IVs
    are, and one that is not is telling it there is nothing to decrypt.
    """
    out = []

    def walk(start, end, depth):
        pos = start
        while pos + 8 <= end and len(out) < limit:
            size, kind = _box_at(data, pos)
            if size < 8:
                # A 1 means the real size is a 64-bit field after the type;
                # a 0 means "to the end". Neither appears in a fragment here,
                # and guessing past one would invent boxes that do not exist.
                out.append("%s?%d" % (kind.decode("ascii", "replace"), size))
                return
            name = kind.decode("ascii", "replace")
            out.append(("  " * depth) + name)
            if kind in _CONTAINERS:
                walk(pos + 8, min(pos + size, end), depth + 1)
            elif kind == b"stsd":
                walk(pos + 16, min(pos + size, end), depth + 1)
            elif kind in _STSD_ENTRY_HEADER:
                walk(pos + _STSD_ENTRY_HEADER[kind],
                     min(pos + size, end), depth + 1)
            pos += size

    walk(0, len(data), 0)
    return out



def find_box(data, path, start=0, end=None):
    """(offset, size) of the box reached by following ``path``, or (None, None).

    ``path`` is a list of four-byte type names, outermost first.
    """
    end = len(data) if end is None else end
    pos = start
    want = path[0]
    while pos + 8 <= end:
        size, kind = _box_at(data, pos)
        if size < 8 or pos + size > end:
            return None, None
        if kind == want:
            if len(path) == 1:
                return pos, size
            return find_box(data, path[1:], pos + 8, pos + size)
        pos += size
    return None, None


def _u(data, at, width):
    return int.from_bytes(data[at:at + width], "big")


def crypto_info(fragment, absolute):
    """How this movie fragment says its samples are encrypted.

    ``absolute`` is the fragment's byte offset in the whole file, and it is the
    point of the exercise: saio gives the position of the per-sample IVs, and
    that position is either relative to this fragment or absolute in the file.
    InputStream Adaptive hands the decrypter a stream that begins at the
    subsegment, not at the file, so an absolute offset would be read from the
    wrong place -- wrong IVs, plausible-looking output, no error anywhere. This
    reports the number so the two cases can be told apart instead of argued
    about.
    """
    out = []

    tfhd, size = find_box(fragment, [b"moof", b"traf", b"tfhd"])
    if tfhd is not None:
        flags = _u(fragment, tfhd + 9, 3)
        note = "tfhd flags=0x%06x" % flags
        if flags & 0x000001:
            note += " base_data_offset=%d" % _u(fragment, tfhd + 16, 8)
        else:
            note += " (no base_data_offset)"
        out.append(note)

    saiz, size = find_box(fragment, [b"moof", b"traf", b"saiz"])
    if saiz is not None:
        at = saiz + 12
        if _u(fragment, saiz + 9, 3) & 1:
            at += 8
        out.append("saiz default_size=%d sample_count=%d"
                   % (_u(fragment, at, 1), _u(fragment, at + 1, 4)))

    saio, size = find_box(fragment, [b"moof", b"traf", b"saio"])
    if saio is None:
        out.append("no saio")
        return "; ".join(out)

    version = fragment[saio + 8]
    flags = _u(fragment, saio + 9, 3)
    at = saio + 12
    if flags & 1:
        at += 8
    count = _u(fragment, at, 4)
    at += 4
    width = 8 if version else 4
    offsets = [_u(fragment, at + i * width, width) for i in range(min(count, 3))]
    out.append("saio v%d flags=0x%06x count=%d offsets=%s"
               % (version, flags, count, offsets))
    if offsets:
        first = offsets[0]
        reading = ("looks ABSOLUTE in the file"
                   if first >= absolute
                   else "looks relative to this fragment")
        out.append("fragment at %d, first aux offset %d -> %s"
                   % (absolute, first, reading))
    return "; ".join(out)


def track_encryption(init):
    """What the init segment's tenc declares: (is_protected, iv_size, kid).

    The per-sample IV size matters as much as the key does. saiz says how many
    bytes of auxiliary information each sample has, tenc says how many of them
    are the IV, and a decrypter that disagrees with the file about that reads
    the right bytes in the wrong shape -- output that is the correct length,
    entirely wrong, and reported by nobody.
    """
    for box in _boxes_named(init, b"tenc"):
        size = int.from_bytes(init[box:box + 4], "big")
        if not _MIN_BOX <= size <= _MAX_BOX or box + size > len(init):
            continue
        return init[box + 14], init[box + 15], init[box + 16:box + 32].hex()
    return None, None, ""



def _saiz_sizes(fragment, saiz):
    """(default_size, sample_count, [per-sample sizes]) from a saiz box."""
    flags = _u(fragment, saiz + 9, 3)
    at = saiz + 12
    if flags & 1:
        at += 8
    default = _u(fragment, at, 1)
    count = _u(fragment, at + 1, 4)
    at += 5
    sizes = [] if default else [_u(fragment, at + i, 1) for i in range(count)]
    return default, count, sizes


def _saio_offsets(fragment, saio):
    """Every entry of a saio box, as integers."""
    version = fragment[saio + 8]
    flags = _u(fragment, saio + 9, 3)
    at = saio + 12
    if flags & 1:
        at += 8
    count = _u(fragment, at, 4)
    at += 4
    width = 8 if version else 4
    return [_u(fragment, at + i * width, width) for i in range(count)]


def aux_report(fragment, init=b""):
    """Whether this fragment's sample auxiliary information is reachable.

    Bento4 seeks to ``moof_offset + saio[0]`` in InputStream Adaptive's stream
    and reads ``saiz`` bytes per sample: the IV, and optionally a subsample
    map. Everything the CDM is then told about a sample comes from those
    bytes. If the region they name falls outside the fragment the bridge
    served, or if saiz counts a different number of samples than trun does,
    the decrypter is handed the wrong shape and the CDM answers
    kDecryptError -- which looks exactly like a key problem and is not one.
    So measure it: where the region starts, where it ends, how big the
    fragment is, and what the first entry actually contains.
    """
    out = []
    protected, iv_size, kid = (None, None, "")
    if init:
        protected, iv_size, kid = track_encryption(init)
        out.append("tenc protected=%s iv_size=%s kid=%s"
                   % (protected, iv_size, kid[:16]))

    moof, moof_size = find_box(fragment, [b"moof"])
    out.append("moof size=%s fragment=%d" % (moof_size, len(fragment)))

    samples = 0
    data_offset = None
    total_bytes = 0
    for trun in _boxes_named(fragment, b"trun"):
        flags = _u(fragment, trun + 9, 3)
        count = _u(fragment, trun + 12, 4)
        samples += count
        at = trun + 16
        if flags & 0x000001:
            offset = _u(fragment, at, 4)
            if offset >= 0x80000000:
                offset -= 0x100000000
            data_offset = offset if data_offset is None else data_offset
            at += 4
        if flags & 0x000004:
            at += 4
        width = (4 if flags & 0x000100 else 0) + (4 if flags & 0x000200 else 0) \
              + (4 if flags & 0x000400 else 0) + (4 if flags & 0x000800 else 0)
        if flags & 0x000200:
            size_at = at + (4 if flags & 0x000100 else 0)
            for i in range(count):
                total_bytes += _u(fragment, size_at + i * width, 4)
    out.append("trun samples=%d data_offset=%s sample bytes=%d"
               % (samples, data_offset, total_bytes))
    if data_offset is not None:
        out.append("samples span %d..%d of %d"
                   % (data_offset, data_offset + total_bytes, len(fragment)))

    saiz, _size = find_box(fragment, [b"moof", b"traf", b"saiz"])
    saio, _size = find_box(fragment, [b"moof", b"traf", b"saio"])
    if saiz is None or saio is None:
        out.append("no saiz/saio")
        return "; ".join(out)

    default, count, sizes = _saiz_sizes(fragment, saiz)
    offsets = _saio_offsets(fragment, saio)
    total = default * count if default else sum(sizes)
    out.append("saiz default=%d count=%d%s"
               % (default, count,
                  "" if default else " sizes[:4]=%s" % sizes[:4]))
    out.append("saio count=%d offsets[:3]=%s" % (len(offsets), offsets[:3]))
    if count != samples:
        out.append("<< saiz counts %d samples, trun counts %d" % (count, samples))

    if not offsets:
        return "; ".join(out)
    start = offsets[0]
    end = start + total
    out.append("aux region %d..%d of %d -> %s"
               % (start, end, len(fragment),
                  "inside the fragment" if end <= len(fragment)
                  else "PAST THE END by %d" % (end - len(fragment))))
    if end <= len(fragment):
        first = default or (sizes[0] if sizes else 0)
        out.append("first entry (%d bytes) %s"
                   % (first, fragment[start:start + first].hex()))
    return "; ".join(out)


def movie_header(data):
    """ftyp and moov only, dropping whatever the file keeps after them.

    A SABR initialisation part is not the file's initialisation. Measured
    against the same track fetched as a file: initRange is 0..1711 and
    indexRange 1712..3483, and SABR sends all 3484 bytes as one part --
    ftyp, moov, and the sidx that belongs to the index. The DASH path that
    decrypts this track hands InputStream Adaptive the first 1712 bytes and
    reads the index separately, so the bridge should hand it the same
    thing. The media itself needs no editing: fragments 1 and 2 came off
    SABR byte for byte identical to the file.
    """
    out = bytearray()
    seen_moov = False
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if size < 8 or pos + size > len(data):
            break
        if kind in (b"ftyp", b"moov"):
            out += data[pos:pos + size]
            seen_moov = seen_moov or kind == b"moov"
        elif seen_moov:
            break
        pos += size
    return bytes(out) if seen_moov else data


def _trun_sample_sizes(fragment, trun):
    """Every sample's size out of a trun box, or [] if it does not carry them.

    trun is a full box: version, flags, sample_count, then optional
    data_offset and first_sample_flags, then per-sample fields in a fixed
    order. Only the sizes are wanted, so the others are stepped over by
    width rather than decoded.
    """
    flags = _u(fragment, trun + 9, 3)
    count = _u(fragment, trun + 12, 4)
    at = trun + 16
    if flags & 0x000001:            # data-offset-present
        at += 4
    if flags & 0x000004:            # first-sample-flags-present
        at += 4
    if not flags & 0x000200:        # sample-size-present
        return []
    stride = ((4 if flags & 0x000100 else 0) + 4
              + (4 if flags & 0x000400 else 0)
              + (4 if flags & 0x000800 else 0))
    before = 4 if flags & 0x000100 else 0
    return [_u(fragment, at + i * stride + before, 4) for i in range(count)]


def _trun_data_offset(fragment, trun):
    """(offset of the field, its value) if trun carries a data offset."""
    flags = _u(fragment, trun + 9, 3)
    if not flags & 0x000001:
        return None, 0
    at = trun + 16
    value = _u(fragment, at, 4)
    if value >= 0x80000000:
        value -= 0x100000000
    return at, value


def explicit_subsamples(fragment):
    """Rewrite a fragment's encryption signalling to name subsamples.

    YouTube TV encrypts audio whole-sample: the auxiliary data is one IV per
    sample and nothing else, which CENC spells as a subsample count of zero
    and every implementation is then supposed to read as "all of it". Video
    is encrypted with subsamples -- a clear NAL header and an encrypted
    payload -- and carries an explicit entry per sample.

    That is the one difference between the track InputStream Adaptive 21.5.22
    plays and the track it turns to noise. This says the same thing the other
    way: one subsample per sample, zero clear bytes, the whole sample
    encrypted. The ciphertext is untouched -- only how it is described.

    Returns the fragment unchanged if there is nothing to do or anything is
    not as expected, because serving a fragment we have half rewritten is
    worse than serving the original.
    """
    moof, moof_size = find_box(fragment, [b"moof"])
    if moof is None:
        return fragment
    traf, traf_size = find_box(fragment, [b"moof", b"traf"])
    if traf is None:
        return fragment
    saiz, saiz_size = find_box(fragment, [b"moof", b"traf", b"saiz"])
    saio, saio_size = find_box(fragment, [b"moof", b"traf", b"saio"])
    senc, _senc_size = find_box(fragment, [b"moof", b"traf", b"senc"])
    trun, _trun_size = find_box(fragment, [b"moof", b"traf", b"trun"])
    if saiz is None or saio is None or trun is None or senc is not None:
        return fragment

    default, count, sizes = _saiz_sizes(fragment, saiz)
    if not default or sizes or not count:
        # Per-sample aux sizes mean subsample entries are already there.
        return fragment
    iv_size = default
    if iv_size not in (8, 16):
        return fragment

    offsets = _saio_offsets(fragment, saio)
    if len(offsets) != 1:
        return fragment
    aux = moof + offsets[0]
    if aux + count * iv_size > len(fragment):
        return fragment

    sample_sizes = _trun_sample_sizes(fragment, trun)
    if len(sample_sizes) != count:
        return fragment

    # senc: full box, flags bit 1 says subsample data follows each IV.
    body = bytearray()
    body += b"\x00"                                   # version
    body += (0x000002).to_bytes(3, "big")             # flags
    body += count.to_bytes(4, "big")
    for index in range(count):
        body += fragment[aux + index * iv_size:aux + (index + 1) * iv_size]
        body += (1).to_bytes(2, "big")                # one subsample
        body += (0).to_bytes(2, "big")                # no clear bytes
        body += int(sample_sizes[index]).to_bytes(4, "big")
    senc_box = (len(body) + 8).to_bytes(4, "big") + b"senc" + bytes(body)

    # The aux data itself sits at the front of the mdat payload, not in the
    # moof -- a real fragment reads moof size 1853, saio offset 1861, and the
    # samples starting at 5301, so the 3440 bytes between are the IVs at the
    # head of the mdat. It is left exactly where it is, unreferenced, rather
    # than splicing the mdat: the samples must not move relative to each
    # other, and a few kilobytes of ignored bytes cost nothing.
    #
    # Dropping saiz and saio and adding senc does change the moof's size, and
    # trun's data offset is measured from the start of the moof, so that has
    # to move by the same amount.
    kept = bytearray()
    cursor = traf + 8
    for start, size in sorted([(saiz, saiz_size), (saio, saio_size)]):
        if start < cursor:
            return fragment
        kept += fragment[cursor:start]
        cursor = start + size
    kept += fragment[cursor:traf + traf_size]
    new_traf_body = bytes(kept) + senc_box

    delta = (len(new_traf_body) + 8) - traf_size
    new_traf = (len(new_traf_body) + 8).to_bytes(4, "big") + b"traf" + new_traf_body
    new_moof_body = (fragment[moof + 8:traf] + new_traf
                     + fragment[traf + traf_size:moof + moof_size])
    new_moof = ((len(new_moof_body) + 8).to_bytes(4, "big") + b"moof"
                + new_moof_body)

    rebuilt = bytearray(fragment[:moof] + new_moof + fragment[moof + moof_size:])
    # Re-find trun in the rebuilt moof and correct its data offset.
    new_trun, _ = find_box(bytes(rebuilt), [b"moof", b"traf", b"trun"])
    if new_trun is None:
        return fragment
    field, value = _trun_data_offset(bytes(rebuilt), new_trun)
    if field is not None:
        rebuilt[field:field + 4] = ((value + delta) & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(rebuilt)
