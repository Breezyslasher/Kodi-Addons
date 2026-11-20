# A24 Screening Room for Kodi (in development)

Unofficial Kodi client for the **A24 Screening Room** and the **AAA24**
membership: sign in with your own A24 account and play what that account is
entitled to — titles you own or have rented, the member free movie of the
month, and filmmaker commentaries / member extras.

## Status

**Scaffold (M0).** Menus, settings, the item/entitlement mapping and the
InputStream Adaptive + Widevine wiring are in place. Sign-in, browse and
playback wait on a capture of the real service: each says "not built yet"
and names its row in [`docs/recon.md`](docs/recon.md) until then.

| Milestone | State |
|---|---|
| M0 Identify backend & capture | waiting on HARs (`tools/har_summary.py --backend`) |
| M1 Sign in / sign out | not started |
| M2 Library, Member Free, Store, Extras | not started |
| M3 Widevine playback, rental expiry | ISA wiring ready; *Test playback* in Advanced |
| M4 Polish, release zip | not started |

## What it is not

- **Not "all A24 films".** Only what the A24 app itself serves to your account.
- **No purchases.** Rent or buy in the A24 app or website; Kodi only plays
  what you already have. Unowned titles are labelled "Rent in the A24 app".
- **US only**, like AAA24 and the catalogue.

## Watermarking — personal playback only

A24 stamps every stream with a **visible overlay watermark** and an
**invisible forensic watermark**, both tied to the signed-in account. This
add-on plays streams exactly as A24 serves them. It does not, and will not,
try to remove, hide or alter either watermark, and it is meant only for
watching on your own account.

## Requirements

Kodi 21+, InputStream Adaptive, a Widevine CDM (InputStream Helper installs
it). Quality on the desktop software (L3) CDM may be capped — to be measured
in M3.

## Settings

- **Account:** Sign in, Account status, Sign out. The password is never stored.
- **Playback:** maximum video height, subtitles.
- **Advanced:** developer logging; *Test playback* with a manifest and licence
  URL copied from a capture (proves the DRM path before the API is mapped).

## Development

See [`docs/recon.md`](docs/recon.md). Every endpoint stays `VERIFY` until a
capture proves it. Never commit a HAR. Date-based versions (`YYYY.M.D`).
