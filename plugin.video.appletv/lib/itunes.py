"""The iTunes side of an Apple account: what you own, and where you were.

Apple TV+ and iTunes purchases are two different worlds behind one app. The
rest of this addon speaks the modern UTS API on tv.apple.com; purchases live
on the old store protocol, and nothing on the website exposes them at all --
which is why this took a capture of Apple's Windows client to find.

The chain, all of it observed in that capture:

    POST buy.itunes.apple.com/WebObjects/MZFinance.woa/wa/authenticate
        A plist of appleId, password and a machine guid. No SRP, no OAuth --
        the legacy store login. Returns passwordToken and dsPersonId.

    POST pd.itunes.apple.com/WebObjects/MZPurchaseDaap.woa/purchase/databases/101/items
        The library itself, as gzipped DMAP: a tag/length/value format from
        the iTunes sharing days. Each owned title arrives with its UTS
        content id, so a library entry hands straight over to the rest of
        this addon.

    POST upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/getAll
        Resume positions, keyed by adam id. Purchases do not use the
        now-playing service Apple TV+ reports to; they use this key-value
        store instead, where bktm is the position in seconds.

See docs/itunes-library.md for the capture this was built from and for what
is still unverified -- notably whether Apple accepts a guid this addon makes
up rather than one belonging to a registered device.
"""

import gzip
import plistlib
import struct
import time
import uuid
import zlib

from . import kodiutils

STORE_AUTH_URL = "https://buy.itunes.apple.com/WebObjects/MZFinance.woa/wa/authenticate"
LIBRARY_URL = ("https://pd.itunes.apple.com/WebObjects/MZPurchaseDaap.woa"
               "/purchase/databases/101/items")
BOOKKEEPER_GET_ALL = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/getAll"
BOOKKEEPER_PUT = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/put"
BOOKKEEPER_DOMAIN = "com.apple.upp"

STORE_SESSION_CACHE = "itunes_session.json"
STORE_UA = ("AMPLibraryAgent/1.6.4 (Windows 10.0.19045 x64; x64) "
            "Chromium/151.0.4129.59 build/92 (dt:2)")

# DMAP tags carried on each owned title. The names are Apple's; the meanings
# are read off a capture of an account with one film in it.
TAG_TITLE = "minm"          # "Despicable Me"
TAG_CONTENT_ID = "ajci"     # umc.cmc.* -- the id the rest of the addon uses
TAG_ADAM_ID = "aeSI"        # numeric store id; also the bookkeeper's key
TAG_STREAM = "ajhu"         # an HLS playlist url, already carrying its token
TAG_YEAR = "asyr"
TAG_GENRE = "asgn"
TAG_PLOT = "ascn"
TAG_LONG_PLOT = "aslc"
TAG_RATING = "aeCR"         # "mpaa|PG|200|for rude humour and mild action."
TAG_ARTIST = "asar"         # directors, for a film
TAG_ARTWORK = "aecv"
TAG_ARTWORK_ALT = "aeat"
TAG_SHOW = "assn"
TAG_COLLECTION = "asal"
TAG_EXTRAS = "ajEU"         # iTunes Extras, when the title has any

# Containers rather than values, so their payload is more DMAP.
DMAP_CONTAINERS = ("adbs", "mlcl", "mlit", "abro", "msrv", "mccr", "mshl")

# Apple's epoch is 2001-01-01, not 1970.
MAC_EPOCH_OFFSET = 978307200


def _dmap_parse(buf):
    """Walk a DMAP buffer into nested dicts of tag -> value.

    Every element is a four-character tag, a big-endian length and that many
    bytes. Containers hold more elements; everything else is a string or an
    integer, and which it is has to be guessed from the payload because the
    content-code dictionary that would say is not fetched.
    """
    out = {}
    index = 0
    while index + 8 <= len(buf):
        tag = buf[index:index + 4].decode("ascii", "replace")
        try:
            length = struct.unpack(">I", buf[index + 4:index + 8])[0]
        except struct.error:
            break
        body = buf[index + 8:index + 8 + length]
        index += 8 + length
        if tag in DMAP_CONTAINERS:
            child = _dmap_parse(body)
            if tag in out and isinstance(out[tag], list):
                out[tag].append(child)
            elif tag in out:
                out[tag] = [out[tag], child]
            else:
                out[tag] = child
            continue
        if length in (1, 2, 4, 8) and not any(32 <= b < 127 for b in body):
            out[tag] = int.from_bytes(body, "big")
        else:
            out[tag] = body.decode("utf-8", "replace")
    return out


def _dmap_items(root):
    """Every mlit entry under an mlcl, however the parse nested them."""
    listing = (root.get("adbs") or root).get("mlcl") or {}
    entries = listing.get("mlit")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


class ItunesStore(object):
    def __init__(self, session):
        self.session = session
        self.last_error = None

    # -- account ---------------------------------------------------------

    def _guid(self):
        """A stable machine id for this install.

        Apple's own is seven groups of eight hex digits and belongs to a
        registered device. Whether a made-up one is accepted is the open
        question this feature rests on; it is at least kept stable, since a
        new one on every call would look like a new machine each time.
        """
        guid = kodiutils.get_setting("itunes_guid")
        if not guid:
            digits = uuid.uuid4().hex + uuid.uuid4().hex
            guid = ".".join(digits[i:i + 8].upper() for i in range(0, 56, 8))
            kodiutils.set_setting("itunes_guid", guid)
        return guid

    def _headers(self):
        return {"User-Agent": STORE_UA,
                "Content-Type": "application/x-apple-plist",
                "Accept-Language": "en-us",
                "X-Apple-Store-Front": "%s-1,42" % kodiutils.get_setting("storefront")
                                        or "143441-1,42"}

    def sign_in(self, apple_id, password):
        """Log in to the store. Separate from the Apple TV+ sign-in.

        The web client authenticates with SRP and two-factor; this endpoint
        takes the password directly. They are different services and one
        session does not stand in for the other.
        """
        self.last_error = None
        body = plistlib.dumps({"appleId": apple_id, "password": password,
                               "guid": self._guid(), "createSession": True,
                               "rmp": 0})
        try:
            resp = self.session.post(STORE_AUTH_URL, data=body,
                                     headers=self._headers(), timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("iTunes sign-in failed: %s" % exc)
            return False
        if resp.status_code != 200:
            self.last_error = "HTTP %s" % resp.status_code
            kodiutils.log_error("iTunes sign-in -> %s" % resp.status_code)
            return False
        try:
            data = plistlib.loads(resp.content)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        # Apple reports a refusal in the body, not the status line.
        if data.get("status") not in (0, None) or not data.get("dsPersonId"):
            self.last_error = data.get("customerMessage") or "sign-in refused"
            kodiutils.log_error("iTunes sign-in refused: %s" % self.last_error)
            return False
        kodiutils.write_json(STORE_SESSION_CACHE, {
            "dsid": str(data.get("dsPersonId")),
            "password_token": data.get("passwordToken"),
            "apple_id": data.get("accountInfo", {}).get("appleId") or apple_id,
            "stamp": time.time(),
        })
        return True

    def session_info(self):
        return kodiutils.read_json(STORE_SESSION_CACHE, default={}) or {}

    def is_signed_in(self):
        return bool(self.session_info().get("dsid"))

    def sign_out(self):
        kodiutils.write_json(STORE_SESSION_CACHE, {})

    def _store_headers(self):
        info = self.session_info()
        headers = self._headers()
        if info.get("dsid"):
            headers["X-Dsid"] = info["dsid"]
        if info.get("password_token"):
            headers["X-Token"] = info["password_token"]
        return headers

    # -- library ---------------------------------------------------------

    def library(self):
        """Everything the account owns, as catalogue entries.

        Returned in this addon's usual item shape so the same listing code
        handles them, with the UTS content id as the id: a purchase opens and
        plays through the ordinary path from there.
        """
        self.last_error = None
        if not self.is_signed_in():
            self.last_error = "not signed in to the store"
            return []
        body = plistlib.dumps({"guid": self._guid()})
        try:
            resp = self.session.post(LIBRARY_URL, data=body,
                                     headers=self._store_headers(), timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("iTunes library failed: %s" % exc)
            return []
        if resp.status_code != 200:
            self.last_error = "HTTP %s" % resp.status_code
            kodiutils.log_error("iTunes library -> %s" % resp.status_code)
            return []
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception as exc:
                self.last_error = str(exc)
                return []
        root = _dmap_parse(raw)
        items = [self._map(entry) for entry in _dmap_items(root)]
        items = [i for i in items if i]
        kodiutils.log("iTunes library: %d owned title(s), reported %s"
                      % (len(items), (root.get("adbs") or {}).get("mtco")))
        return items

    @staticmethod
    def _map(entry):
        """One DMAP title as a catalogue entry."""
        content_id = entry.get(TAG_CONTENT_ID)
        title = entry.get(TAG_TITLE)
        if not title:
            return None
        rating = entry.get(TAG_RATING) or ""
        # "mpaa|PG|200|for rude humour..." -- the second field is the one shown.
        parts = rating.split("|")
        art = entry.get(TAG_ARTWORK) or entry.get(TAG_ARTWORK_ALT)
        return {
            # Fall back to the store id when a title carries no UTS id: it is
            # still enough to play, since the stream travels with the entry.
            "id": content_id or str(entry.get(TAG_ADAM_ID) or ""),
            "adam_id": str(entry.get(TAG_ADAM_ID) or ""),
            "title": title,
            "sort_title": title,
            "type": "Movie",
            "plot": entry.get(TAG_LONG_PLOT) or entry.get(TAG_PLOT),
            "year": entry.get(TAG_YEAR),
            "genres": [entry[TAG_GENRE]] if entry.get(TAG_GENRE) else [],
            "mpaa": parts[1] if len(parts) > 1 else None,
            "studio": None,
            "show_title": entry.get(TAG_SHOW),
            "art": {"poster": art, "thumb": art, "icon": art} if art else None,
            # Already tokenised, so it plays without resolving anything first.
            "stream_url": entry.get(TAG_STREAM),
            "extras_url": entry.get(TAG_EXTRAS),
        }

    # -- resume ----------------------------------------------------------

    def resume_points(self):
        """Where the account is in each purchase, keyed by adam id.

        Purchases do not appear in the now-playing service the rest of the
        addon reports to. Their positions live in a key-value store instead,
        one deflated binary plist per title, and the whole domain comes back
        in a single request rather than one per title.
        """
        if not self.is_signed_in():
            return {}
        body = plistlib.dumps({"domain": BOOKKEEPER_DOMAIN})
        try:
            resp = self.session.post(BOOKKEEPER_GET_ALL, data=body,
                                     headers=self._store_headers(), timeout=30)
            data = plistlib.loads(resp.content)
        except Exception as exc:
            kodiutils.log_error("iTunes resume points failed: %s" % exc)
            return {}
        points = {}
        for row in data.get("values") or []:
            key = str(row.get("key") or "")
            value = row.get("value")
            if not key or not isinstance(value, (bytes, bytearray)):
                continue
            try:
                inner = plistlib.loads(zlib.decompress(bytes(value), -15))
            except Exception:
                continue
            position = inner.get("bktm")
            if isinstance(position, (int, float)) and position > 0 \
                    and not inner.get("hbpl"):
                points[key] = float(position)
        return points

    def report_position(self, adam_id, position, finished=False):
        """Tell the store where a purchase was left.

        The value is a binary plist, deflated with no zlib header, wrapped in
        an outer plist -- which is how the client sends it.
        """
        if not self.is_signed_in() or not adam_id:
            return False
        inner = plistlib.dumps({
            "bktm": float(max(0.0, position)),
            "hbpl": bool(finished),
            "plct": 1 if finished else 0,
            "tstm": float(time.time() - MAC_EPOCH_OFFSET),
        }, fmt=plistlib.FMT_BINARY)
        deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = deflate.compress(inner) + deflate.flush()
        body = plistlib.dumps({"domain": BOOKKEEPER_DOMAIN,
                               "key": str(adam_id), "value": packed})
        try:
            resp = self.session.post(BOOKKEEPER_PUT, data=body,
                                     headers=self._store_headers(), timeout=15)
            return resp.status_code == 200
        except Exception as exc:
            kodiutils.log_error("iTunes position report failed: %s" % exc)
            return False
