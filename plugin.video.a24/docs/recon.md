# A24 Screening Room recon worksheet

Every A24-specific detail the code depends on comes from a capture, not from
memory. Fill each row from a HAR, cite the HAR entry number
(`tools/har_summary.py` prints them), and only then write the code that uses
it. Until a row is filled, the matching code raises `NotReadyError` and Kodi
shows "not built yet: it waits on docs/recon.md row X".

Scope: personal playback on the user's own account. A24's platform (built by
Type/Code) adds a user-specific overlay watermark and an invisible forensic
watermark on top of DRM. Nothing in this add-on, its tools or this worksheet
looks at, changes or works around either; streams play exactly as served.

## Captures to collect

The proxy in the cloud dev container blocks `a24films.com`, so captures come
from your own machine. In a signed-in browser (DevTools → Network, "Preserve
log" on, then "Save all as HAR"):

1. **Sign-in HAR** (done 2026-11-19, a24films.com): sign out, then sign in at `screeningroom.a24films.com`
   (and the AAA24 member area at `aaa24.a24films.com` if it signs in separately).
2. **Browse HAR** (done 2026-11-19; on `app.a24films.com` — the screening room, not the
   a24films.com marketing site): home, your library / rentals, the member free title, the
   store, one title's detail page, its commentary/extras, a search.
3. **Play HAR** (still wanted — the 2026-11-19 one stopped at the Brightcove
   player load; it should show `jwt/`, `edge-auth.api.brightcove.com` and the licence POST): press Play on a title you own, rent, or get free as a member;
   let it run ~30 s. Must contain the play/decision call, the manifest and the
   licence POST.
4. Only if the web hides the calls: the A24 mobile app through mitmproxy.

```bash
python3 tools/har_summary.py capture.har --backend   # row 0: which platform?
python3 tools/har_summary.py capture.har --hosts     # API vs CDN vs licence hosts
python3 tools/har_summary.py capture.har             # one line per request, with hints
python3 tools/sanitize_har.py capture.har a24-sanitized.har
```

Never commit a HAR (`*.har` is gitignored): it holds live tokens and an
account-traceable session. Read a sanitized HAR before sharing it.

## Unknowns

| # | Unknown | Finding | HAR ref | Code |
|---|---|---|---|---|
| 0 | **Backend (the gate)** | **Confirmed (app HARs 2026-11-19 + bundles).** `screeningroom.a24films.com` → `app.a24films.com` (Ionic/Vue SPA). Own API `https://api.app.a24films.com/api/v1.6/`; accounts on `account.a24films.com/api/v1/` ("nexus"); video on **Brightcove** (account `6276845754001`, player `8h9yBgsS9`, Brightcove player 7.30) using **Playback Restrictions**: an A24-minted JWT, no policy key. | app HARs, `VideoPlayer-*.js`, player `index.min.js` | everything |
| A | Sign-in | **Confirmed (sign-in HAR, a24films.com, 2026-11-19).** Accounts on `account.a24films.com/api/v1/` (Django "nexus"). `GET account/exists/?email=` → `{exists, has_usable_password}`. `POST auth/passwordless/request/` JSON `{email, type: "otp_code"}` → 200 empty; A24 emails a 4-digit code. `POST auth/passwordless/validate/` JSON `{email, otp_code}` → account JSON + `Set-Cookie: nexussessionid` (32 chars, HttpOnly, `Domain=.a24films.com`, Max-Age 1209600 = 14 days) and `nexus_session_exists=1`. No CSRF header, no captcha (preflight asks only for `content-type`). Password login (`auth/login/`) exists in the bundle but is not used: Kodi never needs the password. | sign-in HAR #183, #193, #211 | `A24Auth.request_code`, `validate_code` |
| A1 | Account / membership endpoints (leads) | `/api/v1/all-access/membership/`, `/all-access/plans/user/`, `/all-access/settings/` (bundle). Not captured yet. | `recon_public.py` | |
| A2 | AAA24 vs Screening Room | **VERIFY** whether one account/session covers both, or the member area has its own sign-in. | | `A24Auth` |
| A3 | Account status | **Confirmed.** `GET account/` with the cookie → `{has_usable_password, is_email_confirmed, current_plan, user_profile: {nexus_id, email, first_name, last_name, timezone, region, credit: {current_balance, last_increase, next_increase}, is_active, is_guest, ...}}`. `current_plan` is `null` on the captured account (no AAA24 plan) — **VERIFY** its shape on a member account. | sign-in HAR #217 | `A24Auth.account`, `do_account` |
| B | Session | **Confirmed:** cookie `nexussessionid` on `.a24films.com`, 14 days. Used only on `account.a24films.com`: the app API ignores cookies. | sign-in HAR #211 | `A24Auth` |
| B1 | Sign-out | Bundle: browser redirect to `auth/auto-logout/?client_id=&redirect_to=`. Kodi signs out locally. | bundle | `sign_out` |
| B2 | App tokens | **Bundle (index-*.js), not yet seen on the wire.** OAuth2 public client + PKCE: `client_id nHodnPAsF9MSYmRkVlFH1EsD142RZ9Nk1xNg1f1e`, web `redirect_uri https://app.a24films.com/tabs/home/`. `GET account…/api/v1/auth/authorize/?response_type=code&client_id&code_challenge&code_challenge_method=S256&redirect_uri&back_to_site_title=A24 App` → redirect `?code=`; `POST account…/api/v1/auth/token/` form `grant_type=authorization_code, code, client_id, code_verifier, redirect_uri` → `{access_token, refresh_token}`; refresh `grant_type=refresh_token, refresh_token, client_id` (400/401 → sign in again). App calls: `Authorization: Bearer <access>`, `PLATFORM: web`, JSON. Access token = HS256 JWT `{id, salt, exp}`. **Confirmed on a real box 2026-11-20:** with only the session cookie, `authorize` redirects straight to the code (no consent page) — but only for the **native** return URL `https://api.app.a24films.com/redirect/`; the web one answers HTTP 400 `invalid_request: Mismatching redirect URI`. Signed out, every variant (even a bad client_id) → 302 `/login?next=` (CI probe). | app HAR #443 (Bearer), bundle, kodi.log | `A24Auth.authorize`, `_token`, `request` |
| C1 | Library | **Confirmed:** `GET library/` → `{products: [product], entitlements: [{id, purchase_type ('b' bought, 'r' rental), purchase_date, expiration_date, is_active, product_id, entitlement_progress: {id, progress_timestamp, is_watched, is_started, start_next_cta, auto_start_next}}]}`. | app HAR a #452 | `A24Api.library` |
| C2 | Member free title | Products carry `is_member_content`; the member tab's endpoint is **not captured** (needs an AAA24 account). `portals/?collection_page_size=&collection_page=` holds the home rows. | | `member_free` (NotReady) |
| C3 | Store | **Confirmed:** `GET products/?purchaseable=true&page=N` → `{count: 147, next, previous, results}` (12/page). Variants: `purchase_type 'r'` rent / `'p'` purchase, `price_type.price`, `start_watching_window` 720 h, `complete_watching_window` 48 h. Unowned titles are labelled "Rent $x / Buy $y in the A24 app" and do not play. | app HAR a #354 | `A24Api.store` |
| C4 | Product | **Confirmed:** `GET products/{id}/` → `{id, display_name, description, content_type ('f' film, 't' trailer), release_date, is_member_content, title_properties: {video: {id, external_id (Brightcove id), name, is_vertical}, audio_options, rating, year, duration, closed_caption, director_commentary, punch_in/out, supplemental_video_content}, poster_art/static_art: {images: {thumbnail, small, medium, large}}, credits: [{credit_type 's' star/'d' director/'w' writer, person: {name}}], product_variants, associated_content: [product]}`. | app HAR b #76 | `to_item` |
| C5 | Commentaries / extras | Extras: `associated_content` (trailer `'t'` etc., each its own product + Brightcove id). **Director commentary: confirmed on a real box 2026-11-20** — it is an alternate audio track inside the film's own DASH manifest (`audio_options` lists only English, but `director_commentary: true` marks it); Kodi's audio-track menu switches to it. Nothing to fetch separately. | app HAR b #76, real box | `A24Api.extras`, `to_item` (`commentary`) |
| C6 | Search | No server search captured; the add-on matches title/credits across the purchaseable pages. | | `A24Api.search` |
| C7 | Progress | Bundle: `PUT entitlementprogess/{entitlement_progress.id}` `{progress_timestamp}` (sic). Not wired yet (M4). | `VideoPlayer-*.js` | |
| D | Play / decision | **Bundle (VideoPlayer-*.js):** `POST api…/v1.6/jwt/ {"product_id"}` → `{"jwt"}` (Brightcove playback token). Then (Brightcove player 7.30: `bcovAuthToken` with `policyKey: null` → "epa" host) `GET https://edge-auth.api.brightcove.com/playback/v1/accounts/6276845754001/videos/{external_id}` with `Authorization: Bearer <jwt>` → Brightcove video JSON (`sources`, `text_tracks`). **Confirmed on a real box 2026-11-20** (Backrooms played). | bundles, kodi.log | `A24Api.playback` |
| E | Manifest | Brightcove `sources[]`: expect DASH (`bccenc`) + HLS on `manifest.prod.boltdns.net`. Add-on prefers Widevine DASH, then Widevine HLS. Real box: DASH VOD, 1 period, 4 streams, H.264 video + AAC stereo, captions. `{WMT}` in a source URL would be a forensic-watermark placeholder the A24 app does not fill: played as served. Top seen: 1440p, display-limited (row G). | player source | `select_source` |
| F | Licence | **Player source:** for DRM sources the player sets `emeHeaders: {"BCOV-Auth": <jwt>}` and POSTs the raw Widevine challenge to `key_systems["com.widevine.alpha"].license_url` → ISA `drm_legacy = com.widevine.alpha|<license_url>|BCOV-Auth=<jwt>` (no relay needed). `bcov_auth=` query is only for AES-128 HLS. **Confirmed on a real box 2026-11-20.** | player `index.min.js` | `build_isa_listitem` |
| G | Widevine level / quality | **Measured 2026-11-20, 1440p laptop, software L3 CDM:** Backrooms plays at **2560×1440, 23.976 fps**, H.264 (software decode), AAC stereo 48 kHz. That is the **screen** limit (ISA log: "max allowed: 3072x1776"; a 3840-wide stream does not fit), not an L3 cap — L3 gets at least 1440p. **VERIFY** the top rendition on a 4K display (and L1 Android). | real-box screenshot, kodi.log | README |
| H | Geo | AAA24 and the catalogue are US-only; **VERIFY** what a non-US request returns, for a clear message. | | |

## Sources (leads only)

- A24 Screening Room: https://screeningroom.a24films.com/
- AAA24 FAQ (rentals, commentaries): https://aaa24.a24films.com/page/all-access-faqs/
- Type/Code case study (custom build, DRM, overlay + forensic watermarking): https://typecode.com/a24-screeningroom/
- Vimeo OTT DRM (only if row 0 says VHX): https://help.vhx.tv/article/718-drm-with-vimeo-ott
