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

Continue Watching cards carry a `seek` marker whose value is progress **as a
fraction of the running time** (`"0.01795995379283789"`), alongside a
`lastUpdatedTime`. It is the only progress the service sends, and it needs the
card's `duration` to become a position. Values outside 0..1 are worth
rejecting rather than trusting.

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

Not established: whether `epg/play/<id>` is answered this way for a programme
still on the air. The capture that proves the VOD shape was of one that had
already finished. The addon offers the choice and lets the service's own
refusal speak if there is one.

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

## Favourites

```
GET /service/api/auth/user/favourite/item?path=<path>&action=1   (add)
GET /service/api/auth/user/favourite/item?path=<path>&action=0   (remove)
→ {"response": {"message": "Added to My Stuff"}, "status": true}
```

A GET that changes state, which is the service's choice. Captured against both
kinds of path a client would send — a guide airing's `epg/play/<id>` and a
show's own `series/shows/<id>` — and both answer identically, so the path is
simply whatever the item is.

Whether something is already a favourite is on the card itself, as
`pageAttributes.isFavourite`, a **string** `"true"`/`"false"` like every other
attribute; a title's own page says it in `pageButtons.isFavourite` instead. So
a client can show only the verb that applies rather than both.

The service calls this My Stuff in its confirmations and "Favorite" on its
button, for the same feature.

## Other endpoints seen but not used

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
