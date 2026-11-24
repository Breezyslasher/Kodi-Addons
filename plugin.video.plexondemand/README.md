# Plex On Demand (plugin.video.plexondemand)

Unofficial Kodi client for Plex's **free, ad-supported Movies & Shows** catalogue
(the `tv.plex.provider.vod` cloud provider). Not your own Plex Media Server (use
PlexKodiConnect for that), and not Plex Live TV.

## What works without an account
Plex hands out **anonymous tokens** (`POST plex.tv/api/v2/users/anonymous`), and an
anonymous token is enough to browse and play the whole free catalogue. Signing in
with **plex.tv/link** (4-character PIN) is optional; it only puts watch progress on
your real Plex account.

## Confirmed endpoints (captured from GitHub Actions, US)
| What | Call |
|---|---|
| Provider list | `GET plex.tv/media/providers` -> `vod.provider.plex.tv` |
| Home rows | `GET {vod}/hubs` -> `/hubs/sections/movies/<slug>` |
| Genres | `GET {vod}/library/sections/movies/genre?type=1` (movies) / `type=2` (shows) |
| Genre listing | `GET {vod}/library/sections/movies/all?genre=<id>&type=1&availabilityType=free` (paged with `X-Plex-Container-Start/Size`; 404 without a genre; without `availabilityType=free` movie genres mix in rental titles) |
| Title / children | `GET {vod}/library/metadata/<ratingKey>[/children]` |
| Search | `GET discover.provider.plex.tv/library/search?searchProviders=discover,plexAVOD` -> section `id=plex` |
| Stream | `Media[].Part[].key` -> `/library/parts/<id>-hls.m3u8` and `-dash.mpd`; the manifest needs `X-Plex-Token`, segments on `vod-content.plexvideos.com` need nothing |
| DRM | about half the titles (`Media.drm: true`): HLS is FairPlay, **DASH is Widevine**; licence `POST {vod}/library/parts/<id>-dash/license` with header **`X-Plex-DRM: widevine`** (else 400 "Missing DRM") |
| Progress | `GET {vod}/timeline?ratingKey&key&state&time&duration` (POST/PUT 404) |
| Resume / watched | `GET discover.provider.plex.tv/library/metadata/<rk>[,<rk>...]/userState` (batched; `/library/metadata` has no `viewOffset`) |
| Continue Watching | `GET {vod}/hubs/continueWatching` |
| Actions | `PUT {vod}/actions/scrobble`, `unscrobble`, `removeFromContinueWatching` `?ratingKey=` |
| Trailers | `primaryExtraKey` -> `.../extras/<id>/parts/hls.m3u8` (redirects to open HLS) |
| Subtitles | `Part.Stream[streamType=3].key` -> `/library/streams/<id>-<lang>?format=srt` (redirects to `vod-static.plex.tv`; works for DRM titles) |
| Markers | `/library/metadata/<rk>?includeMarkers=1` -> `credits` only. Survey of 250 movies / 407 episodes: credits on 22% of movies, on 0% of episodes; no `intro` markers and no chapters anywhere |
| Rating | `PUT {vod}/actions/rate?ratingKey=&rating=0..10` (`-1` clears); read back as `userState.userRating` |
| Not interested | `PUT {vod}/actions/addToDislikes` / `removeFromDislikes` `?ratingKey=`: accepted, but write-only (no list, not in userState) |
| Store (rentals) | `GET {vod}/hubs/sections/store` (rows) and `/hubs/sections/store/myRentalsAndPurchases` (the account's); items carry `rentalPrice`, `purchasePriceLabel`, `viewableDuration`, `publicPagesURL`. Same Widevine DASH as free DRM titles; without a rental the manifest answers 404 "User has no active order for this rental" |
| Brands | `GET {vod}/library/sections/channels/all` (Plex calls them channels) -> `/library/channels/<id>` (paged titles) |
| Categories | `GET {vod}/library/sections/categories/all` -> ready keys (`type=99`, `availabilityType=free`) with PNG artwork. That artwork is a bare gradient (Plex prints the name over it), so the addon bundles copies with the name drawn in (`resources/media/categories/`, built by `tools/make_category_art.py` from probe round17); a category added later shows without art |
| Watchlist | `PUT {vod}/actions/addToWatchlist`, `removeFromWatchlist` `?ratingKey=`; list `GET discover.provider.plex.tv/library/sections/watchlist/all` |
| More like this | `GET {vod}/library/metadata/<rk>/similarTitles` and `/more-from-credits` (free titles; `/library/people/<id>` has no filmography) |
| Artwork | `images.plex.tv/photo?url=&width=&height=` (no token; a 920 KB poster becomes about 29 KB) |
| Sort | genre listings take `sort=titleSort`, `titleSort:desc`, `audienceRating:desc`; year/decade/rating filters are ignored by Plex |
| Search size | `limit=100` is honoured; `offset` is ignored |
| Up next | `POST {vod}/playQueues?uri=provider://tv.plex.provider.vod/library/metadata/<rk>&type=video&continuous=1` |
| Ads | `Metadata.Ad[]` = VMAP at `{vod}/vmap?...` (client-side VAST pods) |

## Live TV
Plex's free live channels (`tv.plex.provider.epg`, captured in probe rounds 18-19):

| What | Endpoint |
|---|---|
| Provider | `plex.tv/media/providers` -> `tv.plex.provider.epg` at `https://epg.provider.plex.tv` |
| Filters | `GET {epg}/` -> `Feature[type=grid]`: Featured (`/lineups/plex/channels/featured`) and ~15 genres (`/lineups/plex/channels?genre=<id>`) |
| Channels | `GET {epg}/lineups/plex/channels` -> ~700 `Channel[]` (id, gridKey, title, slug, thumb logo, art, genreRatingKeys) |
| Stream | `{epg}/library/parts/<channel id>.m3u8` with `X-Plex-Token` -> HLS master; variants carry the token, segments are open; no DRM |
| Guide | `GET {epg}/grid?channelGridKey=<gridKey>&date=YYYY-MM-DD` -> one channel, one day (`Media[0].beginsAt/endsAt`); a list of keys or no date answers 400 |

The add-on shows Featured, All channels (100 per page) and the genres, each channel
with what's on now and next; a channel's context menu has **What's on later** (the next
24 hours). Channels report play count 0 and no resume point, so Kodi never ticks one as
watched after leaving the live edge. **IPTV Manager** (Settings > Live TV) puts every channel,
grouped by genre, into Kodi's TV section with a guide from now to about a day ahead
(one guide call per channel and day, 16 at a time, so a full refresh takes a little while).

## Rentals
Rentals and purchases are made in Plex's own apps or on watch.plex.tv; Kodi cannot rent. Once
you sign in (plex.tv/link) with the same account, **My Rentals & Purchases** on the home menu
lists them and they play like the free DRM titles (it appears only when there is something in it). Playing a store title you have not rented shows its
price and where to rent it. The Rentals menu (the store; Plex mixes a few free titles into its rows, the add-on hides them) appears only when signed in, and Settings → Browse →
Show Rentals store hides it; your own rentals stay on the home menu either way. *Playback of an actual rental has not been tested yet* (no rental
was available when this was written); the not-rented path is tested.

## Known gaps
- **Ads are not played.** Plex inserts ads client-side (VMAP/VAST); this add-on plays
  the content stream only. That is a Plex ToS question; it is documented here on purpose.
- **451 "Playback is unavailable while using a VPN or proxy"**: some licensors block
  datacenter/VPN IPs per title. The add-on shows that message.
- Region-limited catalogue (mainly US).
- Widevine L3 devices may be limited to SD on DRM titles.
- Plex has no intro markers for these titles, so there is no Skip intro. The Next episode
  countdown appears only at a real Plex credits marker, which episodes almost never have;
  otherwise the next episode simply autoplays when the episode ends (setting: Autoplay).
  On movies with a credits marker, reaching the credits marks the movie watched.

## Skip Buttons
`script.module.skipbuttons` is a required dependency, so Kodi installs it with the add-on. Its
buttons appear only after **Kodi is restarted** following that first install, because the
add-on's service has to reload with the module available.

## Endpoint check
Plex On Demand is one entry in the shared `.github/workflows/endpoint-checks.yml` matrix:
`tools/check_endpoints.py`, which runs four parts in turn and fails if any fails. The daily
scheduled run files one rolling issue labelled `plexondemand-endpoint`.
1. **spec**: `tools/endpoints.json` through the shared engine (token, PIN, providers, rows,
   genres and a genre's movies, store, search).
2. **addon**: `tools/smoke_test.py` drives the addon's own `lib/` against the live service
   (playback incl. the DRM licence, watch state, watchlist, rentals, ...).
3. **service**: `tools/test_service.py`, the service and Skip Buttons wiring with Kodi stubbed
   (no network).
4. **offline**: `tools/test_offline.py`, failure paths with Kodi and the network stubbed
   (offline, revoked sign-in, the ISA secure-decoder flag, a channel without an id).

Pushes that touch these files or the addon's `lib/`/`service.py` run the matrix too.

## Dev tools (not shipped in the zip)
- `tools/smoke_test.py`: runs `lib/` against the live service with Kodi stubbed.
- `tools/probe.py`: raw endpoint captures (`PROBE_MODE=round1`...). Plex hosts are only
  reachable from GitHub's runners, so to capture something new, add a temporary
  `script: plugin.video.plexondemand/tools/probe.py` entry to the matrix on a branch.
- `tools/make_category_art.py`: rebuilds the bundled category art from a saved probe
  round17 log (colour grid per category, name drawn in Inter Bold). Re-run when the
  smoke test reports categories without bundled art.
