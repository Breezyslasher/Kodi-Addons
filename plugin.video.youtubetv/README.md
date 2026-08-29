# YouTube TV for Kodi

Live channels, the programme guide and on-demand titles from your own YouTube
TV subscription, played through InputStream Adaptive with Widevine DRM.

**Experimental.** It plays, and it does so by speaking a protocol Google does
not document. See [Status](#status).

## What it does

* Live channel list, showing what is on now on each channel in your lineup
* Programme guide, channel by channel, from the same 7-day EPG the web app uses
* Search across live and on-demand, and browsing a show to its episodes --
  including the seasons the show page defers rather than lists
* Widevine playback via InputStream Adaptive at up to 1080p
* A local licence proxy that handles YouTube's JSON-wrapped, rotating-key
  Widevine exchange, which InputStream Adaptive cannot speak on its own
* A SABR client and a bridge that serves it to InputStream Adaptive as DASH,
  because YouTube TV no longer delivers entitled media any other way

## Status

Playback works, live and on-demand, in HD. That took undoing three earlier
conclusions in this file, so it is worth saying what changed.

YouTube TV does not serve its `dashManifestUrl` segments. Across every capture
the only `videoplayback` GET answered 200 is the guide's 240p unencrypted
preview tiles; all real media is fetched by **SABR**, a proprietary POST
protocol with UMP-framed responses and a proof-of-origin token. Everything that
looked like a signing problem -- 403s on every segment, oldest to newest, with
and without `n`, ranged and not -- was that.

So the addon implements SABR: a UMP parser, the `ClientAbrState` request loop,
and a bridge that writes a DASH manifest of its own and serves the segments
through the local proxy, which is the one thing InputStream Adaptive can read.
Getting HD out of it needed three fields in the request rather than one --
naming a height is necessary and not sufficient; a viewport and a capability
list are what make the server serve it.

The proof-of-origin token is minted in **pure Python**. BotGuard is a 63 KB
obfuscated interpreter running a 10 KB bytecode program, and it now runs on a
vendored ES5 engine with seven corrections to it, so the addon needs no Node
and no JavaScript runtime on the box. A token takes about eight seconds to mint
and lasts twelve hours.

What is not done: 4K. No format above 1920x1080 has ever been offered to this
addon or to a browser on the same account, and no format has ever carried
`DRM_TRACK_TYPE_UHD1` -- the licence grants a UHD1 key, but there is no UHD1
track to use it on.

The full analysis, including every request tried and every conclusion since
retired, is in [docs/youtube-tv-protocol.md](../docs/youtube-tv-protocol.md).

## Requirements

* Kodi 22 (Piers) or newer
* **InputStream Adaptive 22.3.20 or newer**, with a working Widevine CDM.
  Not 21: on the same account, licence and media, ISA 21.5.22 cannot decrypt
  YouTube TV's audio fragments and 22.3.20 can -- the video track plays and
  the audio is silent or noise, and no change to this addon fixes it. ISA 22
  is a binary addon built against Kodi 22, which is why the Kodi requirement
  moves with it. `addon.xml` requires 22.3.20, so on Kodi 21 the addon will
  not resolve its dependencies rather than half-playing
* A paid YouTube TV subscription
* The addon's **service must be enabled** -- the licence proxy runs there, and
  protected playback fails without it

## Signing in

A short code, authorised on your phone or laptop. Google shows the code, you
approve it once, and the addon holds a token that refreshes itself -- there is
nothing to re-export later.

The addon used to take a cookie jar exported from a signed-in browser instead.
That worked, and it went stale every few days: Google rotates those cookies
constantly, and the fix was always the same errand of opening a browser,
exporting again and getting the file onto a TV box. It is gone.

**Where the API project comes from.** Google's device flow has no anonymous
grant, so a project has to exist somewhere. Three places are tried, in order:

1. this addon's own settings, under **Account**;
2. **plugin.video.youtube's**, if you have that addon set up -- it is the same
   pair, and reusing it costs nothing, because this addon never calls
   `googleapis.com` at all. Its InnerTube requests go to `tv.youtube.com` with
   a bearer token; the project is used to mint that token and for nothing
   else, so none of the Data API quota is spent here;
3. one built into the build (`lib/baked_oauth.py`, absent from this repository
   -- see below).

If none applies, create one at `https://console.cloud.google.com`: enable the
*YouTube Data API v3*, make an **OAuth client ID** of type *TVs and Limited
Input devices*, and paste the ID and secret into the addon's settings. This is
the one thing the cookie route did not need.

**On shipping a project in a public build.** `lib/baked_oauth.py` is gitignored
on purpose. A client ID published in a repo fronts every install at once:
whoever owns it carries any abuse, and Google's cap on users of an unverified
app with a sensitive scope applies to the lot of them together. When it is
suspended, sign-in breaks for everyone simultaneously and there is nothing the
installed addon can do. Personal builds are a different matter -- bake it and
hand out the zip.

**Then, in Kodi:** open the addon and choose **Sign in**. It shows a short code
and a URL; open that on any device signed in to the account with the YouTube TV
subscription and enter the code. The addon immediately asks YouTube TV for the
lineup with the new token and tells you how many channels it sees, so a token
the service will not accept fails at the dialog rather than at the first play.

The token is stored in the addon's profile directory rather than in
`settings.xml`, which ends up in backups and bug reports.

### When it stops working

The token refreshes itself, so the usual causes are outside the addon:
revoking the addon's access in your Google account, deleting the API project,
or the subscription lapsing. The symptom is HTTP 401/403 and a message saying
the stored sign-in was refused. Choose **Sign in again**; nothing else needs
redoing.

## Settings

| Setting | Why you would touch it |
| --- | --- |
| Maximum resolution | Cap the height ISA selects, on hardware that cannot decode 1080p smoothly |
| Client version | The web player version the addon claims to be. Google bumps it regularly; a stale value is the likeliest cause of a sudden sign-in failure |
| Licence proxy port | Only if something else on the machine already uses it |

## How it fits together

```
default.py     routing: channels, guide, search, play
service.py     licence proxy lifecycle + the live heartbeat loop
lib/auth.py    the credential: the stored token and the identity it is accepted as
lib/oauth.py   the device-code flow and token refresh
lib/api.py     InnerTube calls: browse(FEunplugged_epg), player, search, heartbeat
lib/epg.py     renderers -> stations and airings
lib/playback.py  player response -> a ListItem wired to InputStream Adaptive
lib/license_proxy.py  raw Widevine <-> YouTube's JSON envelope, with key rotation
```

Two things need explaining.

**The licence proxy.** InputStream Adaptive can only POST a raw Widevine
challenge and read raw licence bytes back. YouTube instead wants the challenge
base64'd inside a JSON envelope carrying the video id, the playback nonce, a
session id and `drmParams`, and returns the licence wrapped the same way. The
proxy is a localhost HTTP server that sits between the two and translates. It
is secured with a per-session secret, because "localhost" includes every other
process on the machine and any web page open in a browser there.

**Key rotation.** Live channels rotate keys, and each period's licence is keyed
by a `cryptoPeriodIndex` that ISA knows nothing about. From a single observed
request the index appears to be `ceil(unix_time / 86400)` -- a daily period.
One data point is not a specification, so the proxy tries the neighbouring
indices when the server rejects its first guess, and logs when a neighbour is
the one that works.

**The heartbeat.** Live playback must poll `player/heartbeat` every 30 seconds.
It carries `HEARTBEAT_CHECK_TYPE_YPC`, the entitlement check, so the loop runs
in the service for as long as a stream is playing.

## Caveats

* Every endpoint here is private and undocumented. Google changes them without
  notice and nothing about this is supported.
* The lineup follows the account's home market. Locals are whatever that market
  grants.
* Google's terms of service do not permit third-party clients. This is your
  subscription and your account risk.
* Not affiliated with or endorsed by Google LLC.
