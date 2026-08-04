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

# The capture uses a pod-specific host and puts the guid in the query string
# as well as the body.
STORE_AUTH_URL = "https://p18-buy.itunes.apple.com/WebObjects/MZFinance.woa/wa/authenticate"
LIBRARY_URL = ("https://pd.itunes.apple.com/WebObjects/MZPurchaseDaap.woa"
               "/purchase/databases/101/items")
# iTunes for Windows lists the same locker as plain JSON, which is far easier
# to read than DMAP. mt selects the media type; observed: 1 music, 4, 6 films.
PURCHASES_URL = ("https://se-edge.itunes.apple.com/WebObjects"
                 "/MZStoreElements.woa/wa/purchases")
PURCHASES_MEDIA_TYPE_MOVIES = "6"
# The locker returns store ids only; their titles come from here. The client
# also sends X-JS-SP-TOKEN and X-JS-TIMESTAMP, signed by its storefront
# JavaScript. Whether they are required is not answerable from a capture, so
# this asks without them and reports what Apple says.
LOOKUP_URL = ("https://client-api.itunes.apple.com/WebObjects"
              "/MZStorePlatform.woa/wa/lookup")
BOOKKEEPER_GET_ALL = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/getAll"
BOOKKEEPER_PUT = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/put"
BOOKKEEPER_DOMAIN = "com.apple.upp"

STORE_SESSION_CACHE = "itunes_session.json"
# What the Apple TV client sends. AMPLibraryAgent is the music side's agent
# and is not what the store sees on these calls.
STORE_UA = ("TV/1.6.4 (Windows 10.0.19045 x64; x64) "
            "Chromium/151.0.4129.59 (dt:2)")

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
TAG_DURATION = "astm"       # DAAP's track time, in milliseconds

# Tags whose payload is a number. Guessing from the bytes is not enough: a
# duration of 5,633,558 ms begins with 0x56, which is "V", so it reads as a
# string under any is-this-text test.
DMAP_INTEGERS = ("astm", "asyr", "mtco", "mrco", "mstt", "astn", "astc",
                 "aeSI", "aeAI", "muty", "ascr", "asdb", "mavl", "aeli")

# Containers rather than values, so their payload is more DMAP.
# aefl holds one aeif per downloadable asset, and the runtime is in there
# rather than on the title itself -- confirmed by parsing aefl's payload and
# finding it consumes exactly as DMAP.
DMAP_CONTAINERS = ("adbs", "mlcl", "mlit", "abro", "msrv", "mccr", "mshl",
                   "aefl", "aeif")

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
        if length in (1, 2, 4, 8) and (
                tag in DMAP_INTEGERS or not any(32 <= b < 127 for b in body)):
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
        storefront = kodiutils.get_setting("storefront") or "143441"
        return {"User-Agent": STORE_UA,
                "Content-Type": "application/x-apple-plist",
                "Accept-Language": "en-US",
                "Accept-Encoding": "gzip, deflate",
                "X-Apple-Client-Application": "com.apple.TV",
                "X-Apple-Store-Front": "%s-1,42 t:tv1" % storefront,
                "Cache-Control": "no-cache"}

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
                                     params={"guid": self._guid()},
                                     headers=self._headers(), timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("iTunes sign-in failed: %s" % exc)
            return False
        if resp.status_code != 200:
            # Apple often explains itself in the body even on a 4xx, and its
            # own words beat any guess made here.
            detail = ""
            try:
                said = plistlib.loads(resp.content)
                detail = (said.get("customerMessage")
                          or said.get("failureType") or "")
            except Exception:
                detail = resp.text[:300].strip()
            self.last_error = ("HTTP %s%s"
                               % (resp.status_code, ": " + detail if detail else ""))
            kodiutils.log_error("iTunes sign-in -> %s %s"
                                % (resp.status_code, detail or resp.text[:300]))
            if resp.status_code == 403 and not detail:
                # Apple's own client signs this request with device
                # attestation -- X-Apple-ActionSignature, X-Apple-AMD and
                # X-Apple-AMD-M -- which nothing here can produce. A 403 is
                # very likely that, not a wrong password.
                self.last_error = ("refused (403): the store appears to want "
                                   "a signed device attestation")
                kodiutils.log_error(
                    "Store sign-in refused. Apple's client signs this call "
                    "with X-Apple-ActionSignature and X-Apple-AMD headers "
                    "generated on the device; see docs/itunes-library.md.")
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

    def pasted_cookies(self):
        """A store session captured from a real Apple client, if one is set.

        Apple's own software proves itself with a SAP handshake and signed
        attestation headers, neither of which can be reproduced here, so the
        sign-in above is refused. Borrowing a session that a genuine client
        already established is the way round that: it needs no attestation
        because the hard part has already happened elsewhere.
        """
        return (kodiutils.get_setting("itunes_cookies") or "").strip()

    def locker(self, media_type=PURCHASES_MEDIA_TYPE_MOVIES):
        """Store ids the account owns, from the JSON purchases endpoint.

        Returns ids only. Their titles come from a separate lookup, which is
        what the client does too.

        spDsid says whose purchases to list. A family member's dsid returns
        theirs, which is how iTunes for Windows shows shared purchases and
        why asking for your own can come back empty while the family's does
        not.
        """
        self.last_error = None
        # A pasted session is used when there is one. Otherwise the session
        # this addon already holds is tried: signing in to Apple TV+ also
        # calls the store's own web login, so it may carry enough. Whether it
        # does is a question a capture cannot answer -- the request either
        # returns a locker or it does not -- so it is asked rather than
        # assumed, and the log says which.
        cookies = self.pasted_cookies()
        if not cookies:
            kodiutils.log("No pasted store session; trying the addon's own")
        params = {"dataOnly": "true", "mt": media_type, "restoreMode": "false"}
        dsid = (kodiutils.get_setting("itunes_sp_dsid") or "").strip()
        if dsid:
            params["spDsid"] = dsid
        headers = dict(self._headers())
        headers.pop("Content-Type", None)
        if cookies:
            headers["Cookie"] = cookies
        try:
            resp = self.session.get(PURCHASES_URL, params=params,
                                    headers=headers, timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("iTunes locker failed: %s" % exc)
            return []
        if resp.status_code != 200:
            self.last_error = "HTTP %s: %s" % (resp.status_code,
                                               resp.text[:200].strip())
            kodiutils.log_error("iTunes locker -> %s %s"
                                % (resp.status_code, resp.text[:300]))
            return []
        try:
            content = (resp.json().get("lockerData") or {}).get("content") or {}
        except Exception as exc:
            # A signed-out session is answered with the sign-in page rather
            # than an error, so say that instead of a parse failure.
            self.last_error = (
                "the store did not return a locker; the pasted session may "
                "have expired" if cookies else
                "the store did not accept this addon's own session; paste one "
                "from a signed-in Apple client instead")
            kodiutils.log_error("iTunes locker was not JSON: %s" % exc)
            return []
        ids = []
        for key, variants in content.items():
            ids.append(str(key))
        kodiutils.log("iTunes locker: %d owned title(s) for mt=%s"
                      % (len(ids), media_type))
        return ids

    def lookup(self, ids):
        """Turn store ids into titles, in batches as the client does."""
        found = {}
        cookies = self.pasted_cookies()
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            headers = dict(self._headers())
            headers.pop("Content-Type", None)
            if cookies:
                headers["Cookie"] = cookies
            try:
                resp = self.session.get(
                    LOOKUP_URL, headers=headers, timeout=30,
                    params={"id": ",".join(batch), "p": "lockup",
                            "caller": "DI6", "version": "2"})
            except Exception as exc:
                kodiutils.log_error("iTunes lookup failed: %s" % exc)
                break
            if resp.status_code != 200:
                kodiutils.log_error(
                    "iTunes lookup -> %s %s. If this is rejected, the signed "
                    "X-JS-SP-TOKEN the storefront sends is likely required."
                    % (resp.status_code, resp.text[:200]))
                break
            try:
                results = (resp.json() or {}).get("results") or {}
            except Exception:
                kodiutils.log_error("iTunes lookup did not return JSON")
                break
            found.update(results)
        return found

    def owned_movies(self):
        """The account's purchased films, as catalogue entries."""
        ids = self.locker(PURCHASES_MEDIA_TYPE_MOVIES)
        if not ids:
            return []
        items = []
        for store_id, row in self.lookup(ids).items():
            if not isinstance(row, dict) or not row.get("name"):
                continue
            entry = self._from_lookup(store_id, row)
            if entry:
                items.append(entry)
        return items

    @staticmethod
    def _from_lookup(store_id, row):
        """One store lookup result as a catalogue entry.

        Field names here are the store's, not the UTS API's, and were read off
        a real response: the synopsis is itunesNotes.standard rather than a
        description, the certificate is nested per rating system, and the
        release date is already a date rather than epoch milliseconds.
        """
        if row.get("kind") not in (None, "movie"):
            return None
        art = ((row.get("artwork") or {}).get("url") or "")
        art = (art.replace("{w}", "600").replace("{h}", "900")
                  .replace("{f}", "jpg").replace("{c}", ""))
        notes = row.get("itunesNotes")
        plot = notes.get("standard") if isinstance(notes, dict) else None
        # contentRatingsBySystem is keyed by system; take whichever is there.
        rating = None
        for system in (row.get("contentRatingsBySystem") or {}).values():
            if isinstance(system, dict) and system.get("name"):
                rating = system["name"]
                break
        released = str(row.get("releaseDate") or "")
        year = None
        if len(released) >= 4 and released[:4].isdigit():
            year = int(released[:4])
        return {
            "id": str(store_id),
            "adam_id": str(store_id),
            "title": row.get("name"),
            "sort_title": row.get("nameSortValue") or row.get("name"),
            "type": "Movie",
            "plot": plot,
            "genres": row.get("genreNames") or [],
            "mpaa": rating,
            "year": year,
            "premiered": released[:10] or None,
            "studio": row.get("artistName"),
            "art": {"poster": art, "thumb": art, "icon": art} if art else None,
        }

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
    def _duration(entry):
        """Runtime in seconds, from the asset rather than the title.

        astm is DAAP's track time in milliseconds and sits inside aeif, one
        of which describes each downloadable asset. A title with several
        assets lists them all, so the longest is the feature.
        """
        assets = (entry.get("aefl") or {}).get("aeif")
        if assets is None:
            return None
        if not isinstance(assets, list):
            assets = [assets]
        times = [a.get(TAG_DURATION) for a in assets
                 if isinstance(a, dict) and isinstance(a.get(TAG_DURATION), int)]
        return max(times) // 1000 if times else None

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
            # astm is DAAP's track time in milliseconds. Seconds are what the
            # rest of the addon and Kodi both work in.
            "duration": ItunesStore._duration(entry),
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
