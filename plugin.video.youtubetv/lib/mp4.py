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
