"""Apple's X-Apple-HC proof-of-work ("hashcash").

Apple's web sign-in requires an X-Apple-HC header on /signin/complete. It is a
hashcash stamp of the form::

    1:<bits>:<timestamp>:<challenge>::<counter>

where <bits>/<challenge> come from the X-Apple-HC-Bits / X-Apple-HC-Challenge
response headers returned earlier in the flow, and <counter> is brute-forced so
that SHA1(stamp) begins with <bits> zero bits. bits is small (10-12) so this is
a few thousand SHA1 hashes -- negligible cost.
"""

import hashlib
import time


def _leading_zero_bits(data):
    count = 0
    for byte in data:
        if byte == 0:
            count += 8
            continue
        mask = 0x80
        while mask:
            if byte & mask:
                return count
            count += 1
            mask >>= 1
        break
    return count


def make_stamp(bits, challenge, timestamp=None, max_iterations=5_000_000):
    """Return a valid X-Apple-HC stamp for the given bits/challenge."""
    bits = int(bits)
    if timestamp is None:
        timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    prefix = "1:%d:%s:%s::" % (bits, timestamp, challenge)
    counter = 0
    while counter < max_iterations:
        candidate = prefix + str(counter)
        digest = hashlib.sha1(candidate.encode("utf-8")).digest()
        if _leading_zero_bits(digest) >= bits:
            return candidate
        counter += 1
    # Should never happen for the small bit counts Apple uses.
    return prefix + "0"
