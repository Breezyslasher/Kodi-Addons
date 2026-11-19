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
| Ads | `Metadata.Ad[]` = VMAP at `{vod}/vmap?...` (client-side VAST pods) |

## Known gaps
- **Ads are not played.** Plex inserts ads client-side (VMAP/VAST); this add-on plays
  the content stream only. That is a Plex ToS question; it is documented here on purpose.
- **451 "Playback is unavailable while using a VPN or proxy"**: some licensors block
  datacenter/VPN IPs per title. The add-on shows that message.
- Region-limited catalogue (mainly US).
- DRM titles: no external subtitles yet (the HLS master, which carries them, is FairPlay).
  Widevine L3 devices may be limited to SD.

## Dev tools (not shipped in the zip)
- `tools/smoke_test.py`: runs `lib/` against the live service with Kodi stubbed.
- `tools/probe.py`: raw endpoint captures. Both run from
  `.github/workflows/plexondemand-probe.yml` (push = smoke test; manual = probe too).
