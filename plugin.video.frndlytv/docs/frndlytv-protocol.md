# The Friendly TV protocol, as captured

Everything in this addon was read from HAR captures of the web player at
`watch.frndlytv.com` taken on 2026-09-01, plus the player's own JavaScript
bundle where a capture did not reach. Nothing here is guessed; where something
is *not* known, this document says so rather than filling the gap.

Friendly TV's backend is **Revlet** (`revlet.net`), a white-label OTT platform,
so the shapes below are Revlet's rather than Friendly TV's own.

## Hosts, which the client asks for rather than assumes

The web player fetches this before anything else:

```
GET https://paas-init.revlet.net/clients/frndlytv/init/live/frndlytv-live-v2.json
→ {"default": {...}, "web": {...}, "roku": {...}, "android": {...},
   "androidtv": {...}, "amazontv": {...}, "appletv": {...}, "ios": {...}}
```

Each block is the same shape:

```json
{"location": "https://frndlytv-api.revlet.net",
 "api":      "https://frndlytv-api.revlet.net",
 "search":   "https://frndlytv-api.revlet.net",
 "pgURL":    "https://frndlytv-api.revlet.net",
 "guideURL": "https://frndlytv-tvguideapi.revlet.net",
 "tivo":      "https://op4fswl7z7.execute-api.us-east-1.amazonaws.com",
 "tivoClick": "https://zc0o5laiad.execute-api.us-east-1.amazonaws.com/...",
 "tenantCode": "frndlytv", "product": "frndlytv", "isSupported": true}
```

**The blocks differ**, which is the proof they matter: Roku's `search` is
`frndlytv-rokuapi`, and Android's whole `api` is `frndlytv-androidapi`. This
addon presents itself as the web player everywhere else, so it reads `web`
over `default`.

The addon caches this for a day and falls back, per key, to the captured
value. A value that is not an `https://` url is refused. So a moved host is
followed rather than fatal, and a hostile or broken file cannot redirect the
addon anywhere.

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

### `device_id` is **5**, and 61 no longer works

A capture of signing in from a signed-out browser sends:

```
GET /service/api/v1/get/token?tenant_code=frndlytv&box_id=<uuid>
    &product=frndlytv&device_id=5&display_lang_code=ENG
    &device_sub_type=Firefox,5,UNIX&timezone=America/New_York
```

An older capture used `device_id=61`, and the addon copied it. The service
now answers 61 with **HTTP 403 and an empty body**, which surfaces as
"Friendly TV issued no session id" and locks out sign-in completely. 5 is
also the value already sent as `stream_provider_device_id` and as `di` on a
playback report.

This one call carries **fewer headers than any other**: User-Agent, Accept,
Accept-Language and Origin, and no `box-id`, `session-id`, `tenant-code` or
`Referer` — there is no session yet. The `auth/v2/signin` that follows does
carry all three.


## Home is assembled from two endpoints

This is the single easiest thing to get wrong, and the first build did.

```
GET /service/api/v1/page/content?path=home&count=25
```

carries the banners and **one** row — Live Now — and nothing else. Listing it
the way every other page is listed produces a Home showing only live channels.
Every row a viewer expects comes from a different endpoint:

```
GET /service/api/v1/tivo/content?path=homeScreen&carouselCount=10&assetsCount=30
→ {"response": {"data": [ ...10 panes... ],
                "pageCursor": "MTBgd2F0Y2hBZ2Fpbk...", "pageType": "content"}}
```

and then, for each further page, `carouselCount=4` plus the `pageCursor` the
previous response returned, until no cursor comes back. The captured session
settled after five requests: Continue Watching, Recommended for You, New
Episodes, Just Added Movies, A Recipe for Romance, Legendary Entertainment,
Blockbuster Boulevard, Because You Watched "…", Frndly Featured, Watch Again,
and more behind the cursor.

The panes are the ordinary section/card shape described below, so the same
parser reads them. Only Home works this way — Movies, TV and My Stuff are
ordinary `page/content` pages.

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

## A deferred section answers with a list, not a page

A page can name a section without filling it in — `sectionData` comes back
empty with a `dataRequestDelay` — and this is the call that populates it:

```
GET /service/api/v1/section/data?path=my_recordings&count=24&offset=-1
    &code=epg_series,reality_myrecording,your_sitcoms
```

`code` is **comma-separated**, and `response` is a **bare list**, one element
per code asked for, in the order asked:

```json
[{"section": "epg_series", "data": [ <cards> ],
  "hasMoreData": false, "lastIndex": 5,
  "dataRequestDelay": 0, "showViewAll": false}, ...]
```

The trap is `section`: here it is the **code as a plain string**, where in a
page pane it is an object holding `sectionInfo` / `sectionData` /
`sectionControls`. Nothing else about the two shapes lines up either — the
cards are a sibling `data` list rather than nested under `sectionData`.

Reading it with the page reader raised, on a real box:

```
unhandled error in ['?action=section&code=continue_watching_movies&path=movies']:
  'list' object has no attribute 'get'
  File "lib/parse.py", line 201, in sections
    for pane in (response.get("data") or []):
```

**Not known:** `hasMoreData` and `lastIndex` clearly describe pagination
against the `offset` parameter, but every captured response has
`hasMoreData: false`, so no capture shows a follow-up request and the meaning
of `offset` is unverified. The addon asks once, with the `offset=-1` the
capture uses, and does not page.

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

The web guide is a grid, and its columns are half hours. A Kodi listing cannot
be a grid, so the addon offers both axes: a channel opens its own schedule,
and "What's on at..." opens the half hours, each listing every channel and
what it is showing at that moment. The second one asks for the whole lineup,
which is the same request in twelves — the batch size is the player's, not a
choice — issued together rather than one after another.

### Which airings are recorded

```
GET /service/api/v1/tvguide/user/data?channel_ids=31,44,...&start_time=<ms>&end_time=<ms>
→ {"response": {"data": [{"channelId": 44,
                          "programs": [{"id": 3478978, "info": {}}, ...]},
                         {"channelId": 31, "programs": []}, ...]}}
```

Same channel batch and window as `static/tvguide`; the web player fetches the
two in parallel. **It is a membership set and nothing more** — every `info`
object in every captured response is empty (37 of them), and the player uses
it exactly that way:

```js
hasRecordMarker = programIds.indexOf(program.id) > -1
```

So a listed id is an airing with a record marker. Whether that means recorded
or merely scheduled is not distinguished by the response, and the player does
not distinguish it either.

This is worth a request because the flag is **nowhere else**: a schedule row
from `static/tvguide` does not carry it, so the alternative is one lookup per
airing.

### The schedule carries no metadata — the overlay does

An airing in `static/tvguide` is a title, an id and two times. Everything worth
reading is in the overlay the web player opens when one is selected:

```
GET /service/api/v1/template/data?template_code=tvguide_overlay&path=epg/play/<id>
→ response.data = {
    name, description, cast, image, channel_icon_url,
    subtitle:  "MeTV Toons",                       (the channel)
    subtitle1: "Tue, Sep 1 | 12:00 AM - 12:30 AM", (when)
    subtitle2: "Repeat",
    subtitle3: "S9 Ep2 | Dregg Of The Earth",      (S/E and episode title)
    subtitle4: "TVY7",                             (certificate)
    target_watchlive:       "channel/live/metv_toons",
    target_browse_episodes: "series/shows/521500",
    target_record:          "recording_form" }
```

That is one request per airing, which is why it is pooled, capped and behind a
setting — the same trade as a listing's descriptions.

### Resume

The service sends progress **twice**, and only one of the two actually
resumes playback.

On the card, a Continue Watching row carries a `seek` marker whose value is a
fraction of the running time (`"0.01795995379283789"`), alongside a
`lastUpdatedTime`. That is what draws a progress bar in a listing; it needs the
card's `duration` to become a position, and values outside 0..1 are worth
rejecting rather than trusting.

On the **stream**, `streamStatus.seekPositionInMillis` is an absolute position,
and that is what resumes: opening an episode from Continue Watching answers
with it set (12000 in the observed case), and ISA seeks there before the first
frame — `PosTime (12000)`, then `Seek time 12.0 … continues at 12.0`. A client
that reads only the card's fraction will draw the right progress bar and still
start from zero.

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

### `streams` is one entry per DRM system, and order is not a promise

`streamType` names the **DRM system**, never the container. The five captured
stream responses split cleanly in two:

| path | `stream_provider_device_id` | streams returned |
|---|---|---|
| `channel/live/me_tv` | `5` | widevine, DASH |
| `channel/live/metv_toons` | `5` | widevine, DASH |
| `epg/play/3488570` | `5` | widevine, DASH |
| `video/play/vszuyoc` | *absent* | widevine DASH, playready DASH, **fairplay HLS** |

Friendly TV's packager puts DASH under `/v1/dash/` and HLS under
`/v1/master/`, which is a steadier signal than the extension given how long
the query strings on these urls are. All three entries in the VOD answer carry
`attributes.mimeType: "eia608/1"` — that is the caption format, not the
manifest type, and it says nothing about which entry to take.

**Take the entry whose `streamType` is `widevine`.** Taking the first entry
with a url is wrong. When the FairPlay entry comes first, that hands ISA an
HLS manifest encrypted for FairPlay while declaring the key system to be
Widevine, and ISA cannot report that cleanly. The Kodi 21 log reads:

```
Manifest successfully parsed (Periods: 1, Streams in first period: 2, Type: live)
ParseChildManifest: Cannot detect container type from media url, fallback to TS
Cannot create sample reader due to unhandled representation container type
OpenStream: Codec id 27 require extradata.
InitializePeriod: Unhandled encrypted stream.
```

That last line is the same message a missing key system produces, so it reads
like the ISA-version DRM bug rather than the wrong stream — and the `Type:
live` on a VOD, the TS fallback and the "require extradata" above it are the
tell that the manifest itself is the wrong one. Whatever rung ends up on
screen in that state is incidental; resolution cannot be judged from it.

**Not known:** whether `stream_provider_device_id` is what selects between the
one-stream and three-stream answers. The correlation holds across all five
captures but no capture varies the parameter against a fixed path, so cause is
not established. The addon sends the parameter exactly where the capture shows
the web player sending it — live and guide paths, not VOD — and picks by
`streamType` rather than trusting position, so it is correct either way.

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

### Which ISA property carries the DRM — verified the hard way

ISA has two spellings for this and **the boundary is ISA 22.1.5**, not 21:

| ISA | Property |
|-----|----------|
| ≥ 22.1.5 | `inputstream.adaptive.drm` — a JSON object |
| < 22.1.5 | `inputstream.adaptive.license_type` + `license_key` |

This addon first shipped with the threshold at 21, and on Kodi 21.3 (ISA
21.5.22) every protected stream died like this:

```
inputstream.adaptive: Manifest successfully parsed (Periods: 1, Streams in first period: 2, Type: live)
inputstream.adaptive: InitializePeriod: Unhandled encrypted stream.
CVideoPlayer::OpenInputStream - error opening [plugin://plugin.video.frndlytv/...]
```

ISA 21 does not warn about the JSON property, it simply does not read it, and
then meets an encrypted stream with no key system configured. The same build
played correctly on Kodi 22. Two logs, one changed variable — that is the whole
evidence, and it is why `kodiutils.isa_has_json_drm()` compares against
`(22, 1, 5)` and fails closed to the legacy pair.

The legacy `license_key` field is `server|headers|challenge|response`, where
`R{SSM}` is the raw challenge and an empty response field takes raw licence
bytes back — which is exactly what this licence server speaks.

### The missing KID — flagged as a risk, now measured

The DASH manifest carries `ContentProtection` elements for Widevine and
PlayReady but **no `cenc:pssh` and no `cenc:default_KID`**:

```xml
<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>
<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
```

so ISA has to recover the key id from the init segment rather than from the
manifest. This was carried as the addon's main unverified risk. It is not a
risk any more: on Kodi 21.3 / ISA 21.5.22, desktop L3, the stream plays, and
ISA says so on the way past:

```
ConvertKidStrToBytes: Cannot convert KID "" as bytes due to wrong size
Initializing stream with unknown KID!
... Creating Demuxer / CDVDVideoCodecFFmpeg H.264 / audio decoder aac
```

Those two lines are **normal for this service** and not a fault to chase. ISA
opens the session with no KID, the licence server returns the keys anyway
because the entitlement rides in the licence url's token rather than in the
challenge's key id, and decryption proceeds. Verified playing on both Kodi 21
and Kodi 22.

If a stream ever does load and never decrypt, the fix would be a manifest proxy
injecting a PSSH built from the init segment's `tenc` KID — still not written,
and now with less reason to be.

Streams observed: AVC up to **720p**, AAC audio, five video renditions. No
`HDCP-LEVEL` gating of the kind Apple TV+ uses, and the web player plays these
tiers on a software (L3) CDM, so no tier filtering is needed.

### Start Over is a different path, not a different request

The web player's Start Over is a **client-side flag** — `setStartOver(true)` in
the bundle — and the `page/stream` request it makes is byte-for-byte the one it
makes to play live. The overlay's `target_startover_live` is the *same value*
as `target_watchlive`, so following it would simply play live again.

What actually differs is which path is asked for:

| path | answers with |
|------|--------------|
| `channel/live/<slug>` | `contentType: "live"`, from `sr-live-*`, at the live edge |
| `epg/play/<program id>` | `contentType: "vod"`, from `sr-vod-gen`, `seekPositionInMillis: 0`, the programme's full duration |

So starting a programme over is asking the stream endpoint for the
**programme's own path** rather than its channel's. Both come back Widevine
with a licence url as usual.

**A programme still on the air is answered differently**, and this is now
established rather than assumed. Asked for one that is still running,
`epg/play/<id>` returns a **live** manifest — five periods, against one or two
for the plain channel — not the VOD asset it returns once the programme has
finished. ISA opens a live manifest at the live edge, so following that path
alone plays from wherever the channel is, which is not starting over.

What makes it start over is telling ISA to open at the beginning of that
manifest's timeshift window
(`inputstream.adaptive.play_timeshift_buffer`). The window is the one the
service chose to publish for this programme's path, and its start is the
programme's start — confirmed on hardware.

### Related titles

```
GET /service/api/v1/foliotabs?tab=morelikethis/<id>&count=56&offset=0
→ {"response": {"data": [ ...ordinary cards... ]}}
```

`<id>` is the numeric tail of the title's path — `movies/1058109` and
`series/shows/521500` ask as `morelikethis/1058109` and `morelikethis/521500`.

When it has nothing to suggest it **refuses rather than returning an empty
list**, with `status: false` and **code `-4`**:

> Oops! We did not find anything similar right now. Check back later or explore
> Recommended For You on the Homescreen…

Note the code: search's no-match is `404` and this one is `-4`, so a client
that special-cases only the first will report this as an error.

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

## Why a stream plays at 288p, and what actually fixes it

The service does not send a low-quality copy. The captured VOD manifest
(`sr-vod-dai-frndly.akamaized.net/.../stream_ad_tp.mpd`, 120 KB) offers four
video rungs, all H.264, in **one** AdaptationSet:

| height | width | bandwidth |
|--------|-------|-----------|
| 288    | 512   | ~0.70 Mbit/s |
| 360    | 640   | ~0.85 Mbit/s |
| 432    | 768   | ~1.67 Mbit/s |
| 720    | 1280  | ~2.28 Mbit/s |

**720p is the ceiling — there is no 1080p in this service.** Alongside them is
an `image/jpeg` AdaptationSet of 640x180 thumbnail tiles for trick play, which
is why a naive scan of the manifest reports a "180p" rung that no player would
ever choose.

The manifest has **21 periods**: six of content and fifteen inserted ads
(dynamic ad insertion — `-dai-` is in the hostname). The ad periods are
packaged by a different encoder, marking their video set with `mimeType`
where the content periods use `contentType`, but **every period carries all
four rungs in a single AdaptationSet**, so a period boundary never strands a
player on a lower one. Segments are 6 s (`timescale="15360"`, `d="92160"`).

So neither the service nor the manifest is the cause. The choice is
InputStream Adaptive's, made by its default representation chooser
(`src/common/ChooserDefault.cpp`):

```
score = |rep.width * rep.height  -  screen.width * screen.height|
if rep.bandwidth > 0.9 * estimated_bandwidth:  skip this rep entirely
score += sqrt(0.9 * estimated_bandwidth - rep.bandwidth)
pick the lowest score
```

On any screen 720p or larger the 720p rung always has the best pixel score, so
the *only* way to land on 288p is the bandwidth filter. Working the numbers
against the rungs above:

* below ~0.75 Mbit/s estimated — nothing qualifies, and ISA falls back to
  `selector.Lowest()`, which is literally the first representation listed
* ~0.75 to ~0.91 Mbit/s — **only the 288p rung qualifies**
* ~2.5 Mbit/s and up — 720p qualifies and wins

And where does that estimate come from? `src/Session.cpp` seeds it from the
speed of the **manifest download**, scaled by a stated hack:

```cpp
// The download speed with small file sizes is not accurate ...
if (manifestResp.dataSize < 512 * 1024)
  manifestResp.downloadSpeed = (manifestResp.downloadSpeed / manifestResp.dataSize) * 512 * 1024;
m_reprChooser->SetDownloadSpeed(manifestResp.downloadSpeed);
```

That is a 120 KB object fetched over a freshly opened HTTPS connection, and
Kodi's `GetFileDownloadSpeed()` counts connect and TLS handshake time in the
elapsed total. After that, only downloads **over 512 KB** update the average
(`AdaptiveStream.cpp`), which here means the video segments do count (a 288p
6 s segment is ~527 KB) but audio segments never do, so the average moves
slowly. A stream can therefore open low and stay there.

### The lever

`inputstream.adaptive.stream_selection_type` = `fixed-res` selects
`CRepresentationChooserFixedRes`, which takes `selector.Highest()` — the
highest representation fitting inside a resolution limit — and **never adapts
away from it**. The limit comes from
`inputstream.adaptive.chooser_resolution_max` and, for a DRM session,
`inputstream.adaptive.chooser_resolution_secure_max`; these streams are
Widevine, so both must be set or the setting has no effect.

The chooser names, verbatim from `GetReprChooser()` in
`src/common/Chooser.cpp`, are `default` / `adaptive`, `fixed-res`,
`ask-quality`, `manual-osd`, `test`. The accepted resolution strings, verbatim
from `RES_CONV_LIST` in `src/CompSettings.h`, are `auto`, `disabled`, `480p`,
`640p`, `720p`, `1080p`, `2K`, `1440p`, `4K` — anything else is logged as
"Resolution not valid" and dropped.

All four properties are read by **ISA 21.5.x and 22.x alike**, so unlike the
DRM property this needs no version fork. Verified against
`src/CompKodiProps.cpp` on both the `Omega` (21.5.24) and `master` (22.3.21)
branches.

Two things this is *not*:

* `inputstream.adaptive.max_bandwidth` — gone; the property is now
  `inputstream.adaptive.chooser_bandwidth_max`.
* `inputstream.adaptive.config` with `resolution_limit` — that exists, but it
  marks representations *unplayable* (`Session.cpp`), which is a cap, not a
  floor, and so cannot raise anything.

If a log shows `Disabled stream repr ID "..." as not HDCP compliant`, none of
the above applies: the DRM session itself is refusing the higher rungs, and
that is a display-chain limit rather than a bandwidth one.

The addon exposes this as **Settings > Playback > Video quality**, defaulting
to "Best available" (`1080p`, which on this service resolves to the 720p rung).
"Let InputStream Adaptive decide" sets nothing and restores the old behaviour.

## Browse by Genre is a row of words, not pages

Home carries a `browse_by_genre` section of fifteen cards, marked
`isThirdPartySection: true`. Each has an **empty `pageType`, empty
`pageAttributes`, and a bare word for a path**:

```
Romance  DIY  Comedy  Reality  Crime  Drama  Westerns  Gameshow
Suspense  nostalgia  Mystery  Action  Cooking  Documentary  Faith
```

(The display name and the path differ where the service felt like it —
`Crime` shows as "True Crime", `DIY` as "Home & DIY", and `nostalgia` is the
one lowercase token.)

There is no page at `Westerns`, so routing these like any other card opens
nothing. The web player hands the word to its own genre screen, which no
capture has exercised. Handing it to **search** instead is not a fudge: the
search endpoint matches on genre, as the `action` row above shows.

## Details pages are not made of sections

A film or series page (`movies/<id>`, `series/shows/<id>`) does **not** use the
section shape. It has one pane of `paneType: "content"`:

```
response.data[0].content
  .title, .posterImage, .backgroundImage
  .dataRows[].elements[]   {elementType, elementSubtype, data, target}
```

and the thing that plays it is a **button element whose `target` is the path**:

```json
{"elementType": "button", "elementSubtype": "start_watching",
 "data": "Start Watching", "target": "channel/live/metv_toons"}
```

A series then has section panes after that content pane, one per season. **A
film has none at all** — which is why reading only sections rendered a film's
page as completely empty, with `0 section(s)` in the log and nothing on screen.

Buttons that act locally rather than open a path — "Record", "Favorite" — carry
a blank or whitespace `target`, and the add-on info button targets the literal
string `settings`. Requiring the target to look like a path (to contain a `/`)
is what keeps all three out.

**A film's page therefore holds exactly one thing**, which is why this addon
lists films as playable and resolves the button behind the scenes rather than
making a viewer open a folder to find a single item. A series page is a real
folder and stays one.

### A film's page and a series' page are not the same shape

They differ in exactly the way that breaks a naive reader:

| | series (`series/shows/<id>`) | film (`movies/<id>`) |
|---|---|---|
| synopsis | a `description` element | **no `description` at all** — it is in `subtitle2` |
| `subtitle1`/`subtitle2` | the episode **on the air right now** | the film's own synopsis |
| `subtitle` | — | when it airs, e.g. `Sat, Aug 29 \| 10:00 AM - 12:00 PM \| 2h` |
| `Director` | absent | present |
| below the pane | one section per season | nothing |

So reading only `description` leaves every film blank, and folding `subtitle2`
into the plot regardless gives a series the wrong synopsis — its current
episode's, not its own. Both fields have to be gathered and then sorted out by
which of them the page actually has.

`marker`/`tag` carries the year and certificate together as one string:
`"1975 | TVG "`.

### Listings carry no synopsis at all

Measured across every captured response: `display.description` and
`display.Director` are empty on **all 8191** cards, and `display.cast` on all
but 160. A row of films has titles and artwork and nothing else.

That is a hard limit on what a listing can show without extra requests. Kodi's
own Information dialog reads the list item, so filling it in means fetching
each title's page — one request per row, which is why this addon does it on a
small thread pool, behind a setting, and capped.

### Telling the kinds apart

The service routes each kind of thing to its own page prefix, and that is the
only reliable signal for what a card *is* — `contentType` in `pageAttributes`
takes at least five values across the same kind (`movie`, `epg`,
`epgseriesdetails`, `tvshowepisode`, `unifiedshows`).

| Path prefix | What it is |
|-------------|-----------|
| `channel/live/<slug>` | a live channel |
| `movies/<id>` | a film's page — one play button |
| `series/shows/<id>` | a series' page — seasons under it |
| `video/play/<id>`, `epg/play/<id>` | plays directly |

Season and episode numbers are only ever in `display.subtitle1`, at the front,
in one of three spellings: `S3 E33 | 30m`, `S7 - Ep8 | Mon, Aug 31 | ...`, and
`S1 E1 - Fallen Timbers`.

One trap: a **live channel's card carries the season and episode numbers of
whatever is on it**, and is titled with that programme. Treating a card as an
episode purely because it has S/E numbers turns the Live TV list into a list of
programmes retitled away from their channels.

## Recording a title, as opposed to an airing

The form a card names — `player_recording_form`, as opposed to the guide's
`recording_form` — was never captured: clicking Record in the web player
reloads the page and drops the request. Running it from the addon settles
what it does, and a later capture settles what the web player does instead.

Running the form from the addon:

```
form?code=player_recording_form&path=series/shows/...   -> options; recorded
form?code=player_recording_form&path=movies/1054314     -> no options
   returned only: [('title', 'label'), ('form_cancel', 'cancel')]
```

So it is the **path**, not the form. A series card records; a `movies/...`
card gets a form with a title and a cancel button and nothing to choose. Both
kinds of card say recording is allowed, and 488 movie cards do:

```json
"isRecordingAllowed": "true", "isRecordingDisabled": "false"
```

so the flags do not predict it either, and the addon is right to offer the
entry and report what came back.

**A title is recorded from its own page, by a call of its own.** Pressing
Record or Stop Recording on `watch.frndlytv.com/movies/…` or
`…/series/shows/…` sends no form at all — four captures, two titles, both
buttons:

```
POST /service/api/auth/unify/series/record
Content-Type: application/x-www-form-urlencoded

path=movies/11883820150&action=1        → {"message": "Scheduled to Record"}
path=movies/434993&action=0             → {"message": "Stop Recording"}
path=series/shows/1897528247&action=1   → {"message": "Scheduled to Record"}
path=series/shows/1897528247&action=0   → {"message": "Stop Recording"}
```

The title's own path — the same one its card carries — and `action` 1 to
record, 0 to stop. No other value of `action` has been seen and none is sent.
The endpoint's name is not a misnomer for the film case: a series' page uses
the same call, which is why it is named for series.

This is what makes a Coming Soon film recordable: it has not aired, so there
is no airing to record against and its page has nothing to play.

Which verb to offer comes from the card: `pageAttributes.isRecorded`, which
every one of the 569 captured film cards carries (and 2104 of the 2303 other
recordable cards). Where it is missing, both are offered rather than guessed.

The **other** route is real too, and it is the one the guide overlay uses —
recording a film from the player, rather than from its page:

```
GET /service/api/v1/form?code=recording_form&path=epg/play/3498954
→ record_series / "Record Movie",
  value = "action:1;contentId:982759543;contentType:movie;programId:3498954"

POST /service/api/v1/form/submit
{"code": "recording_form", "path": "epg/play/3498954",
 "fields": {"record_program": "<that value>"}}
→ {"message": {"message": "Added to My Stuff"}}
```

so a film's airing records like any other airing. The addon keeps the form
for guide airings and channels, because there it is a different question —
this episode, or the series? — that the page call has no way to express. For
a film or a show it uses the page call, which is what those pages do.

## Recordings

Recording is not a dedicated endpoint. It is a generic **form** mechanism: the
service describes the choices, and the client sends back the one that was
picked.

```
GET /service/api/v1/form?code=recording_form&path=epg/play/<program id>
→ response.elements[] = [
    {elementCode: "record_series",  fieldType: "radio-button",
     data: "Record All New and Repeat Episodes",
     value: "action:1;contentId:127_521500;contentType:series;programId:3477311"},
    {elementCode: "record_episode", fieldType: "radio-button",
     data: "Record This Episode",
     value: "action:1;contentId:3477311;contentType:program"},
    ... plus a hidden heading, a submit and a cancel ]
```

```
POST /service/api/v1/form/submit
{"code": "recording_form", "path": "epg/play/<id>",
 "fields": {"record_program": "<the chosen element's value, verbatim>"}}
→ {"response": {"message": {"message": "Added to My Stuff"}}, "status": true}
```

**`player_recording_form`** is what 2221 captured cards name in
`pageAttributes.recordingForm` — films, shows and channels — as opposed to the
guide's `recording_form`. No capture contains a request for it, for a
mechanical reason: clicking Record in the web player reloads the page, which
clears the network log unless "Preserve log" is ticked, so the request is
discarded before the HAR is exported. Running it from the addon settled it
instead, as the section above records: it works from a series card and
returns nothing to choose from a `movies/...` one.

The form self-describes either way: a client asks for the code the card names,
lists whatever radio buttons come back, and echoes the chosen value verbatim —
so there is nothing here to guess wrong, and the worst case is a form that
offers nothing, which is the case the film retry handles.

`stop_recording_form` has the same shape with three options —
`stop_recording_episode`, `stop_recording_series`, `stop_delete_recoding_series`
(the service's own spelling) — carrying `action:0` and `action:4`.

Two things make this safe to implement without knowing what the instruction
string means: **every submission uses the single field name
`record_program`**, whichever radio button it came from, and the `value` is
echoed back exactly as it arrived rather than constructed. Six distinct
instruction strings were captured, and the addon can only ever send one of the
values a form handed it.

## Search

Search runs on a **different API surface** from everything else — the same
host, but `/search/api/tivo/v1` rather than `/service/api/v1` — with the same
session headers.

```
GET /search/api/tivo/v1/get/search/query?query=gun&limit=16&offset=0&bucket=All
→ {"response": {"hasMore": true, "totalCount": 37, "queryId": "...",
                "searchResults": {"count": 16, "displayName": ..., "sourceType": ...,
                                  "data": [ ...ordinary cards... ]}}}
```

Paged with `offset`, sixteen at a time, while `hasMore` is true.

**Search matches on more than titles**, which is the most useful thing about
the endpoint and is not obvious from its shape. It is a relevance ranking over
titles, people and genre together — not a title match, and not a genre filter
either:

| query | total | of the first 16, how many have the word in the title | what comes back |
|-------|-------|------|------|
| `perry mason` | 2 | 2 | the series and a Perry Mason film |
| `Raymond Burr` | 3 | **0** | Perry Mason, *Count Three and Pray*, *Please Murder Me!* — the **actor's** titles |
| `drama` | 2417 | **0** | The Golden Girls, Murder She Wrote, Perry Mason, Gunsmoke — the **genre** |
| `action` | 447 | 1 | Gunsmoke, NCIS, Bonanza, Monk — the genre again |
| `adventure` | 83 | **13** | *Adventures of Champion*, *Adventures of Sherlock Holmes* — mostly **title** matches |

`adventure` is the one that shows what is really happening: where a word is
common in titles, those rank first and the genre sense is buried; where it is
not (`drama`, `action`, a person's name), the other senses surface. So this is
a good way to reach a genre or an actor, and not a guarantee of one.

Searching by person and by genre therefore needs no separate endpoint or
parameter — it is the same query. That is what makes the fifteen "Browse by
Genre" cards usable (see below) and a cast list worth being able to search.

`bucket` filters by type. Four values are captured: **`All`**, **`Series`**,
**`Movie`** and **`Station`**, which the web player labels Shows, Movies and
Channels.

**A search with no matches is reported as a failure, and is not one.** It comes
back HTTP 200 with:

```json
{"error": {"code": 404, "message": "We didn't find any matches for “gun”..."},
 "status": false}
```

Two things follow. The reason lives in an `error` object, not in
`response.message` where the rest of the API puts it, so a reader that only
knows the main API's shape reports "HTTP 200" instead of the actual message.
And a `404` here means *zero results*, not a fault — it happens routinely,
since the Channels bucket matches nothing for most queries — so it has to reach
the viewer as an empty list rather than an error dialog.

Results are the ordinary card shape and carry a **mix** of `pageType`s: mostly
`details` pages (`series/shows/<id>`, `movies/<id>`) but also directly playable
on-demand episodes (`pageType: "player"`, `path: "video/play/<id>"`). Routing
them by `pageType` like any other card is all that is needed.

Two related endpoints exist and the addon does not use them: the landing screen
`GET /search/api/tivo/v1/search/screen` (which returns `searchResults` as a
*list* of buckets rather than one object) and
`.../search/screen/trendingSearches`.

The bundle's endpoint table also names a `searchApi: "/search/api/v3/"`, which
nothing in any capture calls; the TiVo path above is what the web player
actually uses.

## What still needs captures

Ordered by how much each would add. Each is a short click path in the web
player with devtools open on the Network tab and "Preserve log" ticked.

| # | Missing | How to capture it |
|---|---------|-------------------|
| 1 | **A series / episode browse** — the page shape from a show down to a playable episode | From TV, open a series, open a season, play an episode. Only `page/content?path=series/shows/<id>` has been seen, not the chain through to playback. |
| 2 | **Stream keepalive** — whether anything polls with `streamPollKey` | Play a channel and **leave it running for 6+ minutes** with devtools open. `pollIntervalInMillis` is 180000, implying a call every 3 minutes that has never been seen. |
| 3 | **A lapsed session** — what the API answers with once a session expires | Sign in, leave the tab idle for hours, then click around. This is the one behaviour the addon's re-authentication is written against without evidence. |
| 4 | **A channel the subscription excludes** | Open any add-on-package channel and let it refuse. Would confirm the `hasAccess` / `errorCode` path the addon shows verbatim. |
| 5 | **`section/live_now_home`** — how many channels it returns for a given `count` | On Home, click **View All** on the "Live Now" row. Only the 25 the home page embeds have been seen. Lower priority now that Home no longer depends on it. |

Not needed: search, Home's carousels, recordings, favourites, the guide, live
and VOD playback, and the DRM exchange are all fully captured.

## Capturing more

Chrome or Firefox devtools → Network → "Preserve log" → do the thing → Save
all as HAR. The interesting entries are `revlet.net`, the `.mpd`, and the POST
to `drm-global.videograph.ai`.

**A HAR of a signed-in session contains the account password in the
`auth/v2/signin` request body, in clear text.** Anything shared publicly needs
that request scrubbed first.

## Playback progress is reported, and this addon does not report it

The web player POSTs to a **different host** during playback:

```
POST https://ace.api.yuppcdn.net/analytics/partner
Content-Type: application/x-www-form-urlencoded; charset=UTF-8

data=<url-encoded JSON>&analytics_id=d36bad5f857d14e3d4d4ca4b7055e179
```

**Two form fields, not one.** Sending only `data` is refused:

```
HTTP 400  Request is missing required form field 'analytics_id'
```

`analytics_id` is neither a secret nor per-user: it is a constant in the web
app's own configuration block, beside the API base paths and the Facebook and
Google client ids, and it is byte-identical on all eight captured events.

```js
analyticsId:"d36bad5f857d14e3d4d4ca4b7055e179"
```

No authentication headers are sent at all. The account is identified inside
the payload, by `ui` and `bi`.

Eight of these went out in the two captured minutes, one per player-state
change. The fields that matter:

| field | meaning | example |
|---|---|---|
| `ps` | player state | `idle`, `buffering`, `playing`, `paused` |
| `pp` | **position, ms** | `46642`, then `54838`, then `54871` |
| `tvl` | total length, ms | `2997099` |
| `meta_id` | what is playing | `web_series_episode_vod_51084` |
| `su` | the manifest url | the `.mpd` handed back by `page/stream` |
| `psk` / `ts` | play-session key, epoch ms | `1788394984505` |
| `sk` | session uuid | `df9f58e7-…` |
| `ui` | user id | `2965565` |
| `bi` | box id | the same `box-id` header the API uses |
| `di` | device id | `"5"` — the same value as `stream_provider_device_id` |
| `a1` | account context | verbatim `analyticsInfo.customData` from `page/stream` |
| `cdn` | DRM system | `Widevine` |
| `et` / `ec` / `av` | event type, count, schema version | `1`, `1`, `v2` |
| `pln` / `plv` | player name and version | `bitmovin`, `8.179.0` |
| `dos` / `dosv` / `dc` / `dt` / `appv` | OS, OS version, browser, device type, app version | |

Unset numeric fields go out as `-1`, not omitted.

Two things tie this to Continue Watching rather than to pure telemetry:

* the first `pp` reported, `46642`, is **exactly** the
  `seekPositionInMillis` that `page/stream` had just handed back for the same
  title — the position the addon reads to resume;
* `meta_id` ends in `51084`, and `51084` is exactly the `contentId` that
  `delete/continuewatch/content` takes to remove that title from the row.

**Not known:** whether this endpoint is what *writes* Continue Watching. The
correspondence above is strong and there is no other candidate in any capture
— no `bookmark`, `progress` or `continuewatch`-write endpoint appears on
`revlet.net` — but nothing captured proves the causal link, and no capture
shows the row changing in response to one of these POSTs.

The addon now sends this, from the background service, under **Settings →
Playback → Report what you watch to Friendly TV**. Before it did, a title
watched in Kodi never reached Continue Watching and never got a resume
position — which is why Movies → Continue Watching answered with the row and
no items after watching a film through the addon:

```
section movies/continue_watching_movies: 0 card(s)
```

### The event stream

`ec` counts events from 1 within a play. `psk` is the play-session key, held
constant for the whole play at the epoch ms it began; `ts` moves with each
event. `et` is the event type, and the eight captured events were:

| `ec` | `et` | `ps` | `pp` | what it is |
|---|---|---|---|---|
| 1 | 1 | `idle` | -1 | the session opening — **38 fields**, the only one with the device block |
| 2 | 2 | `idle` | -1 | **unknown** |
| 3 | 11 | `buffering` | -1 | |
| 4 | 12 | `playing` | -1 | |
| 5 | 7 | `playing` | 46642 | the first with a position |
| 6 | 10 | -1 | 46642 | **unknown**; carries `ep` as well as `pp` |
| 7 | 13 | `paused` | 54838 | |
| 8 | 14 | `playing` | 54871 | resumed |

Every event after the first carries the same **25 fields**. The response is a
bare epoch-ms number with HTTP 200, and carries no state.

The addon sends only the codes whose meaning the capture shows: 1, 11, 12, 7,
13 and 14. Types 2 and 10 are not sent.

**The stop event is `et=8`**, captured by leaving a video with the back
button — the ordinary way out of one:

```
et=13 ec=7  ps=paused   pp=18158
et=14 ec=8  ps=playing  pp=18158
et=8  ec=9  ps=idle     pp=20072   <- left the video
```

It carries the same 25 fields as every other event; only `et`, `ps`, `pp`,
`ts` and `ec` differ from the one before it, and `ec` keeps counting. Until
this was captured the addon sent a final position event instead, rather than
invent a code.

**Not known — the cadence.** One position event went out in the two captured
minutes, which establishes no interval. The addon reports every 30 s while
playing, plus on every pause and resume.

Three fields are chosen rather than copied:

* `ip` — **omitted**. The web player sends the client's public address; the
  addon does not know it and will not ask a third party for it. The receiving
  end sees the source address regardless.
* `dos` / `dosv` — Python's `platform.system()`/`machine()` and `release()`,
  rather than the capture's browser platform string. These describe the
  device, and a true answer is better than a copied one. **Not** Kodi's
  `System.OSVersionInfo`: that returns the literal string `"Busy"` while an
  info label is not ready, and a real box duly sent `dos='Busy'`.
* `sk` — a fresh uuid per play. The captured value is a v3 (name-based) uuid
  whose derivation is not known, and nothing captured shows it having to
  match anything.

Everything else is the capture verbatim, `dt: "web"`, `dc: "firefox"`,
`pln: "bitmovin"` and `di: "5"` included: the addon presents itself as the web
player everywhere else — same User-Agent, same Origin, same device id on the
stream request — and being inconsistent here would file these events under a
client that does not exist.

## Badges, and what they mark

A card carries its badges in `display.markers`, as a list of objects on a page
card and as a dict keyed by marker type in the guide. Every badge value across
2112 captured badges:

| badge | cards | means |
|---|---|---|
| `On Now` | 1476 | airing on a live channel right now |
| `Coming Soon` | 384 | listed but not yet available |
| `Expires in ...` | 102 | leaving the catalogue |
| `New Episodes` / `New Episode` | 124 | |
| `+ Add-On` | 22 | **needs an add-on subscription** |
| `New Movie` | 4 | |

Kodi has nowhere to draw a badge, so the addon puts the service's own wording
in the label rather than inventing its own.

### `Coming Soon`

Two independent signals, both on the card, agreeing on all 47 episodes of the
captured series:

* `display.markers[]` → `{"markerType": "badgeV2", "value": "Coming Soon"}`
* `metadata.comingSoon` → `{"key": "comingSoon", "value": "true"}`

`metadata` entries are objects, and their values are strings — `"true"` and
`"false"` included.

These are not cosmetic. Selecting one is refused by the service:

```
The content provider has restricted this program from being available On Demand.
```

39 of the 47 episodes of *The Three Stooges* are Coming Soon, so finding one
that plays takes several tries without the tag.

### `+ Add-On`

**This is the card-level flag for a title outside the plan**, and it is the
one badge that carries `isRedirectToPayment`:

```json
{"markerType": "badgeV2", "value": "+ Add-On", "isRedirectToPayment": true,
 "bgColor": "E6322E2E", "strokeColor": "196BA4", "textColor": "FFFFFF",
 "position": "bottomCenter"}
```

Across all 2112 captured badges that flag appears on exactly this wording, and
on all 22 of its cards; no other badge carries it. The addon reads the **flag**
rather than the wording, so a renamed badge still works. This means a listing
can be filtered without fetching a single page.

## Packages: the base plan and the six add-ons

```
GET /service/api/auth/user/activepackages?version=2
→ {"userAcivePackages": [{"id": 4, "code": "classic", "name": "Classic",
                          "packageType": "Annual", ...}]}
```

Note `userAcivePackages` — the service's own spelling.

```
GET /service/api/auth/v2/addon/packages?package=4
→ [{"pkgMasterId": 27, "pkgName": "HISTORY Vault", "priority": 5,
    "svodNetworkRedirection": "partner/history_vault",
    "ClientAddOnPackInfo": [{"id": ..., "buttonMessage": "Add",
                             "salePrice": ..., "durationCode": "Y"}]}, ...]
```

A bare list of six, all captured with `buttonMessage: "Add"`:

| `pkgMasterId` | add-on |
|---|---|
| 10 | Hallmark+ |
| 58 | UP Faith & Family |
| 24 | Lifetime Movie Club |
| 35 | Great American Pure Flix |
| 27 | HISTORY Vault |
| 31 | A&E Crime Central |

`pkgMasterId` is what a blocked title's `addOnInfo.masterPackageId` names —
the captured film names 27, and 27 is HISTORY Vault — so a subscribe prompt
can say which add-on is wanted rather than just "an add-on".

### `isRedirectToPayment` is account-relative — settled

One search settles it. Made while the account held a HISTORY Vault trial and
nothing else, `query=good witch` answered with eleven titles in a single
response:

| results | channel | flagged |
|---|---|---|
| 6 × "The Good Witch…" | Hallmark Channel, in the base plan | no |
| 5 × "Good Witch…" | **Hallmark+**, an add-on **not** held | **`+ Add-On`** |

So flags were live in that very response. In the same session, searches
returned the **held** add-on's own films unflagged — `query=babe` gives
"Perspectives: Babe Ruth" (`movies/107679378926`), the film that had refused
to play before the subscription, with `"markers": []`.

Held → unflagged. Unheld → flagged. Base plan → unflagged. The flag means
**"you cannot watch this"**, not "this is add-on content", so hiding on it is
correct whatever the account holds.

Two earlier readings of this were wrong, both worth recording:

* Claiming it *confirmed* from the trial capture alone. The unflagged content
  there came from the **partner and Add-ons pages**, and those flag nothing at
  all — 20 cards of Hallmark+ content, an add-on not held, sit unflagged on
  the Add-ons page. That evidence showed only which page it came from.
* Before that, assuming no card-level flag existed at all.

Search is where the flag lives: 21 of the 22 flagged cards captured before the
subscription came from search, one from a series page, and none from anywhere
else.

## A section says where the rest of it lives

Every section carries `sectionControls.viewAllTargetPath`, and across the
captures it takes two shapes — 33 of one, 36 of the other:

```
section/live_now_home        a page path
/carousels/nostalgia         a carousel
```

The first is opened with the ordinary page endpoint, which the captures do:

```
GET /service/api/v1/page/content?path=section/all-networks
→ info.pageType "list", info.code "section_items",
  one section holding the whole row
GET /service/api/v1/page/content?path=section/trending_series_history_vault&count=36
```

**Not known:** how to open a `/carousels/...` path on its own. It is the same
shape the search screen's trending rows use, and those come from
`search/screen` as a set; no capture asks for a single carousel. So the addon
follows the `section/` half and leaves the other alone rather than guess an
endpoint. Without this a long row is silently cut off at whatever the page
chose to embed.

## When a Coming Soon episode airs

The card says, in the service's own wording and timezone, and the addon shows
it rather than only saying the title is unavailable:

```json
"subtitle4": "Sat, Sep 5 | 12:25 AM - 12:50 AM"
"pageAttributes": {"channelName": "MeTV+", "startTime": "1788582300000"}
```

`subtitle4` is a **mixed field**: of the 453 captured cards carrying it at
all, most hold a bare numeric id (`253656`), and only on a Coming Soon card is
it a window. Shape decides, so an id is never shown as a date. One captured
card puts its window in `subtitle1` instead — normally `"S1 E1 | 25m"` — and
the same shape test reads it safely there.

All 39 Coming Soon episodes of the captured series say when they air.

## Up next

```
GET /service/api/v2/next/videos?path=video/play/vszuyoc&count=1
GET /service/api/v2/next/videos?path=epg/play/3488570&count=1
→ {"data": [ <ordinary cards> ]}
```

The web player asks this during playback, naming what is playing. Both
captured calls ask for one; the cards are the ordinary card shape, and both
captured answers parse — `epg/play/3488570` is followed by *Criminal Minds*,
`video/play/vszuyoc` by the next episode of *Kevin Costner's The West*.

## Trending: three carousels on the search surface

Two endpoints, on the same `/search/api/tivo/v1` surface as search rather
than `/service/api/v1`, both answering in the ordinary card shape but
wrapping it differently:

```
GET /search/api/tivo/v1/search/screen
→ searchResults is a LIST of carousels
    {"name": "Trending Movies ",  "path": "/carousels/trendingMovies",
     "description": "Trending Movies based on rovi score and viewEvents...",
     "isThirdPartySection": true, "data": [ <25 cards> ]}
    {"name": "Trending TV Shows", "path": "/carousels/trendingTVSHOWS", ...}

GET /search/api/tivo/v1/search/screen/trendingSearches
→ searchResults is a SINGLE carousel object
    {"name": "Trending Searches",
     "path": "/carousels/trendingSearches_Smart", "data": [ <25 cards> ]}
```

25 cards each. Trending Movies is `movies/...` cards **bar one**: the
captured carousel mixes in a film that happens to be on the air, and that
card is the live channel (`channel/live/family_movie_channel`,
`pageType: player`), not the film's page. Trending TV Shows and Trending
Searches are both `series/shows/...`.

The names, descriptions and paths are the service's own, so the addon shows
them rather than inventing labels.

## The Add-ons page, and the six channels

`system/config`'s own menu already carries `ADD-ONS -> add-ons`, so the folder
comes from the service. The page holds a section `available_add_ons` of six
cards, one per add-on, each marked `pageAttributes.contentType: "network"`:

| card | path |
|---|---|
| Hallmark+ | `partner/hallmark_plus` |
| UP Faith & Family | `partner/up_faith_family` |
| Great American Pure Flix | `partner/pure_flix` |
| Lifetime Movie Club | `partner/lifetime_movie_club` |
| A&E Crime Central | `partner/crime_central` |
| HISTORY Vault | `partner/history_vault` |

All six are listed whether held or not — the held one is there too — so this
is the complete list, where the `addon/packages` catalogue is not. Each
`partner/...` page is an ordinary page: a content pane and sections.

The cards carry nothing to say which are held, so that comes from
`activepackages`. The same add-on is named identically in all three places
("HISTORY Vault"), which is what the match is made on.

**`contentType: "network"` does not mean "add-on".** A search for `metv`
returns `MeTV`, `MeTV+` and `MeTV Toons` as network cards with
`partner/me_tv`, `partner/me_tv_plus` and `partner/metv_toons` pages — and
those are ordinary channels in the base plan. Hiding on the marking alone
would take them out of a search. A channel is only treated as an add-on when
the service names it as one, which is the catalogue's `pkgName`s (the ones
not held) together with the account's own add-on packages (the ones that
are).

**This filtering is not an inference.** Which add-ons the account holds is
stated outright, so hiding the channels it does not hold is exact — unlike
hiding individual titles on the `isRedirectToPayment` flag above.

## A title the subscription does not include

Friendly TV sells add-on channels on top of the base plan. A title on one has
a **full page** — synopsis, cast, artwork, certificate — and no play button.
Where the play button would be there is instead:

```json
{"elementType": "button", "elementSubtype": "addonsubscribe",
 "data": "addOnInfo", "target": "settings", "isClickable": true,
 "properties": {"addOnInfo": "{\"buttonText\":\"Start 7-day Free Trial\",
    \"buttonColor\":\"#d1a128\", \"masterPackageId\":27,
    \"goToSettings\":true, \"showPopupAddonSubscribe\":false,
    \"descriptionAddonSubscribe\":\"\", \"messageAddonSubscribe\":\"\",
    \"imageUrl\":\"network,network/images/xdzdpu.png\"}"}}
```

`properties.addOnInfo` is a JSON **string**, not an object.

Read as an ordinary page this is indistinguishable from a broken one, and the
addon reported it as `Friendly TV's page for this offers nothing to play`.

`info.attributes.upgradeForm: "upgrade_form"` is **not** a second signal, and
must not be read as one: a fully subscribed series page carries it too. Nor is
an `addOnInfo` element by itself — a subscribed page has one, of
`elementType: "text"` with empty properties. What distinguishes the two is the
**button** of `elementSubtype: "addonsubscribe"` with a populated `addOnInfo`.

The addon says so using the service's own wording, which is the only thing
that knows what the offer is. It does not attempt to subscribe: that is a
payment flow and belongs in Friendly TV's own apps.

## Removing something from Continue Watching

```
POST /service/api/v1/delete/continuewatch/content
{"contentId": 3488570, "contentType": "epg"}
→ {"response": {"message": "Deleted successfully"}, "status": true}
```

Both fields are the card's own: `pageAttributes.id` and
`pageAttributes.contentType`. The captured call is exactly the Criminal Minds
card's id and type. Note `contentId` goes out as a **number**, where every
`pageAttributes` value arrives as a string.

Which cards this applies to is knowable without a flag: only a part-watched
card carries the `seek` marker, and that is what the row is made of.

## Favourites

```
GET /service/api/auth/user/favourite/item?path=<path>&action=1   (add)
GET /service/api/auth/user/favourite/item?path=<path>&action=0   (remove)
→ {"response": {"message": "Added to My Stuff"}, "status": true}
```

A GET that changes state, which is the service's choice. Captured against all
three kinds of path a client would send — a guide airing's `epg/play/<id>`, a
show's `series/shows/<id>` and a film's `movies/<id>` — and all three answer
identically, so the path is simply whatever the item is.

Whether something is already a favourite is on the card itself, as
`pageAttributes.isFavourite`, a **string** `"true"`/`"false"` like every other
attribute; a title's own page says it in `pageButtons.isFavourite` instead. So
a client can show only the verb that applies rather than both.

The service calls this My Stuff in its confirmations and "Favorite" on its
button, for the same feature.

## Other endpoints seen but not used

### Active streams

```
GET /service/api/v1/stream/active/sessions
→ {"response": [], "status": true}
```

The concurrency counter's own view of what this account has playing. **Every
capture caught it empty**, so the list is ground truth and the shape of an
entry is not — a client can count them honestly, and should render an entry
from whatever fields it turns out to have rather than from field names nobody
has seen.

- `GET /service/api/v1/tivo/content?path=homeScreen&...` — also serves an
  alternative home screen shape the web player requests and, in one capture,
  abandons.
- `GET /service/api/v1/tvguide/user/data?...` — per-user guide overlay state
  (which airings are recorded or favourited), which would save the per-card
  flag lookup for a whole guide page at once.
- `GET /search/api/tivo/v1/search/screen` and `.../trendingSearches` — the
  search landing screen, for an empty search box.
- `POST /search/api/tivo/v1/send/user/recordings` — the web player posts the
  user's recordings to the search service; purpose unestablished.
