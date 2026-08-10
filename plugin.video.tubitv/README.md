# TubiTV for Kodi

Browse and watch [Tubi](https://tubitv.com/) — free, ad-supported films and TV
shows — from inside Kodi.

This is a fork of [Lunatixz's](https://github.com/Lunatixz) `plugin.video.tubitv`,
kept here so the sign-in can be maintained as Tubi changes its API.

## Features

- Browse the Tubi category rows, series and films
- Search the Tubi catalogue
- Episode listings with season/episode numbers, cast, plot and artwork
- Add a film or a whole show to the Kodi library as `.strm` files
- Optional Tubi account sign-in
- PseudoTV Live recommendation service

## Installation

1. Install **t1m Library Routines** (`script.module.t1mlib`) — it ships in this
   same repository and Kodi pulls it in automatically when installing from the
   repository
2. In Kodi: Settings → Add-ons → Install from repository → Breezyslasher
   Repository → Video add-ons → TubiTV

## Configuration

Signing in is **optional** — Tubi is free to browse without an account. An
account is only needed for titles Tubi gates behind a sign-in.

- **User Email** / **User Password**: your Tubi account credentials
- **Clear saved session**: throws away the cached tokens and forces a fresh
  sign-in on the next browse

## How signing in works

Tubi retired the old `POST /oz/auth/login/` form post, which is what broke the
addon: the previous code crashed on startup the moment that call stopped
returning a session cookie, so nothing in the addon worked at all.

The addon now performs the same handshake as the Tubi web client:

1. `POST /device/anonymous/signing_key` — sends a PKCE style challenge,
   `base64url(sha256(verifier))`, and gets back a signing key
2. `POST /device/anonymous/token` — the query string is signed
   `TUBI-HMAC-SHA256` (an AWS SigV4 lookalike) with that key, and returns an
   anonymous device token
3. `POST /api/v2/user/login` — the actual sign-in, authorised by the anonymous
   device token from step 2
4. `POST https://tubitv.com/oz/user` — hands the tokens to the web frontend,
   which replies with the `connect.sid` cookie the content calls authenticate
   against

Tokens are cached in `session.json` in the addon profile directory and reused
until they expire, so a browse or a playback no longer repeats the handshake.
Only a digest of the credentials is stored, to notice when the settings change.

A failed sign-in is not fatal any more. The addon reports it once, then carries
on anonymously, and waits 15 minutes before retrying so a wrong password does
not hammer the login endpoint.

## Credits

- Original addon by [Lunatixz](https://github.com/Lunatixz)
- `script.module.t1mlib` by [t1m](https://github.com/learningit)

## License

GPL-2.0-or-later — see [LICENSE.txt](LICENSE.txt)
