# Fawesome: confirmed endpoints (M0 gate)

Everything below was read from fawesome.tv's web player and **confirmed against
the live service** by the `fawesome-recon` workflow
(`.github/workflows/fawesome-recon.yml`) on GitHub's runners. The dev container's
network cannot reach fawesome.tv, so that workflow is the add-on's network:
it crawls the site, runs `tools/probe_api.py` and `tools/smoke_test.py`, and
commits the results to `docs/recon/` on the branch that ran it.

Captured 2026-11-21, site build `v469`.

## Where the API lives

- The marketing pages are a React app (`/static/js/main.*.js`). The **player** is
  a jQuery app: `/home/new/<build>/assets/js/iptv-main.js`. All of the below
  comes from the player.
- **API base:** `https://fawesome.tv/home/new/<build>/api/`. `<build>` (`v469`)
  changes with site releases; the add-on reads it from the home page
  (`/home/new/(v\d+)/`) and caches it for a day.
- Backend: Future Today's PHP API (`rapi.ifood.tv`, the same platform as their
  other channels). Feed URLs inside responses point at `rapi.ifood.tv`; the player
  **rewrites** them onto the API base (same file name and query). `rapi.ifood.tv`
  itself answers `5002 Unauthorized` to the website's token.

## Client identity (no account)

Fawesome has no accounts. Every call carries the website's public client ids:

| param | value | note |
|---|---|---|
| `appId` | `9` | Fawesome |
| `siteId` | `236` | Fawesome website |
| `auth-token`, `platform_id` | `1217575` | website platform id (not a secret) |
| `deviceId` | random uuid | the add-on generates and keeps one |
| `apiEnv` | `production` | |

## Token

`GET getSecurityToken.php?<ids>` → `{"securityToken": "..."}` (108 chars). Every
other call sends it as a **`token` request header**. Without it the API answers
HTTP 200 with `{"status":"fail","data":{"code":"5002","description":"Unauthorized access."}}`;
`5003` = expired → fetch a new one and retry (the player does exactly this).

Note: the API sends JSON as `text/html`, and reports errors in the body with
HTTP 200 -- check `status`.

## Catalogue

| call | returns |
|---|---|
| `index.php?parent=home` | `channels[]`: top menu -- Home, Live, Collections, Shows, Movies, En Español, Handpicked (`feed_type: category`) |
| `sub-categories.php?parent=<Name>` | `channels[]`: that section's rows, `{name, url}`. Some rows are ads/bug ads/screensaver (`springserve`, `getFTNlAds.php`, `getFTIScreensaver.php`) -- skip them |
| `recipes.php?searchType=new-qid\|nodeid\|mixedcontent\|general&keys=...` | a row: `count`, `params.limit` (20), `results[]` of titles |
| `recipes.php?searchType=listoflist&keys=...` | `results[]` of `type: listoflist` items, each a `shows.php` URL (genre lists, collections) |
| `shows.php?searchType=listoflist&keys=<id>` | a show: `showdetails{}` + `channels{}` keyed "1", "2"… one per season (`title`, `feed`, `season_no`); or a list's `results[]` |
| `recipes.php?searchType=search&keys=<q>` | search, same shape as a row |
| `recipes.php?searchType=nodeid&nid=<id>&max-results=1&dltype=1&gridstyle=flat-movie` | one title's detail |

**Pagination:** `start-index` is a **page number** (0, 1, 2 …), not an offset
(`start-index=20` → `1003 No Recipe found`). More pages while
`(page+1) * limit < count`.

"Live" on the website is a single "General" row of VOD, not linear channels; the
add-on hides it. FAST channels exist only in the TV apps (not captured).

### Title fields the add-on reads

`node_id, title, episode_title, description, series_name, season, episode,
runtime (s), release_date ("01 January 2021 - 12:00 am"), content_genre
(comma list), actors[], directors[], age_appropriate_rating, main_picture
(poster), bg_image_landscape / hero_image (fanart), content_logo, cc_path
(WebVTT), video_hls_url, video_url, video_format, drm`.

## Playback

- `video_hls_url` -> `https://ftmain.cachefly.net/files/a8wn4hw/vi/../<nid>/s<nid>.m3u8`:
  **clear HLS** (master with `hls-hi/mi/lo/xlo` variants, MPEG-TS segments, **no
  `EXT-X-KEY`**). No token, cookie or referer needed.
- Titles with `drm: 1` have `video_format: mpd` and a Widevine/PlayReady DASH
  `video_url` (`.../idrm/stream.mpd`, licences via KeyOS:
  `wv-keyos.licensekeyserver.com`, `drm.php` gives the `customdata`). **Every**
  `drm: 1` title seen (48 of 194 sampled) also had a clear `video_hls_url`, which
  is what the add-on plays -- so no DRM machinery is needed.
- A few titles have only an `.mp4` `video_url` (`video_format: mp4`); played
  directly.
- Subtitles: `cc_path`, a WebVTT file on the same CDN.

## Ads

Client-side: the player builds a VMAP from the title's `ad_breaks` cue points
at `https://iptv.fawesome.tv/vmap-create.php` and plays it with Google IMA
(SpringServe demand). Nothing is stitched into the HLS. The add-on plays the
programme stream only and does not insert ads; this is stated in the add-on's
description.

## Checks

- `tools/smoke_test.py`: the add-on's router against the live API, Kodi stubbed.
- `tools/endpoints.json`: the daily endpoint check (`Fawesome` in
  `.github/workflows/endpoint-checks.yml`).
