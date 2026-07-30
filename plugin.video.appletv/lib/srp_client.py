"""Self-contained SRP-6a client for Apple ID web sign-in.

Apple's web sign-in (idmsa.apple.com/appleauth/auth) uses SRP-6a with the
RFC 5054 2048-bit group and SHA-256, plus a PBKDF2 pre-hash of the password
whose salt and iteration count are returned by the server's /signin/init
response ("s2k" and "s2k_fo" protocols).

This module implements only the client half of SRP so the addon does not need
the external ``srp`` PyPI package (which is awkward to ship inside a Kodi
addon). It mirrors the flow used by well-known Apple ID clients.

No secret ever leaves the device except the public ephemeral A and the client
proof M1 -- the password itself is never transmitted.
"""

import hashlib
import hmac
import os
from binascii import hexlify

# RFC 5054 2048-bit group.
_N_HEX = (
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050"
    "A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50"
    "E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8"
    "55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B"
    "CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748"
    "544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6"
    "AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB6"
    "94B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73"
)
_N = int(_N_HEX, 16)
_g = 2
_HASH = hashlib.sha256
_HASH_LEN = 32


def _to_bytes(value):
    length = (value.bit_length() + 7) // 8
    return value.to_bytes(length, "big")


def _pad(value):
    """Left-pad an integer to the byte length of N."""
    n_len = (_N.bit_length() + 7) // 8
    return _to_bytes(value).rjust(n_len, b"\x00")


def _hash(*chunks):
    h = _HASH()
    for chunk in chunks:
        if isinstance(chunk, int):
            chunk = _to_bytes(chunk)
        h.update(chunk)
    return h.digest()


def _hash_int(*chunks):
    return int(hexlify(_hash(*chunks)), 16)


class SRPClient(object):
    """One SRP session. Create per login attempt."""

    def __init__(self, account_name):
        self.account_name = account_name
        self._a = int(hexlify(os.urandom(32)), 16)
        self.A = pow(_g, self._a, _N)
        # k = H(N | PAD(g))
        self._k = _hash_int(_pad(_N), _pad(_g))
        self._M1 = None
        self._K = None
        self._expected_HAMK = None

    def public_a_bytes(self):
        return _to_bytes(self.A)

    def _derive_password_key(self, password, salt, iterations, protocol):
        """Apple's PBKDF2 pre-hash of the password."""
        hashed = hashlib.sha256(password.encode("utf-8")).digest()
        if protocol == "s2k_fo":
            hashed = hexlify(hashed)
        return hashlib.pbkdf2_hmac("sha256", hashed, salt, iterations, _HASH_LEN)

    def process_challenge(self, password, salt, iterations, protocol, B_bytes):
        """Given the server's init response, return the client proof M1.

        Raises ValueError if the server public value B is invalid.
        """
        B = int(hexlify(B_bytes), 16)
        if B % _N == 0:
            raise ValueError("Invalid server public value B")

        derived = self._derive_password_key(password, salt, iterations, protocol)
        # Apple computes x WITHOUT the username ("no username in x"):
        #   x = H(salt | H(':' | P))
        # Including the account name here makes the proof mismatch and Apple
        # rejects sign-in with error -20101 even when the password is correct.
        inner = _hash(b":", derived)
        x = _hash_int(salt, inner)

        # u = H(PAD(A) | PAD(B))
        u = _hash_int(_pad(self.A), _pad(B))
        if u == 0:
            raise ValueError("Invalid scrambling parameter u")

        # S = (B - k * g^x) ^ (a + u*x) mod N
        g_x = pow(_g, x, _N)
        base = (B - (self._k * g_x)) % _N
        S = pow(base, self._a + u * x, _N)
        self._K = _hash(_to_bytes(S))

        # M1 = H(H(N) xor H(g) | H(I) | s | A | B | K)
        h_n = _hash_int(_N)
        h_g = _hash_int(_g)
        h_ng = _to_bytes(h_n ^ h_g)
        h_i = _hash(self.account_name.encode("utf-8"))
        self._M1 = _hash(h_ng, h_i, salt, _pad(self.A), _pad(B), self._K)
        # Expected server proof: H(A | M1 | K)
        self._expected_HAMK = _hash(_pad(self.A), self._M1, self._K)
        return self._M1

    def proof_bytes(self):
        if self._M1 is None:
            raise RuntimeError("process_challenge must be called first")
        return self._M1

    def expected_server_proof(self):
        return self._expected_HAMK

    def verify_server_proof(self, hamk_bytes):
        if self._expected_HAMK is None:
            return False
        return hmac.compare_digest(self._expected_HAMK, hamk_bytes)
