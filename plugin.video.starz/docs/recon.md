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
| A | Auth mechanism | Starz's own account. App token: `AuthTokenAuthorization: PLAYAUTH1.0 x=…, apikey=…, signature_method=SHA256, requesttime=…, deviceid=<32 hex>, AllowAuthNOnly=true, platform…, signature=…`, minted per call by `GET www.starz.com/sapi/header/v1/starz/us/{deviceId}` (no auth cookies, raw text body). Sign-in: `GET auth.starz.com/api/v4/Activate/Code` → `{code (4 chars), activationUrl}`, then `POST /api/v4/User/login` JSON `{code, emailAddress, password, captchaResponse}` → `{subscriptionState}`. The session binds to the device id; no cookie or token is returned. **reCAPTCHA Enterprise** token in the web login: **[VERIFY] whether Starz accepts a login without it.** | sign-in #75, #257, #263 | `StarzAuth.sign_in` |
| A2 | **Captcha-free sign-in (used)** | First Kodi test: `User/login` without captcha → HTTP 400, empty body. Token before linking → **412 `{"errorDetails": "Not registered"}`**. starz.com/activate (activation HAR): `GET /api/v4/Activate/{code}` → `{code, hideSignUpButton, authId}`, then `POST /api/v4/Activate/Login` JSON `{code, emailAddress, password, preLoginEmailAddress}` (app token only, **no captcha**) → `{subscriptionState}`. Kodi links its **own** code this way, then calls `Token`. | activation HAR #264, #331 | `StarzAuth.sign_in` |
| B | Session | `GET /api/v4/Token` (app token) → `{playSessionToken: "PLAYSESSION1.0 x=…", accountStatus, residencyCountry}`; afterwards sent as `SessionTokenAuthorization` (no app token) on `User`, `User/profiles`, `User/continue`, etc. Lifetime **[VERIFY]**; we re-issue via `Token` on 401. | #266, #268–#272 | `StarzAuth.refresh/request` |
| B1 | Sign-out | `DELETE /api/v4/Token` with `SessionTokenAuthorization` (from the site's JS bundle in the logout HAR; the request itself was cleared from the log by the page navigation). [VERIFY] whether it frees a device-limit slot. Bundle also has `POST Activate/Device/{code}` (signed-in user activates another device) and `play/start`, `play/update`, `play/stop` (body `{contentId, playbackSessionId, mediaResumePointSeconds}`). | logout HAR #49 | `StarzAuth.sign_out` |
| B3 | Viewing history | `GET /User/history` (all) or `?topContentId={series}` → `[{contentId, topContentId, resumeSeconds, isCompleted, runTime, creditTimeIn, season, episodeNumber, nextContentId, lastViewed}]`. Watched = `isCompleted` or `resumeSeconds >= creditTimeIn`. | sign-in #304, series HAR | `apply_history` |
| B4 | Hide from Continue Watching | `PUT /User/resumepoints/hide {"ids": [contentId]}` → 204 (two movies in the hide HAR). [VERIFY] which id an episode needs. | hide HAR #20, #33 | `hide_from_continue` |
| B2 | Profiles | `GET /User/profiles` → `[{id, name, isAdmin, profileIcon}]`; `POST /User/profiles/{id}/select` → 204. | #271, #417; profiles HAR #391 | `select_profile` |
| C | Catalogue | **Public, no auth headers.** `GET playdata.starz.com/metadata-service/play/partner/Web/v8/blocks?playContents=map&lang=en-US&pages=BROWSE,HOME,…&includes=…` → `PAGE` blocks → rows (`SWIM_LANE` etc. with `contentIds`) + one `PLAY_CONTENTS.playContentsById` map (551 titles). `v8/content?contentIds=…` for the rest. Types: `Movie`, `Series with Season`, `Episode`. Art: `stz1.imgix.net/Web/contentId/{id}/type/KEY/dimension/2560x1440/lang/en-US`. Resume: `auth…/User/continue`. Series (series HAR): `v8/content?lang=en-US&contentIds={id}` with **no includes** → whole tree: series `childContent` = `Season`s (`order`) → `childContent` = `Episode`s (`order`, `seasonNumber`, `runtime`, `logLine`; `titleBrief` is a production code, not a title). Episode art `type/STUDIO`, season `type/KEY`. Search: `v8/content?include=contentId&contentType=Movie&contentType=Series%20with%20Season&searchString=…&limit=1000` → ids. My List: `GET /User/Queue` → `[{contentId, order, lastModified, remindMe}]`, `PUT /User/Queue/{id} {"remindMe": false}`, `DELETE /User/Queue/{id}` (204). | #285, #325, #305; series HAR #222, #324, #250–#259 | `lib/api.py` |
| C2 | Genres / categories / filter / recommendations | No endpoint for filter or sort: the website filters the blocks map client-side (every title, with `simplifiedGenres`, `popularity` (higher = more popular), release years). Categories = Browse's `CATEGORY_GRID`. Recommendations: `GET recommender.starz.com/api/v3/recommendations/partner/Web/profile/{profileId}[?type=Movies]` with the app token → `{ranks: [{contentId, title, value}]}`. | filter HARs, recommender HARs | `browse_all`, `recommendations` |
| D | Playback-info call | `GET /api/v4/Play?contentId=` → 204 (entitlement/device check; 403 errorCode 1305 at the device limit). `POST /api/v4/Play/start {"contentId": int, "mediaResumePointSeconds": null}` → `{playbackSessionId, keepAliveSeconds: 600, mediaUrl, mediaLicenseToken, mediaDrmEndpoint, mediaResumePointSeconds, captions[], prerollUrls[], preferredCdnHost, alternateCdnHosts}`. Both use `SessionTokenAuthorization`. | play HAR #5, #24 | `StarzApi.playback` |
| E | Manifest type | **DASH** (`.mpd` on `mecdn3.starz.com`), CENC CTR. A clear `.mpd` preroll plays first on the web; skipped. | #29 | `manifest_type=mpd` |
| F | DRM/licence | Widevine. `POST {mediaDrmEndpoint}/{default_KID}?token={mediaLicenseToken, fully percent-encoded}`; **raw challenge** body, **no auth headers**, no Content-Type; response `application/octet-stream` raw licence. | #104 | `license_key ...|R{SSM}|` |
| G | Proxy needed? | Not by protocol (raw challenge, no auth). But the first Kodi test got **HTTP 500** from license.starz.com with the direct URL in `license_key`; suspected cause: Kodi's URL handling altering the 766-char percent-encoded token. Now relayed via `lib/license_relay.py` (127.0.0.1 + per-run secret), which keeps the URL intact, logs Starz's error body, and retries a 5xx once with `Content-Type: application/octet-stream`. **Confirmed (Kodi log 16:07:30):** through the relay the first, unchanged request (no Content-Type) returned HTTP 200, 735-byte licence, and playback started. So the 500 was the token being altered on the direct path; Content-Type is not needed. Keep the relay. | Kodi logs 16:02:39, 16:07:30 | `license_relay.py` |
| H | PSSH/KID | Manifest carries Widevine + PlayReady ContentProtection with pssh; one `cenc:default_KID` for every track. | #29 | `_default_kid` |
| I | Resolution/HDCP | Video tiers 202p–1080p, H.264 (avc1 Baseline/Main/High), no robustness/HDCP tags. Firefox (L3) played the 576p `fmp4_lowend` tier. [VERIFY] what Kodi L3/L1 get. | #29, #91 | |
| J | Subtitles | Sidecar WebVTT per language from `Play/start.captions[]` (en-US, es-419). | #24 | `setSubtitles` |
| J2 | Keep-alive / stop | `keepAliveSeconds: 600`. `POST /Play/stop {"contentId": "<string>", playbackSessionId, mediaResumePointSeconds: int}` → 204 (episode HAR #398). `Play/update`: same fields per the web bundle, not captured [VERIFY]. service.py sends update every 300 s and stop on end. Episode play confirmed identical to movie (one KID, licence URL matches). | episode HAR #343–#398 | `service.py` |
| K | Required headers | auth.starz.com: `Origin`/`Referer: https://www.starz.com`, `X-Client-Features: DevicePlaybackLimitSupport`. Kodi sends the capture's Firefox UA. | all auth calls | `StarzAuth._base_headers` |

## Next captures needed

- None blocking. Nice to have: a play left running past 5 minutes (to see `Play/update` for real).

## Open questions (answer first)

- Standalone Starz sign-in, or an account through a provider (Amazon, Apple,
  cable/MVPD)? This changes A entirely.
- DASH or HLS? (E)
- Does the licence server want a per-play token from D? (F/G)
- Any device-registration or concurrent-stream call? Look in the sign-in and
  play HARs.
