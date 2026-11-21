# HappyKids: confirmed endpoints

Confirmed live from GitHub's runners on 2026-11-21 (the dev container cannot
reach happykids.tv); see `.github/workflows/ft-siblings.yml`.

HappyKids runs on the same Future Today backend as Fawesome
(`plugin.video.fawesome/docs/recon.md` documents the API in full). Only the
site constants differ:

| | |
|---|---|
| Player page | `https://happykids.tv/home/` |
| API base | `https://happykids.tv/home/new/<build>/api/` (build `v438` on 2026-11-21, read from the player page) |
| appId / **siteId** / auth-token (= platform_id) | `9` / **`40`** / `1217575` |
| Headers | `token` (from `getSecurityToken.php`), plus the site's `Referer`/`Origin` -- the API answers an empty body without them |

Most rows are shows (folders); titles carry `video_hls_url` / `video_url` like Fawesome's.

How the ids were found: the shared player bundle (`iptv-main.js`) defaults to
`siteId=1`; HappyKids' player is at `/home/` (the root is a marketing site) and siteId 40 is the value that returns HappyKids' own sections. With the wrong siteId the same host serves another
Future Today service (siteId 236 on this host returns Fawesome's catalogue).

Sections on 2026-11-21: Discover, Gaming, Ages 0-4, Ages 4-6, Ages 6-9, Tweens, LEGO, Español.
