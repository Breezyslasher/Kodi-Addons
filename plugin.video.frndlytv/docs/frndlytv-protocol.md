# The Friendly TV protocol, as captured

Everything in this addon was read from HAR captures of the web player at
`watch.frndlytv.com` taken on 2026-09-01, plus the player's own JavaScript
bundle where a capture did not reach. Nothing here is guessed; where something
is *not* known, this document says so rather than filling the gap.

Friendly TV's backend is **Revlet** (`revlet.net`), a white-label OTT platform,
so the shapes below are Revlet's rather than Friendly TV's own.

## Hosts

| Host | What it serves |
|------|----------------|
| `frndlytv-api.revlet.net` | everything: auth, pages, streams, sessions |
| `frndlytv-tvguideapi.revlet.net` | the schedule (`static/tvguide`) only |
| `d229kpbsb5jevy.cloudfront.net` | artwork |
| `drm-global.videograph.ai` | Widevine licences |

## Session headers

Every authenticated call carries three headers and nothing else that
identifies the caller — no bearer token, no cookie:

```
box-id:      <a uuid this installation invented once and kept>
session-id:  <from sign-in>
tenant-code: frndlytv
```

## Sign-in, in two steps

**1. An anonymous session.**

```
GET /service/api/v1/get/token
      ?tenant_code=frndlytv&box_id=<uuid>&product=frndlytv
      &device_id=61&display_lang_code=ENG
      &device_sub_type=Firefox,5,UNIX&timezone=America/New_York
→ {"response": {"sessionId": "...", "countryCode": "US", ...}, "status": true}
```

`device_id=61` is the web client. The backend keys stream limits off this
device class — the teardown call answers "Mobile Browser stream has been
closed" — so the addon sends the captured values rather than inventing a
device type nothing has been observed accepting.

**2. The credentials.**

```
POST /service/api/auth/v2/signin      (headers: box-id, session-id, tenant-code)
{"login_id": "...", "login_key": "...", "manufacturer": "123", "login_mode": 1}
→ {"response": {"generatedId": "...", "userId": 0000000, "email": "...",
                "packages": [...]}, "status": true}
```

**`generatedId` replaces the anonymous `session-id` on every later call.** This
is the single most important detail of the whole protocol and it is easy to
miss: the value is not called a session id anywhere in the response.

A refusal comes back as HTTP 200 with `status: false` and the reason in
`response.message`, so the HTTP status line is not enough to detect failure.

## Pages, sections and cards

Almost everything is a *page*:

```
GET /service/api/v1/page/content?path=home&count=25
```

```
response.data[]           one pane per row
  .section
    .sectionInfo          {name, code, dataType}
    .sectionControls      {viewAllTargetPath, showViewAll, infiniteScroll}
    .sectionData.data[]   the cards
```

A **card** is the universal content object:

```json
{
  "display": {"title": "...", "subtitle1": "...", "imageUrl": "common,path.jpg",
              "parentIcon": "logo,channel/logos/x.png", "markers": [...]},
  "target": {"pageType": "player", "path": "channel/live/me_tv",
             "pageAttributes": {"startTime": "...", "endTime": "...",
                                "channelName": "MeTV", "networkid": "43",
                                "isLive": "true", "episodeTitle": "..."}}
}
```

`target.pageType == "player"` means it plays; anything else means it opens
another page at `target.path`. Every `pageAttributes` value is a **string**,
including numbers and booleans.

A page may describe a section without filling it in (`sectionData.data` empty,
with a `dataRequestDelay`). Those are fetched separately:

```
GET /service/api/v1/section/data?path=<page>&code=<section code>&count=24&offset=-1
```

### Images

Image references are `"<profile>,<path>"`. The profile names a CDN prefix
listed in `system/config`'s `resourceProfiles`:

```
common  -> https://d229kpbsb5jevy.cloudfront.net/frndlytv/320/180/content/common/
logo    -> https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/common/logos/
epg     -> https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/common/epgs/
banner  -> https://d229kpbsb5jevy.cloudfront.net/frndlytv/content/banner/mobile/
```

The table also lists a `horizontal` profile at 1920x1080 whose prefix differs
from `common`'s 320x180 only in the size segment, which looks like a free
upgrade for artwork Kodi draws full-screen. **The web player never requested
it in any capture**, so there is no evidence these asset paths exist under it,
and the addon does not rewrite urls to use it.

## The menu

`GET /service/api/v1/system/config?version=4` carries `menus` (search, home,
guide, movies, tv_series, my_recordings, add-ons, settings) and the
`resourceProfiles` table above. It is cached for a day.

## Live channels

The lineup is described in two places and **neither is sufficient alone**:

- `GET /service/api/v1/tvguide/channels` has the channel `id` the schedule is
  keyed by — but every row's `target.path` is the literal string `"channel//"`,
  with no slug. It is not playable.
- The **Live Now** section (`page/content?path=section/live_now_home`) has the
  playable `channel/live/<slug>` path and what is on right now, but is keyed by
  `pageAttributes.networkid`.

They join on the network id, which the guide's rows also carry in
`target.pageAttributes.networkid`. (Note that a channel's `id` and its
`networkid` differ — MeTV is id 44, networkid 43.)

A third route exists and the addon uses it only as a fallback: the guide
overlay

```
GET /service/api/v1/template/data?template_code=tvguide_overlay&path=epg/play/<program id>
→ response.data.target_watchlive = "channel/live/<slug>"
```

It names the channel a given programme is on. That is one request *per
channel*, where the join above is one request for the whole lineup, so it is
reached only for a channel the Live Now listing did not carry.

Worth knowing: **`section/live_now_home` itself was never captured.** Its path
comes from the home page's own `sectionControls.viewAllTargetPath`, which is
ground truth, but how many channels it returns for a given `count` is not. The
overlay fallback exists because of that gap.

## The guide

```
GET https://frndlytv-tvguideapi.revlet.net/service/api/v1/static/tvguide
      ?channel_ids=31,44,127,...&start_time=<ms>&end_time=<ms>&page=N[&skip_tabs=1]
→ response.data[] = [{channelId, programs: [...], banners: [...]}]
```

Asked for **twelve channels at a time, a day at a time** — that is the shape
the web player uses and the only one observed answering. A programme carries
its times in `display.markers.startTime/endTime` (again as strings) and a
`target.path` of `epg/play/<id>`.

`GET /service/api/v1/tvguide/user/data?channel_ids=...&start_time=...&end_time=...`
returns per-user overlay state (favourites, recordings). The addon does not
use it.

## Playback

```
GET /service/api/v2/page/stream?path=<path>&stream_provider_device_id=5
```

Note **v2**, where the rest of the API is v1.

```json
{"response": {
  "streamStatus": {"hasAccess": true, "errorCode": 999, "message": "",
                   "totalDurationInMillis": 3900000},
  "sessionInfo": {"streamPollKey": "<uuid>", "pollIntervalInMillis": 180000},
  "streams": [{"streamType": "widevine",
               "url": "https://sr-live-weigel1.akamaized.net/.../index.mpd?...",
               "keys": {"licenseKey": "https://drm-global.videograph.ai/drm/wv/license/rights?token=<JWT>",
                        "certificate": ""}}]}}
```

Both live (`channel/live/<slug>`) and on-demand (`ads.contenttype=catchup`,
served from `sr-vod-gen.akamaized.net`) come back through this same endpoint
in this same shape.

### The DRM, and why there is no licence proxy

The captured licence request is a **plain POST of the raw Widevine challenge**
— the body begins with the protobuf challenge bytes — to the `licenseKey` url,
answered with `Content-Type: application/octet-stream` and raw licence bytes.
The entitlement rides in the JWT already baked into that url's query string.
The only headers the browser sent that identify anything are `User-Agent` and
`Origin`.

That is exactly what InputStream Adaptive emits and expects, so **ISA posts
straight to the url and this addon has no licence proxy** — nothing translates
a challenge, mints a token, or listens on localhost. The Apple TV+ style proxy
exists because those services wrap the challenge in a JSON envelope; this one
does not.

### One risk worth knowing

The DASH manifest carries `ContentProtection` elements for Widevine and
PlayReady but **no `cenc:pssh` and no `cenc:default_KID`**:

```xml
<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>
<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
```

So ISA must recover the key id from the init segment's `tenc` box rather than
from the manifest. ISA does do this, but it is the least-travelled path in this
addon and the first thing to suspect if a stream loads and never decrypts. The
fix, if it is ever needed, is a manifest proxy that injects a PSSH built from
the `tenc` KID — deliberately not written in advance of evidence it is needed.

Streams observed: AVC up to **720p**, AAC audio, five video renditions. No
`HDCP-LEVEL` gating of the kind Apple TV+ uses, and the web player plays these
tiers on a software (L3) CDM, so no tier filtering is needed.

### Stream slots

Friendly TV counts concurrent streams. A slot is taken by the `page/stream`
call and freed by:

```
POST /service/api/v1/stream/session/end     (multipart form: poll_key=<streamPollKey>)
→ {"response": {"message": "Mobile Browser stream has been closed."}, "status": true}
```

`GET /service/api/v1/stream/active/sessions` lists what is currently held.

A client that never posts the teardown locks the account out of its own
subscription after a few plays, which is why this addon runs a background
service purely to send it when playback stops.

**Not known:** `sessionInfo.pollIntervalInMillis` is 180000, which implies a
keepalive every three minutes, but **no poll endpoint appears in any capture or
in the JavaScript that was captured** — the only uses of `streamPollKey` in the
bundle are the teardown above and the active-sessions list. The player chunk
that would do the polling was not in the capture. The addon therefore does not
poll, and a long unattended stream may be reaped server-side.

## Not mapped

- **Search** runs on a different API surface (`/search/api/v3/`, with a TiVo
  variant at `/search/api/tivo/v1/`) that no capture exercised. Left out
  rather than guessed at.
- **Recordings** are created and cancelled through a generic form mechanism
  (`GET /service/api/v1/form?code=recording_form&path=epg/play/<id>` then
  `POST /service/api/v1/form/submit`). Browsing `my_recordings` works through
  the ordinary page route; scheduling one is not implemented.
- **Favourites**: `GET /service/api/auth/user/favourite/item?path=...&action=1|0`.
- `GET /service/api/v1/tivo/content?path=homeScreen&...` is an alternative home
  screen the web player requests and, in the capture, abandons.

## Capturing more

Chrome or Firefox devtools → Network → "Preserve log" → sign in and play →
Save all as HAR. The interesting entries are `revlet.net`, the `.mpd`, and the
POST to `drm-global.videograph.ai`.

**A HAR of a signed-in session contains the account password in the
`auth/v2/signin` request body, in clear text.** Anything shared publicly needs
that request scrubbed first.
