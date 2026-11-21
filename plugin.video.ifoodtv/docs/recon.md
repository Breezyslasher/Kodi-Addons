# iFood.tv: confirmed endpoints

Confirmed live from GitHub's runners on 2026-11-21 (the dev container cannot
reach ifood.tv); see `.github/workflows/ft-siblings.yml`.

iFood.tv runs on the same Future Today backend as Fawesome
(`plugin.video.fawesome/docs/recon.md` documents the API in full). Only the
site constants differ:

| | |
|---|---|
| Player page | `https://ifood.tv/` |
| API base | `https://ifood.tv/home/new/<build>/api/` (build `v446` on 2026-11-21, read from the player page) |
| appId / **siteId** / auth-token (= platform_id) | `9` / **`1`** / `1217575` |
| Headers | `token` (from `getSecurityToken.php`), plus the site's `Referer`/`Origin` -- the API answers an empty body without them |

Titles are `type: recipe` with an MP4 `video_url` and no `video_hls_url`; the add-on plays the MP4 directly.

How the ids were found: the shared player bundle (`iptv-main.js`) defaults to
`siteId=1`; the player maps the hostname to a siteId (`ifood` -> 1; also chickensoup 1728, filmrise 898, nosey 1030, popcornflix 1518) and loads `assets/data/config_1.json` (`site_id` 1, `platform_id` 1217575). With the wrong siteId the same host serves another
Future Today service (siteId 236 on this host returns Fawesome's catalogue).

Sections on 2026-11-21: Home, Top Youtubers, Movies & Shows, Dish, Fun With Food, Ingredients, Cuisine, Healthy, Method, Speciality, Taste.
