# A24 Screening Room for Kodi (in development)

Unofficial Kodi client for the **A24 Screening Room** and the **AAA24**
membership: sign in with your own A24 account and play what that account is
entitled to — titles you own or have rented, the member free movie of the
month, and filmmaker commentaries / member extras.

## Status

**First working build, awaiting a real-box test.** The backend is known
(A24's own API on `api.app.a24films.com`, video on Brightcove with Widevine).
Sign-in, My Library, Store, search, extras and playback are built from
captures and the app's code; Member Free still waits on a capture from an
AAA24 account. See [`docs/recon.md`](docs/recon.md).

| Milestone | State |
|---|---|
| M0 Identify backend & capture | done: A24 API + Brightcove (Playback Restrictions) |
| M1 Sign in / sign out | built: emailed code, then app tokens (no password) |
| M2 Library, Member Free, Store, Extras | Library, Store, Search, Extras built; Member Free needs a capture |
| M3 Widevine playback, rental expiry | built (Brightcove + `BCOV-Auth` licence header); needs a real-box test |
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

## Artwork

`resources/icon.png` and `resources/fanart.jpg` are built by
`tools/make_art.py` (run by the *A24 recon and art* workflow) from A24's
official logo — public domain on Wikimedia Commons, a trademark of A24 —
used only to identify the service this unofficial add-on plays.
