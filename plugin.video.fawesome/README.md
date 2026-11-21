# Fawesome for Kodi *(unofficial)*

Browse and play [Fawesome](https://fawesome.tv)'s free, on-demand movies and TV
shows in Kodi. There is no account or sign-up: Fawesome has none.

## Features

- Fawesome's own sections and rows: Home, Collections, Shows, Movies, En Español
  and Handpicked
- Genre lists and collections, shows by season, episodes
- Search, with paging
- Plays clear HLS through InputStream Adaptive (adaptive quality), with
  subtitles where Fawesome has them
- Posters, fanart, plot, year, runtime, genres, cast and director

## Known gaps

- **Ads:** Fawesome's web player requests ads separately from the video
  (client-side VMAP). This add-on plays the programme stream only.
- **FAST linear channels** from Fawesome's TV apps are not included.
- **Region:** Fawesome is US-centric; outside the US the catalogue may differ.

## How it works

Fawesome publishes no API. The add-on uses the calls the fawesome.tv web player
makes; see [docs/recon.md](docs/recon.md). A daily check on GitHub (`Fawesome` in
`.github/workflows/endpoint-checks.yml`) tests those calls and opens an issue
when one changes.

Development: the cloud dev container cannot reach fawesome.tv, so
`.github/workflows/fawesome-recon.yml` runs the recon scripts and
`tools/smoke_test.py` on GitHub's runners and commits the results to
`docs/recon/`.

Not affiliated with or endorsed by Future Today Inc.
