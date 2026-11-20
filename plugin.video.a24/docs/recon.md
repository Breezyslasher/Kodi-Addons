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
2. **Browse HAR** (next; on `app.a24films.com` — the screening room, not the
   a24films.com marketing site): home, your library / rentals, the member free title, the
   store, one title's detail page, its commentary/extras, a search.
3. **Play HAR**: press Play on a title you own, rent, or get free as a member;
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
| 0 | **Backend (the gate)** | **Lead (public recon, signed out, CI run 44644218234 — not yet confirmed by a Play HAR):** `screeningroom.a24films.com` → **`app.a24films.com`**, an Ionic/Capacitor SPA on S3 (`/assets/index-*.js`). Own REST API on **`api.app.a24films.com`** (`/api/v1/...`). The bundle names **Brightcove** (no `vhx`, `jwplayer` or `mux`) → expect Brightcove Playback API + Widevine; **VERIFY** account id / policy key and whether playback goes through A24's API first. Player route `/player/:productId`. Also `account.a24films.com`, Stripe/Shopify for purchases (out of scope), AAA24 site separate (`aaa24.a24films.com`, nginx). | `recon_public.py` | everything |
| A | Sign-in | **Confirmed (sign-in HAR, a24films.com, 2026-11-19).** Accounts on `account.a24films.com/api/v1/` (Django "nexus"). `GET account/exists/?email=` → `{exists, has_usable_password}`. `POST auth/passwordless/request/` JSON `{email, type: "otp_code"}` → 200 empty; A24 emails a 4-digit code. `POST auth/passwordless/validate/` JSON `{email, otp_code}` → account JSON + `Set-Cookie: nexussessionid` (32 chars, HttpOnly, `Domain=.a24films.com`, Max-Age 1209600 = 14 days) and `nexus_session_exists=1`. No CSRF header, no captcha (preflight asks only for `content-type`). Password login (`auth/login/`) exists in the bundle but is not used: Kodi never needs the password. | sign-in HAR #183, #193, #211 | `A24Auth.request_code`, `validate_code` |
| A1 | Account / membership endpoints (leads) | `/api/v1/all-access/membership/`, `/all-access/plans/user/`, `/all-access/settings/` (bundle). Not captured yet. | `recon_public.py` | |
| A2 | AAA24 vs Screening Room | **VERIFY** whether one account/session covers both, or the member area has its own sign-in. | | `A24Auth` |
| A3 | Account status | **Confirmed.** `GET account/` with the cookie → `{has_usable_password, is_email_confirmed, current_plan, user_profile: {nexus_id, email, first_name, last_name, timezone, region, credit: {current_balance, last_increase, next_increase}, is_active, is_guest, ...}}`. `current_plan` is `null` on the captured account (no AAA24 plan) — **VERIFY** its shape on a member account. | sign-in HAR #217 | `A24Auth.account`, `do_account` |
| B | Session | **Confirmed:** cookie `nexussessionid` on `.a24films.com` (so it also reaches `app.`/`api.app.a24films.com`), 14 days, no refresh call seen. Kodi stores it with its expiry and asks for a new code when it lapses. **VERIFY** whether the Screening Room API (`api.app.a24films.com`) takes the cookie directly or swaps it for a token via `account/tokens/?client_id=` (bundle). | sign-in HAR #211 | `A24Auth.request` |
| B1 | Sign-out | `auth/logout/` in the bundle; not captured. Kodi signs out locally (forgets the cookie). | | `sign_out` |
| C1 | Library | **VERIFY.** Owned titles + active rentals with expiry. | | `ENDPOINTS["library"]` |
| C2 | Member free title | **VERIFY.** Current movie of the month (rotates monthly — never cache). | | `ENDPOINTS["member_free"]` |
| C3 | Store | **VERIFY.** Rent/own titles with price. Decide whether to list unowned titles at all (they show "Rent in the A24 app" and don't play). | | `ENDPOINTS["store"]` |
| C4 | Title detail + field names | **VERIFY.** Title, year, plot, runtime, art, cast/director, entitlement state + rental expiry. Fills `to_item` (field list in `lib/listing.py`). | | `to_item`, `ENDPOINTS["title"]` |
| C5 | Commentaries / extras | **VERIFY.** Separate videos or an alternate audio track on the main title? | | `ENDPOINTS["extras"]` |
| C6 | Search | **VERIFY** whether one exists; the catalogue may be small enough to filter locally. | | `ENDPOINTS["search"]` |
| D | Play / decision | **VERIFY.** Request + response: manifest URL, licence URL/token, subtitles; what a lapsed rental returns (403?). | | `A24Api.playback` |
| E | Manifest | **VERIFY** HLS vs DASH, codecs (AVC/HEVC, SDR/HDR), top height. If HEVC/HDR variants appear, add Apple TV's `avc_only`/`sdr_only` toggles. | | `manifest_type` |
| F | Licence | **VERIFY.** Widevine licence URL, required headers/body wrapping. Plain headers → ISA `license_key` (done). Per-request signing or a JSON-wrapped challenge → add a `service.py` localhost relay (Apple TV `license_proxy.py` / Starz `license_relay.py`). Advanced → *Test playback* plays a captured manifest + licence URL to check this before row D is mapped. | | `build_isa_listitem` |
| G | Widevine level / quality | **VERIFY** top height on the software L3 CDM (desktop) and on an L1 Android box. | | README |
| H | Geo | AAA24 and the catalogue are US-only; **VERIFY** what a non-US request returns, for a clear message. | | |

## Sources (leads only)

- A24 Screening Room: https://screeningroom.a24films.com/
- AAA24 FAQ (rentals, commentaries): https://aaa24.a24films.com/page/all-access-faqs/
- Type/Code case study (custom build, DRM, overlay + forensic watermarking): https://typecode.com/a24-screeningroom/
- Vimeo OTT DRM (only if row 0 says VHX): https://help.vhx.tv/article/718-drm-with-vimeo-ott
