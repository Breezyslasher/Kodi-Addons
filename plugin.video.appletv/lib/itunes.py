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
import json
import plistlib
import re
import struct
import time
import uuid
import zlib

import requests

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
# mt selects the media type, and the numbers are not the ones the tab names
# suggest. Every value was checked against a real locker: 1 returns songs, 6
# returns films, and 4 -- not 3 -- returns television. mt=3 answers 200 with
# an empty locker on every account tried, including the one that owns 94
# films and 15 episodes, so whatever it selects, this account has none of it.
PURCHASES_MEDIA_TYPE_MOVIES = "6"
PURCHASES_MEDIA_TYPE_TV = "4"
# The locker returns store ids only; their titles come from here. The client
# also sends X-JS-SP-TOKEN and X-JS-TIMESTAMP, signed by its storefront
# JavaScript. Whether they are required is not answerable from a capture, so
# this asks without them and reports what Apple says.
LOOKUP_URL = ("https://client-api.itunes.apple.com/WebObjects"
              "/MZStorePlatform.woa/wa/lookup")
# The profile the library view asks for. Not "redownload-image": every
# captured library lookup sends this longer name.
LOOKUP_PROFILE = "redownload-image-tracklist-item"
# Every lookup in every capture sends this storefront suffix -- ,32 rather
# than the ,42 t:tv1 the TV app uses elsewhere.
LOOKUP_STOREFRONT_SUFFIX = "-1,32"
# The public iTunes lookup, which needs no session and no signed token. Used
# when the store's own lookup refuses, so a locker of ids can still be turned
# into titles.
PUBLIC_LOOKUP_URL = "https://itunes.apple.com/lookup"
# The lookups in the captures are made by iTunes rather than the TV app, and
# the user agent is one of several things that differed.
LOOKUP_UA = ("iTunes/12.13.10 (Windows; Microsoft Windows 10 x64 "
             "(Build 19045); x64) AppleWebKit/7613.2007.1014.14 (dt:2)")
BOOKKEEPER_GET_ALL = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/getAll"
BOOKKEEPER_PUT = "https://upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/put"
BOOKKEEPER_DOMAIN = "com.apple.upp"
# Asking for a purchase again -- what the cloud-download button does. It is a
# purchase call with the price set to zero and STDRDL for "standard
# redownload"; there is no separate redownload endpoint.
REDOWNLOAD_URL = "https://p18-buy.itunes.apple.com/WebObjects/MZBuy.woa/wa/buyProduct"
REDOWNLOAD_PRICING = "STDRDL"
PRODUCT_TYPE_VIDEO = "V"

STORE_SESSION_CACHE = "itunes_session.json"
TV_LOCKER_CACHE = "itunes_tv.json"
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
        # Each locker row files an owned title under a collection (its
        # "playlist" -- a TV season's store id). The catalogue drops a
        # purchased episode, but the public store still lists the season and
        # its episodes, so this store-id -> collection-id map is kept as the
        # locker is read and used to name what nothing else will.
        self._collection_ids = {}

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

        The store-page session goes in itunes_cookies; the Android TV app's
        mz_at_ssl session lives in itunes_uts_cookies (its main job is the UTS
        Continue Watching caller). Both are store sessions, so when only the
        latter is pasted it is tried against the locker too rather than
        forcing the user to paste the same thing twice.
        """
        return ((kodiutils.get_setting("itunes_cookies") or "").strip()
                or (kodiutils.get_setting("itunes_uts_cookies") or "").strip())

    def account_dsid(self):
        """The signed-in account's own dsid.

        A store session names its account in the cookie itself: the capture
        carries ``amia-12305910250=...``, and 12305910250 is exactly what the
        client then sends as X-Dsid. So a pasted session needs no second
        setting to say whose it is. This is not the same number as the family
        dsid used for spDsid -- that one says whose purchases to list.
        """
        info = self.session_info()
        if info.get("dsid"):
            return str(info["dsid"])
        cookies = self.pasted_cookies()
        found = re.search(r"(?:mz_at_ssl-|amia-|mt-tkn-|mz_at0-)(\d+)=", cookies) \
            or re.search(r"X-Dsid=(\d+)", cookies)
        return found.group(1) if found else ""

    def family_members(self):
        """Who shares purchases with this account, and their dsids.

        There is no endpoint for this -- the bag lists family permission and
        sharing toggles, but nothing that enumerates members. The roster is
        embedded in the purchases page itself as JSON, one object per person:

            iCloudDsid, iTunesPreferredDsid, accountName, displayName,
            sharingPurchases, isMe

        iTunesPreferredDsid is what spDsid wants, and sharingPurchases says
        whether asking for theirs will return anything. So a family member's
        number never has to be typed in by hand -- as long as there is a store
        session, which is the same thing everything else here waits on.
        """
        self.last_error = None
        cookies = self.pasted_cookies()
        headers = dict(self._headers())
        headers.pop("Content-Type", None)
        if cookies:
            headers["Cookie"] = cookies
        try:
            resp = self.session.get(PURCHASES_URL, headers=headers, timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("Family roster request failed: %s" % exc)
            return []
        if resp.status_code != 200:
            self.last_error = "HTTP %s" % resp.status_code
            kodiutils.log_error("Family roster -> %s" % resp.status_code)
            return []
        members = []
        for blob in re.finditer(r'\{[^{}]*"iCloudDsid"[^{}]*\}', resp.text):
            try:
                row = json.loads(blob.group(0))
            except ValueError:
                continue
            dsid = str(row.get("iTunesPreferredDsid")
                       or row.get("iCloudDsid") or "")
            if not dsid:
                continue
            members.append({
                "dsid": dsid,
                "name": row.get("displayName") or row.get("accountName") or dsid,
                "is_me": bool(row.get("isMe")),
                "shares": bool(row.get("sharingPurchases")),
            })
        kodiutils.log("Family roster: %d member(s), %d sharing purchases"
                      % (len(members), sum(1 for m in members if m["shares"])))
        return members

    def locker(self, media_type=PURCHASES_MEDIA_TYPE_MOVIES, dsid=None):
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
        if dsid:
            params["spDsid"] = str(dsid)
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
        # content is not purely ids: alongside them sits an orderedKeys entry
        # holding the display order, and treating that as a title sends Apple
        # a lookup for an id named "orderedKeys" -- which it rejects, taking
        # the real ids down with it. Use the order Apple gives when it is
        # there, and otherwise whatever keys are actually numeric.
        ordered = content.get("orderedKeys")
        if isinstance(ordered, list) and ordered:
            ids = [str(i) for i in ordered]
        else:
            ids = [str(k) for k in content if str(k).isdigit()]
        kodiutils.log("iTunes locker: %d owned title(s) for mt=%s%s"
                      % (len(ids), media_type,
                         " (dsid %s)" % dsid if dsid else " (own)"))
        # Each numeric row files its title under a collection: the row's
        # "playlist" is the store id of the season (or film bundle) it belongs
        # to. The catalogue drops a purchased episode, but the public store
        # still lists that collection and every episode in it, so the id is
        # recorded here and used later to name what nothing else would.
        sampled = False
        for key, value in content.items():
            if not (str(key).isdigit() and isinstance(value, dict)):
                continue
            collection = value.get("playlist")
            if collection:
                self._collection_ids[str(key)] = str(collection)
            if not sampled:
                # Reveal one row's fields and body once: a video locker was
                # never captured here, so the shape is shown rather than
                # assumed, and the collection id above is read off it.
                kodiutils.log("iTunes locker row %s fields: %s"
                              % (key, ", ".join(sorted(value.keys()))))
                kodiutils.log("iTunes locker row %s sample: %s"
                              % (key, json.dumps(value)[:600]))
                sampled = True
        return ids

    def lookup(self, ids):
        """Turn store ids into titles, in batches as the client does.

        Apple's own lookup wants a signed token, so this may be refused; the
        public lookup below needs nothing and stands in when it is.
        """
        found = {}
        cookies = self.pasted_cookies()
        storefront = (kodiutils.get_setting("storefront") or "143441")
        refused = False
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            headers = dict(self._headers())
            headers.pop("Content-Type", None)
            # The capture's lookups differ from the rest of the store calls:
            # a plain storefront suffix, and an Origin on the store front-end
            # rather than the TV app.
            headers["X-Apple-Store-Front"] = storefront + LOOKUP_STOREFRONT_SUFFIX
            headers["Origin"] = "https://se-edge.itunes.apple.com"
            headers["Referer"] = "https://se-edge.itunes.apple.com/"
            # The captured lookups come from iTunes, not the TV app, and carry
            # neither the TV client header nor a plist content type. Saying
            # the 403 is about the signed token is a guess while the request
            # still differs in ways that can be fixed, so these are matched.
            headers["User-Agent"] = LOOKUP_UA
            headers["Accept"] = "*/*"
            headers.pop("X-Apple-Client-Application", None)
            headers.pop("Cache-Control", None)
            if cookies:
                headers["Cookie"] = cookies
            dsid = self.account_dsid()
            if dsid:
                headers["X-Dsid"] = dsid
            try:
                resp = self.session.get(
                    LOOKUP_URL, headers=headers, timeout=30,
                    params={"id": ",".join(batch), "p": LOOKUP_PROFILE,
                            "caller": "DI6", "version": "2"})
            except Exception as exc:
                kodiutils.log_error("iTunes lookup failed: %s" % exc)
                refused = True
                break
            if resp.status_code != 200:
                # Every captured lookup carries X-JS-SP-TOKEN, and no two
                # requests share one even within the same second, so it signs
                # the request rather than the session and cannot be made here.
                kodiutils.log_error(
                    "iTunes lookup -> %s; falling back to the public lookup, "
                    "which needs no signed token" % resp.status_code)
                refused = True
                break
            try:
                results = (resp.json() or {}).get("results") or {}
            except Exception:
                kodiutils.log_error("iTunes lookup did not return JSON")
                refused = True
                break
            found.update(results)
        if refused and not found:
            return self.public_lookup(ids)
        return found

    def public_lookup(self, ids):
        """Titles for store ids from the public lookup service.

        A different service with a different shape -- flat results keyed by
        nothing, camelCase names, trackId rather than id -- so it is mapped
        into the same shape the store lookup returns rather than handled
        separately downstream.
        """
        found = {}
        country = (kodiutils.get_setting("locale") or "en-US").split("-")[-1].lower()
        self._public_batch(ids, country, found, None)
        # Films resolve almost completely without an entity hint -- 89 of 94 --
        # and television barely at all -- 4 of 15 -- so the unresolved are
        # asked for again as episodes and then as seasons. The hint was
        # dropped earlier for the wrong reason: the 400 that prompted it came
        # from an id called "orderedKeys", not from this parameter.
        for entity in ("tvEpisode", "tvSeason"):
            missing = [i for i in ids if i not in found]
            if not missing:
                break
            self._public_batch(missing, country, found, entity)
        kodiutils.log("Public lookup: %d of %d id(s) resolved"
                      % (len(found), len(ids)))
        return found

    def public_collection_lookup(self, collection_ids):
        """Episodes of a purchased season, from the public store by collection.

        A delisted purchase is the hard case: the catalogue answers 404 for
        its episode canonical and 404 for its season, the store's own lookup
        wants a signed token, and the public lookup by episode id finds
        nothing. But the public lookup expands a *collection*: asked for a
        season's store id with entity=tvEpisode, it returns the season row and
        every episode under it, each with its trackId -- which is the store id
        the locker owns them by. So this names owned episodes by looking up the
        one season they share rather than each episode on its own.

        Returns a map of episode store id -> a row in the store lookup's shape,
        the same shape _from_lookup already reads.
        """
        found = {}
        country = (kodiutils.get_setting("locale") or "en-US").split("-")[-1].lower()
        for collection in sorted(set(str(c) for c in collection_ids if c)):
            attempts = [
                {"id": collection, "entity": "tvEpisode", "country": country},
                {"id": collection, "entity": "tvEpisode"},
            ]
            results = None
            for params in attempts:
                try:
                    resp = requests.get(PUBLIC_LOOKUP_URL, timeout=30,
                                        params=params,
                                        headers={"User-Agent": STORE_UA,
                                                 "Accept": "*/*"})
                except Exception as exc:
                    kodiutils.log_error("Collection lookup failed: %s" % exc)
                    resp = None
                    break
                if resp.status_code == 200:
                    try:
                        results = (resp.json() or {}).get("results") or []
                    except Exception:
                        results = None
                    if results:
                        break
                else:
                    kodiutils.log_error(
                        "Collection lookup %s -> %s %s"
                        % (collection, resp.status_code,
                           resp.text[:160].strip().replace("\n", " ")))
            if not results:
                # A 200 with nothing means this collection is not in the public
                # index either -- the honest end of the road for this season,
                # said plainly rather than passed over.
                kodiutils.log("Collection lookup %s: no episodes returned"
                              % collection)
                continue
            episodes = 0
            for row in results:
                if not isinstance(row, dict):
                    continue
                # The collection carries its season row too (wrapperType
                # collection); only the episode tracks name owned ids.
                if row.get("wrapperType") != "track" and row.get("kind") != "tv-episode":
                    continue
                track_id = str(row.get("trackId") or "")
                if not track_id:
                    continue
                found[track_id] = self._store_shape(row)
                episodes += 1
            kodiutils.log("Collection lookup %s: named %d episode(s)"
                          % (collection, episodes))
        return found

    def _public_batch(self, ids, country, found, entity):
        """One pass of the public lookup, filling `found` in place."""
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            # Looking up by id needs nothing else. An entity filter is for
            # searches and is what a 400 here means, so it is not sent; the
            # country is dropped too if Apple still objects, since the ids
            # already identify their storefront.
            base = {"id": ",".join(batch), "country": country}
            if entity:
                base["entity"] = entity
            attempts = [base, {k: v for k, v in base.items() if k != "country"}]
            resp = None
            for params in attempts:
                try:
                    # Deliberately not self.session: that one carries
                    # tv.apple.com's cookies and headers, and this is a
                    # different service that has no use for them.
                    resp = requests.get(PUBLIC_LOOKUP_URL, timeout=30,
                                        params=params,
                                        headers={"User-Agent": STORE_UA,
                                                 "Accept": "*/*"})
                except Exception as exc:
                    kodiutils.log_error("Public lookup failed: %s" % exc)
                    resp = None
                    break
                if resp.status_code == 200:
                    break
                kodiutils.log_error("Public lookup -> %s %s"
                                    % (resp.status_code,
                                       resp.text[:160].strip().replace("\n", " ")))
            if resp is None or resp.status_code != 200:
                break
            try:
                results = (resp.json() or {}).get("results") or []
            except Exception:
                kodiutils.log_error("Public lookup did not return JSON")
                break
            if not results:
                # A 200 with nothing in it is a different problem from a
                # refusal, and the body says which: the service reports a
                # resultCount, and returns none for an id it does not carry.
                kodiutils.log_error(
                    "Public lookup returned no results for %d id(s): %s"
                    % (len(batch), resp.text[:200].strip().replace("\n", " ")))
            for row in results:
                if not isinstance(row, dict):
                    continue
                store_id = str(row.get("trackId") or row.get("collectionId") or "")
                if not store_id:
                    kodiutils.log_error("Public lookup row had no id: %s"
                                        % sorted(row)[:12])
                    continue
                found[store_id] = self._store_shape(row)
                # The locker's id and the id the lookup answers with are not
                # always the same number, and a title filed under the one the
                # locker gave is what the caller will go looking for.
                for asked in batch:
                    if asked not in found and str(row.get("collectionId") or "") == asked:
                        found[asked] = found[store_id]

    @staticmethod
    def _store_shape(row):
        """One public-lookup result in the store lookup's shape."""
        kind = {"feature-movie": "movie", "tv-episode": "tvEpisode",
                "tv-season": "tvSeason"}.get(row.get("kind") or "", "movie")
        if row.get("wrapperType") == "collection":
            kind = "tvSeason"
        art = row.get("artworkUrl100") or row.get("artworkUrl60") or ""
        # The public service sizes artwork in the path; ask for something a
        # poster can use rather than a 100px thumbnail.
        art = art.replace("100x100", "600x600").replace("60x60", "600x600")
        rating = row.get("contentAdvisoryRating")
        return {
            "id": str(row.get("trackId") or row.get("collectionId") or ""),
            "kind": kind,
            "name": row.get("trackName") or row.get("collectionName"),
            "nameSortValue": row.get("trackName") or row.get("collectionName"),
            "artistName": row.get("artistName"),
            "collectionName": row.get("collectionName"),
            "collectionId": row.get("collectionId"),
            "artistId": row.get("artistId"),
            "releaseDate": (row.get("releaseDate") or "")[:10],
            "genreNames": [row["primaryGenreName"]] if row.get("primaryGenreName") else [],
            "itunesNotes": {"standard": row.get("longDescription")
                            or row.get("shortDescription")}
                           if (row.get("longDescription")
                               or row.get("shortDescription")) else None,
            "contentRatingsBySystem": {"public": {"name": rating}} if rating else {},
            "artwork": {"url": art} if art else {},
            "trackNumber": row.get("trackNumber"),
            "episodeNumber": row.get("trackNumber"),
            "trackCount": row.get("trackCount"),
            "copyright": row.get("copyright"),
        }

    def all_ids(self, media_type=PURCHASES_MEDIA_TYPE_MOVIES):
        """Store ids from every locker this account can see.

        One locker holds one person's purchases, so asking only for one's own
        shows only one's own -- which for this account is a single film while
        the family between them own ninety-odd. The setting still wins when it
        names somebody, and otherwise every member the roster says is sharing
        is asked, own included.
        """
        chosen = (kodiutils.get_setting("itunes_sp_dsid") or "").strip()
        if chosen:
            return self.locker(media_type, chosen)
        dsids = [None]
        for member in self.family_members():
            if member["shares"] and not member["is_me"]:
                dsids.append(member["dsid"])
        ids = []
        for dsid in dsids:
            for store_id in self.locker(media_type, dsid):
                if store_id not in ids:
                    ids.append(store_id)
        if len(dsids) > 1:
            kodiutils.log("iTunes locker: %d title(s) across %d locker(s)"
                          % (len(ids), len(dsids)))
        return ids

    def owned(self, media_type=PURCHASES_MEDIA_TYPE_MOVIES, resolver=None):
        """What the account owns of one media type, as catalogue entries."""
        ids = self.all_ids(media_type)
        if not ids:
            return []
        rows = self.lookup(ids)
        items = []
        for store_id, row in rows.items():
            if not isinstance(row, dict) or not row.get("name"):
                continue
            entry = self._from_lookup(store_id, row)
            if entry:
                items.append(entry)
        missing = [i for i in ids if i not in rows]
        if missing and resolver:
            # Not every owned id is in the public index: one whole season of
            # eleven episodes resolved none of them while the single-episode
            # shows resolved fine. The catalogue answers to a store id
            # directly, so it names what the lookup would not -- at one
            # request each, which is why it is only asked about the leftovers.
            kodiutils.log("Asking the catalogue about %d id(s) no lookup would "
                          "name" % len(missing))
            named = 0
            for store_id in missing:
                entry = resolver(store_id, media_type)
                if entry:
                    items.append(entry)
                    named += 1
            if named < len(missing):
                kodiutils.log("Catalogue named %d of %d; %d left for the "
                              "collection lookup"
                              % (named, len(missing), len(missing) - named))
            else:
                kodiutils.log("Catalogue named %d of %d" % (named, len(missing)))
        elif missing:
            kodiutils.log("%d owned id(s) no lookup would name: %s"
                          % (len(missing), ", ".join(missing[:10])))
        # Last tier: whatever nothing above named. The store's own lookup wants
        # a signed token and the catalogue has dropped these titles, but the
        # public store still lists the season each one belongs to, so they are
        # named by looking up that shared collection. Only the leftovers are
        # asked about, and only when a collection id was recorded for them.
        have = set(str(e.get("adam_id") or e.get("id")) for e in items)
        unnamed = [i for i in ids if i not in have]
        by_collection = [self._collection_ids.get(i) for i in unnamed]
        if unnamed and any(by_collection):
            kodiutils.log("Collection lookup for %d unnamed id(s) across %d "
                          "collection(s)"
                          % (len(unnamed),
                             len(set(c for c in by_collection if c))))
            rows = self.public_collection_lookup(by_collection)
            gained = 0
            for store_id in unnamed:
                row = rows.get(store_id)
                if not row:
                    continue
                entry = self._from_lookup(store_id, row)
                if entry:
                    items.append(entry)
                    have.add(store_id)
                    gained += 1
            kodiutils.log("Collection lookup named %d of %d unnamed"
                          % (gained, len(unnamed)))
        # A delisted episode carries its real title (named from its url slug)
        # but no episode number -- no content endpoint serves one. The locker's
        # order is the only episode order there is, so number by it within each
        # season for a stable sort, keeping the real title. Only when a title
        # is genuinely missing is a positional "Episode N" put in its place.
        order = {sid: i for i, sid in enumerate(ids)}
        by_season = {}
        for entry in items:
            if entry.get("delisted") and not entry.get("episode"):
                by_season.setdefault(entry.get("season_id") or "", []).append(entry)
        for group in by_season.values():
            group.sort(key=lambda e: order.get(str(e.get("adam_id")), 0))
            for number, entry in enumerate(group, 1):
                entry["episode"] = number
                if not entry.get("title"):
                    base = entry.get("show_title") or entry.get("season_title")
                    entry["title"] = ("%s – Episode %d" % (base, number)
                                      if base else "Episode %d" % number)
                    entry["sort_title"] = entry["title"]
        still = [i for i in ids if i not in have]
        if still:
            # These are genuinely unnamed: dropped from the catalogue, absent
            # from the public store, and behind a signed token in Apple's own
            # library lookup. Said plainly, with the ids, rather than hidden.
            kodiutils.log("%d owned title(s) could not be named by any public "
                          "source: %s" % (len(still), ", ".join(still[:10])))
            if not items:
                self.last_error = (
                    "Apple owns these to this account but no longer publishes "
                    "them, and its signed library lookup is refused here, so "
                    "there is nothing left to name them by.")
        return items

    def owned_movies(self, resolver=None):
        return self.owned(PURCHASES_MEDIA_TYPE_MOVIES, resolver)

    def owned_tv(self, resolver=None):
        """Owned episodes, cached so a season can be opened without refetching.

        Kodi starts a new process for every navigation, so the grouping below
        would otherwise have to ask Apple again just to list one season.
        """
        episodes = self.owned(PURCHASES_MEDIA_TYPE_TV, resolver)
        if episodes:
            kodiutils.write_json(TV_LOCKER_CACHE,
                                 {"stamp": time.time(), "items": episodes})
        return episodes

    def owned_tv_cached(self):
        cached = kodiutils.read_json(TV_LOCKER_CACHE, default={}) or {}
        return cached.get("items") or []

    def owned_tv_seasons(self, resolver=None):
        """Owned episodes gathered into the seasons they belong to.

        The television locker lists episodes and nothing else -- no season
        rows, no series rows -- so the seasons here are built rather than
        fetched. Each episode names its own in collectionId and
        collectionName, and that name already reads "Show, Season 1", so a
        season needs no extra call to describe itself.
        """
        seasons = {}
        for episode in self.owned_tv(resolver):
            key = episode.get("season_id") or episode.get("show_id") or ""
            group = seasons.get(key)
            if group is None:
                group = seasons[key] = {
                    "id": key,
                    "type": "Season",
                    "title": (episode.get("season_title")
                              or episode.get("show_title") or ""),
                    "sort_title": (episode.get("season_title")
                                   or episode.get("show_title") or ""),
                    "show_title": episode.get("show_title"),
                    "show_id": episode.get("show_id"),
                    "season": episode.get("season"),
                    "year": episode.get("year"),
                    "genres": episode.get("genres") or [],
                    "mpaa": episode.get("mpaa"),
                    "studio": episode.get("studio"),
                    "art": episode.get("art"),
                    "episode_count": 0,
                }
            group["episode_count"] += 1
        return sorted(seasons.values(), key=lambda s: s["sort_title"].lower())

    def owned_season(self, season_id):
        """The owned episodes of one season, in order."""
        episodes = [e for e in self.owned_tv_cached()
                    if str(e.get("season_id") or "") == str(season_id)]
        if not episodes:
            episodes = [e for e in self.owned_tv()
                        if str(e.get("season_id") or "") == str(season_id)]
        return sorted(episodes, key=lambda e: e.get("episode") or 0)

    @staticmethod
    def _from_lookup(store_id, row):
        """One store lookup result as a catalogue entry.

        Field names here are the store's, not the UTS API's, and were read off
        a real response: the synopsis is itunesNotes.standard rather than a
        description, the certificate is nested per rating system, and the
        release date is already a date rather than epoch milliseconds.
        """
        kind = row.get("kind")
        if kind not in (None, "movie", "movieBundle", "tvSeason", "tvEpisode"):
            return None
        television = kind in ("tvSeason", "tvEpisode")
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
            "type": "Episode" if kind == "tvEpisode" else (
                "Season" if kind == "tvSeason" else "Movie"),
            # artistName is the series for television and the director for a
            # film -- the same field meaning two different things, so which
            # one it is has to be decided by kind.
            "show_title": row.get("artistName") if television else None,
            # Only an episode carries its season; a season row is the season.
            "season": row.get("episodeSeasonNumber"),
            "episode": row.get("episodeNumber") or row.get("trackNumber")
                       if kind == "tvEpisode" else None,
            "season_id": str(row.get("collectionId") or "") or None,
            "season_title": row.get("collectionName"),
            "show_id": str(row.get("artistId") or "") or None,
            # An episode count, on a season row.
            "track_count": row.get("trackCount"),
            # Television rows carry no synopsis under this profile -- not an
            # empty one, the field is simply absent. Only films have notes.
            "plot": plot,
            "genres": row.get("genreNames") or [],
            "mpaa": rating,
            "year": year,
            "premiered": released[:10] or None,
            "studio": row.get("copyright") if television else row.get("artistName"),
            "art": {"poster": art, "thumb": art, "icon": art} if art else None,
        }

    def session_info(self):
        return kodiutils.read_json(STORE_SESSION_CACHE, default={}) or {}

    def is_signed_in(self):
        """Whether there is a store session at all, minted or borrowed.

        The sign-in above is refused, so in practice this is true only when a
        session has been pasted in from a real Apple client -- but the calls
        below do not care which of the two they got.
        """
        return bool(self.session_info().get("dsid") or self.pasted_cookies())

    def sign_out(self):
        kodiutils.write_json(STORE_SESSION_CACHE, {})

    def _store_headers(self):
        info = self.session_info()
        headers = self._headers()
        dsid = self.account_dsid()
        if dsid:
            headers["X-Dsid"] = dsid
        if info.get("password_token"):
            headers["X-Token"] = info["password_token"]
        cookies = self.pasted_cookies()
        if cookies:
            headers["Cookie"] = cookies
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

    # -- playback --------------------------------------------------------

    def redownload(self, adam_id, owner_dsid=None):
        """Ask the store for a purchase again, and read where it may be got.

        This is the cloud-download button. There is no endpoint named
        "redownload": it is an ordinary purchase with the price at zero and
        STDRDL as the pricing parameter, and Apple answers with both a
        progressive file and a streaming playlist.

        ``owner_dsid`` is whose copy to fetch, and a family member's number is
        accepted there -- the capture this was read from redownloads a film
        the signed-in account does not own itself.

        The open question is ``kbsync``: the real request carries a device
        keybag blob, which is the same class of thing as the attestation
        headers the sign-in wants and cannot be produced here. This asks
        without it rather than assuming the answer.
        """
        self.last_error = None
        if not self.is_signed_in():
            self.last_error = "not signed in to the store"
            return None
        body = plistlib.dumps({
            "guid": self._guid(),
            "salableAdamId": str(adam_id),
            "productType": PRODUCT_TYPE_VIDEO,
            "pricingParameters": REDOWNLOAD_PRICING,
            "price": "0",
            "pg": "default",
            "needDiv": "1",
            "machineName": kodiutils.get_setting("itunes_machine_name") or "Kodi",
            "ownerDsid": str(owner_dsid or self.account_dsid() or ""),
            "supportsGpuContentProtection": True,
        })
        try:
            resp = self.session.post(REDOWNLOAD_URL, data=body,
                                     headers=self._store_headers(), timeout=30)
        except Exception as exc:
            self.last_error = str(exc)
            kodiutils.log_error("iTunes redownload failed: %s" % exc)
            return None
        try:
            data = plistlib.loads(resp.content)
        except Exception:
            self.last_error = "HTTP %s: %s" % (resp.status_code,
                                               resp.text[:200].strip())
            kodiutils.log_error("iTunes redownload -> %s %s"
                                % (resp.status_code, resp.text[:300]))
            return None
        # A refusal comes back as a 200 with the reason in the body, the same
        # way the sign-in does.
        songs = data.get("songList") or []
        if not songs:
            self.last_error = (data.get("customerMessage")
                               or data.get("failureType")
                               or "the store returned no download")
            kodiutils.log_error("iTunes redownload refused: %s" % self.last_error)
            return None
        return self._from_redownload(songs[0])

    @staticmethod
    def _from_redownload(song):
        """The parts of a redownload answer that playback could use.

        Two routes come back. ``URL`` is a progressive .m4v carrying its own
        access key -- it needs no cookies, and it is FairPlay encrypted, with
        the keys wrapped into sinf boxes for the device that asked.
        ``hls-playlist-url`` is the streaming route, and it is an ordinary url
        with the adam id in the query string.

        Which DRM that playlist offers is the thing worth knowing and is not
        settled here: the key certificate Apple hands the Windows client is
        the FairPlay bundle, but the licence host is the very same
        MZPlayLocal/fpsRequest this addon already proxies Widevine through for
        Apple TV+. So the playlist is returned as found, unjudged.
        """
        return {
            "adam_id": str(song.get("songId") or ""),
            "download_url": song.get("URL"),
            "hls_url": song.get("hls-playlist-url"),
            "key_server": song.get("hls-key-server-url"),
            "key_cert": song.get("hls-key-cert-url"),
            "artwork": song.get("artworkURL"),
            "is_redownload": bool(song.get("isRedownload")),
            "has_4k": bool(song.get("has-4k")),
            "has_hdr": bool(song.get("has-hdr")),
            "has_dolby_vision": bool(song.get("has-dolby-vision")),
            # Present when the answer is FairPlay-protected, which the
            # progressive file always is.
            "fairplay": bool(song.get("sinfs")),
            "info": ItunesStore._from_store_metadata(song.get("metadata")),
        }

    @staticmethod
    def _from_store_metadata(md):
        """The store's own metadata block, as a catalogue entry.

        This travels with a redownload and is the fullest description the
        store side gives of a title -- fuller than the lookup, and the only
        place a purchased episode says which show and season it belongs to.
        The field names are this block's own: hyphenated where the lookup is
        camel-cased, and a season is ``playlistName`` rather than a
        collection.
        """
        if not isinstance(md, dict):
            return None
        kind = md.get("kind") or ""
        episode = md.get("kind") == "tv-episode"
        rating = md.get("rating")
        released = str(md.get("releaseDate") or "")
        duration = md.get("duration")
        return {
            "id": str(md.get("itemId") or ""),
            "adam_id": str(md.get("itemId") or ""),
            "title": md.get("itemName"),
            "sort_title": md.get("sort-name") or md.get("itemName"),
            "type": "Episode" if episode else "Movie",
            "plot": md.get("longDescription") or md.get("description"),
            "genres": [md["genre"]] if md.get("genre") else [],
            # us-tv gives "TV-G", the film systems give their own labels.
            "mpaa": rating.get("label") if isinstance(rating, dict) else None,
            "year": md.get("year"),
            "premiered": released[:10] or None,
            # A film credits its studio here; an episode credits its network.
            "studio": md.get("network-name") or md.get("copyright"),
            "show_title": md.get("show-name") or md.get("playlistArtistName"),
            "season": md.get("season-number"),
            # episode-number is "S1E1"; episode-sort-id is the number alone.
            "episode": md.get("episode-sort-id") or md.get("trackNumber"),
            # The store counts in milliseconds; Kodi counts in seconds.
            "duration": duration // 1000 if isinstance(duration, int) else None,
            # The season this episode belongs to, as a store id of its own.
            "season_id": str(md.get("playlistId") or "") or None,
            "show_id": str(md.get("artistId") or "") or None,
            "kind": kind,
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
