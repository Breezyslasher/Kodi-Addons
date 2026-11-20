"""A24 Screening Room catalogue, entitlements and playback.

From the app.a24films.com HARs and bundle (docs/recon.md rows C-F):

* App API ``https://api.app.a24films.com/api/v1.6/`` with the Bearer token
  (lib/auth.py). A "product" is a film, trailer or extra:
  ``{id, display_name, description, content_type ('f' film, 't' trailer..),
  title_properties: {video: {external_id = Brightcove video id}, year,
  duration, rating, director_commentary}, poster_art/static_art: {images:
  {thumbnail, small, medium, large}}, credits: [{credit_type 's'|'d'|'w',
  person: {name}}], product_variants: [{purchase_type 'r'|'p', price_type:
  {price}}], associated_content: [product, ...], is_member_content}``.
* ``GET library/`` -> ``{products: [...], entitlements: [{product_id,
  purchase_type, expiration_date, is_active, entitlement_progress}]}``.
* ``GET products/?purchaseable=true[&page=N]`` -> ``{count, next, results}``
  (12 per page).
* ``GET products/{id}/`` -> one product.
* Playback (VideoPlayer-*.js): ``POST jwt/ {"product_id"}`` -> ``{"jwt"}``;
  the Brightcove player (account 6276845754001, player 8h9yBgsS9) then calls
  the Playback API with that token and no policy key, which (Brightcove
  player 7.30 source) means ``GET https://edge-auth.api.brightcove.com/
  playback/v1/accounts/{account}/videos/{external_id}`` with
  ``Authorization: Bearer <jwt>``; DRM licence requests carry the same token
  in a ``BCOV-Auth`` header (``emeHeaders``).

Nothing here is cached across calls: rentals expire and entitlements change.
"""

import time
from datetime import datetime

from lib import kodiutils
from lib.auth import not_ready

API = "https://api.app.a24films.com/api/v1.6/"
BC_ACCOUNT = "6276845754001"
BC_PLAYBACK = "https://edge-auth.api.brightcove.com/playback/v1/accounts/%s/videos/%s"
WIDEVINE = "com.widevine.alpha"

# Entitlement states (lib/listing.py turns them into labels).
OWNED, RENTED, MEMBER_FREE, FREE, RENTABLE, UNAVAILABLE = (
    "owned", "rented", "member_free", "free", "rentable", "unavailable")
PLAYABLE = (OWNED, RENTED, MEMBER_FREE, FREE)
FREE_TYPES = ("t",)        # trailers play without an entitlement

CREDIT_CAST, CREDIT_DIRECTOR, CREDIT_WRITER = "s", "d", "w"


class RentalExpiredError(Exception):
    """Playback refused because the rental lapsed or the title is not on the
    account; shown once, never retried."""


def _epoch(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (TypeError, ValueError):
        return 0


def _image(art, size="large"):
    images = (art or {}).get("images") or {}
    return images.get(size) or images.get("large") or images.get("medium") or ""


def _price_text(product):
    parts = []
    variants = sorted(product.get("product_variants") or [],
                      key=lambda v: v.get("purchase_type") != "r")   # rent first
    for variant in variants:
        price = (variant.get("price_type") or {}).get("price")
        if not price or variant.get("is_nonpurchasable"):
            continue
        kind = kodiutils.localize(32067 if variant.get("purchase_type") == "r" else 32068)
        parts.append("%s $%s" % (kind, price))
    return " / ".join(parts)


def to_item(product, entitlement=None):
    """A24 product (+ its entitlement, if any) -> the add-on's item dict."""
    props = product.get("title_properties") or {}
    video = props.get("video") or {}
    credits = product.get("credits") or []

    def people(kind):
        return [c["person"]["name"] for c in credits
                if c.get("credit_type") == kind and (c.get("person") or {}).get("name")]

    content_type = product.get("content_type") or "f"
    item = {
        "id": product.get("id"),
        "title": product.get("display_name") or product.get("name") or "",
        "type": "movie" if content_type == "f" else "extra",
        "year": props.get("year"),
        "plot": product.get("description") or "",
        "duration": props.get("duration"),
        "genres": [g.get("name") for g in product.get("genres") or [] if g.get("name")],
        "directors": people(CREDIT_DIRECTOR),
        "writers": people(CREDIT_WRITER),
        "cast": [{"name": n, "order": i} for i, n in enumerate(people(CREDIT_CAST))],
        "mpaa": (props.get("rating") or "").upper(),
        "art": {k: v for k, v in {
            "poster": _image(product.get("poster_art")),
            "fanart": _image(product.get("static_art")),
            "thumb": _image(product.get("static_art"), "medium")
                     or _image(product.get("poster_art"), "medium"),
        }.items() if v},
        "video_id": video.get("external_id"),
        "has_extras": bool(product.get("associated_content")),
        "commentary": bool(props.get("director_commentary")),
        "price": _price_text(product),
    }
    if entitlement and entitlement.get("is_active", True):
        if entitlement.get("purchase_type") == "r":
            item["entitlement"] = RENTED
            item["rental_expires"] = _epoch(entitlement.get("expiration_date"))
        else:
            item["entitlement"] = OWNED
        progress = entitlement.get("entitlement_progress") or {}
        if progress.get("progress_timestamp"):
            item["resume"] = progress["progress_timestamp"]
        if progress.get("is_watched"):
            item["playcount"] = 1
    elif content_type in FREE_TYPES:
        item["entitlement"] = FREE
    elif item["price"]:
        item["entitlement"] = RENTABLE
    else:
        item["entitlement"] = UNAVAILABLE
    return item


class A24Api(object):

    def __init__(self, auth):
        self.auth = auth

    def _get(self, path, **params):
        resp = self.auth.request("GET", API + path, params=params or None)
        resp.raise_for_status()
        return resp.json()

    # -- browse --------------------------------------------------------------

    def _library_raw(self):
        data = self._get("library/")
        ents = {}
        for ent in data.get("entitlements") or []:
            if ent.get("is_active", True):
                ents[ent.get("product_id")] = ent
        return data.get("products") or [], ents

    def library(self):
        products, ents = self._library_raw()
        return [to_item(p, ents.get(p.get("id"))) for p in products]

    def member_free(self):
        # The member title list is not in a capture yet (needs an AAA24
        # account): products carry is_member_content, but the endpoint the
        # member tab uses is unknown.
        raise not_ready("C2", "Member Free")

    def store(self, page=1):
        """(items, has_next) for one page of rent/buy titles."""
        data = self._get("products/", purchaseable="true", page=page)
        _, ents = self._library_raw()
        items = [to_item(p, ents.get(p.get("id"))) for p in data.get("results") or []]
        return items, bool(data.get("next"))

    def search(self, query, max_pages=20):
        """Title/credit match over the purchaseable catalogue (~150 titles);
        no server search has been captured."""
        query = query.lower().strip()
        _, ents = self._library_raw()
        found, seen, page = [], set(), 1
        while page <= max_pages:
            data = self._get("products/", purchaseable="true", page=page)
            for p in data.get("results") or []:
                text = " ".join([p.get("display_name") or ""] +
                                [(c.get("person") or {}).get("name") or ""
                                 for c in p.get("credits") or []]).lower()
                if query in text and p.get("id") not in seen:
                    seen.add(p.get("id"))
                    found.append(to_item(p, ents.get(p.get("id"))))
            if not data.get("next"):
                break
            page += 1
        return found

    def product(self, product_id):
        return self._get("products/%s/" % product_id)

    def extras(self, product_id):
        product = self.product(product_id)
        _, ents = self._library_raw()
        return [to_item(p, ents.get(p.get("id")))
                for p in product.get("associated_content") or []]

    # -- playback ------------------------------------------------------------

    def playback(self, product_id, video_id=None):
        """{"manifest_url", "manifest_type", "license_url", "license_headers",
        "subtitles"} for an entitled product (rows D-F)."""
        resp = self.auth.request("POST", API + "jwt/", json={"product_id": int(product_id)})
        if resp.status_code in (402, 403, 404):
            raise RentalExpiredError(kodiutils.localize(32052))
        resp.raise_for_status()
        jwt = (resp.json() or {}).get("jwt")
        if not jwt:
            raise RentalExpiredError(kodiutils.localize(32052))
        if not video_id:
            video_id = ((self.product(product_id).get("title_properties") or {})
                        .get("video") or {}).get("external_id")
        bc = self.auth.session.get(BC_PLAYBACK % (BC_ACCOUNT, video_id), timeout=20,
                                   headers={"Authorization": "Bearer %s" % jwt,
                                            "Accept": "application/json",
                                            "Origin": "https://app.a24films.com"})
        if bc.status_code in (401, 403):
            kodiutils.log_error("Brightcove refused playback: HTTP %d %s"
                                % (bc.status_code, bc.text[:300]))
            raise RentalExpiredError(kodiutils.localize(32052))
        bc.raise_for_status()
        return select_source(bc.json(), jwt)


def select_source(video, jwt):
    """Pick a Widevine DASH source (ISA's best-supported path), else Widevine
    HLS, else a clear HLS/DASH one. Brightcove's player sends the playback
    token to the licence server as BCOV-Auth."""
    sources = [s for s in video.get("sources") or []
               if (s.get("src") or "").startswith("https://")]

    def pick(kind, drm):
        for s in sources:
            stype = (s.get("type") or "").lower()
            is_kind = "dash" in stype if kind == "mpd" else "mpegurl" in stype
            has_wv = WIDEVINE in (s.get("key_systems") or {})
            if is_kind and has_wv == drm:
                return s
        return None

    chosen, kind = None, None
    for kind, drm in (("mpd", True), ("hls", True), ("hls", False), ("mpd", False)):
        chosen = pick(kind, drm)
        if chosen:
            break
    if not chosen:
        raise RentalExpiredError(kodiutils.localize(32052))
    playback = {"manifest_url": chosen["src"], "manifest_type": kind}
    wv = (chosen.get("key_systems") or {}).get(WIDEVINE)
    if wv and wv.get("license_url"):
        playback["license_url"] = wv["license_url"]
        playback["license_headers"] = {"BCOV-Auth": jwt}
    subs = []
    for track in video.get("text_tracks") or []:
        if track.get("kind") in ("captions", "subtitles") and track.get("src", "").startswith("https://"):
            subs.append(track["src"])
        for s in track.get("sources") or []:
            if (s.get("src") or "").startswith("https://") and track.get("kind") in ("captions", "subtitles"):
                subs.append(s["src"])
                break
    if subs:
        playback["subtitles"] = list(dict.fromkeys(subs))
    return playback
