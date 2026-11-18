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
| B2 | Profiles | `GET /User/profiles` → `[{id, name, isAdmin, profileIcon}]`; `POST /User/profiles/{id}/select` → 204. | #271, #417; profiles HAR #391 | `select_profile` |
| C | Catalogue | **Public, no auth headers.** `GET playdata.starz.com/metadata-service/play/partner/Web/v8/blocks?playContents=map&lang=en-US&pages=BROWSE,HOME,…&includes=…` → `PAGE` blocks → rows (`SWIM_LANE` etc. with `contentIds`) + one `PLAY_CONTENTS.playContentsById` map (551 titles). `v8/content?contentIds=…` for the rest. Types: `Movie`, `Series with Season`, `Episode`. Art: `stz1.imgix.net/Web/contentId/{id}/type/KEY/dimension/2560x1440/lang/en-US`. Resume: `auth…/User/continue`. **Missing: series → seasons → episodes, search.** | #285, #325, #305 | `lib/api.py` |
| D | Playback-info call | Partly captured (play HAR, blocked by device limit): `GET auth.starz.com/api/v4/Play?contentId={id}` then `POST /api/v4/Play/start {"contentId": int, "mediaResumePointSeconds": null}`, both with `SessionTokenAuthorization`. Success response not seen yet: both returned **403 errorCode 1305** "device limit is reached, DeviceCount=8, DevicePlaybackLimit=6" (`properties.messageUrl = starz.com/settings/managedevices`). Web player is Bitmovin. `starzPlaybackEndpoint = https://playback.starz.com/` still unseen. | play HAR #6, #24 | `StarzApi.playback` |
| E | Manifest type | Not captured | | |
| F | DRM/licence | Not captured | | |
| G | Proxy needed? | Not captured | | |
| H | PSSH/KID | Not captured | | |
| I | Resolution/HDCP | Not captured | | |
| J | Subtitles | Not captured | | |
| K | Required headers | auth.starz.com: `Origin`/`Referer: https://www.starz.com`, `X-Client-Features: DevicePlaybackLimitSupport`. Kodi sends the capture's Firefox UA. | all auth calls | `StarzAuth._base_headers` |

## Next captures needed

1. **Series drill-down + search**: open a series, switch seasons, then search for a title.
2. **Playback**: first remove extra devices at starz.com/settings/managedevices (limit 6), then press play and let it run 30 s (rows D–K).

## Open questions (answer first)

- Standalone Starz sign-in, or an account through a provider (Amazon, Apple,
  cable/MVPD)? This changes A entirely.
- DASH or HLS? (E)
- Does the licence server want a per-play token from D? (F/G)
- Any device-registration or concurrent-stream call? Look in the sign-in and
  play HARs.
