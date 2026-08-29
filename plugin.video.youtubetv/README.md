# YouTube TV for Kodi

Live channels, the programme guide and on-demand titles from your own YouTube
TV subscription, played through InputStream Adaptive with Widevine DRM.

**Experimental, and honestly so: playback is not yet confirmed to work.** See
[Status](#status) before installing.

## What it does

* Live channel list, showing what is on now on each channel in your lineup
* Programme guide, channel by channel, from the same 7-day EPG the web app uses
* Search across live and on-demand
* Widevine playback via InputStream Adaptive, up to 1080p where the account allows
* A local licence proxy that handles YouTube's JSON-wrapped, rotating-key
  Widevine exchange, which InputStream Adaptive cannot speak on its own

## Status

The API side is derived from real captures of a signed-in session and is
verified against them: the guide parser reads all 150 stations out of a real
EPG response, and the player wiring builds correctly from real live and
on-demand responses. The protocol is written up in
[docs/youtube-tv-protocol.md](../docs/youtube-tv-protocol.md).

What has **not** been confirmed is that the stream actually plays. Every
`player` response offers a `dashManifestUrl` next to Google's own
`serverAbrStreamingUrl`, and the web player always takes the SABR path -- so
the DASH route this addon depends on is offered but lightly travelled. If
Google does not serve it to a non-browser client, this addon cannot work and
there is no fallback short of reimplementing SABR, which is a moving target.

Settle it before expecting anything to play:

```
python3 tools/youtube_tv_check_dash.py cookies.txt --save-mpd live.mpd
```

Exit status 0 means the manifest serves segments InputStream Adaptive can
fetch.

## Requirements

* Kodi 21 (Omega) or newer
* InputStream Adaptive 21+ with a working Widevine CDM
* A paid YouTube TV subscription
* The addon's **service must be enabled** -- the licence proxy runs there, and
  protected playback fails without it

## Signing in

Google grants no OAuth scopes for YouTube TV and does not accept scripted
password login, so there is no "enter your password" screen and no pairing
code. Signing in means handing the addon the cookies of a browser that is
already signed in to `tv.youtube.com`.

You need a computer with a browser once. After that the Kodi box is on its own
until the cookies expire.

**On the computer:**

1. Sign in to `https://tv.youtube.com` in Chrome or Firefox and check a channel
   plays. If it does not work there, it will not work in Kodi.
2. Install a cookies.txt extension -- *Get cookies.txt LOCALLY* (Chrome) or
   *cookies.txt* (Firefox) are the usual ones.
3. With tv.youtube.com open, export. Choose **all domains** rather than just
   the current site if the extension offers the choice.

**Getting the file to Kodi**, whichever is easiest:

* a USB stick;
* a network share -- Kodi's file browser reads SMB/NFS paths directly, so the
  file can stay on the computer;
* if the Kodi box has a browser of its own, do the export there and skip the
  transfer.

**In Kodi:** open the addon, choose **Sign in** -> *Choose a cookies.txt file*,
and point it at the file. It calls the guide immediately and tells you how many
channels the account can see, so a bad import fails at the dialog rather than
at the first play.

If moving a file around is more trouble than it is worth, **Sign in** ->
*Paste a Cookie header* takes the raw `Cookie:` header copied from devtools
(F12 -> Network -> any tv.youtube.com request -> Request Headers). It is about
3 KB of text, which is unpleasant on a remote but fine with a keyboard or a
phone app that can send text to the box.

Only the cookies that matter are kept, and they go in the addon's profile
directory rather than `settings.xml`, which ends up in backups and bug reports.
Where the export carries both `.google.com` and `.youtube.com` copies of a
cookie, the youtube.com one wins -- that is the domain the API lives on.

### When it stops working

Cookies expire, and Google rotates them faster on accounts with 2FA. The
symptom is HTTP 401/403 and a "the session was rejected" message. Re-export and
sign in again; nothing else needs redoing.

Signing out from the browser you exported from also invalidates the addon's
copy. So does "sign out everywhere" in Google account security.

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
lib/auth.py    cookie import, SAPISIDHASH request signing
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
