# Starz recon worksheet

Everything Starz-specific that the code depends on comes from captures, not
memory. Fill each row from a HAR, cite the HAR entry number
(`tools/har_summary.py` prints them), and only then write the code that uses
it. Until a row is filled, the matching code raises `NotReadyError`.

## Captures to collect

From a signed-in browser at the Starz web player, using DevTools → Network
with "Preserve log" on, then "Save all as HAR":

1. **Sign-in HAR**: log out, then log in. Every request from entering
   credentials to landing signed in.
2. **Browse HAR**: home rows, then series → season → episodes, then a search.
3. **Play HAR**: press play on a title and let it run about 30 seconds. This
   should contain the playback-info call, the manifest, the licence POST and
   the subtitles.

```bash
python3 tools/har_summary.py capture.har --hosts
```

```bash
python3 tools/har_summary.py capture.har
```

```bash
python3 tools/sanitize_har.py capture.har starz-sanitized.har
```

Never commit a HAR (`*.har` is gitignored). Read a sanitized HAR before
sharing it.

## Unknowns

| # | Unknown | Finding | HAR ref | Code |
|---|---|---|---|---|
| A | Auth mechanism: own email/password → token, device code, or provider SSO? | | | `lib/auth.py` `sign_in` |
| B | Token refresh: refresh token? lifetime? header or cookie? rotates? | | | `lib/auth.py` `refresh` |
| C | Catalogue API: host, paths for home/row/series/season/search, pagination | | | `lib/api.py` |
| D | Playback-info call: request on play; where manifest URL, licence URL, per-play token live | | | `StarzApi.playback` |
| E | Manifest type: DASH or HLS | | | `manifest_type` |
| F | DRM: key system, licence URL, raw or wrapped challenge, licence auth | | | `license_key` |
| G | Proxy needed? (only if wrapped body or computed per-request header) | | | stays `license_key` unless yes |
| H | PSSH/KID present in manifest | | | none expected |
| I | Resolution/HDCP tiers; what L3 gets | | | README |
| J | Subtitles: WebVTT/TTML, sidecar or in-manifest, render in ISA? | | | `playback["subtitles"]` |
| K | Required headers: User-Agent, Origin, device id, `x-*` | | | `StarzApi._headers` |

## Open questions (answer first)

- Standalone Starz sign-in, or an account through a provider (Amazon, Apple,
  cable/MVPD)? This changes A entirely.
- DASH or HLS? (E)
- Does the licence server want a per-play token from D? (F/G)
- Any device-registration or concurrent-stream call? Look in the sign-in and
  play HARs.
