# iFood.tv for Kodi *(unofficial)*

Browse and play [iFood.tv](https://ifood.tv/)'s free catalogue in Kodi. There is
no account or sign-up: the service has none.

## Features

- Recipe sections: Home, Top Youtubers, Movies & Shows, Dish, Cuisine, Ingredients, Healthy, Method...
- Search, with paging, and **My List** (watchlist, saved per device)
- Recipe videos are plain MP4 (some HLS); no DRM module needed
- Posters, fanart, plot, runtime and genres

## Known gaps

- **Ads:** the web player requests ads separately from the video (client-side
  VMAP). This add-on plays the programme stream only.
- **Region:** the service is US-centric; elsewhere the catalogue may differ.

## How it works

iFood.tv runs on Future Today's platform, the same backend as
[Fawesome](../plugin.video.fawesome/); the service is selected by `siteId`.
The add-on is the Fawesome add-on with this site's constants -- see
[docs/recon.md](docs/recon.md). A daily check on GitHub (`iFood.tv` in
`.github/workflows/endpoint-checks.yml`) tests the calls it depends on.

Not affiliated with or endorsed by Future Today Inc.
