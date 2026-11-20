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

1. **Sign-in HAR**: sign out, then sign in at `screeningroom.a24films.com`
   (and the AAA24 member area at `aaa24.a24films.com` if it signs in separately).
2. **Browse HAR**: home, your library / rentals, the member free title, the
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
| 0 | **Backend (the gate)** | **VERIFY.** Candidates: Vimeo OTT/VHX (`*.vhx.tv`, `api.vhx.tv`; Widevine L3 → SD on Kodi), Brightcove (`edge.api.brightcove.com`, policy key), JW Player (`cdn.jwplayer.com`), Mux (`stream.mux.com`), or bespoke `*.a24films.com`. Type/Code's case study describes a custom build. `har_summary.py --backend` lists the matches. | | everything |
| A | Sign-in | **VERIFY.** Credential POST → token/JWT, vendor OAuth exchange, or device/PIN link. Note captcha (if any), body field names, where the token comes back. | | `A24Auth.sign_in`, `SIGN_IN_URL` |
| A2 | AAA24 vs Screening Room | **VERIFY** whether one account/session covers both, or the member area has its own sign-in. | | `A24Auth` |
| A3 | Account / membership status | **VERIFY.** Call that says email, member tier, country. | | `A24Auth.account_status` |
| B | Session | **VERIFY.** Bearer header vs cookie; lifetime; refresh call; sign-out/revoke call. | | `auth_headers`, `refresh`, `sign_out` |
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
