# HappyKids for Kodi *(unofficial)*

Browse and play [HappyKids](https://happykids.tv/)'s free catalogue in Kodi. There is
no account or sign-up: the service has none.

## Features

- Sections by age group (Ages 0-4 ... Tweens), Gaming, LEGO and Español; shows by season
- Search, with paging, and **My List** (watchlist, saved per device)
- Plays through InputStream Adaptive; no DRM module needed
- Posters, fanart, plot, runtime and genres

## Known gaps

- **Ads:** the web player requests ads separately from the video (client-side
  VMAP). This add-on plays the programme stream only.
- **Region:** the service is US-centric; elsewhere the catalogue may differ.

## How it works

HappyKids runs on Future Today's platform, the same backend as
[Fawesome](../plugin.video.fawesome/); the service is selected by `siteId`.
The add-on is the Fawesome add-on with this site's constants -- see
[docs/recon.md](docs/recon.md). A daily check on GitHub (`HappyKids` in
`.github/workflows/endpoint-checks.yml`) tests the calls it depends on.

Not affiliated with or endorsed by Future Today Inc.
