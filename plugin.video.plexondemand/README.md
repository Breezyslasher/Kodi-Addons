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
| Genre listing | `GET {vod}/library/sections/movies/all?genre=<id>&type=1` (paged with `X-Plex-Container-Start/Size`; 404 without a genre) |
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
| Watchlist | `PUT {vod}/actions/addToWatchlist`, `removeFromWatchlist` `?ratingKey=`; list `GET discover.provider.plex.tv/library/sections/watchlist/all` |
| More like this | `GET {vod}/library/metadata/<rk>/similarTitles` and `/more-from-credits` (free titles; `/library/people/<id>` has no filmography) |
| Artwork | `images.plex.tv/photo?url=&width=&height=` (no token; a 920 KB poster becomes about 29 KB) |
| Sort | genre listings take `sort=titleSort`, `titleSort:desc`, `audienceRating:desc`; year/decade/rating filters are ignored by Plex |
| Search size | `limit=100` is honoured; `offset` is ignored |
| Up next | `POST {vod}/playQueues?uri=provider://tv.plex.provider.vod/library/metadata/<rk>&type=video&continuous=1` |
| Ads | `Metadata.Ad[]` = VMAP at `{vod}/vmap?...` (client-side VAST pods) |

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
`.github/workflows/plexondemand-probe.yml` runs the offline service test and the live smoke
test on every push to this branch, and daily from `main`. A failing scheduled run opens (or
comments on) one issue labelled `plexondemand-endpoint`.

## Dev tools (not shipped in the zip)
- `tools/smoke_test.py`: runs `lib/` against the live service with Kodi stubbed.
- `tools/probe.py`: raw endpoint captures. Both run from
  `.github/workflows/plexondemand-probe.yml` (push = smoke test; manual = probe too).
