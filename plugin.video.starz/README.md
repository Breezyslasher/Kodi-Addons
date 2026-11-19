# Starz for Kodi *(experimental)*

An unofficial Starz client for Kodi: sign in with your Starz account, browse
the full catalogue and play movies and series through InputStream Adaptive
with Widevine DRM.

Starz publishes no API for third-party clients. This add-on uses the same
calls as the Starz website, read from browser captures (see
[docs/recon.md](docs/recon.md)), so a change on Starz's side can break it until
the add-on is updated. A daily check on GitHub
(`.github/workflows/starz-endpoint-check.yml`) tests the public part of those
calls and opens an issue when one changes.

## Features

**Watching**
- Movies, series and episodes in Widevine DRM, with English and Spanish
  audio and subtitles where Starz has them
- Resume points and Continue Watching shared with Starz's own apps
- Next episode: works with the [Up Next](https://github.com/im85288/service.upnext)
  add-on (its pop-up appears when the credits start); without it, the next
  episode plays automatically (Settings → Playback)
- Trailers (context menu, and the Trailer button in Kodi's info dialog) and
  each series' Extras: previews, behind-the-scenes and promos

**Browsing**
- Home, Series, Movies and Browse rows as on the website, Continue Watching,
  My List and Recommended For You (per profile)
- All Movies / All Series, Genres and Categories, each with **Sort** (A–Z,
  Popular, Release year) and **Filters** (Movies/Series, release decade, run
  time, genre)
- Search; Cast & crew pages (everything a person is in on Starz); "If you
  like …" similar titles
- Watched marks from your Starz viewing history; Coming Soon with
  **Remind me**
- Cast, director, writer and studio in Kodi's info dialog

**TV**
- **TV Schedule**: the 17 STARZ, STARZ Encore and Plex channels with today's
  and tomorrow's line-ups. **STARZ is live** (marked Live; the same broadcast
  as starz.com/livetv); the other channels have no live stream, so their *On now* plays the scheduled title on demand from the
  point the channel has reached.
- The same channels and a two-day guide in **Kodi's TV guide** through
  [IPTV Manager](https://github.com/add-ons/service.iptv.manager)
  (Settings → TV Guide).

**Account**
- Profiles with their pictures, and **Manage profiles** (Settings → Account): add a profile (name
  and picture), rename it or change its picture, delete one, and set its parental controls (highest TV and
  movie rating) with your Starz parental-control PIN, from the account owner's profile (Kodi offers to switch to it). Each profile's limits
  are applied: titles with a blocked rating are hidden.
- Not in Kodi: changing your email, password or PIN (use starz.com)
- **Manage devices** (Settings → Account): log out other devices. Starz allows 6; at the limit,
  playback offers to log one out and tries again.
- Sign out: ends the session on Starz and removes this Kodi from your Starz
  devices, freeing its place in the 6-device limit

## Signing in

Settings → Account → Sign in, or *Sign in* in the add-on's main menu, offers:

- **Email and password.** Kodi links itself the way starz.com/activate does,
  which needs no captcha.
- **Activation code.** Kodi shows a 4-character code; enter it at
  starz.com/activate on a phone or computer. Use this if your Starz account
  signs in with **Google or Apple**: sign in on the website the usual way,
  then enter the code.

TV-provider (cable/satellite) sign-in is not supported: the Starz website
code captured so far has no TV-provider flow, only Google and Apple sign-in
(`Auth/{Google|Apple}/connect`), which the activation code already covers.

## Resolution: what to expect

- **Desktop (Windows, macOS, Linux) and LibreELEC: standard definition**
  (about 576p). Kodi uses the software Widevine **L3** CDM, and Starz keeps HD
  for hardware-protected playback. This is a DRM limit, not a bug.
- **Android with hardware Widevine L1** (Nvidia Shield, certified Android TV
  boxes, most phones): the manifest offers up to 1080p. Not yet confirmed on
  such a device.

## Requirements

- Kodi 21 or later
- InputStream Adaptive and a Widevine CDM (`script.module.inputstreamhelper`
  offers to install the CDM)
- A Starz subscription (US)
- Optional: Up Next, IPTV Manager

After installing or updating, **restart Kodi** once so the add-on's
background service starts: it relays licence requests, reports playback to
Starz, and handles tune-in and the next episode.

## Disclaimer

This add-on is not affiliated with or endorsed by Starz; the Starz names and
logos belong to Starz Entertainment. Use it only with your own subscription.
Third-party clients may be against the service's terms of use, so any risk to
your account is yours.
