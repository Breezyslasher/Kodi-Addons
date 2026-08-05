"""Apple TV catalogue and playback API (reconstructed from the web client).

Captured from a real tv.apple.com session:

  Catalogue (anonymous browse works):
    GET tv.apple.com/api/uts/v3/configurations
    GET tv.apple.com/api/uts/v3/canvases/channels/tvs.sbd.4000   (TV+ Originals)
    GET tv.apple.com/api/uts/v3/search?q=...
  Tokens scraped from the tv.apple.com HTML shell:
    developerToken -> Bearer app token
    utsk           -> UTS session token
  Playback: the server-rendered title page embeds everything needed:
    "hlsUrl"          -> the HLS manifest URL (already carries the ?t= token)
    "userToken"       -> the account media-user-token (present when signed in)
    "fpsKeyServerUrl" -> the Widevine licence endpoint (JSON-wrapped, proxied)
    "assetAdamId"/"svcId"/"isExternal" -> licence-request parameters

Apple serves Widevine (not FairPlay) to non-Apple clients: the HLS manifest
carries a Widevine PSSH, so InputStream Adaptive can decrypt it.
"""

import json
import re
import time
from urllib.parse import parse_qs

from . import kodiutils
from . import license_proxy

UTS_BASE = "https://tv.apple.com/api/uts/v3"
# The same UTS API, asked as Apple's desktop TV app rather than the website.
# It matters for one thing only, and it is the thing tv.apple.com cannot do:
# an owned iTunes title's detail comes back with personalizedOffers, whose
# hlsUrl is the purchase's own stream. The web caller is sent the buy and rent
# offers and nothing else, which is why the website will not play a purchase.
UTS_STORE_BASE = "https://uts-api.itunes.apple.com/uts/v3"
UTS_STORE_CALLER = "wlk"
UTS_STORE_PFM = "windows"
UTS_STORE_VERSION = "94"
# The placeholder id the debug menu entry plays under. Only this id gets the
# pasted manifest; a real title always resolves through the catalogue.
DEBUG_CONTENT_ID = "debug"
WEB_HOME = "https://tv.apple.com"
WIDEVINE_CERT_URL = "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/widevineCert"

DEFAULT_STOREFRONT = "143441"
DEFAULT_LOCALE = "en-US"
UTS_VERSION = "96"
UTS_CLIENT_FLAGS = "OjAAAAEAAAAAAAIAEAAAACMAKwAtAA~~"
APPLE_TV_PLUS_CHANNEL = "tvs.sbd.4000"

# The brand tabs tv.apple.com puts along the top of the home page. Each is a
# canvas of its own under /canvases/channels/{id}; the ids and names are the
# ones Apple returns in the "channels" map of those responses.
# Store content. Its playables say whether the account owns a title but carry
# no assets: the stream comes from the store's own download service, which
# this addon cannot reach. See docs/itunes-library.md.
ITUNES_CHANNEL = "tvs.sbd.9001"
MLS_CHANNEL = "tvs.sbd.7000"
F1_CHANNEL = "tvs.sbd.241000"
CHANNELS = (
    (APPLE_TV_PLUS_CHANNEL, "Apple TV+"),
    (MLS_CHANNEL, "MLS"),
    (F1_CHANNEL, "Formula 1"),
)

# How long a scraped utsk is reused before the shell is fetched again.
# Apple reports a two-hour life; refreshing at one keeps a wide margin.
BOOT_CACHE_SECONDS = 3600

CANVAS_CACHE = "canvas_cache_%s.json"
# Streams found inline on shelf items. The sports channels list clip types
# (NotableMoment, Interview, KeyPlay, ...) that have no detail endpoint of
# their own but carry a full set of playable assets in the shelf itself.
STREAM_CACHE = "stream_cache.json"
STREAM_CACHE_LIMIT = 600

# Shelf entries that are navigation, not something to play. Apple gives these
# no playables at all, so listing them as items would only produce dead ones.
# Room, Team and GrandPrix are not here: each has a canvas of its own.
# MovieBundle is a store collection -- a trilogy pack, a director's set --
# and store search returns them alongside films. It is not playable: its id is
# umc.cmr.its.bun.*, which /movies/ answers with "movie not found", and no
# capture shows any client fetching one, so there is nothing to copy. Listed
# as a container so it is skipped rather than offered as a dead end.
CONTAINER_TYPES = ("Brand", "Upsell", "Preview", "Person", "Originals", "MLS",
                   "MovieBundle")

# Canvas shelves on a title's detail page that hold its extra videos.
TRAILER_SHELF_PREFIX = "uts.col.Trailers"
BONUS_SHELF_PREFIX = "uts.col.BonusContent"

# Apple ships several artworks per item in two shapes: tall/portrait posters
# (e.g. posterArt, 2000x3000) and wide/landscape stills (e.g. coverArt16X9,
# 3840x2160). Each entry declares the source width/height, so the shape is
# read from those rather than assumed, and the requested box is given the same
# aspect ratio as its source -- asking mzstatic for a box of a different shape
# is what cut the posters off.
# Some keys (notably shelfItemImage) are tall for one title and wide for the
# next, so these lists only order candidates of a shape that has already been
# determined from the declared dimensions; a key may appear in both.
PORTRAIT_IMAGE_KEYS = (
    "posterArt", "showPosterArt", "contentImageTall", "shelfItemImageTall",
    "shelfItemImage", "shelfImageBackgroundTall", "epicStageTallImage",
)
WIDE_IMAGE_KEYS = (
    "coverArt16X9", "contentImage16X9", "contentImage", "contentImageLive",
    "contentImagePost", "shelfItemImage", "shelfItemImageLive",
    "shelfItemImagePost", "posterImageLive", "posterImagePost", "coverArt",
    "previewFrame", "shelfImageBackground", "epicStageWideImage",
    "transitionImage",
)
# Logos, glyphs and badges: never usable as poster/thumb/fanart.
IMAGE_KEY_DENYLIST = (
    "logo", "glyph", "icon", "badge", "overlay", "header", "splash",
)

# Long edge requested per Kodi art type. The short edge is derived from the
# source's own aspect ratio, so nothing is cropped or letterboxed.
POSTER_HEIGHT = 900
THUMB_WIDTH = 1280
FANART_WIDTH = 1920

# Body the web app posts to /configurations to obtain a utsk session token.
CLIENT_FEATURE_FLAGS = {"featureFlags": {"clientFeatures": [
    {"name": "catch_up_to_live", "domain": "tvapp", "enabled": True},
    {"name": "opal", "domain": "tvapp", "enabled": True},
    {"name": "plato", "domain": "tvapp", "enabled": True},
    {"name": "server_side_one_tap_multiview", "domain": "tvapp", "enabled": False},
    {"name": "simple_profiles", "domain": "tvapp", "enabled": False},
]}}

# Watch-history reporting ("now playing").
NOW_PLAYING_URL = "https://tv.apple.com/api/np/play/json"
ACCOUNT_INFO_URL = "https://buy.tv.apple.com/account/web/infoRefresh"
PLAYBACK_REPORT_CACHE = "now_playing.json"

# The Up Next list is served as an ordinary shelf, with the context values
# below rather than ones taken from a canvas (nothing links to it from one).
WATCHLIST_SHELF = "uts.col.Watchlist"
# The account-wide resume row. Apple's own configurations document names the
# endpoint outright -- getContinueWatchingShelf -> /uts/v3/shelves/
# continue-watching -- and unlike the per-channel ChannelUpNext shelf (which is
# scoped to tvs.sbd.4000 and so is Apple TV+ only) this one carries every
# service the account is mid-way through, iTunes purchases included.
CONTINUE_WATCHING_SHELF = "continue-watching"
WATCHLIST_CVS = "uts.tcvs.tv-plus-personalized-canvas-adaptive"
WATCHLIST_CTX_SHELF = "uts.shlf.gen.Watchlist_%s"

SUBSCRIPTION_STATUS_URL = \
    "https://speedysub.tv.apple.com/subscription/v1/web/status/tv"


class AppleTVApi(object):
    def __init__(self, auth):
        self.auth = auth
        self.session = auth.session
        self._boot = None
        # Message explaining the most recent playback failure, when there is a
        # better one than "could not be resolved" (e.g. an event not yet live).
        self.last_error = None

    # -- bootstrap (scrape tokens from the web shell) --------------------

    def _bootstrap(self, force=False):
        if self._boot is not None and not force:
            return self._boot

        # Kodi runs a fresh process for every navigation, so an in-memory
        # cache saves nothing between screens. Apple says a utsk lasts two
        # hours (expirationInSeconds on /configurations), so a token kept on
        # disk is reused until it is nearly due, and the 1 MB page shell is
        # fetched once an hour instead of once per directory listing.
        if not force:
            cached = self.auth.tokens.get("boot") or {}
            if cached.get("utsk") and cached.get("developer_token"):
                age = time.time() - (cached.get("stamp") or 0)
                if 0 <= age < BOOT_CACHE_SECONDS:
                    self._boot = {
                        "utsk": cached["utsk"],
                        "developer_token": cached["developer_token"],
                        "storefront": cached.get("storefront") or DEFAULT_STOREFRONT,
                        "user_token": self.auth.tokens.get("media_user_token"),
                    }
                    return self._boot

        boot = {"utsk": None, "developer_token": None, "storefront": DEFAULT_STOREFRONT,
                "user_token": None}
        try:
            # Fetch the page as a browser would. The shared session carries
            # idmsa API headers (Accept: application/json, X-Requested-With,
            # Origin) which, once signed in, make tv.apple.com return a non-HTML
            # response with no embedded tokens. Override them per request.
            html = self.session.get(WEB_HOME, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": None,
                "X-Requested-With": None,
                "Origin": None,
                "Referer": None,
            }, timeout=30).text
            m = re.search(r'"utsk"\s*:\s*"([^"]+)"', html)
            if m:
                boot["utsk"] = m.group(1)
            m = re.search(r'"developerToken"\s*:\s*"([A-Za-z0-9_.\-]{20,})"', html)
            if m:
                boot["developer_token"] = m.group(1)
            m = re.search(r'"storefrontId"\s*:\s*"?(\d+)"?', html)
            if m:
                boot["storefront"] = m.group(1)
            # Present only when the session is signed in to tv.apple.com; this
            # is the account media-user-token needed for playback.
            m = re.search(r'"userToken"\s*:\s*"([^"]+)"', html)
            if m:
                boot["user_token"] = m.group(1)
                self.auth.tokens["media_user_token"] = m.group(1)
                self.auth.save()
        except Exception as exc:
            kodiutils.log_error("Bootstrap scrape failed: %s" % exc)
        # Fall back to the last good tokens if this scrape came up empty.
        cached = self.auth.tokens.get("boot") or {}
        if not boot["utsk"]:
            boot["utsk"] = cached.get("utsk")
        if not boot["developer_token"]:
            boot["developer_token"] = cached.get("developer_token")
        if not boot["utsk"] and boot["developer_token"]:
            # The shell no longer carried one; ask for a session token the way
            # the web app itself does.
            boot["utsk"] = self._request_utsk(boot["developer_token"],
                                              boot["storefront"])
        if not boot["utsk"]:
            kodiutils.log_error("Could not obtain utsk token from tv.apple.com")
        else:
            self.auth.tokens["boot"] = {"utsk": boot["utsk"],
                                        "developer_token": boot["developer_token"],
                                        "storefront": boot["storefront"],
                                        "stamp": time.time()}
            self.auth.save()
        self._boot = boot
        return boot

    def _request_utsk(self, developer_token, storefront):
        """Ask Apple for a UTS session token instead of scraping one.

        The web app posts its feature flags to /configurations and reads utsk
        back from utskProps. Scraping the HTML shell is still tried first
        because it also yields the developer token, which this does not.
        """
        headers = {"authorization": "Bearer " + developer_token,
                   "Content-Type": "application/json",
                   "Origin": WEB_HOME}
        mut = self._media_user_token()
        if mut:
            headers["media-user-token"] = mut
        params = {"caller": "web", "locale": self._locale(), "pfm": "web",
                  "sfh": storefront, "v": UTS_VERSION}
        try:
            resp = self.session.post(
                UTS_BASE + "/configurations", params=params, headers=headers,
                data=json.dumps(CLIENT_FEATURE_FLAGS, separators=(",", ":")),
                timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("configurations -> %s %s"
                                    % (resp.status_code, resp.text[:200]))
                return None
            props = ((resp.json().get("data") or {}).get("utskProps")) or {}
            if props.get("utsk"):
                kodiutils.log("Obtained utsk from /configurations")
                return props["utsk"]
        except Exception as exc:
            kodiutils.log_error("configurations request failed: %s" % exc)
        return None

    def _media_user_token(self):
        tok = (kodiutils.get_setting("media_user_token")
               or self._bootstrap().get("user_token")
               or self.auth.tokens.get("media_user_token"))
        if tok:
            return tok
        # Signed in but no token yet: mint one via the store-login exchange.
        if self.auth.tokens.get("authenticated"):
            dev = self._bootstrap().get("developer_token")
            if dev:
                return self.auth.authorize_media(dev)
        return None

    def _storefront(self):
        return kodiutils.get_setting("storefront") or self._bootstrap().get("storefront") or DEFAULT_STOREFRONT

    def _locale(self):
        return kodiutils.get_setting("locale") or DEFAULT_LOCALE

    def _country(self):
        loc = self._locale()
        return (loc.split("-")[-1] if "-" in loc else "us").lower()

    def _params(self, extra=None):
        params = {
            "caller": "web",
            "pfm": "web",
            "v": UTS_VERSION,
            "locale": self._locale(),
            "sf": self._storefront(),
            "utscf": UTS_CLIENT_FLAGS,
            "utsk": self._bootstrap().get("utsk") or "",
        }
        if extra:
            params.update(extra)
        return params

    def _uts_headers(self):
        """Identify the account on catalogue requests.

        Without these the responses are the signed-out ones: a canvas comes
        back with no personalised shelves (no Continue Watching) and the
        watchlist shelf is empty. The site sends both on every UTS call.
        """
        headers = {"Origin": WEB_HOME}
        token = self._bootstrap().get("developer_token")
        if token:
            headers["authorization"] = "Bearer " + token
        mut = self._media_user_token()
        if mut:
            headers["media-user-token"] = mut
        return headers

    def _get_json(self, path, extra_params=None, _retried=False):
        try:
            resp = self.session.get(UTS_BASE + path, params=self._params(extra_params),
                                    headers=self._uts_headers(), timeout=30)
            if resp.status_code in (401, 403) and not _retried:
                # The cached session token has been rejected: get a fresh one
                # and try once more, so a stale cache costs a retry rather
                # than a failed screen.
                kodiutils.log("UTS %s -> %s; refreshing tokens" % (path, resp.status_code))
                self._bootstrap(force=True)
                return self._get_json(path, extra_params, _retried=True)
            if resp.status_code != 200:
                kodiutils.log_error("UTS %s -> %s %s" % (path, resp.status_code, resp.text[:200]))
                return None
            return resp.json()
        except Exception as exc:
            kodiutils.log_error("UTS request error %s: %s" % (path, exc))
            return None

    # -- catalogue -------------------------------------------------------

    def get_originals_shelves(self):
        return self.get_channel_shelves(APPLE_TV_PLUS_CHANNEL)

    def get_channel_shelves(self, channel_id, max_pages=10):
        """Shelves of one brand tab (Apple TV+, MLS, Formula 1, ...)."""
        return self._canvas_shelves(
            "/canvases/channels/%s" % channel_id,
            {"includePlatter": "true", "platterPassThrough": "true"},
            channel_id, max_pages)

    def get_room_shelves(self, room_id, channel_id=None, max_pages=10):
        """Shelves of a room -- a browse category such as Kids & Family.

        Rooms are canvases like the channel tabs, keyed by canvasType: the
        site opens one with /canvases/rooms/{id}?ctx_brand={channel}.
        """
        return self._canvas_shelves(
            "/canvases/rooms/%s" % room_id,
            {"ctx_brand": channel_id or APPLE_TV_PLUS_CHANNEL},
            room_id, max_pages)

    def get_grand_prix_shelves(self, gp_id, channel_id=None, max_pages=10):
        """Shelves of one Grand Prix weekend (its sessions and highlights)."""
        return self._canvas_shelves(
            "/canvases/grandPrix/%s" % gp_id,
            {"ctx_brand": channel_id or F1_CHANNEL},
            gp_id, max_pages)

    def get_team_shelves(self, team_id, max_pages=10):
        """Shelves of a team page (an MLS club).

        Another canvas type; the site asks for it with no ctx parameters at
        all, unlike a room.
        """
        return self._canvas_shelves(
            "/canvases/teams/%s" % team_id, {}, team_id, max_pages)

    def set_team_favourite(self, team_id, favourite=True):
        """Follow or unfollow a club, as the heart on its page does."""
        return self._list_request("/favorite-teams", team_id, favourite)

    def set_watchlisted(self, content_id, watchlisted=True):
        """Add a title or event to the account's Up Next list, or remove it."""
        return self._list_request("/watchlist", content_id, watchlisted)

    def get_watchlist(self, channel_id=None, max_pages=20):
        """The account's Up Next list.

        Nothing in a canvas links to it, so its context values are the ones
        the site sends when the list is opened directly.
        """
        brand = channel_id or APPLE_TV_PLUS_CHANNEL
        ctx = {"ctx_brand": brand,
               "ctx_cvs": WATCHLIST_CVS,
               "ctx_shelf": WATCHLIST_CTX_SHELF % brand}
        items = []
        seen = set()
        token = None
        for _ in range(max_pages):
            params = dict(ctx)
            if token:
                params["nextToken"] = token
            data = self._get_json("/shelves/%s" % WATCHLIST_SHELF, params)
            shelf = ((data or {}).get("data") or {}).get("shelf")
            if not isinstance(shelf, dict):
                break
            page = self._extract_items(shelf.get("items"))
            for item in page:
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    items.append(item)
            token = shelf.get("nextToken") or None
            if not token or not page:
                break
        return items

    def get_continue_watching(self, max_pages=10):
        """The account-wide Continue Watching row, across every service.

        The per-channel Up Next the home page shows is scoped to Apple TV+, so
        it never lists an iTunes purchase. This asks Apple's account-wide shelf
        instead, the one the TV apps show, which mixes Apple TV+ episodes with
        iTunes films the account is part-way through -- The Greatest Showman
        among them in a capture of it.

        The capture came from a store-authenticated client, and the website's
        own caller is served a narrower catalogue elsewhere (its search returns
        Apple TV+ only, its Up Next is TV+ only), so the web caller may be sent
        an empty shelf here too. It is tried first all the same, and the store
        caller -- the one the capture used -- is the fallback, mirroring how
        search widens from web to store. Whichever returns rows is listed; an
        iTunes film resumes through the same purchase path as the library.
        """
        items = self._continue_watching_via(self._get_json, "web", max_pages)
        if not items:
            items = self._continue_watching_via(self._store_json, "store",
                                                 max_pages)
        kodiutils.log("Continue Watching: %d item(s)" % len(items))
        return items

    def _continue_watching_via(self, getter, label, max_pages):
        """Page the continue-watching shelf through one caller.

        Logs why a caller came back empty -- no shelf in the response at all
        (the caller is not served this endpoint) versus a shelf present but
        carrying no items (served, but this identity's row is empty) -- so a
        zero is diagnosable rather than silent.
        """
        ctx = {"ctx_brand": APPLE_TV_PLUS_CHANNEL}
        items = []
        seen = set()
        token = None
        saw_shelf = False
        raw_total = 0
        for _ in range(max_pages):
            params = dict(ctx)
            if token:
                params["nextToken"] = token
            data = getter("/shelves/%s" % CONTINUE_WATCHING_SHELF, params)
            shelf = ((data or {}).get("data") or {}).get("shelf")
            if not isinstance(shelf, dict):
                break
            saw_shelf = True
            raw = self._as_list(shelf.get("items"))
            raw_total += len(raw)
            for item in self._extract_items(raw):
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    items.append(item)
            token = shelf.get("nextToken") or None
            if not token or not raw:
                break
        kodiutils.log("Continue Watching (%s caller): shelf=%s, raw items=%d, "
                      "listed=%d" % (label, "yes" if saw_shelf else "no",
                                     raw_total, len(items)))
        return items

    def get_followed_teams(self, channel_id):
        """Clubs the account follows, from the tabs already fetched.

        Apple's own favourites shelf on the page is an empty marker that the
        website fills in client-side, so this does the same: every club tile
        reports whether it is followed.
        """
        cache = kodiutils.read_json(self._canvas_cache_name(channel_id),
                                    default={}) or {}
        followed = []
        seen = set()
        for entry in cache.values():
            items = entry.get("items") if isinstance(entry, dict) else entry
            for item in items or []:
                if not isinstance(item, dict) or item.get("type") != "Team":
                    continue
                if item.get("favourite") and item.get("id") not in seen:
                    seen.add(item["id"])
                    followed.append(item)
        return followed

    def get_related(self, content_id, league_id=None):
        """"More like this" for a title, or the other games in a league.

        Both are ordinary shelves keyed by the content id, differing only in
        the context parameter each wants.
        """
        if league_id:
            shelf = "uts.col.SportsRelated.%s" % content_id
            params = {"ctx_league": league_id}
        else:
            shelf = "uts.col.ContentRelated.%s" % content_id
            params = {"ctx_contentId": content_id}
        data = self._get_json("/shelves/%s" % shelf, params)
        node = ((data or {}).get("data") or {}).get("shelf")
        if not isinstance(node, dict):
            return []
        return self._extract_items(node.get("items"))

    # A match page carries these alongside its clubs and related games. Like
    # the clubs shelf they take no context parameters, and their items are the
    # clip types that carry their stream inline rather than having a page.
    EVENT_SHELVES = {"highlights": "uts.col.SportsHighlights",
                     "spotlight": "uts.col.MatchSpotlight",
                     "weekend": "uts.col.F1GrandPrixWeekendSchedule"}

    def get_event_extras(self, event_id, kind="highlights"):
        """One of the shelves on a match or race page.

        The shelf is found on the event's own page rather than addressed
        directly, because two of the three cannot be addressed: highlights and
        spotlight are keyed by the event, but a Grand Prix weekend is keyed by
        a league id that appears nowhere else -- the races it lists report a
        different league of their own.

        Not every event has every shelf. Of three MLS matches captured, all
        had highlights and one had a spotlight; both F1 races had all three,
        and neither had the clubs or related shelves an MLS match carries.
        """
        prefix = self.EVENT_SHELVES.get(kind)
        if not prefix:
            return []
        data = self._get_json("/sporting-events/%s" % event_id, {})
        canvas = ((data or {}).get("data") or {}).get("canvas") or {}
        for shelf in canvas.get("shelves") or []:
            if str(shelf.get("id") or "").startswith(prefix):
                return self._extract_items(shelf.get("items"))
        return []

    def get_event_clubs(self, event_id):
        """The clubs playing in a match; it takes no context parameters."""
        data = self._get_json("/shelves/uts.col.Teams.%s" % event_id, {})
        node = ((data or {}).get("data") or {}).get("shelf")
        if not isinstance(node, dict):
            return []
        return self._extract_items(node.get("items"))

    def logout(self):
        """End the session with Apple as well as forgetting it locally.

        The site posts to both the v1 and v2 endpoints; each takes the bearer
        token, no body. Failure is not fatal -- the local tokens are cleared
        either way -- so this only reports whether Apple acknowledged.
        """
        bearer = self._bootstrap().get("developer_token")
        if not bearer:
            return False
        ok = False
        for path in ("/auth/v2/web/logout", "/auth/v1/web/logout"):
            try:
                resp = self.session.post(
                    "https://auth.tv.apple.com" + path,
                    headers={"authorization": "Bearer " + bearer,
                             "Origin": WEB_HOME},
                    timeout=30)
                ok = ok or resp.status_code in (200, 204)
            except Exception as exc:
                kodiutils.log_error("logout %s failed: %s" % (path, exc))
        return ok

    def subscription_status(self):
        """Apple TV+ subscription state, or None when it cannot be read."""
        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        if not bearer or not mut:
            return None
        try:
            resp = self.session.get(
                SUBSCRIPTION_STATUS_URL,
                headers={"authorization": "Bearer " + bearer,
                         "media-user-token": mut,
                         "Origin": WEB_HOME},
                timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("subscription status -> %s" % resp.status_code)
                return None
            return resp.json()
        except Exception as exc:
            kodiutils.log_error("subscription status failed: %s" % exc)
            return None

    def _list_request(self, path, item_id, add=True):
        """Add to or remove from one of Apple's per-account lists.

        Both the favourite-clubs and watchlist endpoints take the same shape:
        the id goes in a query parameter, POST adds (repeating the id in a
        JSON body) and DELETE removes. Needs a signed-in account.
        """
        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        if not bearer or not mut:
            kodiutils.log_error("%s needs a signed-in account" % path)
            return False
        headers = {"authorization": "Bearer " + bearer,
                   "media-user-token": mut,
                   "Origin": WEB_HOME}
        url = UTS_BASE + path
        params = self._params({"id": item_id})
        try:
            if add:
                headers["Content-Type"] = "application/json"
                resp = self.session.post(
                    url, params=params, headers=headers,
                    # Compact, to match the body the web client sends byte for
                    # byte rather than only semantically.
                    data=json.dumps({"id": item_id}, separators=(",", ":")),
                    timeout=30)
            else:
                resp = self.session.delete(url, params=params, headers=headers,
                                           timeout=30)
        except Exception as exc:
            kodiutils.log_error("%s request failed: %s" % (path, exc))
            return False
        if resp.status_code != 200:
            kodiutils.log_error("%s -> %s %s"
                                % (path, resp.status_code, resp.text[:200]))
            return False
        return True

    # -- watch history ("now playing") -----------------------------------

    def _consumer_id(self):
        """The account id the now-playing report is filed under (pldfltcid)."""
        cached = self.auth.tokens.get("consumer_id")
        if cached:
            return cached
        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        if not bearer or not mut:
            return None
        try:
            resp = self.session.get(
                ACCOUNT_INFO_URL,
                headers={"authorization": "Bearer " + bearer,
                         "media-user-token": mut,
                         "Origin": WEB_HOME},
                timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("account infoRefresh -> %s" % resp.status_code)
                return None
            cid = (resp.json() or {}).get("pldfltcid")
        except Exception as exc:
            kodiutils.log_error("account infoRefresh failed: %s" % exc)
            return None
        if cid:
            self.auth.tokens["consumer_id"] = cid
            self.auth.save()
        return cid

    def now_playing_token(self, assets, duration_secs):
        """Exchange a playable's assets for a now-playing pass-through token.

        Apple mints the token that identifies this playback session from the
        playable's own pass-through, its external id and the stream duration.
        """
        passthrough = assets.get("playable_passthrough")
        external_id = assets.get("external_id")
        if not passthrough or not external_id:
            return None
        bearer = self._bootstrap().get("developer_token")
        headers = {"Origin": WEB_HOME}
        if bearer:
            headers["authorization"] = "Bearer " + bearer
        mut = self._media_user_token()
        if mut:
            headers["media-user-token"] = mut
        params = self._params({
            "brandId": assets.get("brand_id") or APPLE_TV_PLUS_CHANNEL,
            "externalId": external_id,
            "hlsAssetDuration": str(int(duration_secs or 0)),
            "playablePassThrough": passthrough,
        })
        # The web client sends utscf here but no utsk; match it.
        params.pop("utsk", None)
        try:
            resp = self.session.get(UTS_BASE + "/contents/play-metadata/vod",
                                    params=params, headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("play-metadata -> %s %s"
                                    % (resp.status_code, resp.text[:200]))
                return None
            return ((resp.json().get("data") or {}).get("nowPlayingPassThrough"))
        except Exception as exc:
            kodiutils.log_error("play-metadata failed: %s" % exc)
            return None

    def report_now_playing(self, token, position_secs, duration_secs, finished=False):
        """Tell Apple where playback has reached, updating Up Next.

        This is what makes a title appear in Continue Watching and resume at
        the right place on Apple's own clients.
        """
        if not token:
            return False
        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        consumer = self._consumer_id()
        if not bearer or not mut or not consumer:
            return False
        head = int(max(0, position_secs) * 1000)
        length = int(max(0, duration_secs) * 1000)
        body = {
            "context": {
                "user": {"id": consumer, "keyspace": "cid"},
                "userAgent": self.session.headers.get("User-Agent", ""),
                "appleStoreFront": int(self._storefront()),
                "source": "Client_Device",
                "cadence": "Contract",
                "bundleId": "com.apple.tv",
                "millisecondsSinceEvent": 0,
            },
            "event": {"vodEvent": {
                "playHeadInMilliseconds": head,
                "mediaLengthInMilliseconds": length,
                "mainContentInfo": {
                    "isDone": "Done" if finished else "Not_Done",
                    "playHeadInMilliseconds": head,
                    "lengthInMilliseconds": length,
                    "passThrough": token,
                },
            }},
        }
        try:
            resp = self.session.post(
                NOW_PLAYING_URL,
                headers={"authorization": "Bearer " + bearer,
                         "media-user-token": mut,
                         # Apple's own client sends text/plain here, not JSON.
                         "Content-Type": "text/plain;charset=UTF-8",
                         "Origin": WEB_HOME},
                data=json.dumps(body, separators=(",", ":")), timeout=30)
        except Exception as exc:
            kodiutils.log_error("now-playing report failed: %s" % exc)
            return False
        if resp.status_code not in (200, 202, 204):
            kodiutils.log_error("now-playing -> %s %s"
                                % (resp.status_code, resp.text[:200]))
            return False
        return True

    def _canvas_shelves(self, path, params, cache_key, max_pages=10):
        """Walk a canvas to its last page and cache each shelf's first page.

        A canvas is paged: the response carries canvas.nextToken, an offset to
        hand back as the nextToken parameter for the next batch of shelves.
        The site itself walks this until a page comes back with no shelves and
        no token, which is where the last one stops.
        """
        shelves = []
        seen = set()
        token = None
        for _ in range(max_pages):
            request = dict(params)
            if token:
                request["nextToken"] = token
            data = self._get_json(path, request)
            if not data:
                break
            page = self._extract_shelves(data)
            for shelf in page:
                if shelf["id"] not in seen:
                    seen.add(shelf["id"])
                    shelves.append(shelf)
            token = self._canvas_next_token(data)
            if not token or not page:
                break
        # Cache each shelf's first page and what is needed to fetch the rest.
        # Kept per canvas so opening another does not evict this one's.
        cache = {s["id"]: {"items": s["items"], "next": s.get("next"),
                           "ctx": s.get("ctx") or {}}
                 for s in shelves if s.get("id")}
        kodiutils.write_json(self._canvas_cache_name(cache_key), cache)
        return shelves

    @staticmethod
    def _canvas_next_token(data):
        """The canvas' own paging offset, absent once the shelves run out."""
        root = data.get("data") if isinstance(data, dict) and "data" in data else data
        canvas = root.get("canvas") if isinstance(root, dict) else None
        token = canvas.get("nextToken") if isinstance(canvas, dict) else None
        return str(token) if token not in (None, "") else None

    def get_shelf_items(self, shelf_id, channel_id=None, max_pages=20):
        """A shelf's items, following its own paging to the end.

        The canvas only sends a shelf's first page. Handing its nextToken back
        to /shelves/{id}, along with the ctx_* parameters the shelf's url
        spells out, returns the next batch; the last page carries no token.
        """
        cache = kodiutils.read_json(
            self._canvas_cache_name(channel_id or APPLE_TV_PLUS_CHANNEL),
            default={}) or {}
        entry = cache.get(shelf_id)
        if not entry:
            return []
        if isinstance(entry, list):
            return entry  # cache written by an older version of the addon

        cached = entry.get("items") or []
        ctx = entry.get("ctx") or {}
        if not ctx:
            return cached

        # Fetch the shelf afresh rather than continuing from the copy the
        # canvas embedded. Personalised shelves return a different selection
        # each time, so pairing a stale first page with freshly-paged ones
        # would leave holes; the site refetches from the start too.
        items = []
        seen = set()
        token = None
        for _ in range(max_pages):
            params = dict(ctx)
            if token:
                params["nextToken"] = token
            data = self._get_json("/shelves/%s" % shelf_id, params)
            shelf = ((data or {}).get("data") or {}).get("shelf")
            if not isinstance(shelf, dict):
                break
            page = self._extract_items(shelf.get("items"))
            for item in page:
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    items.append(item)
            # Never build a token: the format varies by shelf (20:0:20,
            # 1:0:20, then 40:0:40) and only Apple's reply knows the next one.
            token = shelf.get("nextToken") or None
            if not token or not page:
                break
        return items or cached

    def _canvas_cache_name(self, channel_id):
        """Cache a signed-in canvas apart from a signed-out one.

        The two are different documents -- only the signed-in one carries the
        personalised shelves, Continue Watching among them -- so sharing a
        file meant signing in kept serving the anonymous copy until it aged
        out, and signing out kept serving the personalised one.
        """
        who = "user" if self._media_user_token() else "anon"
        return CANVAS_CACHE % ("%s_%s" % (str(channel_id).replace("/", "_"), who))

    def search(self, query):
        """Everything Apple finds for a term, not just the type-ahead.

        topResultsOnly is what the site sends while someone is still typing:
        one shelf of ten mixed hits. The full search returns Top Results plus
        a Movies shelf plus a TV Shows shelf -- for "greatest" that is 19, 27
        and 27 rather than 10 -- and the store titles an account may already
        own are in the longer lists rather than the top ten.

        The shelves overlap, so a title in Top Results and again under Movies
        is listed once.

        Which catalogue is searched is the caller's doing. tv.apple.com's
        search answers with Apple TV+ originals and nothing else -- "ted"
        returns Ted Lasso, Shrinking and Wolfs -- while the same API asked as
        the store app also returns Green Book, Oppenheimer and Hidden Figures,
        titles that exist only as purchases.

        Purchases do not play here (see docs/itunes-library.md: they license
        under FairPlay, which Kodi has no CDM for), so by default the store is
        left out and search lists what can actually be watched. Turning the
        setting off searches everything.

        Filtering the store results down to the ones the account owns is not
        possible cheaply, and is deliberately not attempted: a search result
        carries no channel and no entitlement -- only id, title, type, genres,
        duration, release date, artwork and a playbackModes that reads
        "Monoscopic" for everything -- so ownership would mean one extra
        request per result, seventy-odd for a broad search.
        """
        if kodiutils.get_setting_bool("search_appletv_only", True):
            return self._search_results(
                self._get_json("/search", {"searchTerm": query}))
        data = self._store_json("/search", {"searchTerm": query})
        if not data:
            kodiutils.log("Store search unavailable; falling back to the web "
                          "search, which lists Apple TV+ titles only")
            data = self._get_json("/search", {"searchTerm": query})
        return self._search_results(data)

    def _search_results(self, data):
        """Flatten a search response's shelves, keeping each title once."""
        items = []
        seen = set()
        for shelf in self._extract_shelves(data):
            for item in shelf["items"]:
                key = item.get("id")
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                items.append(item)
        return items

    def search_hints(self, term):
        """Apple's search suggestions for a partly-typed term."""
        data = self._get_json("/search/hints", {"searchTerm": term})
        hints = ((data or {}).get("data") or {}).get("hints")
        out = []
        for hint in hints or []:
            if isinstance(hint, dict) and hint.get("searchTerm"):
                out.append({"term": hint["searchTerm"],
                            "label": hint.get("displayTerm") or hint["searchTerm"]})
        return out

    def get_search_landing(self):
        """The browse page Apple shows before anything is typed."""
        return self._canvas_shelves("/search/landing", {}, "search_landing")

    def last_played_id(self):
        """Content id of the last title played through the addon, if any."""
        context = kodiutils.read_json(PLAYBACK_REPORT_CACHE, default={}) or {}
        return context.get("content_id")

    def get_show_seasons(self, show_id):
        """A show's seasons, when it has more than one.

        Asking for the episode list with includeSeasonSummary returns a
        seasonSummaries array alongside it, which is where the site gets the
        season picker. Returns [] for a show with a single season, so the
        caller can go straight to the episodes.
        """
        data = self._get_json("/shows/%s/episodes" % show_id,
                              {"includeSeasonSummary": "true"})
        summaries = self._deep_find(data, "seasonSummaries") if data else None
        seasons = []
        for raw in self._as_list(summaries):
            if not isinstance(raw, dict) or raw.get("seasonNumber") is None:
                continue
            seasons.append({
                "number": raw.get("seasonNumber"),
                "title": raw.get("title") or "Season %s" % raw.get("seasonNumber"),
                "count": raw.get("episodeCount"),
            })
        return seasons if len(seasons) > 1 else []

    def get_show_episodes(self, show_id, page=30, max_pages=25, season=None):
        """Return a show's episodes (paginated via nextToken 'offset:size').

        A season is filtered from the full list rather than requested: the
        capture shows the site paging every episode with
        selectedSeasonEpisodesOnly=false, and no per-season request to copy.
        """
        episodes = []
        offset = 0
        for _ in range(max_pages):
            data = self._get_json(
                "/shows/%s/episodes" % show_id,
                {"nextToken": "%d:%d" % (offset, page),
                 "includeSeasonSummary": "false",
                 "selectedSeasonEpisodesOnly": "false"},
            )
            raw_eps = self._deep_find(data, "episodes") if data else None
            if not raw_eps:
                break
            for raw in raw_eps:
                item = self._map_item(raw, force_type="Episode")
                if item:
                    episodes.append(item)
            if len(raw_eps) < page:
                break
            offset += page
        if season is not None:
            episodes = [e for e in episodes if str(e.get("season")) == str(season)]
        return self._harvest_streams(episodes)

    # -- playback --------------------------------------------------------

    def list_playables(self, content_id, item_type="Movie"):
        """The distinct feeds a title offers, when it offers more than one.

        A match is published several times over: a full replay beside a ten
        minute recap, and one feed per commentary language. They differ by
        externalId, so that is what identifies the chosen one.
        """
        self.last_error = None
        data, _mut = self._detail_json(content_id, item_type)
        if data is None:
            return []
        playables = self._deep_find(data, "playables")
        candidates = list(playables.values()) if isinstance(playables, dict) \
            else (playables or [])
        feeds = []
        for playable in candidates:
            if not isinstance(playable, dict):
                continue
            assets = playable.get("assets")
            if not isinstance(assets, dict) or not assets.get("hlsUrl"):
                continue
            if not playable.get("isEntitledToPlay"):
                continue
            locale = playable.get("primaryLocale") or {}
            feeds.append({
                "external_id": playable.get("externalId"),
                "title": playable.get("title") or "",
                "duration": playable.get("duration"),
                "language": locale.get("displayName") or "",
            })
        return feeds if len(feeds) > 1 else []

    def get_playback(self, content_id, item_type="Movie", external_id=None):
        """Resolve a title to an ISA-playable dict, or None."""
        self.last_error = None
        assets = self._prepare_playback(content_id, item_type, external_id)
        if not assets:
            # Sports clips have no detail endpoint; fall back to the stream the
            # shelf listed inline, which is the only one Apple ever offers.
            inline = self._cached_stream(content_id)
            if not inline:
                return None
            kodiutils.log("Using the stream listed inline for %s" % content_id)
            self.last_error = None
            assets = self._prepared_from_assets(inline, self._media_user_token())
        # A manifest pasted into the override setting is not a catalogue
        # stream, so an Apple TV+ account token is not what authorises it.
        return self._build_playback(
            assets, require_user_token=not assets.get("override"))

    def _build_playback(self, assets, require_user_token=True):
        """Turn resolved stream assets into the dict default.py plays.

        Shared by features and trailers: both arrive as the same asset shape
        (an hlsUrl plus the fps key-server details).
        """
        override = bool(assets.get("override"))
        boot = self._bootstrap()
        bearer = boot.get("developer_token")
        if not bearer and not override:
            kodiutils.log_error("No developer token; cannot request playback")
            return None

        mut = assets.get("user_token") or self._media_user_token()
        if not mut and require_user_token:
            kodiutils.log_error(
                "No media-user-token. Sign in (it is read from tv.apple.com when "
                "signed in), or paste one into the addon's advanced settings.")
            return None

        # Headers the manifest/segment requests need (token-authenticated).
        # A pasted manifest gets none of them: whatever authorises it travels
        # in the url, and sending tv.apple.com's credentials to some other
        # host would at best be ignored and at worst refused.
        if override:
            stream_headers = {
                "User-Agent": self.session.headers.get("User-Agent", ""),
            }
        else:
            stream_headers = {
                "authorization": "Bearer " + bearer,
                "Origin": WEB_HOME,
                "User-Agent": self.session.headers.get("User-Agent", ""),
            }
            if mut:
                stream_headers["media-user-token"] = mut
        wv_keys = self._collect_widevine_keys(assets["manifest"], stream_headers)
        kodiutils.log("Collected %d Widevine key(s)" % len(wv_keys))

        # First key collected is the video variant's; used to pre-initialise DRM
        # so a decrypter exists before the first encrypted chapter.
        pre_init = None
        for kid_hex, uri in wv_keys.items():
            pssh_b64 = self._pssh_from_data_uri(uri)
            if pssh_b64:
                import base64 as _b64
                pre_init = "%s|%s" % (
                    pssh_b64,
                    _b64.b64encode(bytes.fromhex(kid_hex)).decode("ascii"))
            break

        kodiutils.write_json("playback_context.json", {
            "bearer": bearer or "",
            "media_user_token": mut or "",
            "adam_id": assets.get("adam_id", ""),
            "svc_id": assets.get("svc_id", ""),
            "is_external": assets.get("is_external", True),
            # Lets the licence proxy tell a purchase from a catalogue title,
            # since the two are authorised by different sessions.
            "override": override,
            "itunes": assets.get("itunes", False),
            "wv_keys": wv_keys,
            "license_server": assets.get("license_server", ""),
            "user_agent": self.session.headers.get("User-Agent", ""),
        })
        return {
            # Served through the local proxy so the KEYID Apple omits can be
            # added; without it ISA decrypts with an all-zero key id.
            "manifest": license_proxy.manifest_url(assets["manifest"],
                                                   clear=not wv_keys),
            "manifest_type": "hls",
            # Says this stream came from the override setting, so callers can
            # skip the catalogue lookups that only make sense for a real id.
            "override": override,
            "license_url": license_proxy.license_url(),
            "certificate_b64": self.get_widevine_certificate(),
            "stream_headers": stream_headers,
            "pre_init_data": pre_init,
            # Trailers and some extras are served in the clear. Asking
            # InputStream Adaptive for a Widevine session on an unencrypted
            # stream leaves it waiting for a licence that can never arrive.
            "encrypted": bool(wv_keys),
            "report": {
                "playable_passthrough": assets.get("playable_passthrough"),
                "external_id": assets.get("external_id"),
                "brand_id": assets.get("brand_id"),
            },
        }

    def _prepare_playback(self, content_id, item_type, external_id=None):
        """Resolve playback assets via the UTS JSON endpoint.

        GET /api/uts/v3/{movies|episodes}/{id} with a Bearer developer token and
        the account media-user-token returns the playables, including hlsUrl (the
        manifest with its ?t= token), fpsKeyServerUrl and the asset ids. This
        avoids scraping the HTML page.
        """
        # Only the debug entry plays the pasted manifest. It used to apply to
        # everything, which meant a set override silently substituted itself
        # for whatever title was actually chosen -- picking one film and
        # playing another, with nothing on screen to say so.
        override = kodiutils.get_setting("manifest_url_override")
        if override and str(content_id) == DEBUG_CONTENT_ID:
            override = override.replace("&amp;", "&").strip()
            return {"manifest": override, "adam_id": self._q(override, "a"),
                    "svc_id": self._q(override, "svcId"), "is_external": True,
                    # Says this manifest did not come from the catalogue, so
                    # the Apple TV+ credentials below do not apply to it.
                    # is_external cannot carry that: it means something else
                    # on the licence path.
                    "override": True}

        data, mut = self._detail_json(content_id, item_type)
        if data is None:
            return None

        # A title can have several playables (feature you are entitled to, an
        # unentitled purchase option, trailers). Pick the entitled one with a
        # stream -- grabbing the first hlsUrl in the JSON returns the trailer.
        assets = self._select_playable_assets(data, external_id)
        if not assets or not assets.get("hlsUrl"):
            # A sporting event with no stream is usually one that has not
            # started; say so rather than reporting a generic failure.
            not_started = self.event_not_started_message(data)
            if not_started:
                kodiutils.log(not_started)
                self.last_error = not_started
                return None
            kodiutils.log_error(
                "No playable stream for %s. Likely not in your subscription/"
                "region, or no media-user-token." % content_id)
            return None

        # A live event can carry a stream before you are allowed to watch it.
        not_started = self.event_not_started_message(data)
        if not_started:
            kodiutils.log(not_started)
            self.last_error = not_started
            return None

        return self._prepared_from_assets(assets, mut)

    def _detail_json(self, content_id, item_type):
        """Fetch a title's UTS detail document; returns (data, media-user-token).

        The same document carries the feature's playables and the Trailers and
        Bonus Content shelves, so trailers need no extra request.
        """
        bearer = self._bootstrap().get("developer_token")
        mut = self._media_user_token()
        headers = {}
        if bearer:
            headers["authorization"] = "Bearer " + bearer
        if mut:
            headers["media-user-token"] = mut
            headers["Origin"] = WEB_HOME

        if str(item_type) == "SportingEvent" or str(content_id).startswith("umc.cse."):
            endpoint = "sporting-events"
        elif str(item_type) == "Episode":
            endpoint = "episodes"
        elif str(item_type) == "Show":
            endpoint = "shows"
        else:
            endpoint = "movies"
        text = self._get_text("/%s/%s" % (endpoint, content_id),
                              {"ctx_brand": APPLE_TV_PLUS_CHANNEL}, headers)
        if not text:
            return None, mut
        try:
            return json.loads(text), mut
        except ValueError:
            kodiutils.log_error("Detail response for %s was not JSON" % content_id)
            return None, mut

    def _prepared_from_assets(self, assets, mut):
        """Normalise an Apple playable's assets into what _build_playback wants."""
        hls = assets["hlsUrl"].encode("utf-8").decode("unicode_escape").replace("&amp;", "&")
        qp = assets.get("fpsKeyServerQueryParameters") or {}
        return {
            "manifest": hls,
            "user_token": mut,
            "license_server": assets.get("fpsKeyServerUrl"),
            "adam_id": str(assets.get("assetAdamId") or qp.get("adamId") or self._q(hls, "a")),
            "svc_id": qp.get("svcId") or self._q(hls, "svcId"),
            "is_external": qp.get("isExternal", True),
            # An iTunes purchase, so the licence proxy can offer the store
            # credentials Apple's own client licenses these with.
            "itunes": bool(assets.get("isItunes")),
            # Carried through for watch-history reporting.
            "playable_passthrough": assets.get("_playablePassThrough"),
            "external_id": assets.get("_externalId"),
            "brand_id": assets.get("_brandId"),
        }

    # -- trailers and bonus content --------------------------------------

    def get_cast(self, content_id, item_type="Movie"):
        """The people credited on a title, in Apple's order.

        They sit in a uts.col.CastAndCrew.<content id> shelf on the title's
        own page, each carrying the character played where there is one and
        the job done otherwise. They are not playable, so they are returned as
        plain names rather than catalogue entries.
        """
        data, _mut = self._detail_json(content_id, item_type)
        if data is None:
            return []
        people = []
        for raw in self._extra_shelf_items(data, "uts.col.CastAndCrew"):
            if not isinstance(raw, dict) or not raw.get("title"):
                continue
            people.append({
                "name": raw["title"],
                "role": raw.get("characterName") or raw.get("roleTitle") or "",
                "art": self._person_art(raw.get("images") or {}),
            })
        return people

    def get_title_info(self, content_id, item_type="Movie"):
        """Everything Kodi's info screen wants about a title, in one request.

        A listing gets this from the shelf item it already has, but playback
        resolves from an id alone, so the item Kodi is given while playing had
        nothing on it and showed "Not available" for the plot. The title's own
        page carries the description, which shelf items do not, along with the
        cast, so both come back together here.
        """
        data, _mut = self._detail_json(content_id, item_type)
        if data is None:
            return {}
        content = (data.get("data") or {}).get("content") or {}
        info = {
            "plot": content.get("description") or content.get("heroDescription"),
            "genres": [g.get("name") for g in self._as_list(content.get("genres"))
                       if isinstance(g, dict) and g.get("name")],
            "mpaa": ((content.get("rating") or {}).get("displayName")
                     if isinstance(content.get("rating"), dict) else None),
            "tagline": content.get("heroDescription"),
            "studio": "Apple TV+" if content.get("isAppleOriginal") else None,
            "premiered": self._release_date(content.get("releaseDate")),
            "title": content.get("title"),
            "show_title": content.get("showTitle"),
            "cast": [],
        }
        for raw in self._extra_shelf_items(data, "uts.col.CastAndCrew"):
            if isinstance(raw, dict) and raw.get("title"):
                info["cast"].append({
                    "name": raw["title"],
                    "role": raw.get("characterName") or raw.get("roleTitle") or "",
                    "art": self._person_art(raw.get("images") or {}),
                })
        try:
            info["year"] = int(str(info["premiered"] or "")[:4])
        except (TypeError, ValueError):
            info["year"] = None
        return info

    def get_extras(self, content_id, item_type="Movie", kind="trailers"):
        """List a title's trailers or bonus features, in Apple's shelf order.

        Apple puts them in canvas shelves whose ids are
        uts.col.Trailers.<content id> and uts.col.BonusContent.<content id>,
        each item already carrying the playable assets, so this is the only
        request an extra needs.
        """
        self.last_error = None
        data, _mut = self._detail_json(content_id, item_type)
        if data is None:
            return []
        prefix = TRAILER_SHELF_PREFIX if kind == "trailers" else BONUS_SHELF_PREFIX
        extras = []
        for item in self._extra_shelf_items(data, prefix):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if not self._playable_assets(item):
                continue
            title = item.get("title") or ""
            duration = item.get("duration")
            label = title
            if duration:
                try:
                    label = "%s (%d:%02d)" % (title, int(duration) // 60, int(duration) % 60)
                except (TypeError, ValueError):
                    pass
            extras.append({
                "id": item["id"],
                "title": title,
                "label": label,
                "duration": duration,
                "art": self._item_art(item.get("images") or {}),
            })
        return extras

    def get_extra_playback(self, content_id, item_type, extra_id):
        """Resolve one trailer or bonus feature to a playable dict."""
        self.last_error = None
        data, mut = self._detail_json(content_id, item_type)
        if data is None:
            return None
        for prefix in (TRAILER_SHELF_PREFIX, BONUS_SHELF_PREFIX):
            for item in self._extra_shelf_items(data, prefix):
                if not isinstance(item, dict) or item.get("id") != extra_id:
                    continue
                assets = self._playable_assets(item)
                if not assets:
                    continue
                # Extras are promotional and play without a subscription, so a
                # missing media-user-token must not block them.
                return self._build_playback(self._prepared_from_assets(assets, mut),
                                            require_user_token=False)
        kodiutils.log_error("Extra %s has no playable stream" % extra_id)
        return None

    def _extra_shelf_items(self, data, prefix):
        """Items of the canvas shelf whose id starts with prefix."""
        root = data.get("data") if isinstance(data, dict) and "data" in data else data
        canvas = root.get("canvas") if isinstance(root, dict) else None
        shelves = canvas.get("shelves") if isinstance(canvas, dict) else None
        for shelf in self._as_list(shelves):
            if isinstance(shelf, dict) and str(shelf.get("id") or "").startswith(prefix):
                return self._as_list(shelf.get("items"))
        return []

    def _playable_assets(self, item):
        """The first of an item's playables that actually carries a stream."""
        playables = item.get("playables")
        candidates = list(playables.values()) if isinstance(playables, dict) else (playables or [])
        for playable in candidates:
            if not isinstance(playable, dict):
                continue
            assets = playable.get("assets")
            if isinstance(assets, dict) and assets.get("hlsUrl"):
                return self._enrich_assets(playable)
        return None

    @staticmethod
    def event_times(data):
        """Kick-off and tune-in times (epoch seconds) of a sporting event."""
        event = None
        for holder in ("content", "data"):
            node = data.get(holder) if isinstance(data, dict) else None
            if isinstance(node, dict) and isinstance(node.get("eventTime"), dict):
                event = node["eventTime"]
                break
        if event is None:
            event = AppleTVApi._find_event_time(data)
        if not isinstance(event, dict):
            return None, None
        kick_off = event.get("gameKickOffStartTime")
        tune_in = (event.get("tuneInTime") or {}).get("startTime")
        to_secs = lambda ms: int(ms) // 1000 if isinstance(ms, (int, float)) else None
        return to_secs(kick_off), to_secs(tune_in)

    @staticmethod
    def _find_event_time(data):
        if isinstance(data, dict):
            if "gameKickOffStartTime" in data:
                return data
            for value in data.values():
                found = AppleTVApi._find_event_time(value)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = AppleTVApi._find_event_time(value)
                if found is not None:
                    return found
        return None

    @classmethod
    def event_not_started_message(cls, data):
        """Message for an event that cannot be watched yet, else None."""
        import time
        kick_off, tune_in = cls.event_times(data)
        starts = tune_in or kick_off
        if not starts or time.time() >= starts:
            return None
        return kodiutils.localize(32050) % cls.format_event_time(kick_off or starts)

    @staticmethod
    def format_event_time(epoch_secs):
        """Local date and time of an event, e.g. 'Thu 31 Jul at 19:30'."""
        import time
        if not epoch_secs:
            return ""
        return time.strftime("%a %d %b at %H:%M", time.localtime(epoch_secs))

    def _select_playable_assets(self, data, external_id=None):
        """Return the assets of the entitled, streamable playable.

        external_id picks one specific feed when a title publishes several
        (a full replay and a recap, or one per commentary language).
        """
        playables = self._deep_find(data, "playables")
        if isinstance(playables, dict):
            candidates = list(playables.values())
        elif isinstance(playables, list):
            candidates = playables
        else:
            candidates = []

        def has_stream(p):
            return isinstance(p, dict) and isinstance(p.get("assets"), dict) \
                and p["assets"].get("hlsUrl")

        if external_id:
            for p in candidates:
                if has_stream(p) and p.get("externalId") == external_id:
                    return self._enrich_assets(p)
            kodiutils.log("Feed %s not found; falling back" % external_id)

        entitled = [p for p in candidates if has_stream(p) and p.get("isEntitledToPlay")]
        # Prefer the Apple TV+ channel when more than one is entitled.
        for p in entitled:
            if p.get("channelId") == APPLE_TV_PLUS_CHANNEL:
                return self._enrich_assets(p)
        if entitled:
            return self._enrich_assets(entitled[0])
        # Fallback: any playable that carries a stream.
        for p in candidates:
            if has_stream(p):
                return self._enrich_assets(p)
        # An owned iTunes title looks entitled but has no assets at all. Say
        # so rather than reporting the generic failure, since nothing about
        # the account or the network is wrong.
        for p in candidates:
            if isinstance(p, dict) and not p.get("assets") and (
                    p.get("isItunes") or p.get("channelId") == ITUNES_CHANNEL):
                kodiutils.log("iTunes playable %s carries no assets; asking the "
                              "store app's endpoint for a redownload offer"
                              % p.get("id"))
                owned = self._itunes_offer(p)
                if owned:
                    return owned
                self.last_error = (
                    "This is an iTunes purchase, and Apple offered no "
                    "redownload for it on this account.")
                break
        return None

    def _itunes_offer(self, playable):
        """The owned stream for an iTunes title, if Apple will name one.

        A purchase's stream is not in assets -- that field is empty for every
        iTunes playable, on every caller. It is in itunesMediaApiData, split
        two ways: offers are what the title costs to buy or rent, and
        personalizedOffers is the copy this account already owns, priced at
        zero and marked redownload. Only the store app's caller is sent the
        second list, which is exactly why the website cannot play a purchase
        and an Apple TV app on another device can.

        Whether the personalised list needs the store session as well as the
        store caller is not answerable from a capture -- Apple's client
        changed both at once -- so this asks with what the addon holds and
        adds a pasted store session when there is one.
        """
        content_id = playable.get("canonicalId") or playable.get("contentId")
        if not content_id:
            return None
        kind = "episodes" if playable.get("contentType") == "Episode" else "movies"
        data = self._store_detail(kind, content_id)
        if not data:
            return None
        playables = self._deep_find(data, "playables")
        if isinstance(playables, dict):
            candidates = list(playables.values())
        elif isinstance(playables, list):
            candidates = playables
        else:
            candidates = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            media = candidate.get("itunesMediaApiData") or {}
            offers = media.get("personalizedOffers") or []
            if not offers:
                continue
            # Apple lists the qualities it will serve; take the best.
            best = None
            for offer in offers:
                if not offer.get("hlsUrl"):
                    continue
                if best is None or offer.get("variant") == "HD":
                    best = offer
            if not best:
                continue
            kodiutils.log("iTunes redownload offer found: %s %s"
                          % (best.get("kind"), best.get("variant")))
            # The svcId the licence request must carry is the purchase's own
            # service, and the playable states it outright: serviceId is
            # tvs.vds.9023 on an iTunes-catalogue purchase (seen in a capture of
            # this title in the Continue Watching shelf, "iTunes US Catalog").
            # A purchase has no assets of its own, so its own
            # fpsKeyServerQueryParameters do not exist; the ones _fps_params
            # finds by walking the document belong to other playables -- a
            # trailer, the background loop -- which are Apple TV+ studio assets
            # on a different service (tvs.vds.4105 in the same capture). Taking
            # svcId from there would licence the purchase against the wrong
            # service, so the playable's serviceId is preferred and the walked
            # params are only a last resort.
            svc_id = best.get("svcId") or candidate.get("serviceId")
            adam_id = str(media.get("id")
                          or self._q(best["hlsUrl"], "a") or "")
            if svc_id:
                qp = {"svcId": svc_id, "adamId": adam_id, "isExternal": True}
                kodiutils.log("iTunes key-server parameters (from serviceId): %s"
                              % qp)
            else:
                qp = self._fps_params(data)
                if qp:
                    kodiutils.log("iTunes key-server parameters (from document "
                                  "walk): %s" % qp)
            return {"hlsUrl": best["hlsUrl"],
                    "adamId": adam_id,
                    "fpsKeyServerQueryParameters": qp or {},
                    "isItunes": True}
        kodiutils.log("No personalizedOffers came back; the store caller alone "
                      "may not be enough without a store session")
        return None

    def resolve_store_id(self, adam_id):
        """A catalogue entry for a store id, via the id the catalogue uses.

        The locker names purchases by store id, and neither lookup service
        would turn those into titles here -- Apple's own wants a signed token,
        and the public one answered with nothing. The catalogue knows the
        mapping though: asked about a store id on the iTunes brand it returns
        the canonical umc id, the type, and the page url.

        That canonical id is the one this addon opens titles by, so an entry
        built from it behaves like any other -- which a bare store id never
        could.

        The request Apple's client makes also carries a playablePassthrough
        naming an internal leg id, which cannot be known for an arbitrary
        store id. This asks without it and reports what comes back.
        """
        # The detail endpoint answers to a store id directly -- playing a
        # library entry proved it, resolving item_id 1610717981 to its
        # playable without any umc id involved. play-metadata was tried first
        # and wants a duration matching the real asset, which is not knowable
        # before the title is resolved; this needs nothing but the id.
        # Only /movies/ takes a store id. /episodes/ answers 400 to a numeric
        # one -- a malformed request rather than a missing title -- so asking
        # it is noise, and a 404 from /movies/ is the real answer: the
        # catalogue does not carry that id at all.
        for kind in ("movies",):
            data = self._store_detail(kind, str(adam_id), quiet=True)
            content = ((data or {}).get("data") or {}).get("content") or {}
            if not content.get("title"):
                continue
            released = self._release_date(content.get("releaseDate"))
            rating = content.get("rating")
            return {
                # The store id is what opens it, since that is what resolved
                # here and what the locker knows it by.
                "id": str(adam_id),
                "adam_id": str(adam_id),
                "title": content.get("title"),
                "sort_title": content.get("title"),
                "type": "Episode" if kind == "episodes" else "Movie",
                "plot": (content.get("description")
                         or content.get("heroDescription")),
                "genres": [g.get("name")
                           for g in self._as_list(content.get("genres"))
                           if isinstance(g, dict) and g.get("name")],
                "mpaa": (rating.get("displayName")
                         if isinstance(rating, dict) else None),
                "premiered": released,
                "year": int(released[:4]) if released[:4].isdigit() else None,
                "show_title": content.get("showTitle"),
                "season": content.get("seasonNumber"),
                "episode": content.get("episodeNumber"),
                "art": self._item_art(content.get("images") or {},
                                      "Episode" if kind == "episodes" else "Movie"),
            }
        # 404 rather than a refusal: the account owns it and Apple no longer
        # lists it. Nothing reachable can name a title the catalogue has
        # dropped.
        return None

    @staticmethod
    def _fps_params(data):
        """The key-server parameters a detail document names for its title.

        They hang off other playables' assets -- a trailer, the background
        video -- rather than off the purchase, which has no assets at all.
        Within one document they agree: the same svcId and the title's own
        adam id every time.
        """
        found = []

        def walk(node):
            if isinstance(node, dict):
                params = node.get("fpsKeyServerQueryParameters")
                if isinstance(params, dict) and params.get("svcId"):
                    found.append(params)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
        return found[0] if found else None

    def _store_detail(self, kind, content_id, quiet=False):
        """A title's detail as Apple's desktop TV app asks for it."""
        return self._store_json("/%s/%s" % (kind, content_id), quiet=quiet)

    def _store_json(self, path, extra=None, quiet=False):
        """Ask the UTS API as Apple's desktop TV app rather than the website.

        The caller decides what Apple is willing to say, and on two things it
        decides a great deal: the website is sent no personalizedOffers for a
        title, and its search returns Apple TV+ originals only. The same
        search asked as the store app returns the whole store.
        """
        params = {
            "caller": UTS_STORE_CALLER,
            "pfm": UTS_STORE_PFM,
            "v": UTS_STORE_VERSION,
            "locale": self._locale(),
            "sf": self._storefront(),
            "utscf": UTS_CLIENT_FLAGS,
            "utsk": self._bootstrap().get("utsk") or "",
        }
        if extra:
            params.update(extra)
        headers = dict(self._uts_headers())
        # The capture identifies itself to this endpoint with the store dsid
        # and its cookies rather than a bearer. Send those too when a session
        # has been pasted in; the dsid names itself inside the cookie.
        cookies = (kodiutils.get_setting("itunes_cookies") or "").strip()
        if cookies:
            headers["Cookie"] = cookies
            found = re.search(r"(?:amia-|mt-tkn-|mz_at0-)(\d+)=", cookies) \
                or re.search(r"X-Dsid=(\d+)", cookies)
            if found:
                headers["X-DSID"] = found.group(1)
        try:
            resp = self.session.get(UTS_STORE_BASE + path, params=params,
                                    headers=headers, timeout=20)
        except Exception as exc:
            kodiutils.log_error("Store request %s failed: %s" % (path, exc))
            return None
        if resp.status_code != 200:
            # A caller sweeping many ids expects some to be absent and says so
            # itself, so it is not worth a line each.
            if not quiet:
                kodiutils.log_error("Store request %s -> %s %s"
                                    % (path, resp.status_code, resp.text[:200]))
            return None
        try:
            return resp.json()
        except ValueError:
            kodiutils.log_error("Store response for %s was not JSON" % path)
            return None

    @staticmethod
    def _enrich_assets(playable):
        """Assets plus the fields watch-history reporting needs.

        playablePassThrough and externalId sit on the playable rather than in
        its assets, and both are required to mint a now-playing token.
        """
        assets = dict(playable.get("assets") or {})
        assets["_playablePassThrough"] = playable.get("playablePassThrough")
        assets["_externalId"] = playable.get("externalId")
        assets["_brandId"] = playable.get("channelId")
        return assets

    def _get_text(self, path, extra_params, headers):
        try:
            resp = self.session.get(UTS_BASE + path, params=self._params(extra_params),
                                    headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("UTS %s -> %s %s" % (path, resp.status_code, resp.text[:200]))
                return None
            return resp.text
        except Exception as exc:
            kodiutils.log_error("UTS request error %s: %s" % (path, exc))
            return None

    def _collect_widevine_keys(self, manifest_url, headers=None):
        """Map each Widevine key id (hex) to its data: URI from the manifest.

        Apple's licence server needs the URI of the exact key a challenge is
        for; a title has separate video and audio keys, and sending the wrong
        one (or none) makes fpsRequest return 500. Index the keys by the key id
        inside each PSSH so the proxy can pick the right one per challenge.
        """
        keys = {}
        # Variant playlists are authenticated by the token in their URL; the web
        # player requests them with no authorization/media-user-token headers,
        # and sending those makes the request fail.
        plain = {k: v for k, v in (headers or {}).items()
                 if k.lower() in ("user-agent", "origin")}
        try:
            resp = self.session.get(manifest_url, headers=headers, timeout=30)
            if resp.status_code != 200:
                kodiutils.log_error("Master manifest -> %s for key collection"
                                    % resp.status_code)
                return keys
            master = resp.text
            base = manifest_url.rsplit("/", 1)[0] + "/"

            def absolute(u):
                return u if u.startswith("http") else base + u

            # Read keys from the variants that will actually be played: Apple
            # keys each tier separately, so a key taken from a tier the proxy
            # drops is never the one InputStream Adaptive asks to decrypt.
            max_h = kodiutils.get_setting_int("max_height", 360)
            sdr_only = kodiutils.get_setting_bool("sdr_only", True)
            avc_only = kodiutils.get_setting_bool("avc_only", True)

            lines = master.splitlines()
            video, skipped = [], 0
            for i, line in enumerate(lines):
                if not line.strip().startswith("#EXT-X-STREAM-INF"):
                    continue
                uri = next((x.strip() for x in lines[i + 1:i + 3]
                            if x.strip() and not x.strip().startswith("#")), None)
                if not uri:
                    continue
                if license_proxy.variant_unwanted(line.strip(), max_h,
                                                  sdr_only, avc_only):
                    skipped += 1
                    continue
                video.append(absolute(uri))

            audio = []
            for line in lines:
                if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line:
                    m = re.search(r'URI="([^"]+)"', line)
                    if m:
                        audio.append(absolute(m.group(1)))
            # A couple of each is enough: every kept video variant shares a
            # tier, and the audio rendition carries the separate audio key.
            targets = video[:2] + audio[:2]
            if not targets:
                kodiutils.log_error(
                    "No usable variants in master for key collection "
                    "(%d variants skipped by the quality filter)" % skipped)
                return keys

            without_key = 0
            for url in targets:
                try:
                    vresp = self.session.get(url, headers=plain, timeout=30)
                except Exception as exc:
                    kodiutils.log_error("Variant fetch failed: %s" % exc)
                    continue
                if vresp.status_code != 200:
                    kodiutils.log_error("Variant -> %s during key collection"
                                        % vresp.status_code)
                    continue
                found = False
                for line in vresp.text.splitlines():
                    if "urn:uuid:edef8ba9" in line:
                        m = re.search(r'URI="([^"]+)"', line)
                        if m:
                            kid = self._kid_from_data_uri(m.group(1))
                            if kid:
                                keys[kid] = m.group(1)
                                found = True
                if not found:
                    without_key += 1
            if not keys:
                kodiutils.log(
                    "No Widevine keys in %d variant(s): this stream carries no "
                    "Widevine encryption" % without_key)
        except Exception as exc:
            kodiutils.log_error("Widevine key collection failed: %s" % exc)
        return keys

    @staticmethod
    def _pssh_from_data_uri(uri):
        """Return the base64 PSSH carried in a key's data: URI."""
        try:
            from urllib.parse import unquote
            if "base64," not in uri:
                return None
            return unquote(uri.split("base64,", 1)[1]).strip()
        except Exception:
            return None

    @staticmethod
    def _kid_from_data_uri(uri):
        """Extract the 16-byte key id (hex) from a Widevine data: URI PSSH."""
        try:
            import base64
            from urllib.parse import unquote
            if "base64," not in uri:
                return None
            pssh = base64.b64decode(unquote(uri.split("base64,", 1)[1]))
            # WidevinePsshData: key_id is field 2 -> tag 0x12, length 0x10 (16).
            i = pssh.find(b"\x12\x10")
            if i >= 0 and len(pssh) >= i + 18:
                return pssh[i + 2:i + 18].hex()
        except Exception:
            pass
        return None

    def get_widevine_certificate(self):
        try:
            resp = self.session.get(WIDEVINE_CERT_URL, timeout=30)
            if resp.status_code == 200:
                # The endpoint returns the raw DER certificate; ISA wants it as
                # correctly-padded base64.
                import base64
                return base64.b64encode(resp.content).decode("ascii")
        except Exception as exc:
            kodiutils.log_error("Widevine cert fetch failed: %s" % exc)
        return None

    @staticmethod
    def _q(url, key):
        m = re.search(r"[?&]%s=([^&]+)" % re.escape(key), url)
        return m.group(1) if m else ""

    # -- response parsing helpers ---------------------------------------

    def _extract_shelves(self, data):
        if not data:
            return []
        shelves = []
        for shelf in self._as_list(self._deep_find(data, "shelves")):
            if not isinstance(shelf, dict):
                continue
            title = self._shelf_title(shelf)
            shelf_id = shelf.get("id") or shelf.get("channelId") or ""
            items = self._extract_items(shelf.get("items"))
            if items and shelf_id:
                shelves.append({
                    "id": str(shelf_id),
                    "title": title,
                    "items": items,
                    # Enough to fetch the rest of this shelf's items later.
                    "next": shelf.get("nextToken") or None,
                    "ctx": self._shelf_context(shelf, data),
                })
        return shelves

    def _shelf_context(self, shelf, data):
        """The ctx_* parameters Apple wants when paging a shelf.

        They are spelled out in the shelf's own url; where one is missing the
        canvas repeats it (its id is ctx_cvs, canvasInfo.entityId is
        ctx_brand) and the shelf's metrics carry ctx_shelf.
        """
        ctx = {}
        url = shelf.get("url")
        if isinstance(url, str) and "?" in url:
            for key, values in parse_qs(url.split("?", 1)[1]).items():
                if key.startswith("ctx_") and values:
                    ctx[key] = values[0]

        metrics = shelf.get("metrics")
        if isinstance(metrics, dict) and not ctx.get("ctx_shelf"):
            shelf_key = metrics.get("data.uts.shelfId")
            if shelf_key:
                ctx["ctx_shelf"] = shelf_key

        root = data.get("data") if isinstance(data, dict) and "data" in data else data
        canvas = root.get("canvas") if isinstance(root, dict) else None
        if isinstance(canvas, dict):
            if not ctx.get("ctx_cvs") and canvas.get("id"):
                ctx["ctx_cvs"] = canvas["id"]
            info = canvas.get("canvasInfo")
            if not ctx.get("ctx_brand") and isinstance(info, dict) and info.get("entityId"):
                ctx["ctx_brand"] = info["entityId"]
        return ctx

    def _harvest_streams(self, items):
        """Move any inline stream assets off the entries and into the cache.

        Sports clips (NotableMoment, Interview, KeyPlay, ...) have no detail
        endpoint of their own; the shelf is the only place their stream is
        offered, so it has to be kept when the listing is built.
        """
        streams = {}
        for item in items:
            assets = item.pop("stream_assets", None)
            if assets and item.get("id"):
                streams[str(item["id"])] = assets
        if not streams:
            return items
        cache = kodiutils.read_json(STREAM_CACHE, default={}) or {}
        cache.update(streams)
        if len(cache) > STREAM_CACHE_LIMIT:
            # Plain dicts keep insertion order, so this drops the oldest.
            for key in list(cache)[:len(cache) - STREAM_CACHE_LIMIT]:
                cache.pop(key, None)
        kodiutils.write_json(STREAM_CACHE, cache)
        return items

    def _cached_stream(self, content_id):
        cache = kodiutils.read_json(STREAM_CACHE, default={}) or {}
        return cache.get(str(content_id))

    def _shelf_title(self, shelf):
        header = shelf.get("header")
        if isinstance(header, dict):
            t = self._deep_find(header, "title")
            if isinstance(t, str) and t:
                return t
        return shelf.get("title") or shelf.get("displayType") or "More"

    @staticmethod
    def _release_date(value):
        """Apple's release dates are epoch milliseconds; Kodi wants a date."""
        try:
            return time.strftime("%Y-%m-%d", time.localtime(float(value) / 1000.0))
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _resume_point(raw):
        """How far into a title the account already is, from playEvent.

        Apple states the position twice, once for the whole media and once
        for the main feature inside it, and a capture of the site reporting
        playback pairs them exactly:

            playCursorInSeconds 288        -> playHeadInMilliseconds 288000
            mediaLengthInSeconds 5613      -> mediaLengthInMilliseconds 5613792
            contentPlayCursorInSeconds 247 -> mainContentInfo 247000
            contentMediaLengthInSeconds    -> mainContentInfo 5412792

        The outer pair measures the stream that is actually played, so it is
        the one Kodi's resume point wants; the inner pair skips whatever
        Apple counts as leading material.
        """
        # Apple hangs the same playEvent off three different shapes: a single
        # "playable" on most shelf items, a "playables" list on others, and on
        # a title's own page a "playables" map keyed by playable id. Take the
        # first that carries one rather than assuming a shape.
        event = None
        for node in (raw.get("playable"), raw.get("playables")):
            if isinstance(node, dict) and isinstance(node.get("playEvent"), dict):
                event = node["playEvent"]
                break
            candidates = (list(node.values()) if isinstance(node, dict)
                          else node if isinstance(node, list) else [])
            for candidate in candidates:
                if isinstance(candidate, dict) and isinstance(
                        candidate.get("playEvent"), dict):
                    event = candidate["playEvent"]
                    break
            if event:
                break
        if not isinstance(event, dict):
            return None
        position = event.get("playCursorInSeconds")
        total = event.get("mediaLengthInSeconds")
        if not isinstance(position, (int, float)) or position <= 0:
            return None
        if not isinstance(total, (int, float)) or total <= 0:
            return None
        # A finished title still carries its cursor; resuming at the end of
        # one would be worse than starting it again.
        if event.get("isDone"):
            return None
        return {"position": float(position), "total": float(total)}

    def _extract_items(self, items):
        results = []
        for raw in self._as_list(items):
            item = self._map_item(raw)
            if item:
                results.append(item)
        return self._harvest_streams(results)

    def _map_item(self, raw, force_type=None):
        if not isinstance(raw, dict):
            return None
        item_id = raw.get("id") or raw.get("canonicalId") or raw.get("adamId")
        title = raw.get("title") or raw.get("name")
        item_type = force_type or raw.get("type") or "Movie"
        # Skip tiles that are navigation rather than something to play.
        if not item_id or not title or item_type in CONTAINER_TYPES:
            return None
        rel = raw.get("releaseDate")
        year = rel[:4] if isinstance(rel, str) and len(rel) >= 4 else None
        long_desc = raw.get("longDescription")
        # A title's own page sends "description", but a shelf item does not:
        # it carries the marketing lines instead, so those stand in rather
        # than leaving Kodi's info screen reading "No information available".
        plot = (raw.get("description")
                or (long_desc.get("standard") if isinstance(long_desc, dict) else "")
                or raw.get("longNote") or raw.get("heroDescription")
                or raw.get("shortNote") or raw.get("promoText") or "")
        season = raw.get("seasonNumber")
        episode = raw.get("episodeNumber")
        label = title
        if item_type == "Episode" and season and episode:
            label = "S%dE%d · %s" % (season, episode, title)

        start_time = None
        if item_type == "SportingEvent":
            kick_off, _ = self.event_times(raw)
            start_time = kick_off
            league = raw.get("leagueName") or raw.get("sportName")
            when = self.format_event_time(kick_off)
            if when:
                label = "%s · %s" % (when, title)
            extra = " · ".join(x for x in (league, when) if x)
            plot = "\n".join(x for x in (extra, plot) if x)
        return {
            "id": item_id,
            "title": label,
            "sort_title": title,
            "type": item_type,
            "plot": plot,
            "year": year,
            "season": season,
            "episode": episode,
            # Continue Watching lists episodes without their show around them,
            # so the show's name and id travel on the item: the name to label
            # it by, the id to tell an episode apart from its show when its
            # own page is fetched.
            "show_title": raw.get("showTitle"),
            "show_id": raw.get("showId"),
            "duration": raw.get("duration"),
            "start_time": start_time,
            # Clubs carry whether the account follows them; the canvas is
            # invalidated on a FAVORITE event, so it comes back up to date.
            "favourite": bool(raw.get("isFavorite")),
            "league_id": raw.get("leagueId"),
            # Apple sends these on shelf items as well as on a title's own
            # page, so Kodi's info screen fills in wherever a title is listed
            # rather than only where a detail document was fetched.
            "genres": [g.get("name") for g in self._as_list(raw.get("genres"))
                       if isinstance(g, dict) and g.get("name")],
            "mpaa": ((raw.get("rating") or {}).get("displayName")
                     if isinstance(raw.get("rating"), dict) else None),
            "tagline": raw.get("tagLine") or raw.get("heroDescription"),
            "studio": "Apple TV+" if raw.get("isAppleOriginal") else None,
            "premiered": self._release_date(raw.get("releaseDate")),
            "watched": bool(raw.get("isWatched")),
            # Which sport a fixture belongs to: a Grand Prix weekend exists
            # only for Motorsports, where MLS matches carry clubs instead.
            "sport": raw.get("sportName"),
            "resume": self._resume_point(raw),
            "art": self._item_art(raw.get("images") or {}, item_type),
            # Harvested by _extract_shelves, never kept on the listed entry.
            "stream_assets": self._playable_assets(raw),
        }

    @staticmethod
    def _person_art(images):
        """A headshot for a cast member.

        Not _item_art: that sorts artwork into tall posters and wide stills by
        aspect ratio and discards anything square, which is how logos and team
        badges are kept out. A headshot is exactly square (1080x1080), so it
        was being discarded too, and the cast listed as bare names.
        """
        for entry in (images or {}).values():
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            url = AppleTVApi._sized_url(entry, width=600, height=600)
            return {"thumb": url, "icon": url, "poster": url}
        return {}

    def _item_art(self, images, item_type=None):
        """Pick a portrait and a wide artwork and size each to its own shape.

        Returns a dict ready for ListItem.setArt(). The poster slot is only
        filled when Apple actually supplies a tall artwork; forcing a 16:9
        still into a poster box is what produced cut-off images.

        An episode listed on its own -- Continue Watching does this -- is sent
        with its own still alongside its show's poster, and the poster was
        winning the tall slot, so every episode of a show looked identical.
        The show's poster is set aside for those, leaving the episode's still,
        which is what Apple's own client shows.
        """
        if str(item_type) == "Episode" and isinstance(images, dict):
            images = {k: v for k, v in images.items()
                      if "showposter" not in k.lower()}
        portrait = self._pick_image(images, PORTRAIT_IMAGE_KEYS, portrait=True)
        wide = self._pick_image(images, WIDE_IMAGE_KEYS, portrait=False)
        if str(item_type) == "Episode":
            # Of the wide artwork on an episode only posterArt differs between
            # episodes; shelfItemImage and shelfImageBackground are the show's
            # and are byte-identical across its episodes, so preferring the
            # usual order gave every episode the same branded title card.
            own = self._pick_image(images, ("posterArt",), portrait=False)
            wide = own or wide
        art = {}
        if portrait:
            art["poster"] = self._sized_url(portrait, height=POSTER_HEIGHT)
        if wide:
            art["thumb"] = self._sized_url(wide, width=THUMB_WIDTH)
            art["fanart"] = self._sized_url(wide, width=FANART_WIDTH)
        elif portrait:
            # Tall artwork only (some sports and channel rows): use it for the
            # thumbnail too rather than leaving the row blank.
            art["thumb"] = art["poster"]
        if art.get("thumb"):
            art["icon"] = art["thumb"]
        return art or None

    @staticmethod
    def _image_shape(key, entry):
        """True/False for portrait/landscape, None when it cannot be told."""
        lowered = key.lower()
        if any(bad in lowered for bad in IMAGE_KEY_DENYLIST):
            return None
        width, height = entry.get("width"), entry.get("height")
        try:
            width, height = float(width), float(height)
        except (TypeError, ValueError):
            # No usable dimensions: fall back to Apple's naming convention.
            return True if "tall" in lowered or "poster" in lowered else None
        if width <= 0 or height <= 0:
            return None
        ratio = width / height
        if ratio <= 0.9:
            return True
        if ratio >= 1.2:
            return False
        return None  # roughly square (logos, team badges) -- not artwork

    def _pick_image(self, images, preferred, portrait):
        """Best image of the wanted shape: preferred keys first, then any."""
        if not isinstance(images, dict):
            return None
        candidates = [k for k in preferred if k in images]
        candidates += [k for k in images if k not in preferred]
        for key in candidates:
            entry = images.get(key)
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            if self._image_shape(key, entry) is portrait:
                return entry
        return None

    @staticmethod
    def _sized_url(entry, width=None, height=None):
        """Fill an mzstatic {w}x{h} template, keeping the source's aspect."""
        try:
            src_w, src_h = float(entry.get("width")), float(entry.get("height"))
            ratio = src_w / src_h if src_w > 0 and src_h > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
        if width is None:
            width = int(round(height * ratio)) if ratio else height
        if height is None:
            height = int(round(width / ratio)) if ratio else width
        return (entry["url"].replace("{w}", str(int(width)))
                .replace("{h}", str(int(height)))
                .replace("{f}", "jpg").replace("{c}", "").replace("{cropcode}", ""))

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
        return []

    def _deep_find(self, data, key):
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for value in data.values():
                found = self._deep_find(value, key)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = self._deep_find(value, key)
                if found is not None:
                    return found
        return None
