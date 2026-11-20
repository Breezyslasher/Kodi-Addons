"""A24 Screening Room / AAA24 catalogue, entitlements and playback.

Every endpoint below is a ``VERIFY`` placeholder (None) until a capture
proves it (docs/recon.md). Calling a method whose endpoint is still None
raises NotReadyError, which default.py shows as a "not built yet" dialog
instead of guessing at a request.

The methods return the add-on's own item dicts (lib/listing.py documents the
fields), so the folders and ListItems are already built and only ``to_item``
has to learn A24's field names once the browse capture is in.
"""

from lib import kodiutils
from lib.auth import not_ready

# docs/recon.md rows C-F. Fill each from a capture; cite the HAR entry there.
ENDPOINTS = {
    "library": None,        # C1  owned titles + active rentals (with expiry)
    "member_free": None,    # C2  AAA24 member movie of the month
    "store": None,          # C3  titles available to rent / own (price)
    "title": None,          # C4  one title's detail + entitlement
    "extras": None,         # C5  commentaries / bonus videos for a title
    "search": None,         # C6  search, if the service has one
    "playback": None,       # D   Play: decision -> manifest + licence
}

# Entitlement states (lib/listing.py turns them into labels).
OWNED, RENTED, MEMBER_FREE, RENTABLE, UNAVAILABLE = (
    "owned", "rented", "member_free", "rentable", "unavailable")
PLAYABLE = (OWNED, RENTED, MEMBER_FREE)


class RentalExpiredError(Exception):
    """Playback refused because the rental lapsed or the title is no longer
    entitled; shown once, never retried."""


def _endpoint(name, row):
    url = ENDPOINTS.get(name)
    if not url:
        raise not_ready(row, name.replace("_", " ").capitalize())
    return url


def to_item(raw):
    """A24 title JSON -> the add-on's item dict (lib/listing.py).

    Row C4 lists A24's field names; until then there is nothing to map.
    """
    raise not_ready("C4", "Title mapping")


class A24Api(object):

    def __init__(self, auth):
        self.auth = auth

    def _get_json(self, name, row, **params):
        resp = self.auth.request("GET", _endpoint(name, row), params=params or None)
        resp.raise_for_status()
        return resp.json()

    # -- browse --------------------------------------------------------------
    # The member title rotates monthly and rentals expire: nothing here is
    # cached across calls; every folder re-reads entitlements.

    def library(self):
        return [to_item(r) for r in self._get_json("library", "C1")]

    def member_free(self):
        return [to_item(r) for r in self._get_json("member_free", "C2")]

    def store(self):
        return [to_item(r) for r in self._get_json("store", "C3")]

    def title(self, title_id):
        return to_item(self._get_json("title", "C4", id=title_id))

    def extras(self, title_id):
        return [to_item(r) for r in self._get_json("extras", "C5", id=title_id)]

    def search(self, query):
        return [to_item(r) for r in self._get_json("search", "C6", q=query)]

    # -- playback ------------------------------------------------------------

    def playback(self, title_id):
        """{"manifest_url", "manifest_type": "hls"|"mpd", "license_url",
        "license_headers", "stream_headers", "subtitles"} for an entitled
        title (rows D-F). A 403 means the rental lapsed or the title is not
        entitled: RentalExpiredError, no retry."""
        url = _endpoint("playback", "D")
        resp = self.auth.request("GET", url, params={"id": title_id})
        if resp.status_code in (402, 403):
            raise RentalExpiredError(kodiutils.localize(32052))
        resp.raise_for_status()
        raise not_ready("D", "Playback response mapping")
