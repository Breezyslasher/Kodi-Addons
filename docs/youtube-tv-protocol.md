# YouTube TV (tv.youtube.com) — protocol notes for a possible Kodi addon

Findings from four browser HAR captures of a signed-in YouTube TV session
(Firefox 154 / Linux, web client `WEB_UNPLUGGED`, August 2026). All identifying
values below — account IP, `sig`/`spc` signatures, visitor id, cookies, license
blobs — are redacted or truncated. Nothing here is secret: it is the shape of
the requests, which is what an addon needs.

Written to answer one question: can Kodi play YouTube TV through
InputStream Adaptive? Short answer: probably yes, and much more cleanly than
Apple TV+, but one thing is still unproven. See "The open question" below.

## The cookie path is gone

The addon signed in with a cookie jar exported from a browser for most of this
document's life, and that is what every capture here was taken from. It is no
longer how the addon works, and the notes below still describe it because a
capture of WEB_UNPLUGGED is what the protocol was read out of.

What made the removal possible was not the credential but the delivery. A
token session is never offered a `dashManifestUrl` -- eight request shapes,
five sending less than the browser and three sending more, all answered
`dash=False` -- so until SABR played, and played in HD, dropping cookies meant
dropping playback. Both are now measured on `TVHTML5_UNPLUGGED`:

    licensed up to 2160p according to the licence: AUDIO, HD, SD, UHD1
    sabr bridge: asking for 1080p, offering [146]
    sabr bridge: opened session s1788028710 as TVHTML5_UNPLUGGED
    sabr bridge: the server chose video 146, audio 150

What went with the jar:

* `lib/signin.py`, the LAN page that took a pasted `Cookie:` header, and the
  cookies.txt import beside it;
* SAPISIDHASH request signing, the `Cookie` header on every InnerTube call,
  and the write-back that absorbed Google's constant re-issues;
* `session_probe`, which existed to answer "were the cookies the problem?" --
  a question a bearer token cannot pose, since it cannot be alive in a browser
  and refused here at the same time;
* the DASH path entire: the licence proxy's `/manifest` handler, and with it
  most of `lib/manifest.py` -- the timescale repair that stopped ISA dividing
  by zero, the SegmentList rebuild, the proof-of-origin injection into every
  BaseURL, and `set_key_ids`. The bridge writes its own manifest, so there is
  nothing to repair;
* `lib/probes.py`, whose three questions ("does the licence exchange work on a
  bearer token? is `serverAbrStreamingUrl` there? is a SABR POST served?") are
  all answered yes by an addon that plays.

What it costs: the device-code flow needs the client ID and secret of a Google
API project, which the cookie route did not. A one-off setup instead of a
recurring one.

### And one thing it took with it: the player js

The removal broke playback on a fresh profile, and the log said so exactly:

    sabr bridge: no player js, n cannot be solved: the page does not name a player js
    no SABR session could be opened for this title

`refresh_bootstrap` reads the running player's identity -- clientVersion, the
signature timestamp, and the url of `base.js` -- off `tv.youtube.com/`. With a
jar that page is the signed-in app and carries a ytcfg with all three. Signed
out it is the `/welcome/` marketing page, which carries no ytcfg at all: a fact
already recorded here, in a comment on the `session_probe` that was deleted in
the same change. Without the player there is no `n`, and without `n` every
media url is a 403, so the bridge refuses to open a session at all.

Browsing, the licence and the sign-in were unaffected -- 148 channels on a
token minted from scratch. It is only the player lookup that needed the page.

The fetch of `base.js` itself never needed a credential: that request has only
ever carried a User-Agent, and it returns 200 and 2.8 MB. So only *discovering*
the player id was lost, and the fix sweeps a list of pages for one that names a
player, logging per candidate which does:

    bootstrap: https://tv.youtube.com/ -> HTTP 200, 41231 bytes, names a player js: no

Only a tv.youtube.com page is allowed to supply the Unplugged clientVersion,
visitorData and rollout token; www.youtube.com may supply the player url alone,
since `/s/player/<id>/` is the same tree on both hosts and the file is fetched
from our own origin either way. A player kept in the profile by a previous run
stands in when no page names one, which does nothing on a fresh profile -- the
case that broke -- and keeps a box that has played before from stopping dead.

## Client identity

Every call to the private API carries:

| Header | Value |
| --- | --- |
| `X-YouTube-Client-Name` | `41` (= `WEB_UNPLUGGED`) |
| `X-YouTube-Client-Version` | `1.20260825.04.00` |
| `X-Goog-Visitor-Id` | opaque, from the page bootstrap |
| `X-Goog-AuthUser` | `0` |
| `X-Origin` | `https://tv.youtube.com` |
| `Authorization` | `SAPISIDHASH <ts>_<sha1>` (plus 1P/3P variants) |
| `Cookie` | full Google jar (`SAPISID`, `__Secure-3PAPISID`, `SID`, …) |

`clientName: 41` is the whole trick — the same InnerTube endpoints under
`tv.youtube.com` return the subscriber's live lineup only for this client.

The JSON body repeats the client in `context.client`, and adds two
YouTube-TV-only blocks:

```json
"unpluggedAppInfo":      { "filterModeType": "UNPLUGGED_FILTER_MODE_TYPE_NONE" },
"unpluggedLocationInfo": { "clientPermissionState": 2, "timezone": "America/New_York" }
```

Location matters: the lineup is market-dependent (this capture resolved to
Pittsburgh locals — KDKA-TV etc.), so an addon inherits whatever market the
account's location grants.

## Auth

No OAuth, no device-code flow, no public API. Auth is the browser cookie jar
plus a `SAPISIDHASH` Authorization header computed as:

```
SHA1(f"{timestamp} {SAPISID} https://tv.youtube.com")
```

sent as `SAPISIDHASH {timestamp}_{hash}`. This is the same scheme yt-dlp uses
for authenticated YouTube requests. Practical consequence for an addon: users
import cookies from a browser. Scripted Google password login is not viable.

## Recording a show

`start_dvr` and `stop_dvr` take the show's id twice -- plainly as `id`, and
inside a small protobuf as `1 { 1: 1, 2: "<show id>" }`. `api.dvr_params`
rebuilds the 2026-08-27 capture's params byte for byte. The field-1 varint is
1 in both the start and the stop request of the one capture there is, so it is
sent as 1; what it means is not established, and it is *not* the on/off switch
-- the endpoint's own name is that.

Both answer with `actions` and `responseContext` and nothing else -- the
Kodi log of 2026-08-29 23:07, a start and a stop of the same show. So the 200
that `call` insists on is not by itself the report: `actions` is where the
client is told what to show, and a refusal arrives the same way, as a 200
with a different sentence in it. `epg.action_text` reads that message out and
it is what the notification says; when none reads, the addon falls back to
its own wording and logs the renderers that did come back.

YouTube TV records a **series**, not an airing, so the id is always a show's.
Every guide airing names its show in `entitiesDvrStatus`:

```json
"entitiesDvrStatus": [{ "entityId": "UCFlNIjsY5_u1foX03MEHiBA" }]
```

Despite the name it carries no state -- only the id -- so it cannot say
whether a show is already recording. That is why both "record" and "stop
recording" are offered rather than the one that applies: choosing between them
would be a guess, and a wrong guess silently cancels a recording.

It is the better of the two sources. On the 843 airings that also carry a
side-sheet command the two agree **every time, none differ**, and it covers
the 143 the side sheet does not -- the ones on the air, whose watchEndpoint
carries no show id anywhere. All 989 airings name their show.

## What YouTube TV's own settings offer, and why most are not worth adding

From `account/get_setting` (2 MB), the categories are Billing, Family sharing,
4K, Downloads, Streaming limits, Ratings filter, Area, Privacy, Nielsen TV
rating measurement, Dark theme and Promo codes. Two were asked about
specifically:

**Ratings filter** is a `settingSingleOptionMenuRenderer` whose options write
through `setClientSettingEndpoint` with `clientSettingEnum:
UNPLUGGED_FILTER_MODE_MENU` and an int value. Its own summary says: *"This
setting only applies to this device."* So it is not an account setting the
add-on could read and honour -- it is per-client, and a client that wants it
must enforce it itself.

That is where it stops, because **nothing in any response carries a structured
rating**. Searched across the guide, the Library and Home: no `rating`-shaped
key at all, and the only rating text is free-form inside a subtitle, as in
`"2016 • PG-13"` or `"S27 E101 • Family Feud • KDKA+ • TV-PG"`. A filter built
on parsing that would silently fail to hide anything it could not parse, which
is the worst property a parental control can have. Not implemented, and this
is the reason.

**Area** is read-only from here. `unpluggedCurrentLocationSettingItemRenderer`
reports `"Pittsburgh Area - 16066"`, but changing it runs
`unpluggedRequestTwofactorLocationCommand` and
`unpluggedGetTwofactorLocationCommand` -- a two-factor check that asks the user
to scan a QR code or visit `tv.youtube.com/verify` **on a mobile device**. A
Kodi add-on cannot complete that flow. The current area could be displayed;
it cannot be set.

**Language** (`UNPLUGGED_I18N_LANGUAGE`) is likewise a client setting, and
Kodi's own language already governs the add-on.

The rest -- Billing, Family sharing, 4K, Downloads, Streaming limits, Privacy,
Nielsen, Dark theme, Promo codes -- are account or billing pages the web UI
owns, or app chrome Kodi has its own version of.

### Why the channel order can be read but not written

The Channel order setting picks one of the five orderings, which is a **read**.
Writing one -- `update_live_guide_order`, or `update_station_visibility` to
hide a channel -- is a different matter: both params embed the account's
market as `508`, `160662`, `US` plus `unplugged-P1`.

`primaryPackageId: "unplugged-P1"` does come back from `account/get_setting`.
**`508` and `160662` appear in no captured response at all** -- only inside
these opaque params. So they cannot be sourced for an arbitrary account, and
hard-coding one account's market would break every other. Not implemented, and
this is what would have to be found first.

The custom order itself is still reachable: arrange it on tv.youtube.com and
choose "Custom" in the setting.

(One detail from the 21:17 capture: `update_live_guide_order`'s leading field
changed from 0 to 1 between the two captures -- `CAAS…` became `CAES…` -- so
that varint is the order being selected, not a constant.)

## What has been seen, and what is implemented

Taken by scanning every `/youtubei/v1/` request across all twenty-three
tv.youtube.com captures in this project, rather than from memory. Call counts
are how often each appeared.

### Feeds

Five exist across all of them, so **no tab is missing**:

| Feed | Requests | Status |
| --- | --- | --- |
| `FEunplugged_epg` | 22 | Live channels, Guide, IPTV |
| `FEunplugged_overlays` | 7 | **not implemented** — and there is nothing in it: it answers 786 bytes of `twoColumnBrowseResultsRenderer` holding one empty `tabRenderer`, a promo slot this account has nothing in |
| `FEunplugged_home` | 4 | Home |
| `FEunplugged_browse` | 2 | Networks |
| `FEunplugged_chips` | 2 | The categories, and their genres |
| `FEunplugged_library` | 2 | Library |

### Endpoints

| Endpoint | Calls | Status |
| --- | --- | --- |
| `browse` | 99 | implemented |
| `log_event` | 50 | telemetry, deliberately not sent |
| `search` | 20 | implemented |
| `suggest` | 33 | implemented, and useless here: a browser gets entity suggestions naming the kind, this client gets ten plain query strings |
| `tenx_player` | 25 | not implemented, but no longer unknown: `{"channelIds": [...]}` in, and a `tenxStreams` list back, one `templatedUrl` per channel — itag 133, `source=yt_tv_broadcast`, `xtags=tenx=1`, with `&sq=` to fill in and a `refreshIntervalSeconds` of 120. The guide's live preview mosaic, and nothing else |
| `att/get` | 6 | not needed — the add-on mints its own proof-of-origin |
| `next` | 5 | **not implemented.** `{"videoId": ..., "params": ..., "unpluggedWatchNextOptions": null}` — the watch-next panel. No response body captured |
| `player` | 5 | implemented |
| `player/get_drm_license` | 5 | implemented, through the licence proxy |
| `update_live_guide_order` | 3 | **not implemented.** Writes a custom channel order: `params` plus a `liveGuideItems` list of `{externalId, settingsGroup}` |
| `account/set_setting` | 2 | not implemented |
| `update_station_visibility` | 2 | **not implemented.** Hides or shows one station: params decode to the channel id, a 0/1 flag, and the account's market |
| `check_client_freshness` | 2 | not implemented — a client-version check |
| `start_dvr` | 1 | implemented — "record this show" |
| `stop_dvr` | 1 | implemented — "stop recording this show" |
| `player/ad_break` | 1 | not implemented; body not captured |
| `account/get_setting` | 1 | not implemented |

`player/heartbeat` appears in **none** of the surviving captures, though the
add-on implements it and this file cites a 2026-08-28 03:10 capture for its
shape. That HAR is not among the twenty that remain, so the shape recorded
below cannot currently be re-checked against a file -- it does run correctly
in Kodi logs.

### The gap worth closing

Nothing on the list is now a *feature* the add-on lacks. `start_dvr` /
`stop_dvr` was the last one and is implemented; the Browse tab was the last
feed not read and is implemented.

Everything still on the not-implemented list is either telemetry, an
attestation the add-on already satisfies another way, a write to account
settings the web UI owns, or an endpoint whose response was never captured.

## Endpoints

### Home and Library — `POST .../browse` with a `continuation`

Neither is asked for by `browseId`. The web client wraps the browse id in a
continuation token and sends *that*, and a token is what the 2026-08-29
captures show going out, so a token is what the addon sends. Both are the same
two-field protobuf — field 80226972 wrapping field 2, the id — which is why the
id is legible in the base64:

| Page    | Token                                    | Decodes to                          |
| ------- | ---------------------------------------- | ----------------------------------- |
| Home    | `4qmFsgIUEhBGRXVucGx1Z2dlZF9ob21lGgA%3D` | `\xe2\xa9\x85\xb2\x02\x14\x12\x10FEunplugged_home\x1a\x00` |
| Library | `4qmFsgIVEhNGRXVucGx1Z2dlZF9saWJyYXJ5`   | `\xe2\xa9\x85\xb2\x02\x15\x12\x13FEunplugged_library` |

The trailing `%3D` on the Home token is percent-encoded base64 padding, sent
verbatim. The token is opaque; decoding it to re-encode it is a way to break it.

The **Live** tab is not a third page: it is `FEunplugged_epg`, the same guide
the addon already fetches. The 19:20 capture shows the tab issuing exactly the
guide request and then paginating it — `maxAiringsPerStation: 24`,
`initialEpgFetchDurationMs` about six hours — so nothing new was needed for it.
Its one extra control is a two-entry dropdown, "Default" and "Custom", which
selects the account's own channel ordering.

#### Home is a page of rows, four at a time

`FEunplugged_home` answers with a `sectionListContinuation` holding four
`unpluggedHomeShelfRenderer` rows — "Top picks for you", "Resume watching",
"Shows", "Add to membership" — and hangs the other twenty behind **one token on
the section list itself**. Following it gives 22 named rows and 606 items in
the 19:16 capture.

That token is the trap. The *first* `nextContinuationData` in the response
belongs to the first shelf, not to the page, so a tree-first search for a
continuation fetches more of "Top picks for you" while believing it fetched the
next twenty rows. The page's own token is the `nextContinuationData` inside
`sectionListContinuation.continuations`, sitting third behind a
`timedContinuationData` (a refresh timer, `timeoutMs` about 813 s) and a
`reloadContinuationData` (the page again) — neither of which is a next page.
`epg.page_continuation` reads only that list; `epg.continuation_token` keeps its
old tree-first behaviour for the guide.

Home names its rows `unpluggedHomeShelfRenderer` / `primaryText`, where the
Library says `shelfRenderer` / `title`. Same thing, twice named, so
`epg.page_shelves` accepts either. Each row carries its own token for more of
itself; the three that had one in the capture were byte-different from each
other, so they really are per-row.

#### Library is one collection, sliced six ways

The Library page carries three plain shelves — "New in your library", "Most
watched", "Scheduled recordings" — and then, inside an
`unpluggedContentDetailsRenderer`, a single `unpluggedSelectableSectionRenderer`
that is **not** shaped like a show page's.

A show page pairs `selectors[i]` with `contents[i]`: ten seasons, ten shelves.
The Library instead carries a *filter* dropdown and one *sort* dropdown per
filter, and `contents` is the cross product flattened row by row:

| Filter    | Sorts                                                          | Cells |
| --------- | -------------------------------------------------------------- | ----- |
| All       | Recent, A to Z, Z to A, Most popular                            | 0–3   |
| Shows     | Recently recorded, A to Z, Z to A, Trending, Most popular, Top rated | 4–9 |
| Movies    | A to Z                                                          | 10    |
| Sports    | Recently recorded, A to Z, Z to A, Most popular                 | 11–14 |
| Events    | A to Z                                                          | 15    |
| Purchased | Recently purchased, A to Z, Z to A                              | 16–18 |

4+6+1+4+1+3 = 19, and `contents` holds exactly 19 — which is what makes the
row-major reading a measurement rather than a guess. `epg.library_filters`
checks that sum on every response and declines the whole grid, with a log line,
if the three lists ever disagree; pairing a name with somebody else's row is
worse than showing nothing.

Only the sort YouTube TV has *selected* arrives with its items inline. Every
other cell holds a `nextContinuationData` and nothing else. A filter the account
has nothing under comes back as an `unpluggedEmptyStateRenderer` ("No movies in
your library") — no items, no token — and is dropped, so an empty tab never
becomes an empty folder. In the capture, Movies and Events were empty and the
two purchased films sat under Purchased.

Note that `section_continuations`, which the show pages use, is *wrong* here and
would pair "Shows" with All's second sort. That is why the Library has a reader
of its own.

#### The TV client answers the Library in its own container

Measured on a real account, 2026-08-29 19:33, as `TVHTML5_UNPLUGGED`:

    library: 0 row(s) and 0 filter(s) -- nothing
    library: no filters recognised; listing the page flat

One item reached the listing. Home, asked as the same client minutes earlier,
came back in exactly the shape the web capture showed — 22 rows of
`unpluggedHomeShelfRenderer`, including three the web capture did not have
("Watch in multiview", "Horror movies", "Networks", the last with 256 items) —
so the client identity is fine and it is the Library page specifically that
differs. The dump answered it. The TV client sends a different container **and** a
different selector:

| | Web (`WEB_UNPLUGGED`) | TV (`TVHTML5_UNPLUGGED`) |
| --- | --- | --- |
| Container | `sectionListContinuation`, a list of rows | `unpluggedLibraryContinuation`, one renderer under `content` |
| Page-level shelves | New in your library, Most watched, Scheduled recordings | none — the whole page is the section |
| Selector | `unpluggedFilterSortSelectorRenderer`: 6 filters × their sorts | `unpluggedHorizontalChipListRenderer`: 9 chips |
| `contents` | 19 cells, the cross product flattened row-major | 9 cells, one per chip |

So on the TV client the pairing is positional — chip *i* names cell *i*, no
cross product to unpick — and the cells are the same
`unpluggedSelectableSectionContentsRenderer` → `shelfRenderer` → `gridRenderer`
as the web client's. `_filter_cells` picks the pairing from the selector it
finds and checks the count either way, returning nothing rather than naming a
tab with somebody else's row.

Asking as `WEB_UNPLUGGED` is not a way round this, and was tried: the bearer is
rejected for it outright.

    browse -> HTTP 400 as WEB_UNPLUGGED v1.20260826.04.00:
      INVALID_ARGUMENT: Request contains an invalid argument.

Two things written while the shape was unknown have been kept, because the next
page to differ will not announce itself either:

* `epg.any_rows` finds named rows **by shape** — any renderer carrying a title
  and two or more things `parse_items` can reach — with no container name
  needed. Only the innermost such renderer counts, tracked with an ancestor
  stack, because a page is itself a titled renderer holding every row. It found
  nothing on the TV Library, since the chips carry the names and the shelves
  under them are untitled, but on the web captures it recovers the same rows
  the named readers do.
* An unreadable page is written to `library-shape.json` (or `home-shape.json`,
  or `guide-shape.json`) in the add-on's profile directory, minus
  `responseContext`, and `epg.describe` logs the renderer names and the lists
  they sit in. That log — nine chips against nine
  `unpluggedSelectableSectionContentsRenderer`, under
  `unpluggedLibraryContinuation` — is what settled the shape above, in one
  round trip and without a capture.

#### Nine named tabs holding nothing

31.10 paired the chips correctly and every tab still came back empty:

    library: 0 row(s) and 9 filter(s) -- New for you (0), Recently recorded (0),
      Most watched (0), Scheduled (1), Series (0), Sports (0), All (0),
      Purchased (0), Expired (0)
    New for you: page 1 added 0 item(s)

The shape dump had already counted 19 `unpluggedBrowseItemRenderer` and 7
`unpluggedGridVideoRenderer` in that same response, so the tiles are there and
`parse_items` cannot place them. Scheduled found exactly 1 of its 7 — the one
tile whose videoId hides in a popup dialog, which is read specially. That is
the signature of a tile whose endpoint is filed under a key `_endpoint_id`
does not check: the web client uses `navigationEndpoint`, and the TV client
evidently does not.

`_endpoint_id` now tries a short list of carriers, still one level deep:
`navigationEndpoint`, `onSelectCommand`, `tapCommand`,
`entityPageNavigationEndpoint`. Checked against every capture — Library, Home
both pages, the three Rick and Morty responses — the item and unplayable
counts are identical to before, so the widening costs nothing on known data.

`command` is deliberately **not** on that list. It is the key an
`unpluggedMenuItemRenderer` keeps its watchEndpoint under, so accepting it
would list the two buttons of a "Join live / Start from beginning" dialog as
two rows of their own.

That was not it either. The sampler, once it was capable of firing, named the
tile exactly:

    library: unpluggedBrowseItemRenderer x19 carries [contentType,
      navigationEndpoint, primaryText, secondaryContainer, style,
      tertiaryText, thumbnail, trackingParams]

It has a `primaryText`, a `thumbnail` and a `navigationEndpoint` — everything a
tile needs, under the keys the addon already reads — and was dropped nineteen
times anyway. So what sits *inside* that endpoint is called neither
`watchEndpoint` nor `browseEndpoint`.

Rather than guess at a third name, `_endpoint_id` now falls back to accepting
**any** endpoint under a carrier that names the id it is looking for. A
`navigationEndpoint` has exactly one destination, so an endpoint under it
holding a `videoId` *is* the video and one holding a `browseId` *is* the page,
whatever Google has called it this year. It looks one level down only, so it
cannot reach into an `unpluggedPopupEndpoint`'s dialog — still read by
`_popup_video_id`, which picks the right one of its two buttons — or into a
menu. And it stays silent on endpoints that name no id at all, so the ten buy
prompts below are still dropped. All six captures parse identically.

The chips' own tokens are a dead end, incidentally: following one answers with
the chip list again and no contents.

    New for you: page 1 added 0 item(s)
    New for you: unpluggedSelectableSectionRenderer x1 carries [selectors, ...]
    New for you: unpluggedHorizontalChipListRenderer x1 carries [trackingParams]

The items were never behind the token — they are inline in each cell's
`gridRenderer`, which the sampler also confirmed (`gridRenderer x9 carries
[continuations, items, trackingParams]`).

#### The Library's tiles do not navigate — they open a side sheet, with the id inside

Accepting any endpoint that names an id was not it either. With the sampler
reporting one level further in:

    library: unpluggedBrowseItemRenderer x19 carries [contentType,
      navigationEndpoint, primaryText, ...];
      navigationEndpoint -> [clickTrackingParams, unpluggedGetSidesheetCommand]

`unpluggedGetSidesheetCommand` — and note it ends in *Command*, not *Endpoint*,
so the fallback could not have matched it even had it held an id. The TV
Library's tiles do not navigate anywhere: selecting one opens a side sheet, a
detail panel, which is consistent with the `unpluggedSidesheetRenderer`,
`unpluggedSidesheetTextHeaderRenderer` and `unpluggedSidesheetContentRenderer`
the first shape dump counted in the same response.

The id is inside the command, and no fetch is needed. `params` is base64 of a
small protobuf:

```
":\x1c\n\x18UCmXMw6OyWJH1O6cA7JZS9Fg\x10\x01@\x01P\x01`\x01"
```

— an outer length-delimited field wrapping field 1, the browse id. The outer
field number is what the tile *is*: 3 for a movie, 4 for a show, 5 for an
event, 7 for a sports team, matching the `requestType`
(`UNPLUGGED_SIDESHEET_REQUEST_TYPE_SHOW` and friends). So `sidesheet_id` walks
the token by shape rather than by field number, taking the first nested string
that looks like an id, with a small protobuf reader that stops rather than
guesses when an offset stops making sense.

The check that this is right: eight titles appear in both the web and the TV
capture, and on seven of them the id read out of the TV token is **the same id**
the web client puts in a plain `browseEndpoint`. The eighth is the scheduled
game, where the TV client's `EVENT` token names `UCzqrIe11dHk2TcbCDfLg7mg`
while the web client navigates to `SEEV_g_11z7gny2t0` — two different entities
for the same fixture. That token also carries an eleven-character string after
the entity id, the length of a video id. Which of the two the TV client's own
side sheet opens is not established here; the first is taken, because that is
the one the other seven verify.

With it, the TV Library reads in full: nine tabs, 26 tiles, nothing counted
unplayable.

    New for you 2, Recently recorded 2, Most watched 2, Scheduled 7,
    Series 4, Sports 1, All 5, Purchased 2, Expired 1

Whether `onSelectCommand` is actually used by the TV client remains
unverified.
`epg.unreadable_sample` therefore logs the keys carried by renderers that look
like tiles and name nowhere to go, whenever a page answers and holds nothing
readable — which names the key to read next off one log line.

Run against the Rick and Morty season shelf, it settled an older question:

    unpluggedCompactVideoRenderer carries [badge, clientStateSyncData,
      description, ..., navigationEndpoint, primaryText, ...]

Those ten episodes do have a `navigationEndpoint` — holding
`unpluggedInitiateInlinePurchaseCommand`. They are not episodes the account
lacks rights to; they are **buy prompts**. Dropping them is correct, and the
count of ten this file has quoted throughout is accurate.

### A later page of the guide names no channel

Reading the plain `videoId` field took page one from 144 airings to 953. The
pages after it were still being thrown away entirely, and the reason is that
they describe no channel:

| | page 1 | page 2 |
| --- | --- | --- |
| container | `contents` → `epgRowRenderer` | `epgPaginationRenderer` → `contents` |
| rows | 148 | 148, same order |
| `epgStationRenderer` | 148 | **none** |
| channel named by | the station renderer | `stationId` on the row itself |
| airings | 953 | 748 |

`parse_epg` required the station renderer, so page two parsed as zero stations
and its 748 airings were lost. Reading the row's own `stationId` and folding
the result in with `merge_airings` takes the same lineup to **1555 airings, a
median of 10 per channel and up to 22**.

The merge deduplicates by video id, and drops any channel page one never
described -- without its station renderer it has no name and no logo, so it
would list as a channel called `UCtfoIx_MZ0h5bX3i9YsF5xw`.

`lib/iptv.py` used to ask for a single twenty-four hour window. It now asks for
four pages of six hours, which is the shape the web client's live tab actually
uses and the only one any capture measures. Against the capture it hands Kodi
1555 programmes across 148 channels where it previously handed 148.

### Pagination brings more hours, not more channels

Worth stating plainly because it decides what the loop stops on. Across the
eleven pages of the 2026-08-29 20:22 capture, **every page carried the same
148 rows in the same order**. What grows is the schedule:

| pages | airings | median per channel | max |
| --- | --- | --- | --- |
| 1 | 962 | 6 | — |
| 2 | 1576 | 10 | 23 |
| 4 | 2708 | 17 | 38 |
| 8 | 4657 | 29 | 67 |
| 11 | 6306 | 40 | 90 |

After merging all eleven: **zero duplicate airings, zero channels out of time
order, zero missing names or logos**, and the guide reaches 2026-09-05 — the
full week `maxDurationMs` allows. The eleventh page still handed back a token,
so the server does not appear to run out.

So "until no more channels come back" is not a stop condition: no page ever
adds a channel. The loop stops when a page adds no *new airing*, when the token
repeats, or at a page cap, and the cap is purely how far ahead the guide
reaches. IPTV Manager fetches eight (about 29 airings per channel); the addon's
own per-channel view fetches four.

### The channel order is a token sent beside the browseId

The live tab's order dropdown offers five, and picking one re-requests
`FEunplugged_epg` with a `continuation` **alongside** the browseId rather than
instead of it:

```
80226972 {
  2: "FEunplugged_epg"
  3: "8gMEIgIwAQ%3D%3D"          # itself 62 { 4 { 6: <order> } }
  22 { 1 { 1: maxAiringsPerStation, 3: maxDurationMs,
           4: initialEpgFetchStartTimeMs,
           5: initialEpgFetchDurationMs, 6: paginationDurationMs } }
}
```

Field 22 repeats, field for field, the `epgOptions` the request body already
carries -- so the token is **rebuilt** from those values rather than copied; a
stored copy would ask for a window from somebody else's session. Only field 6
of the inner selector separates the five:

| Order | field 6 | selector |
| --- | --- | --- |
| Default (locals first) | 1 | `8gMEIgIwAQ%3D%3D` |
| Custom (as arranged on the web) | 2 | `8gMEIgIwAg%3D%3D` |
| Most watched | 3 | `8gMEIgIwAw%3D%3D` |
| A-Z | 4 | `8gMEIgIwBA%3D%3D` |
| Z-A | 5 | `8gMEIgIwBQ%3D%3D` |

`api.epg_order_token` reproduces all five of the capture's own tokens **byte
for byte**, which is what makes it a reconstruction rather than a guess, and
`tools/checks/test_pages.py` pins them so it stays that way. Measured, the
orders really do differ: Default begins KDKA-TV, WTAE 4, NBC 11, FOX 53 while
Most watched begins KDKA+, FOX News, WTAE 4, KDKA-TV.

The setting defaults to sending **no** token, which keeps the previous
behaviour: the account's own last choice on tv.youtube.com decides.

Two neighbouring endpoints were captured and are *not* implemented, since
neither is needed to read a guide: `update_live_guide_order`, which writes a
custom order as a list of `{externalId, settingsGroup}`, and
`update_station_visibility`, which hides one station by id.

### The TV client's guide hides the programme id in a side sheet

The diagnostic answered it in one run, and the answer was the opposite of the
guess it was written to test. The response carries the whole schedule:

| | airings in the response | airings parsed |
| --- | --- | --- |
| Live channels, 2 h | `epgAiringRenderer` x387 | 143 |
| Guide, 6 h | `epgAiringRenderer` x989 | 143 |

989 arrived and 143 were read, so this was never the server sending a
now-and-next grid. It was the reader dropping 846 airings.

Two structural differences from the `WEB_UNPLUGGED` capture the reader was
written against:

* rows arrive under `/contents/epgRenderer/paginationRenderer/epgPaginationRenderer/contents`
  even on the first request, where the web client puts them directly under
  `contents`; each row keeps its station under a `station` key and repeats
  `stationId` beside it;
* every airing carries an `epgInfoPanelRenderer`, which the web capture has
  none of.

And the one that mattered: **only the airing currently on the air carries a
`watchEndpoint`.** The other 846 carry an `unpluggedGetSidesheetCommand` --
the same mechanism the Library's tiles use -- and no `videoId` field at all,
where the web client puts one on every airing.

    143 with a watchEndpoint + 846 with a side sheet = 989, exactly

Every one of those 846 params holds precisely two ids: the show's
twenty-four character one and the programme's eleven-character one.

    UCTy7yMhdCqhduRTvA_Bx9TQ   the show
    _u_J5hBoorE                the programme

Which one is wanted depends on the caller, so neither is taken by position:
`sidesheet_video_id` matches the eleven-character shape for an airing, and
`sidesheet_id` keeps returning the first id for a Library tile, where the show
*is* the destination. Reading it takes the guide from 143 airings to **989**,
a median of 7 per station, 989 distinct video ids, with all 148 stations still
resolving a playable "now" and the 143 on-air markers intact.

The web captures are unaffected: `live_2` still parses 953 airings and
`live_13` still 748.

### A crash during install leaves the add-on unloadable

Recorded because two changes were made at once and the conclusion is
therefore weaker than it looks. Kodi began segfaulting at startup on 21.3
*and* 22.0-BETA1 with 2026.8.31.17 installed:

    /app/bin/kodi: line 217: 7 Segmentation fault (core dumped) ${KODI_BINARY}

No stack trace: gdb is not installed on that box, so all four crash logs end
at the last line written before the process died. The shipped zip was intact,
both XML files parsed, the .po was well-formed and every module compiled --
so nothing static explained it, and a pure-Python add-on segfaulting Kodi is
odd on its face.

What the timeline says: 31.15 ran for sixteen minutes, played a film and
listed the Library, then died at 20:31:01 **during the install of the next
build**, with the repository scan still running. Every start after that
crashed. Deleting the add-on folder and installing cleanly stopped it.

That fits everything, and the settings.xml suspicion below does not fit the
part where 31.15 was already installed and working. The likeliest account is
a half-written add-on folder from the interrupted install, which Kodi loads
before almost anything else.

The settings change was kept regardless, on its own merits: the Channel order
setting was the only list in settings.xml written as `type="string"` with an
empty option value and `allowempty` beside `<options>`, where `max_height`,
`audio_itag` and `sabr_floor` are integers with plain numeric values. It is an
integer now. Whether that ever mattered is **not established** -- the folder
was deleted in the same step.

### The whole chain, once the schedule was readable

Measured 2026-08-29 20:53, and worth writing down because the failure was at a
different layer each time:

| stage | programmes |
| --- | --- |
| the add-on, `iptv manager: offering` | 8050 |
| IPTV Merge, `Wrote … EPG programme entries` | 8050 |
| pvr.iptvsimple, `LoadEpgEntries - Loaded` | 8046 |
| Kodi's guide | still one per channel |

The last row was Kodi's own EPG cache (`Running database version Epg16`), not
anything upstream: `grep -c GetEPGForChannel` over the whole session returns
**0**, so Kodi never asked the client for EPG at all. IPTV Merge's
disable/enable cycle reloads the *add-on*; it does not invalidate Kodi's cache,
which refreshes on Kodi's own schedule. Settings → PVR & Live TV → Guide →
Clear data forces it, and the grid then filled completely.

A tell worth recognising: the channel *list* had already changed to the new
merge's order while the grid had not. A fresh playlist against a stale EPG is
exactly that shape.

### No programme carries a stream url, and the reason is the clock

Kodi draws a marker on any EPG entry it is given a stream url for. `lib/iptv.py`
gave one to every airing carrying a video id, with a comment saying only the
one on now would resolve -- which was honest by accident, because before the
side-sheet id was read the only airings *with* an id were the 143 on the air.

Reading the other 846 gave all 8050 a url and marked a week's schedule
playable. Marking only the `on_air` airing was no better, and the reason is
the clock: the guide is **built when the merge runs and read hours later**. By
then the marker sits on a programme that has ended, its url is dead, and the
programme actually on the air has none.

So no programme carries one. The JSON-EPG spec calls `stream` "the endpoint
that will be called when this program should play... to directly play a
program from the EPG" -- a catch-up facility, which this add-on has not
established it has.

Nothing is lost by leaving it out. Selecting a live programme in Kodi's guide
plays its channel, and the channel url from JSON-STREAMS is
`?action=play_channel&station_id=...`, which looks up what is on **at the
moment it is played** rather than the moment the guide was built. That is the
one thing in this chain that cannot go stale, and it is why the channel url
names a station and never a video -- see `_channels`.

### The PVR guide is IPTV Manager's copy, not this addon's

The Kodi TV section's guide is populated by whatever consumes
`?action=iptv_epg` -- service.iptv.manager directly, or plugin.program.iptv.merge,
which speaks the same protocol and merges several sources into the M3U and
XMLTV that pvr.iptvsimple reads. Either way the pull happens **on its own
schedule** and the result is cached; the addon cannot trigger a refresh. So a guide fix shows up in the addon's own Guide
folder immediately and in the TV section only after IPTV Manager next runs --
which is why one programme per channel survived the fix that took the guide
from 144 airings to 953. Confirming a guide change means looking at the
addon's own Guide, or forcing the merge/refresh by hand. In the 20:06 log
neither had run: there is no `iptv manager: offering N airing(s)` line
anywhere, which is the addon's own record of having been asked.

`lib/iptv.py` goes through the same `parse_epg`, so it needs no separate fix.

### The guide census

`route_channels` skips any station whose current airing has no video id, and
`route_guide` skips any station with no airings — both silently. A lineup
showing eighty channels where the web app shows a hundred and fifty logged
exactly the same as one that worked. `_guide_census` now counts the three
faults apart on every guide fetch:

* no station parsed at all — the guide's own shape has changed;
* a station with no airings — Guide drops it;
* a station whose airing has no video id — Live channels drops it.

Against the 19:20 web capture: 148 stations, 144 airings, 144 playable, and 4
dropped for having no airings (three NBCSN Extra feeds and Cartoon Network).
The response is kept only when nothing is playable or more than half the
lineup is dropped or unnamed; it is a couple of megabytes.

### The guide carries 953 airings and the addon read 144 of them

The Kodi PVR guide showed what was on each channel and nothing after it, on
every channel. That was not pagination: page one of the 2026-08-29 guide holds
**953 `epgAiringRenderer`** and `parse_epg` returned 144 — one per station.

The two sets differ in exactly one field:

| | airings with a watchEndpoint | airings without |
| --- | --- | --- |
| Count | 144 (one per station) | 809 |
| `navigationEndpoint` | `watchEndpoint` | `browseEndpoint` to the show page |
| `videoId` field | present | **present** |
| Extra | `spoilerModeBadge`, `spoilerModeEntities` | — |

Every airing carries its id in a plain `videoId` field. Only the one actually
on the air also gets a `watchEndpoint`, and `parse_airing` read the endpoint
alone, so every future programme was dropped for having "no video id".

Reading the field as well takes the guide from 144 airings to 953 — a median of
6 per station and up to 15 — and every station now has a schedule instead of a
single row.

That endpoint is not redundant, though: it is the guide's own statement of
which airing is on now. `Airing.on_air` records it, and `Station.now` asks it
*before* comparing timestamps. Run against the capture from a later day, the
clock picked a different airing on 113 of the 148 stations while the marker was
right on all of them. It also keeps Live channels exactly as it was: before
this change a station had one airing, the marked one, and `now` was always it;
falling through to `airings[0]` would now play whatever was on that morning.
Measured against the capture, Live channels resolves to the same video on 144
of 148 stations, and the four that differ are the four that previously had no
airings at all — they gain a schedule rather than lose one.

### Station names live on the logo, not in a name field

Of the 148 stations in that capture, **7 carry `name` and 7 carry `callSign`.**
Every one of the other 141 is named solely by the accessibility label on its
logo:

```json
"icon": { "thumbnails": [ { "url": "//yt3.ggpht.com/…", "width": 500 } ],
          "accessibility": { "accessibilityData": { "label": "YouTube TV Zen" } } }
```

So the key that logo sits under is the difference between a lineup and 141 rows
called `UC5M1ACzZ9iIL42YKinxZrFQ`, and a client that files it anywhere else
loses the name and the picture *together* — which is what "mostly missing
station names and no logos" looks like from the sofa.

`parse_epg` still prefers `icon` where it exists (four stations have a
`secondaryIcon` nearer 400px and would otherwise swap to it; with the
preference, all 148 logos are byte-identical to before) and falls back to
searching the whole station renderer. `thumbnail` and `accessibility_label`
both already search at any depth, so the fallback assumes no key name at all.
Renaming `icon` to `stationIcon` across the capture now costs neither a name
nor a logo.

The census counts nameless and logoless stations apart from dropped ones — a
station listed as its own id is not dropped, so the drop counts would call that
lineup healthy — and when many are nameless it logs the field names the
stations actually carry:

    guide: 141 station(s) with no name and 148 with no logo
    guide: station fields (first 40) -- navigationEndpoint x40, trackingParams x40,
      stationId x40, isDiscreteStation x40, tenxId x39, name x4, callSign x4

#### A scheduled recording that is on the air has no watchEndpoint

Six of the seven tiles in "Scheduled recordings" carry a plain `browseEndpoint`
to the show page. The seventh — the one then airing — carries no watch endpoint
at all:

```json
"navigationEndpoint": { "unpluggedPopupEndpoint": { "popupRenderer": {
  "unpluggedSelectionMenuDialogRenderer": { "items": [
    { "unpluggedMenuItemRenderer": { "primaryText": { "runs": [{ "text": "Join live" }] },
      "command": { "watchEndpoint": { "videoId": "…", "params": "0gEEEgIwAQ%3D%3D" } } } },
    { "unpluggedMenuItemRenderer": { "primaryText": { "runs": [{ "text": "Start from beginning" }] },
      "command": { "watchEndpoint": { "videoId": "…", "params": "0gEKEgIwARjwyM3UBg%3D%3D" } } } } ] } } } }
```

Both menu items name the *same* videoId and differ only in `params`, which
`route_play` does not carry, so either will do. The addon dropped this tile
entirely before: it has a title and a thumbnail and, as far as `_endpoint_id`
could see, nowhere to go — it was the one row `unplayable_count` reported.

The popup's presence is YouTube TV's own statement that the title is playable
now; every not-yet-started recording had a `browseEndpoint` instead. So no clock
arithmetic is needed on this side, and none is done.

#### Two airings of one show are two rows

`parse_items` deduplicated on destination alone, which collapsed the 22:00 and
22:30 recordings of Phineas and Ferb into one row — both point at the same show
page — and showed 4 of the 7 scheduled recordings. The key is now
`(destination, startTimeSeconds)`. Nothing without a start time is affected,
which is every show page, so the Rick and Morty counts are unchanged.

Because two such rows carry the same title, the listing puts the airing time in
front of it: `19:30  Family Feud`, `Sun 19:00  Kitchen Nightmares`. Those match
the badges in the capture's own tiles.

### Guide — `POST https://tv.youtube.com/youtubei/v1/browse?alt=json`

```json
{
  "browseId": "FEunplugged_epg",
  "unpluggedBrowseOptions": {
    "epgOptions": {
      "maxAiringsPerStation": 11,
      "initialEpgFetchStartTimeMs": "...",
      "initialEpgFetchDurationMs": 9618000,
      "paginationDurationMs": 5514000,
      "maxDurationMs": "604800000"
    }
  }
}
```

Returns ~1.9 MB of EPG: 150 `epgStationRenderer` + 550 `epgAiringRenderer`.
Paginates through a `continuation` token, up to 7 days (`maxDurationMs`).

A station:

```json
{
  "name":     { "runs": [ { "text": "KDKA-TV" } ] },
  "callSign": { "runs": [ { "text": "KDKA-TV" } ] },
  "icon":     { "thumbnails": [ { "url": "//yt3.ggpht.com/...", "width": 400 } ] },
  "stationId": "UCbmNmhvPNwkyvLXZVNspDXA",
  "tenxId":    "UCbmNmhvPNwkyvLXZVNspDXA",
  "isDiscreteStation": false
}
```

An airing — note it carries the live `videoId`, which is the key to playback:

```json
{
  "beginTimeMs": "1787864400000",
  "endTimeMs":   "1787868000000",
  "title":         { "runs": [ { "text": "KDKA-TV News at Five" } ] },
  "quaternaryText":{ "runs": [ { "text": "Midday news update." } ] },
  "thumbnail":     { "thumbnails": [ { "url": "//yt3.ggpht.com/...", "width": 1920 } ] },
  "navigationEndpoint": {
    "watchEndpoint": { "videoId": "z0sfuXTVx8g", "params": "0gEEEgIwAQ%3D%3D" }
  }
}
```

This maps onto a Kodi PVR channel list + EPG almost one-to-one: station name,
call sign, logo, and a per-airing title/description/start/stop.

### Guide preview mosaic — `POST /youtubei/v1/tenx_player?alt=json`

Takes `{"channelIds": [...]}`, returns per-channel `tenxStreamerUrl.templatedUrl`
— itag 133 (240p), `ctier=A`, `xtags=tenx=1`, **unencrypted** fMP4, with a
`&sq=` segment-number placeholder and a ~2 minute `urlExpirationUtcMillis`.

These are the little tiles that animate in the guide. Not useful for real
playback, but worth knowing: they are DRM-free and trivially fetchable, so a
"preview" feature is cheap if it's ever wanted.

### Watch metadata — `POST /youtubei/v1/next?alt=json`

~60 KB of watch-page furniture. Not required for playback.

### Player — `POST /youtubei/v1/player?prettyPrint=false`

```json
{
  "videoId": "z0sfuXTVx8g",
  "context": { "client": { "clientName": "WEB_UNPLUGGED", "clientVersion": "1.20260825.04.00", ... } },
  "playbackContext": {
    "contentPlaybackContext": {
      "html5Preference": "HTML5_PREF_WANTS",
      "signatureTimestamp": 20689,
      "referer": "https://tv.youtube.com/watch/<videoId>?..."
    },
    "devicePlaybackCapabilities": { "supportsVp9Encoding": true, "supportXhr": true }
  },
  "cpn": "<16-char random client playback nonce>",
  "racyCheckOk": true,
  "captionParams": {}
}
```

**There is no `poToken` and no `serviceIntegrityDimensions` in this request.**
The PO-token enforcement that has been breaking third-party YouTube clients did
not apply to `WEB_UNPLUGGED` in this capture. That is the single biggest
positive finding, and also the most likely thing to change without notice.

Response `streamingData` contains:

| Key | Meaning |
| --- | --- |
| `dashManifestUrl` | `https://manifest.googlevideo.com/api/manifest-yttv/dash/...` — a real MPD |
| `licenseInfos` | `[{drmFamily: WIDEVINE, url}, {drmFamily: PLAYREADY, url}]` |
| `drmParams` | opaque base64, echoed back on every license call |
| `serverAbrStreamingUrl` | the SABR endpoint the web player actually used |
| `adaptiveFormats` | 21 entries, 144p → 1080p |
| `initialAuthorizedDrmTrackTypes` | `["DRM_TRACK_TYPE_AUDIO", "DRM_TRACK_TYPE_SD"]` |
| `expiresInSeconds` | `21540` (~6 h) |

The manifest URL's own query string is informative:
`as/fmp4_audio_cenc,fmp4_sd_hd_cenc` (CENC-encrypted fMP4, SD **and** HD),
`ctier/UL`, `tvn/CBS`, `tvc/<stationId>`.

Formats offered (all video/audio CENC-encrypted; text tracks are not):

```
146  avc1.4d4028  1080p   WIDEVINE, PLAYREADY
275  vp9          1080p   WIDEVINE
359  vp9          1080p   WIDEVINE
145  avc1.4d401f   720p   WIDEVINE, PLAYREADY
274  vp9           720p   WIDEVINE
144/317           480p    …down to 161/279 at 144p
149  mp4a.40.2    audio   WIDEVINE, PLAYREADY
381  ac-3         audio   WIDEVINE
386/387/406  text/mp4     (no DRM)
```

H.264 is available at every rung alongside VP9, which matters because it is the
safe codec on low-power Kodi hardware.

### Search is rows, and the films are on the second page

`{"query": "blues"}` answers with a `sectionListRenderer` of named shelves,
one per kind, and **defers the rest of them**:

| Page | Rows |
| --- | --- |
| 1 | Shows (5), Sports (8), On now & upcoming (8) |
| 2 | From your library (1), On demand (8), **Movies (7)** |

The Blues Brothers is in that Movies row, `contentType: "MOVIE"`. Read one
page deep, a search of 14 results answers `SHOW x11, SPORTS_TEAM x3` and no
film at all -- which reads as a client that cannot tell a film from a show,
and is really a client that stopped reading too early.

The page token is spent at **`search`**, not at `browse` where every other
continuation in this addon goes:

```json
POST /youtubei/v1/search?alt=json    {"continuation": "EnESBWJsdWVzGmhjallLTkVOblVWRkZRbWhCUTJk"}
```

**The TV client answers a search differently, and mistypes films in it.**
The same query as `TVHTML5_UNPLUGGED` gives four rows across three pages
against the browser's six, and the types do not agree:

| | Browser (client 41) | Kodi (TVHTML5_UNPLUGGED) |
| --- | --- | --- |
| rows | Shows, Sports, On now & upcoming, From your library, On demand, Movies | Top picks, On now & upcoming, From your library, On demand |
| The Blues Brothers | `MOVIE`, in Movies *and* in On now & upcoming | **`SHOW`**, in Top picks |
| Blues Brothers 2000 | -- | **`SHOW`** |
| On demand tiles | `MOVIE x5, SHOW x3` | no `contentType` at all |
| a Movies row | 7 films | none |

Two separate things, from the log of 2026-08-30 01:29:

* `unpluggedBrowseItemRenderer` carries `contentType` here as it does
  everywhere, and **the value is wrong for films**: The Blues Brothers and
  Blues Brothers 2000 both come back `SHOW`. So nothing in a search result
  says "film", and a film found by search is a folder.
* `unpluggedGridVideoRenderer` carries no `contentType` at all -- its keys
  are `[badge, description, endTimeSeconds, entityPageNavigationEndpoint,
  onMultiviewPress, primaryText, secondaryText, startTimeSeconds, style,
  thumbnail, trackingParams]`. Those tiles are *airings*, not titles, and
  untyped is right for them. They navigate by
  `entityPageNavigationEndpoint`, so unlike the browser's, they do not
  carry a videoId either.

The film is not lost: opening it lands on its own page, whose
`unpluggedContentDetailsHeaderRenderer` says `contentType: "MOVIE"` and
whose Watch now tab holds the film. That is one click more than a film
found in a category, which types itself correctly and plays on selection.

The addon does **not** regroup these rows: rows it invents are rows the
service does not have. The row's own continuation is worth nothing either
-- spending it added 0 items, twice.

`SPORTS_TEAM` is a fourth `contentType`, alongside MOVIE, SHOW and EVENT.

### Search — `POST /youtubei/v1/search?alt=json`

**A real search sends `params`.** Scanning the request bodies of every
capture, this is the one endpoint the addon was calling with a shape no
client uses: seven captured searches send `{"query": ..., "params": ...}`
(one adds `suggestStats`), and none sends the query alone.

Two forms appear, and only one of them is ours to send:

| params | Decoded | Seen for |
| --- | --- | --- |
| `6gMOCgASABoAIgAqADIAQgA%3D` | field 61 wrapping seven empty sub-fields — "no filters" | "blues" *and* "St. Louis Blues", unchanged between them |
| `cgIgAw%3D%3D`, `cgIgAg%3D%3D` | field 14 wrapping one varint, 3 then 2 | one query each, after picking a suggestion |

The second tracks which suggestion was clicked and changes per query, so
the addon sends the first, copied verbatim.

**It is not what decides how a film is typed.** Sending it changed nothing:
`Top picks (SHOW x11, SPORTS_TEAM x3)` before and after, character for
character (2026-08-30 01:36). The call was wrong and is now right, and the
mistyping is something else.

What is left is the client identity, and that is not testable from here: a
bearer token is refused as `WEB_UNPLUGGED` with HTTP 400, so this addon
cannot ask the way the browser asks. The evidence stands as:

* the **same account, same query, same request shape**, typed `MOVIE` as
  client 41 and `SHOW` as client 65;
* the **same client 65** types a film `MOVIE` in a *category* -- not
  inferred from a capture but from the addon itself, which only offers to
  play what it reads as a film, and which played John Wick 3, John Wick 4
  and Airplane! straight from the Movies category;
* so it is the search endpoint's answer to this client, and nothing the
  addon sends changes it.

A search tile also carries no menu -- `unpluggedBrowseItemRenderer x14
carries [contentType, navigationEndpoint, primaryText, style, thumbnail,
trackingParams]` -- so the DVR toast that names the kind elsewhere is not
there to fall back on either. Nothing *in a search result* says "film".

**`suggest` says, in words — but only to a browser.** Asked by
`TVHTML5_UNPLUGGED` it answers with ten `searchSuggestionRenderer`s
carrying `[navigationEndpoint, suggestion, trackingParams]` — plain query
text — and **no `entitySuggestionRenderer` at all**, so there is nothing in
it to type anything by. The addon does not call it for this. What follows
is the browser's answer, kept because the shape is real and the reader for
it costs nothing to keep.

 Asked `{"input": "blues"}` it answers with
`entitySuggestionRenderer`s carrying a browse id and the kind spelled out
beside it, in one 20 KB call for the whole query:

```
St. Louis Blues     Team    UC0tM0q-x5pc3lvV96zv1Wjw
The Blues Brothers  Movie   UC8todI5O2ZpZ5FhZ6aVMmKw
Blues on Beale      Movie   UCYrKyBwbQW9PMoGAoC0HiqQ   (badge "$")
Air Disasters       Show    UChwvXpAFBPfOhiayQfNhwTQ
Airplane!           Movie   UCboHgTIRnHZyX7GiIHGDxKw
```

The word is in the suggestion's `secondaryContainer`, in an
`unpluggedTextRenderer`; the `unpluggedTextBadgeRenderer` next to it holds
`"$"`, which says a title must be bought rather than what it is. It covers
the handful of entities matching the typed prefix -- six for these queries
-- not everything a search returns.

Its `searchNavigationEndpoint` also carries `searchEndpoint.params:
"cgIgAQ%3D%3D"`, which is where the per-query `params` form above comes
from: it is the endpoint attached to clicking a suggestion.

**So the page one level down answers for all of it.** A result's own
`unpluggedContentDetailsHeaderRenderer` says `contentType: "MOVIE"`, and it
is the same header that makes a film play from a category. So the addon
asks it: the pages of the results it cannot type are fetched together, the
way a show page's deferred shelves are, and what they answer is kept --
a title does not change what it is, and the same searches come back.
Nothing playable is asked about, nothing already known is asked again, and
one search fetches at most 24 pages however many folders it returns.

```json
{ "query": "rick", "params": "6gMOCgASABoAIgAqADIAQgA%3D" }
```

With `POST /youtubei/v1/suggest?alt=json` behind the search box for
autocomplete. Both are cheap and behave like ordinary InnerTube search.

### A show page — `POST /youtubei/v1/browse?alt=json`

```json
{ "browseId": "UCZYw_wEStht0rr7CDQIC2Hw" }
```

The body is the browse id and a context, nothing else: the browser sends no
`params` here, and the capture of 2026-08-27 23:38 confirms it field for
field.

**The page does not carry the episodes.** Browsing Rick and Morty answers
with 71 KB holding exactly two playable items — the two newest episodes —
and eleven `shelfRenderer` blocks of which ten are empty. Those ten are the
seasons. They sit under one `unpluggedSelectableSectionRenderer` whose
`selectors` hold a `dropdownRenderer` naming them ("Season 1" … "Season 9",
"Extras") and whose `contents` hold one shelf each, carrying nothing but a
`nextContinuationData.continuation`. The two lists are parallel, not nested:
`selectors[i]` names `contents[i]`, and the only link between a label and its
shelf is the index.

Each token is spent as an ordinary continuation — `{"context": …,
"continuation": "4qmFsgJO…"}` to the same endpoint — and answers with that
season. Two further shelves (`4qmFsgIq…`) hold Cast & Crew and related
shows rather than episodes.

Episodes the account cannot play are still listed, with no `watchEndpoint`.
On this account Season 9 returns ten episodes of which seven are playable,
Season 8 ten of which one is, Season 7 ten of which one is, and Season 6 none
— nine playable in total, which is what the page would show if all ten
shelves were asked for. Listing only what the page itself carries showed two.

The counts above are the addon's own parser run over the browser's recorded
responses, not a reading of the UI.

### The Browse tab — `POST /youtubei/v1/browse?alt=json`

```json
{ "browseId": "FEunplugged_browse" }
```

Asked for by browseId, unlike Home and the Library, which the web client
sends as continuation tokens. 712 KB, in the `shelfRenderer` container the
web readers already know, holding exactly two rows:

| Row | Holds | Renderer |
| --- | --- | --- |
| Browse | 5 category chips | `unpluggedIntentChipRenderer` in an `unpluggedHorizontalChipListRenderer` |
| Networks | 256 networks | `unpluggedGridChannelRenderer` in a `horizontalListRenderer` |

256 networks against 147 stations in the same account's guide, because a
network here is a brand and not a channel slot.

**The chips are all one browseId.** Sports, Shows, Movies, News and Family
each navigate to `FEunplugged_chips`, and what tells them apart is `params`:

```json
{"browseEndpoint": {"browseId": "FEunplugged_chips",
                    "params": "8gMGKgQI75wB"}}
```

That is why `epg.Item` carries `params` and why `parse_items` keys its
dedupe on the pair. Keyed on the browseId alone, five categories collapsed
into one row -- the same trap as two airings of one show pointing at one
show page, in a place where nothing has a start time to break the tie.
`params` reaches the request unchanged, trailing `%3D` included, for the
reason `HOME_CONTINUATION` does: the token is opaque, and decoding it to
re-encode it is a way to break it.

### A network page is tabs, and only one of them arrives

A network tile carries a plain `browseEndpoint` with no params. What it opens
is **not** a show page: it is a `singleColumnBrowseResultsRenderer` of tabs,
and only the selected one ships with anything in it.

| Network | Tabs |
| --- | --- |
| ABC | LIVE (26 items), SERIES, DRAMA, COMEDY, NEWS, REALITY, DAYTIME, LATE NIGHT |
| AMC | LIVE (11), SERIES, MOVIES, ORIGINALS, THE WALKING DEAD |
| Adult Swim | LIVE (1), UPCOMING (399 inline), ADULT SWIM, SERIES |

Every tab but the selected one -- and Adult Swim's UPCOMING, which arrives
inline -- carries a content of exactly this and nothing else:

```json
{"sectionListRenderer": {"continuations": [
  {"nextContinuationData": {"continuation": "4qmFsgI4EhhVQ1Jfdkt3T09j…"}}]}}
```

So a network listed flat showed what was on now and nothing else: the rest
of the page was seven tokens nobody spent. Spent, AMC's tabs answer with 26
series, 60 films across two pages, 23 originals and 7 Walking Dead titles.

Two things to get wrong. A tab titles itself with a **plain string**
(`"title": "SERIES"`), where every shelf on every other page uses `runs`.
And the selected tab's own shelves carry continuations that fetch more of
one shelf -- so the tab's token must be read from the tab's own section
list, the same care `page_continuation` takes on Home.

**The TV client defers a tab differently.** Adult Swim answered a browser
with four tabs (LIVE, UPCOMING, ADULT SWIM, SERIES) and answered Kodi with
two, titled `Live` and `Upcoming` rather than in caps -- the 2026-08-29
23:46 log. Logging the keys of every tab on the page settled why:

```
Live carries [content, selected, title, trackingParams];
Upcoming carries [content, title, trackingParams];
Adult Swim carries [content, title, trackingParams];
Series carries [content, title, trackingParams]
```

All four are sent, and all four carry a `content`. The two not on screen
hold neither an item nor a `nextContinuationData`: the TV client defers them
in the shape most of InnerTube has moved to, a `continuationItemRenderer`
holding a `continuationCommand.token`, where the web client uses the older
`sectionListRenderer.continuations`.

**It took a third reading, because the diagnostic was ambiguous.** The tab
that reads and the tab that does not both hold exactly one string, both
under a key called `continuation`, both inside one `sectionListRenderer` --
so the log line for each was identical character for character:

```
Adult Swim carries [content, title, trackingParams];
  content [sectionListRenderer] holding sectionListRenderer x1;
  strings under continuation
```

The wrapper between is the entire difference and it was the one thing not
being printed. Printed, it is `reloadContinuationData`:

```
Series carries [content, title, trackingParams]; content [sectionListRenderer]
  holding sectionListRenderer x1; strings under
  sectionListRenderer/continuations/reloadContinuationData/continuation
```

The web client uses `nextContinuationData` for the same tab. Both name the
tab's content; `page_continuation` accepts only the first, deliberately,
because on a page of shelves a reloadContinuationData is the page again.
With that read, AMC gives 5 tabs, ABC 8 and Animal Planet 4, all of them. The reader now takes any continuation inside a tab that holds nothing
else -- a tab with no items has no shelves to confuse it -- skipping only a
`timedContinuationData`, which is a refresh timer and asks for the same
empty tab back. And `_inside` prints the path to a string rather than its
last key, so two shapes can no longer log the same line.

**And that was not it either.** On 2026-08-30 00:19 the same page still read
as two tabs with the continuationCommand shape handled. So the two unread
tabs hold neither shape, and the tab's own keys cannot say what they do
hold: every tab on the page carries `[content, title, trackingParams]`, the
ones that read and the ones that do not alike. `tab_shapes` now reports what
is *inside* an unread tab's content, and the page is kept as
`network-tabs.json` in the addon's data folder. Two readings off a log line
have each been wrong about a different thing.

So the rule is by what the tab holds rather than by which key it holds it
under. A tab with items reads its token from its own section list only --
its shelves have tokens that fetch more of one shelf. A tab with **no**
items has no shelves either, so any continuation anywhere in it is that
tab's own, in either shape; and a tab arriving with no `content` at all may
name a page rather than a continuation, which is opened as that page.

### A category is rows under chips — `browseId: FEunplugged_chips`

```json
{ "browseId": "FEunplugged_chips", "params": "8gMFKgMIoFQ%3D" }
```

Movies answers with named rows -- "Picked for you", "On now & upcoming",
"Thriller movies" -- and defers more behind a page-level continuation, the
way Home does: two requests gave eight rows.

**It is not the same page twice.** Two requests for Movies twenty seconds
apart came back with "Drama movies" and then "Drama thriller movies", and
with 18 and then 17 fantasy comedies (2026-08-29 23:49). Only "Picked for
you", "On now & upcoming" and "All" appear every time. So a row cannot be
reopened by asking for the page again and matching its name: the row drawn a
moment ago may not be in the page that comes back. Each row folder carries
its own continuation token, and that is what is spent when the name is not
found.

Above them sits a shelf with **no title** holding fifteen
`unpluggedChipRenderer` genres (Action, Comedy, Crime, Drama, Fantasy,
History, Horror, Musical, Romance, Science fiction, Thriller, Adventure,
Romantic comedy, Marvel Cinematic Universe, Magic). `page_shelves` drops an
untitled shelf, correctly, so `page_chips` reads that one separately.

### A tile says what it is, and carries its own DVR endpoints

Every tile on a category page is an `unpluggedBrowseItemRenderer` carrying

```json
{"contentType": "MOVIE",
 "primaryText":   {"runs": [{"text": "The Accountant"}]},
 "secondaryText": {"runs": [{"text": "2016 • R"}]},
 "navigationEndpoint": {"browseEndpoint": {"browseId": "UCUlwmd1Fk7Tr2XLsLe3rYcQ"}}}
```

`contentType` is **MOVIE**, **SHOW** or **EVENT** -- 1048, 303 and 1 across
the captures -- and it is the only thing that separates a film, which has
one thing to play, from a series, which is a folder of episodes. Nothing
else does: both are a browseId beginning UC, and **neither tile carries a
video id at all**. A film's stream is named only on its own page, which is
why playing one from a listing means fetching that page first.

The tile's `menu.menuRenderer` is where YouTube TV keeps the rest, and it is
worth reading for two reasons. It offers "Go to \<title\>" -- the page is a
menu entry on the service's own tile, not the tile's action. And it carries
the DVR endpoints outright:

```json
"toggleMenuServiceItemRenderer": {
  "defaultText":  {"runs": [{"text": "Add to library"}]},
  "defaultServiceEndpoint": {"startDvrEndpoint": {
      "startDvrParams": "ChwIARIYVUNVbHdtZDFGazdUcjJYTHNMZTNyWWNR",
      "id": "UCUlwmd1Fk7Tr2XLsLe3rYcQ"}},
  "toggledText":  {"runs": [{"text": "Added to library"}]},
  "toggledServiceEndpoint": {"stopDvrEndpoint": {"stopDvrParams": "…", "id": "…"}},
  "defaultToastText": {"runs": [{"text":
      "Movie added to your library. We'll record it as it becomes available."}]}}
```

Those params are **byte for byte what `api.dvr_params` rebuilds** from the
id alone -- checked, and pinned by `test_pages.py`. A capture taken two days
after the one that builder was written from is the only independent
confirmation of it there is, so it is worth having even though the params
are not read from here.

### A title's page is four tabs

A film or show opened from a listing answers with `Watch now`, `About`,
`Lead cast` and `Suggested` -- not the two a network page has. Only the
first and last are somewhere to go:

| Tab | Holds |
| --- | --- |
| Watch now | the film, and any recording of it: John Wick 3 gave the on-demand copy and an AMC airing from four weeks earlier, in that order |
| About | `unpluggedContentDetailsAboutFieldsRenderer` -- a description and `attributes`, no items. **The attributes are the metadata**: an unlabelled line of genres ("Science fiction, Adventure, Action, Fantasy"), then `Released 2016`, `On FX`, `Provider: Disney`, `Directors: Gareth Edwards`. A labelled line marks its label bold; `Released` and `On` are not bold and are read by their leading word. **`TVHTML5_UNPLUGGED` also sends a whole line as one `simpleText`** where the browser always sends `runs` -- John Wick: Chapter 4's About named both paths in one response, `attributes/simpleText` beside `attributes/runs/text` (2026-08-30 02:04). A whole line must be split on its own label (`Provider: Lionsgate`, `Directors: Chad Stahelski`), because a line with no label is how the genres line is recognised, and reading every one-string line as unlabelled lets each overwrite the genres in turn |
| Lead cast | `unpluggedPersonRenderer` x22, each with `name`, the `role` they played, and a searchEndpoint for that name |
| Suggested | 39 titles for Harry Potter and the Order of the Phoenix, 26 for John Wick Chapter 2 |

So a title's page always reads as two tabs and drops two, by design, and
that is not a shape worth keeping a copy of.

**A show's page is not a film's.** Three of them (Rick and Morty,
Superjail!, Tuca & Bertie) agree:

| | Film | Show |
| --- | --- | --- |
| tabs | Watch now, About, Lead cast, Suggested | RECENT or ABOUT, EPISODES, LEAD CAST, SUGGESTED |
| header `secondaryText` | `PG-13 • 2016` | `2013 – Present`, `2008 – 2014` |
| genres | inline in About | inline in About |
| studio | `Production Companies: Lucasfilm, …` and `Provider: Disney` | `On: Adult Swim, Cartoon Network, HBO Max` |
| director | `Directors: Gareth Edwards` | none — a show has no director line |

**And a show's About can be empty.** Rick and Morty carries genres and a
network; "Bathroom Makeover" carries only `On=CHARGE!, Comet TV, FOX 53, …`
and "Paid Programming" carries nothing whatever (2026-08-30 02:55). So a
title with no genres, no year and no cast is usually the service having
none rather than a field going unread -- which is what the "its about
carried [...]" line in the log is for, and what it settled here.
| **cast** | **inline**, 16–28 `unpluggedPersonRenderer` | **deferred**, the LEAD CAST tab is empty and holds 7 behind its token |

So a show costs one request more than a film for the same detail, which is
why its cast is fetched when the show is opened and not for every title in
a listing. Its header gives a span rather than a year, and the year it
started is still a year. Its network is what a film calls a provider.

**A film nobody has bought has no Watch now items either**, so that tab is
dropped as well and Suggested becomes the first one. Which is why the tab
holding the title itself has to be found by what is in it rather than by
position: The Blues Brothers answered with 26 suggestions and nothing to
play, and reading tabs[0] as the one that plays hid the only tab there was.

Its **header** is a different renderer from a channel's, and that is what
tells the two pages apart:

| Page | Header |
| --- | --- |
| A film or show | `unpluggedContentDetailsHeaderRenderer` |
| A channel | `unpluggedNetworkPromoHeaderRenderer` |

Which matters because only one of them has tabs that are a menu. A
channel's Live, Series and Movies are different parts of a channel; a
title's Watch now and Suggested are the thing and then some notes about it.

The title header also carries what the tiles do not:

```json
{"contentType": "MOVIE",
 "title": {"simpleText": "Rogue One: A Star Wars Story"},
 "secondaryText": {"simpleText": "PG-13 • 2016"},
 "subscribeButton": {"dvrButtonRenderer": {
     "dvrOn": false, "dvrOnAndRecording": false,
     "serviceEndpoints": [{"startDvrEndpoint": {…}}, {"stopDvrEndpoint": {…}}]}}}
```

**`dvrOn` is the library state** -- the thing no tile carries, and the
reason a tile has to offer both "record" and "stop recording". On a page it
is known, so only the action that applies is offered. The Watch now tile
adds a `duration` ("2:13:57"), and the About tab a real synopsis; a tile
carries neither.

`contentType` is not the same everywhere. Search answers with the field on
the same renderer -- `unpluggedBrowseItemRenderer x14 carries [contentType,
navigationEndpoint, primaryText, style, thumbnail, trackingParams]` -- and
not one of those 14 read as a film against the literal "MOVIE" a category
tile uses. So the kind is matched on the word rather than the whole string,
and a value carrying no word this knows is handed back whole so a log names
it. The DVR toast, written per kind, stands in where the field is absent
altogether.

What is *not* there is the state. `isToggled` appears on none of the 2007
toggle renderers across every capture, so a *tile* cannot say whether a show
is already being recorded, and both "record" and "stop recording" are
offered there rather than the one that applies. The title's own page can
say -- see `dvrOn` above -- and on a page only the applicable one is
offered.

### Categories, continued

Each genre is `FEunplugged_chips` again with longer params --
`8gMJKgcIoFQIn5YB` for Action against the category's own `8gMFKgMIoFQ%3D` --
and picking a second narrows further: `8gMQKg4IoFQIn5YBCL98COCWcQ%3D%3D`
carries several. Fifteen chips behind one browseId is the params trap again,
at a second depth, and this is where it was captured rather than reasoned
about.

### Heartbeat — `POST /youtubei/v1/player/heartbeat?alt=json`

Required during live playback. The response carries `pollDelayMs: 30000`, so
the client re-posts every 30 s:

```json
{
  "videoId": "z0sfuXTVx8g",
  "cpn": "<same cpn>",
  "sequenceNumber": 15,
  "heartbeatToken": "...",
  "heartbeatServerData": "<opaque, echoed from the previous response>",
  "heartbeatRequestParams": {
    "heartbeatChecks": ["HEARTBEAT_CHECK_TYPE_LIVE_STREAM_STATUS",
                        "HEARTBEAT_CHECK_TYPE_YPC"]
  },
  "playbackState": { "playbackPosition": { "utcTimeMillis": "..." } }
}
```

It returns a fresh `playabilityStatus` and `heartbeatServerData` to carry into
the next call. An addon has to run this loop for the life of a live channel —
`HEARTBEAT_CHECK_TYPE_YPC` is the subscription/entitlement check, so dropping
it should be assumed to end the stream.

### On-demand

The same `player` endpoint serves VOD. A capture of an Adult Swim title
(`isLiveContent: false`, `lengthSeconds: 1443`) returned the identical
structure — `dashManifestUrl`, `licenseInfos`, `drmParams`, CENC formats,
`initialAuthorizedDrmTrackTypes: [AUDIO, SD]` — differing only in the URL
parameters:

| | Live | On-demand |
| --- | --- | --- |
| manifest path | `/api/manifest-yttv/dash/` | `/api/manifest/dash/` |
| `source` | `yt_tv_broadcast` | `youtube` |
| `ctier` | `UL` | `UD` |

VOD responses additionally carry `captions`, `adPlacements` and `adSlots`, so
ad breaks are signalled in-band and would need handling. Live and on-demand can
share one playback path.

### Widevine license — `POST /youtubei/v1/player/get_drm_license?alt=json`

Not a raw Widevine license server. The challenge is wrapped in JSON:

```json
{
  "context":          { ...same client block... },
  "drmSystem":        "DRM_SYSTEM_WIDEVINE",
  "videoId":          "z0sfuXTVx8g",
  "cpn":              "<same cpn as the player call>",
  "sessionId":        "ad_KTS0r2b-d1UkU",
  "licenseRequest":   "<base64 raw Widevine challenge>",
  "drmParams":        "<echoed from streamingData>",
  "isKeyRotated":     true,
  "cryptoPeriodIndex": 20693,
  "drmVideoFeature":  "DRM_VIDEO_FEATURE_SDR"
}
```

Response:

```json
{
  "status":  "LICENSE_STATUS_OK",
  "license": "<base64 raw Widevine license>",
  "authorizedFormats": [
    { "trackType": "DRM_TRACK_TYPE_UHD1", "keyId": "..." },
    { "trackType": "DRM_TRACK_TYPE_HD",   "keyId": "..." },
    { "trackType": "DRM_TRACK_TYPE_SD",   "keyId": "..." }
  ],
  "canRenew": false,
  "sabrLicenseConstraint": ""
}
```

Two things follow.

**`isKeyRotated: true` with a `cryptoPeriodIndex`.** Live channels rotate keys,
and each new crypto period needs a fresh license request with an incremented
index. InputStream Adaptive's static `license_key` config cannot compute a
changing index, so a **local license proxy** is required — the same shape as
`plugin.video.appletv/lib/license_proxy.py`. ISA points at `127.0.0.1:<port>`,
the proxy unwraps ISA's raw challenge, wraps it in the JSON above with the
current period index and auth headers, and hands back the decoded `license`
bytes. That module is a genuine head start; this is the second time the same
pattern has been needed.

**The license returned HD and UHD1 key ids to a Linux/Firefox client**, i.e. to
Widevine **L3** — even though `initialAuthorizedDrmTrackTypes` advertised only
audio + SD. The QoE beacons confirm the session settled on `fmt=275` (1080p
VP9) with `afmt=149`. If that holds under ISA, this addon would not be stuck at
the standard-definition ceiling that limits the Apple TV+ addon. Worth
re-testing rather than assuming: `initialAuthorizedDrmTrackTypes` may be
enforced elsewhere.

## The answer: DASH segments are not served, and SABR is the only path

The question this document originally left open -- whether `dashManifestUrl`
serves -- has been settled against us by running the addon on real hardware.
Recording it in full so nobody repeats the work.

**The manifest serves. Its segments do not.** InputStream Adaptive fetches and
parses the MPD without complaint ("Manifest successfully parsed ... Type: live"
and "Type: VOD"), and every media request built from it comes back
`HTTP 403, Server: gvs 1.0, Content-Length: 0`.

The addon probed this directly rather than inferring it from ISA's behaviour,
fetching segments itself with ISA's own headers. Everything below returned 403,
on live and on-demand alike:

| tried | result |
| --- | --- |
| oldest, middle and newest segment in the list | 403 |
| with the session cookies | 403 |
| with `n` removed (unsigned, so removable) | 403 |
| with a `cpn` added | 403 |
| path-style vs query-style spelling | 403 |
| ranged and unranged | 403 |
| a proof-of-origin token from *another* video's session | 403 (see below) |

**The proof-of-origin row does not prove what it looks like it proves.** The
token was lifted from a browser capture taken while the browser was playing
something else, and yt-dlp's PO Token Guide states that these tokens are bound
to the video id -- "a new token is required for each video". So that test showed
only that a token minted for one video does not authorise another, which is the
expected result and tells us nothing about whether a correctly bound token
would work.

Testing it properly requires a token minted for the exact video being played:
play that title in the browser, capture, and take the `pot` from a request
carrying the same content id. Until that is done, PO token enforcement remains
a live hypothesis for the 403 rather than a ruled-out one.

The rest of the table stands: position, cookies, `n`, `cpn`, spelling and
ranging make no difference.

Meanwhile, across every capture taken -- guide browsing, a live channel, search,
an on-demand title, and two captures of on-demand actually playing -- **the only
`videoplayback` GET that has ever returned 200 is `itag=133 ctier=A`**: the
guide's 240p, unencrypted preview tiles. Every request for entitled media, live
and on-demand, is a `POST` carrying `ump=1`, `srfvp=1`, `rn`, `rbuf`, `range`
and a proof-of-origin token. That is SABR.

What is certain is that the web player never uses the DASH GET path for
entitled media, and that nothing we can vary about the request short of a
correctly bound PO token has made it serve. Whether the path is closed
outright, or merely gated behind a token we have not yet supplied correctly, is
not settled.

### Every delivery path, and why each is closed

Four ways to reach the media were tried. All are gated behind SABR.

**1. The DASH manifest.** Served and parsed, its segments refused with
`HTTP 403 (Server: gvs 1.0)` -- every position in the list, with and without
cookies, with `n` removed, with a `cpn` added, in path and query spelling,
ranged and unranged, and with a proof-of-origin token injected into all twelve
BaseURLs. Live and on-demand alike.

**2. Other client identities.** WEB_UNPLUGGED is one of six Unplugged clients.
ANDROID_UNPLUGGED, IOS_UNPLUGGED and TVHTML5_UNPLUGGED answer
`HTTP 400: Request contains an invalid argument` even with their own app
User-Agents and platform context fields; TV_UNPLUGGED_ANDROID answers 403 and
TV_UNPLUGGED_CAST 404, on the same cookie jar that serves WEB_UNPLUGGED
without complaint. The 400 body names no field, so what those clients want is
not recoverable from the response.

**3. The signature cipher.** Every format arrives as a `signatureCipher`
(35 of 35 on-demand), never a plain `url` -- the ordinary YouTube mechanism,
where a scrambled `s` is descrambled by a function lifted from the player
JavaScript and written back as `sig`. That is what the regular Kodi YouTube
addon does, and it was the most promising lead precisely because these URLs are
a different family from the manifest's: their `sparams` carry `aitags` and
`bui`, matching the requests the browser is served.

The addon fetched the watch page, found the player script and read all
2,574,392 bytes of it. There is no scrambler in it. `join("")` appears 23
times and `reverse()` twice, and no window anywhere in the file both splits a
string into characters and rejoins it. The two `reverse()` sites are a version
string being split on `"."` and a list of itags being reordered. The
tv.youtube.com player never unscrambles `s`; it passes it into the SABR
request, which is why the bundle carries no descrambling code at all.

**4. Proof-of-origin tokens -- ruled out.** An earlier reading of this was
wrong twice over. Injecting a browser-minted token changed nothing, and that
test looked weak because tokens bind to the video id and the one available had
been minted for another title. But a later capture of the browser playing the
*same* title settles it from the other side: 22 GETs and 2 POSTs, `pot` on none
of them, and a POST returning 15,010,219 bytes with status 200. The web player
fetched the media with no proof-of-origin token at all. It is not the gate, and
obtaining a correctly bound one would prove nothing.

The only `videoplayback` GET that has ever returned 200, across every capture,
is `itag=133 ctier=A` -- the guide's 240p unencrypted preview tiles.

### SABR is not derived, it is handed to us

Worth stating plainly, because it changes what a future attempt would have to
build. `serverAbrStreamingUrl`, which every player response already contains,
is byte for byte the URL the browser POSTs to:

```
player response  id = o-AKi9TYRHMgHBmnID__PMDWRnuVnhDlOzx6TUJotbYGkd  sabr = 1
browser POST     id = o-AKi9TYRHMgHBmnID__PMDWRnuVnhDlOzx6TUJotbYGkd  sabr = 1
```

Same host, same opaque id. That id is per-session and is *not* the content id
(`15b3613898561ecd`) that every DASH URL carries -- they address different
resources, which is why no amount of repairing the DASH URLs could ever have
reached the media.

So the endpoint needs no reverse engineering; we are given it on every play and
have never used it. What is missing is only the conversation: a protobuf request
body describing which formats and byte ranges are wanted, and a UMP response
parser to cut the returned stream into segments. One captured POST returned 15 MB
in a single response, so the chunking is coarse.

That is a smaller and better-defined problem than "implement SABR" suggested
earlier in this document, though still a substantial one: the protobuf schema is
undocumented and yt-dlp's implementation of it is a 227-commit branch that has
not merged.

### The request was not the problem

Worth recording, because it is the obvious suspicion and it has been settled
rather than argued about. Our player request was diffed against the browser's
own, same account and same client, captured minutes apart:

|  | browser | addon |
| --- | --- | --- |
| client | WEB_UNPLUGGED | WEB_UNPLUGGED |
| formats | 35 | 35 |
| plain `url` | 0 | 0 |
| `signatureCipher` | 35 | 35 |
| dash / sabr offered | yes / yes | yes / yes |

Identical. The addon is not served a lesser response for asking differently.
The browser, holding that same ciphered response, then issues SABR POSTs and
four `itag=133 ctier=A` preview-tile GETs, and fetches no DASH segment either.

The diff did find two real defects, both since fixed: `clientVersion` and
`signatureTimestamp` were pinned from a capture and already a release stale
within a day, and eighteen fields the web player sends were missing entirely,
including the top-level `params`. The request now matches field for field, on a
`clientVersion` and `signatureTimestamp` read live from the page -- and the
response and the 403 are both unchanged. Whatever refuses these URLs, it is not
the shape of the request.

### The DRM session is now correct too

The Apple TV addon's playbook (`CLAUDE.md`) describes this failure in its
section 3: a service that sends no KEYID and a PSSH whose key id ISA cannot
read, leaving ISA to open a session with an all-zero KID. That is exactly
`ConvertKidStrToBytes: Cannot convert KID ""`, on every play.

The fix works, and it was a real defect. Key ids are recorded from the licence
response and supplied on the next play through `pre_init_data`
(`{PSSH}|{KID}`), and the second play of a title measurably gets further than
the first:

    Opening stream: 1002 source: 256
    Finding audio codec for: 86018
    CDVDAudioCodecFFmpeg::Open() Successful opened audio decoder aac
    Creating audio thread

None of which had ever happened before -- ISA had never reached a second
stream, let alone opened a decoder. The second play also makes no licence
request, because the pre-initialised session already holds the keys.

So ISA now opens a correct DRM session, resolves both streams, and starts a
decoder. It then asks for the media and is refused, exactly as before, on both
video and audio. Every client-side defect that was found has been fixed, and
the 403 is unmoved by all of them: it is not a consequence of anything the
addon was doing wrong.

### What that means for a Kodi addon

InputStream Adaptive speaks DASH and HLS. It does not speak SABR, which is a
proprietary POST protocol carrying UMP-framed responses. Making this addon play
would mean:

1. implementing a SABR client in Python (yt-dlp needed a dedicated downloader
   and is still chasing changes),
2. minting proof-of-origin tokens, which requires running Google's BotGuard
   JavaScript -- yt-dlp delegates this to an external Node.js helper, and the
   tokens expire within hours,
3. and then remuxing SABR output into something ISA can consume, because ISA
   cannot be pointed at a SABR endpoint.

Each of those is a project. Together they are a moving target maintained
against an actively hostile protocol. That is a considered assessment, not a
refusal: the code up to this boundary is written, tested and working.

### What does work

Everything short of fetching media bytes, verified on real hardware:

* Cookie sign-in with SAPISIDHASH request signing.
* The full 150-station lineup and EPG, parsed with names, logos and schedule.
* Search (20 results for "rick"), browsing a show, and reaching its episodes.
* `player` for both live and on-demand, returning real streaming data.
* The hand-built Widevine PSSH -- YouTube's manifests carry no usable one --
  which ISA accepts.
* **Widevine licence exchange, granted by Google**: `licence granted: 1954
  bytes, 12 formats` for a live channel and `946 bytes, 4 formats` for an
  on-demand episode, covering AUDIO, SD, HD and UHD1.
* The manifest repair that stops ISA dividing by a missing timescale and
  crashing Kodi with SIGFPE.

The addon negotiates the entire YouTube TV protocol correctly, up to and
including being issued decryption keys, and is then refused the encrypted
bytes those keys would decrypt.

## Running the decisive test

`tools/youtube_tv_check_dash.py` performs it end to end: it signs a
`SAPISIDHASH` header from an exported cookie jar, reads the guide, calls
`player` for the first current airing, then fetches the `dashManifestUrl` and
reports whether the result is a DASH MPD whose segments are ordinary
`videoplayback` URLs.

```
python3 tools/youtube_tv_check_dash.py cookies.txt --save-mpd live.mpd
```

Exit status 0 means the addon is worth building. It also doubles as a check of
the auth chain: if `player` returns anything other than `OK`, the cookie or
`SAPISIDHASH` handling is wrong before DRM ever enters the picture.

## The `n` parameter is the gate on media

Every googlevideo URL minted by our session is refused with `HTTP 403`,
`Content-Length: 0`, while the browser is served from the same IP in the same
minute. The refusal is not about us as a client: the browser's own captured
SABR request, POSTed from the Kodi box, returns `HTTP 200, 15010219 bytes`.

Crossing the two requests names the half that is wrong:

| | result |
|---|---|
| their url + their body (the replay) | 200, 15,010,219 bytes |
| their url + our body | 200, 31 bytes |
| our url + their body | 403, empty |
| our url + our body (the probe) | 403, empty |

So the body is accepted and the URL is refused. Diffing the player response's
`serverAbrStreamingUrl` against the URL the browser actually POSTs shows two
kinds of difference:

* four parameters the browser appends itself -- `cpn` (the playback nonce from
  the player call), `cver`, `alr=yes`, and `rn` counting up per request; and
* **`n`, which the browser rewrites**:

```
player response :  UQpyO2dm0XQSunbyNa
browser posted  :  ygW6YjigTA7D-Q
```

Everything else -- `sig`, `lsig`, `sparams`, `spc`, the opaque per-session
`id` -- is byte-identical. `n` is not listed in `sparams`, so changing it does
not invalidate `sig`; the edge checks it separately. The transform is the
`nsig` challenge: a function in the player JS, keyed to that player release,
which yt-dlp solves by interpreting the JavaScript. Adding `cpn`/`cver`/`alr`/
`rn` alone does not lift the 403, which leaves `n` as the remaining
explanation.

The same scrambled `n` appears on the DASH URLs InputStream Adaptive fetches,
which is why that path returns 403 too.

### Confirmed by breaking a URL that works

Rather than infer, damage the captured browser request in one place. Same
length, same alphabet, one character of `n` rotated:

```
sabr replay [verbatim ]: HTTP 200, 15010219 bytes
sabr replay [n altered]: HTTP 403, 0 bytes
sabr replay [n dropped]: HTTP 403, 0 bytes
```

So `n` is the gate, it cannot be omitted, and it has to be computed.

### Computing it

The transform is generated afresh in each player release, so there is no
algorithm to reimplement -- only a language to run. `lib/jsinterp.py` is
yt-dlp's JavaScript interpreter, vendored verbatim (Unlicense, public domain),
with `lib/nsig.py` on top to find the player JS, locate the transform, and run
it.

One caveat, recorded because it predicts how this ends. **yt-dlp no longer uses
that interpreter for YouTube.** As of 2026.8 every n-challenge provider it
ships -- deno, node, bun, quickjs -- shells out to a real JavaScript runtime,
and no pure-Python path remains. That is the project with the most invested in
this problem concluding the interpreter cannot keep up with the obfuscation.
Our case is narrower (one player, one function), so it is worth trying, and
`nsig.solve` falls back to a runtime on PATH if the interpreter fails. The log
says which route worked.

### Why `n` cannot be extracted from this player

The player source settles it. `tv.youtube.com/s/player/06ab6907/player_ias.vflset/en_US/base.js`
is 2,936,988 bytes and contains **none** of the landmarks every published
extraction pattern relies on:

| landmark | occurrences |
|---|---|
| `enhanced_except` | 0 |
| `String.prototype.split.call` | 0 |
| `String.fromCharCode(110)` | 0 |
| `.set("n",` | 0 |
| `.get("n")` | 1 — an HLS helper that rewrites `/n/` path segments |

Not because the transform is absent, but because the player no longer contains
identifiable functions. The URL-parameter class is declared like this:

```js
g.TW = function(R, m){ return rv[Rt[15]](this, 56, 3413, R, m) };
```

`Rt` is a string table:

```
Rt = "indexOf;fromCharCode;length;K;set;push;;X;/videoplayback;=;Untrusted URL;
      V;r;://;reverse;call;scheme;slice;...;split;..."
```

and `rv` is an opcode-dispatched virtual machine with XOR-computed control flow
(`var f = m ^ R; ... switch(L){ case f^4435: ...`). Every method name is reached
by index into `Rt`, so `String.fromCharCode` is `String[Rt[1]]` and `.split` is
`[Rt[24]]`; every function is an opcode rather than a name. The primitives the
signature transform is built from -- `reverse`, `slice`, `split` -- are sitting
in that table.

This is what defeated the four pattern shapes, and it is not fixable by writing
a fifth. It is also why yt-dlp stopped using its own Python interpreter for
YouTube: its current solver parses the player into an AST with `meriyah`,
rewrites it with `astring`, and evaluates the result in deno, node, bun or
quickjs. Solving `n` for this player needs a real JavaScript engine plus a
JavaScript parser -- not a regex and not a small interpreter.

The vendored `jsinterp.py` stays because it is sound and cheap, but on this
player it has nothing to find.

### The client survey

If some other client identity were handed URLs that need no `n`, none of the
above would matter. Asked with a correct per-client context -- own version, and
none of the web player's `rolloutToken`, `configInfo` or `visitorData`:

| client | version | result |
|---|---|---|
| `ANDROID_UNPLUGGED` | 6.36 | 400 INVALID_ARGUMENT |
| `IOS_UNPLUGGED` | 6.36 | 400 INVALID_ARGUMENT |
| `TVHTML5_UNPLUGGED` | 6.36 | 400 INVALID_ARGUMENT |
| `TV_UNPLUGGED_ANDROID` | 1.37 | 403 PERMISSION_DENIED |
| `TV_UNPLUGGED_CAST` | 0.1 | 404 NOT_FOUND |
| `WEB_UNPLUGGED` | 1.20260826.04.00 | 200 -- 35 formats, **all** ciphered |

The 403 is a real refusal: the session probe confirms the same cookies are
served a signed-in page, so that identity is simply not granted this surface.
The three 400s are InnerTube rejecting the request rather than the client, and
it declines to say which argument it dislikes -- `error.details` is absent, not
merely unread. Sending the web player's session state to them was one cause and
is fixed; whatever remains is unnamed.

What that leaves, in order of honesty:

1. **A JavaScript runtime.** Install deno, node, bun or quickjs on the Kodi
   host and drive yt-dlp's solver (`yt.solver.core.js`, which needs `meriyah`
   and `astring`). This is the only route with evidence that it works.
2. **Keep pulling on the three 400s**, with a captured mobile-client request to
   compare against -- the same method that solved the SABR URL. It needs a HAR
   from the YouTube TV Android or iOS app, which is a different capture setup.

What is *not* worth another attempt: a fifth extraction pattern, a sixth cookie
theory, or any change to the DASH URLs. Those are all settled above.

## Playback works

The chain, end to end:

1. `player` for the video, with a `cpn` minted for the session;
2. the PSSH built by hand from `drmParams`, since the manifest declares
   `schemeIdUri="http://youtube.com/drm/2012/10/10"` and carries no key ids;
3. the licence fetched through the local proxy, which records the key id of
   each authorised track on the way past;
4. `n` computed by running the player's own transform in a JavaScript engine;
5. the manifest rewritten with the solved `n` and with a `cenc:default_KID`
   naming the key each track needs.

Two things had to be true at once for step 5, and either alone still fails.
`n` wrong, and every segment is refused with an empty 403. `n` right and the
key unnamed, and the segments arrive, the codecs open, playback starts, and
every sample fails to decrypt -- because YouTube TV grants four keys and the
manifest says nothing about which is which.

### The n transform

Reached through the value handed to `set("n", ...)`, in the `tce` builds only:

```js
a.D&&(eO(a),b=a.j.n||null)&&(b=Yma(b),a.set("n",b))
```

`Yma` in `player_ias_tce`, `Nia` in `player_es6_tce`; the builds the page
points at hide it behind an opcode VM and have no such call at all. It opens
with `if(typeof Xma==="undefined")return a;` -- a sentinel that must be carried
with the function, or it returns its input untouched and silently.

Running it needs a real JavaScript engine. The vendored Python interpreter
stops on an unbraced `if` body and again on `typeof`.


## The license_data property, and why it silenced the audio track

Worth writing down in full, because the property's name suggests it is one more
place to put init data, and it is really a switch that reaches all the way to
which tracks survive.

`build_item` used to set `inputstream.adaptive.license_data` to the PSSH built
from `drmParams`. Three steps follow from that, all read out of ISA's source
rather than inferred:

1. `src/parser/DASHTree.cpp`

        m_isCustomInitPssh = !CSrvBroker::GetKodiProps().GetLicenseData().empty();
        ...
        if (m_isCustomInitPssh || GetProtectionData(adpSet->ProtectionSchemes(),
                                                    repr->ProtectionSchemes(),
                                                    pssh, kid, licenseUrl))
        {
          uint16_t psshSetPos = InsertPsshSet(..., pssh, kid, licenseUrl);

   With the property set the `||` short-circuits, `GetProtectionData` never
   runs, and `pssh`/`kid` reach `InsertPsshSet` empty. Every
   `cenc:default_KID` `manifest.set_key_ids` writes is discarded before it is
   read, and one PSSH set covers every track. That is the
   `ConvertKidStrToBytes: Cannot convert KID ""` line, and
   `Initializing stream with unknown KID!` immediately after it.

2. `src/decrypters/widevine/WVCencSingleSampleDecrypter.cpp`, `GetCapabilities`,
   which `Session.cpp` calls with that empty kid:

        m_fragmentPool[poolId].m_key = keyId.empty() ? m_keys.front().m_keyId : keyId;

   so the capability probe decrypts its test sample with whichever key the
   licence happened to list first, not the track's own.

3. The same function, when that probe fails:

        if (media == DecrypterCapabilites::SSD_MEDIA_VIDEO)
          caps.flags |= (DecrypterCapabilites::SSD_SECURE_PATH |
                         DecrypterCapabilites::SSD_ANNEXB_REQUIRED);
        else
          caps.flags = DecrypterCapabilites::SSD_INVALID;

   and `Session.cpp` answers `SSD_INVALID` with
   `m_currentPeriod->RemovePSSHSet(ses)`.

Video falls back to the secure path and plays. Audio is removed outright.
Video with no sound, which is what on-demand did while the property was set.

So the property is gone. `GetProtectionData` now runs and reads what the proxy
serves: a `<cenc:pssh>` and that Representation's own `cenc:default_KID` on
every Representation, so each track probes with its own key and gets its own
CDM session.

Two things this does not change, and one cost:

* Per-sample key selection was never affected. `CFragmentedSampleReader` takes
  `m_defaultKey` from the track's `tenc` box in the media itself, not from the
  manifest, and passes it to `SetFragmentInfo`. The manifest's key ids matter
  for session setup and the capability probe, not for decrypting a sample.
* The init data ISA opens the session with is the same bytes either way. It
  used to come from the property; it now comes from the `<cenc:pssh>` the proxy
  writes into the manifest.
* The key ids come from a licence, and a licence arrives during playback. The
  first play of a title therefore still has an empty kid and may still lose
  audio; the second play has them, the same one-play learning step the
  resolution ceiling already has. `set_key_ids` declares a Representation even
  when its key id is unknown, so that first play is no worse than before.

The consequence for live/on-demand parity: there is no DRM asymmetry between
them. Both take this path. The only live-specific mechanisms are the `n`
spelling (path, not query -- see above), `cryptoPeriodIndex` on the licence
request, and the crypto-period fields in the PSSH.


## The video keys come back output-restricted, and what follows from it

Measured with Kodi debug logging on, playing an on-demand title on a Linux
desktop with the Widevine L3 CDM (4.10.3050.0). One licence, four keys, and the
CDM reports their statuses individually:

    OnSessionKeysChange: KID 92D444F944355272905C8F0FD78FE8DE, Status: 0
    OnSessionKeysChange: KID 166B5C174CC65406A12921557350A257, Status: 0
    OnSessionKeysChange: KID 4D3521E29EDF52199C85970D6765E334, Status: 1, System code: 5
    OnSessionKeysChange: KID 13720FBBF85052649C8AB16DC3E14852, Status: 1, System code: 5

Status 1 is `kOutputRestricted`. Which key is which follows from the probe
counts, and they match what `set_key_ids` declared for that manifest
(`AUDIO x1, HD x3, SD x6`):

    92d444f9  probed 3x -> "GetCapabilities: Single decrypt possible"      AUDIO
    4d3521e2  probed 6x -> "Single decrypt failed, secure path only"       SD
    13720fbb  probed 3x -> "Single decrypt failed, secure path only"       HD

So audio can be decrypted to the clear and decoded by Kodi; **both video tiers
cannot**. ISA answers that the only way it can:

    OpenStream(1001): Create secure crypto session
    Creating video codec with codec id: 27
    VideoCodec::Open

which is `CVideoCodecAdaptive` -- video decrypted *and decoded inside the CDM*
(`src/main.cpp`, guarded by `stream->m_isEncrypted &&
m_session->IsCDMSessionSecurePath(cdmSessionIndex)`).

Three things this rules out, so they are not worth revisiting:

* **It is not something we ask for.** The licence request body we send carries
  exactly the browser's fields -- `context, cpn, drmParams, drmSystem,
  drmVideoFeature, licenseRequest, sessionId, videoId` and nothing else --
  including the `sessionId` taken from drmParams field 5, verified against the
  2026-08-27 22:53 capture (`-R87LyT0KWAPy-Nh`, matching the request's own
  `sessionId`).
* **ISA's "Ignore HDCP status" cannot change it.** That flag only skips ISA's
  own `CheckHDCP()`, and `Session.cpp` reaches it *after* every capability
  probe has already been decided.
* **"Disable secure decoder" cannot change it either.** It clears
  `SSD_SECURE_DECODER`, not `SSD_SECURE_PATH`, and `main.cpp` tests the latter.

One CDM session serves everything. ISA creates a session for the first PSSH set
and then shares it, because our licence returns all four keys at once and
`HasLicenseKey(existing, kid)` is true for each subsequent set -- which is why
twelve PSSH sets produce exactly one `licence granted` line.

The failure that ends playback follows from that decode path, not from the
manifest. At 9.5s:

    DecryptAndDecodeVideo: Returned CDM status: 1
    ffmpeg [aac] channel element 3.11 is not allocated       (hundreds)
    ffmpeg [aac] Reserved bit set. / Prediction is not allowed in AAC-LC.
    CDVDAudioCodecFFmpeg::GetChannelMap - FFmpeg reported 33 channels,
                                          but the layout contains 0
    CVideoPlayerAudio::Process - stream stalled
    CVideoPlayer::HandlePlaySpeed - audio stream stalled, triggering re-sync

The CDM's video decode fails and the audio coming out of the same shared
session stops being valid AAC in the same instant. The `PosTime` / `Seek time`
pairs that follow every three seconds are Kodi's stall recovery, with the clock
free-running -- a consequence, not a cause. This is the same "9 seconds" that
has ended every build since the beginning; it only became audible once the
audio track stopped being deleted outright.


## Why on-demand played for 9.5 seconds, and what actually fixed it

The answer is InputStream Adaptive's version, and the route to it is worth
keeping because six other answers looked right first.

The shape of the failure: on-demand audio played for about 9.5 seconds and then
became invalid AAC -- hundreds of `channel element ... is not allocated`,
`Reserved bit set`, `Number of bands exceeds limit` -- and Kodi answered with
`CVideoPlayerAudio::Process - stream stalled`, a flush, and a re-seek every
three seconds forever. The video track's `DecryptAndDecodeVideo` returned
kNoKey in the same instant, sixteen milliseconds after the flush, which made it
look like a second fault and was only ever a consequence of the first.

### Reproduced through the bridge, on 2026-08-29

The original comparison was made on the DASH path, when the addon still held a
cookie jar, so whether ISA 21 failed the same way on media the SABR bridge
serves was inferred rather than measured. It is measured now: Kodi 21.3 with
**inputstream.adaptive 21.5.22**, the same code as the Kodi 22 build to the
byte apart from `addon.xml`.

The addon side is clean -- `asking for 1080p, offering [146]`, `opened session
as TVHTML5_UNPLUGGED`, per-track key ids, `the server chose video 146, audio
150`, `licence granted: 949 bytes, 4 formats`, and watchtime reported out to
47.9s. Then:

    16:59:58.009  Creating video codec with codec id: 27
    17:00:07.466  [aac] channel element 2.10 is not allocated
                  [aac] Reserved bit set.

**9.46 seconds**, against the 9.5 measured on the DASH path -- one audio
subsegment, the same failure, and no reason left to think the delivery had
anything to do with it. 4912 AAC errors and 28 `stream stalled` in the run.

One thing did change, and it is the session's fixes showing: **zero kNoKey**.
On the DASH path the video track went kNoKey sixteen milliseconds after the
audio flush, which looked like a second fault. Here video keeps decoding while
audio is destroyed, because each Representation now carries its own key id and
the two tracks no longer share a CDM session. So ISA 21's remaining fault is
audio decryption alone.

**What has not been tested, and the claim that overreached.** This was written
up as "nothing in this addon can reach it", which the evidence does not
support. Every ISA 21 run has used one audio rendition, because the bridge
declares a single Representation per track and always picks the
highest-scoring one. YouTube TV offers four:

    itag 148   audio/mp4   mp4a.40.5   HE-AAC       DRM_TRACK_TYPE_AUDIO
    itag 149   audio/mp4   mp4a.40.2   AAC-LC med   DRM_TRACK_TYPE_AUDIO
    itag 150   audio/mp4   mp4a.40.2   AAC-LC high  DRM_TRACK_TYPE_AUDIO
    itag 381   audio/mp4   ac-3        AC-3         DRM_TRACK_TYPE_AUDIO

Only 150 has ever been played, on either ISA version. The failure is ffmpeg's
AAC decoder rejecting bytes it was given, which says the bytes were wrong, not
which layer made them wrong -- and this audio is packaged full-sample with an
8-byte IV and no subsamples, which is the awkward case for a CENC
implementation. Whether ISA 21.5.22 mishandles that packaging in general, or
this rendition in particular, has never been separated, and **itag 381 is not
even AAC**, so it exercises a different decoder path entirely.

An `audio_itag` setting now names the rendition, so one playback per itag
answers it. Until that is run, the honest statement is that ISA 21.5.22 fails
on the rendition the addon chooses -- not that ISA 21 cannot work.

**The first attempt at it tested nothing, and the log said so.** Naming the
rendition in `_pick` -- which decides what the manifest declares -- left every
audio format in the SABR offer, and the endpoint picks from what it is given:

    sabr bridge: audio itag 148, because the setting asks for it
    sabr bridge: session s1788037712, audio itag 148, video itag 146 (1080p)
    sabr bridge: the server chose video 146, audio 150

Three lines, one second apart, and `ISA asked itag 150` for every segment
after them. Itag 150 was played in all five runs -- 148, 149, 381 and
automatic alike -- so four "different renditions" were the same bytes four
times. This is the video-height finding again in the other track: **the offer
decides, not the manifest.** The setting now narrows the offer.

Also learned from those runs: this title has no itag 381 at all. AC-3 is not
on everything, so a rendition test has to say when the one asked for is absent
rather than silently falling back.

### What actually differs between the track that works and the one that does not

"Could it be an audio/video mismatch?" is the right question, and the logs
answer it in two parts.

**Nothing the addon declares is mismatched.** In every run the key id the
bridge puts in the manifest is the one the media's own `tenc` carries:

    sabr bridge: key ids {146: '13720fbbf8505264', 148: '92d444f944355272'}
    itag 146 fragment 3 aux: tenc ... kid=13720fbbf8505264
    itag 148 fragment 2 aux: tenc ... kid=92d444f944355272

**Retracted: the subsample explanation below does not survive reading ISA's
source.** ISA 21.5.22's `CWVCencSingleSampleDecrypter::DecryptSampleData`
handles a sample with no subsamples correctly on the non-secure path, which is
the path audio takes here (`GetCapabilities: Single decrypt possible`):

    else
    {
      subsampleCount = 1;
      bytesOfCleartextData = &clearBytes;      // 0
      bytesOfEncryptedData = &encryptedBytes;  // the whole sample
    }

and the 8-byte IV is zero-padded to 16 in `CAdaptiveCencSampleDecrypter`. The
large 21 to 22 rewrite of that file -- including the new comment "If IV present
but no subs were provided, treat as fully encrypted payload" -- is inside the
`SSD_SECURE_PATH` branch, which is the *video* path. So the observation below
is real and the conclusion drawn from it was not.

**What the source does show** is a fragment-level difference, in
`FragmentedSampleReader::ParseFragment`. Both versions inject an empty `senc`
when a fragment carries no `saiz`, `saio` or `senc` at all:

    // ISA 21.5.22
    if (!traf->GetChild(SAIO) && !traf->GetChild(SAIZ) && !traf->GetChild(SENC))
      traf->AddChild(new AP4_SencAtom());

    // ISA 22.3.20
    bool isDefaultProtected{true};
    ... isDefaultProtected = trackEnc->GetDefaultIsProtected() != 0;
    if (isDefaultProtected && !traf->GetChild(SAIO) && ...)
      traf->AddChild(new AP4_SencAtom());

21 injects it unconditionally; 22 first asks the track's `tenc` whether samples
are protected by default. This addon's audio has exactly the shape that reaches
that code -- **fragment 1 clear with no saiz/saio, every fragment after it
encrypted** -- so it is the one difference found so far that both versions
would take differently on this media. Whether it is *the* cause is not
established: our `tenc` says `protected=1`, which would make `isDefaultProtected`
true on 22 as well.

**The observation, which stands:** the two tracks are not encrypted the same
way, and the difference lines up with which one fails:

    VIDEO 146   saiz default=0  count=160  sizes[:4]=[16, 16, 16, 16]
    AUDIO 150   saiz default=8  count=430

Sixteen bytes of auxiliary data per video sample is an 8-byte IV plus a
subsample count plus one subsample entry: **subsample encryption**, clear NAL
headers with encrypted payload. Eight bytes per audio sample is the IV and
nothing else: **the whole sample is encrypted, with no subsamples at all.**
Both tracks use `iv_size=8`.

It is a plausible corner for a CENC implementation, and it was written up here
as the cause. Reading 21.5.22 does not support that: the non-secure path
handles it. Kept as the measurement it is, not as the explanation it was
claimed to be.

### Every AAC rendition fails at the same instant, and that is the proof

The audio is not degrading after ten seconds. **It is never decrypted at all.**
Audio fragment 1 arrives `clear (no saiz/saio)` and fragment 2 onward
`ENCRYPTED`, and a fragment is 9984 ms:

    itag 148 fragment 1 ( 61281 bytes) clear      itag 148 fragment 2 ENCRYPTED
    itag 149 fragment 1 (161773 bytes) clear      itag 149 fragment 2 ENCRYPTED
    itag 150 fragment 1 (321489 bytes) clear      itag 150 fragment 2 ENCRYPTED

So the ~9.5 seconds that plays *is* the clear first fragment, and the AAC
errors start the moment the first encrypted one is decoded. All three AAC
renditions, offered one at a time so the endpoint had no choice:

    itag 148  HE-AAC      61 KB/fragment   first error at 9.54 s
    itag 149  AAC-LC med 162 KB/fragment   first error at 9.58 s
    itag 150  AAC-LC hi  321 KB/fragment   first error at 9.54 s

Fragment sizes differ five-fold and the failure time does not move, because it
is not a byte count or a bitrate -- it is the encryption boundary. Codec
profile and rendition are ruled out by measurement; itag 381 (AC-3) is absent
from these titles and untested, but it is full-sample encrypted like the rest,
so the mechanism covers it.

Nothing in this addon reaches this: the key ids are right, the codecs are
right, and YouTube decides how it encrypts.

### Kodi 20: the manifest type, and then an old Widevine CDM

Kodi 20.5 with inputstream.adaptive 20.3.18 does everything up to playback --
signs in, browses, mints a proof-of-origin token, opens a SABR session at
1080p -- and then refuses the stream in five milliseconds, with Kodi saying
only `CVideoPlayer::OpenInputStream - error opening`. ISA 20's own `Open()`
says the rest:

    if (m_kodiProps.m_manifestType == PROPERTIES::ManifestType::UNKNOWN)
      return false;

ISA 21 infers the manifest type from the mime type and deprecates the
property, so the addon had stopped setting it. It is set again below ISA 21.

With that in, ISA reaches the media and cannot decrypt any of it. The reason
is not ISA's:

    licence exchange failed: licence refused (LICENSE_STATUS_UNPLAYABLE)
    CCurlFile::Open - <http://127.0.0.1:57814/license?...> Failed with code 502
    GetCapabilities: Keys empty

YouTube refused the licence, four times, on every run -- `licence granted`
never appears. And the machine is the same one that plays perfectly under
Kodi 21 and 22, on the same account, the same title and the same addon, which
leaves very little to vary:

    Kodi 21 / 22 (Flatpak)   CDM 4.10.3050.0   licence granted
    Kodi 20    (~/.kodi)     CDM 4.10.2934.0   LICENSE_STATUS_UNPLAYABLE

YouTube sees the challenge, the video id, the cpn and the drmParams. Kodi's
version and ISA's are invisible to it; the challenge is what the CDM builds.
So the CDM is the variable, and an out-of-date one is the first thing to
suspect when a licence is refused outright.

The refusal now says so rather than surfacing as an HTTP 502 with nothing
attached -- and updating the CDM on that box proved it: `CDM version:
4.10.3050.0`, `licence granted: 949 bytes, 4 formats`, zero decrypt failures,
zero AAC errors, where every run before it was refused four times.

**And then Kodi crashed, on the licence.** The log ends two lines after the
grant, mid-playback-start, with nothing after it. So ISA 20.3.18 has a further
fault of its own, past the point where every earlier obstacle was cleared.

Kodi 20 is not supported, and the floor stays at inputstream.adaptive 21.5.22.
Reaching this far took two addon changes -- naming the manifest type, which
ISA 20 requires and 21 infers, and an old CDM that YouTube would not license --
and both were worth finding, but the crash after them is ISA's and not
something this addon can reach. The manifest-type gate has been removed again:
with the floor at 21 it can never run, and dead code that looks like support
is worse than none.

One incidental measurement from those runs, since the rewrite was toggled
across them: with it off, 51 decrypt failures and 5387 AAC errors; with it on,
102 decrypt failures and none. Without keys nothing decrypts either way, but
the rewrite makes ISA fail rather than feed the decoder garbage.

### Live audio: the sample size is in tfhd, not trun

With the manifest fix in, live started on Kodi 21 -- and the audio was noise
while the video froze. The instrumentation added for exactly this said why in
one line:

    sabr session: itag 381 not re-signalled -- trun gives 0 sample sizes
                  for 156 encrypted samples

The subsample rewrite needs each sample's length. It read them from `trun`,
which is where they are on demand. Live AC-3 does not put them there: every
sample is the same length, so the packager says it once in `tfhd` as
`default_sample_size` and `trun` carries no sizes at all. The rewrite declined,
the fragment went out as it arrived, and ISA 21 turned it to noise -- which is
the original bug, reappearing on the one shape the rewrite did not cover.

`_tfhd_default_sample_size` reads it, and a fragment whose `trun` names no
sizes uses the tfhd default for every sample. Confirmed on the same box and
channel: zero fragments declined, zero decoder errors, one audio stream at six
channels, zero stalls, audio and video in step at 2861509/2861510.

That closes the matrix -- Kodi 21 and 22, on demand and live, all measured.

Kodi 22 on the same channel at the same time: one audio stream, `ac3, channels:
6`, zero decoder errors, zero stalls, audio and video in step at sequence
2861434/2861435. So live is well on 22 and this was the last thing between
Kodi 21 and the same.

Also worth recording: this channel offers AAC as well as AC-3 (itags 149 and
148 beside 381), which the candidate table now shows -- it listed video only
until a live channel served AC-3 and there was no way to see the alternatives.

### Live on Kodi 21 died inside our own diagnostic

The subsample rewrite works on live too -- the endpoint served AC-3 (itag 381)
and the bridge re-signalled it -- but playback never started on Kodi 21, with
zero segment fetches. The log says why, and it is nothing to do with DRM:

    18:10:51.018  opened session s1788041450 as TVHTML5_UNPLUGGED
    18:10:51.824  itag 381 fragment 2861150 (241912 bytes) ENCRYPTED
    18:11:11.609  CCurlFile::Open ... CURLOpen failed
                  inputstream.adaptive: Download failed, internal error
    18:11:21.928  could not read itag 381 as a file: ... Read timed out.
    18:11:21.929  client hung up before the response was sent

Media was in hand within a second. Then `compare_against_file` -- a diagnostic
that fetches the same rendition as a progressive file and compares it with what
SABR served -- ran **inside the manifest request** on a 30-second timeout. ISA
waits 20 seconds for a manifest. It gave up at 18:11:11 and the bridge finished
its perfectly good manifest ten seconds later, for a client that had gone.

A live rendition is not a file: its player response carries no `initRange`, no
`indexRange` and no `contentLength`, so that fetch was never going to return
anything useful. It is now skipped on live, and no diagnostic gets the full
timeout inside a request ISA is waiting on -- five seconds.

On Kodi 22 the same call failed too, but instantly, so the manifest was served
in time and playback continued. Its error names a second problem: `Could not
find a suitable TLS CA certificate bundle, invalid path:
.../script.module.certifi/lib/certifi/cacert.pem`. Kodi's requests takes its
CA bundle from that addon, and on a box where it is mid-update every HTTPS call
fails. The addon now points requests at the system bundle when certifi's file
is genuinely missing -- never disabling verification, never overriding a
`REQUESTS_CA_BUNDLE` someone set deliberately.

### Resolved: it was the subsample spelling, and the addon can fix it

Everything below stands as measurement and is wrong as a conclusion. "Nothing
in this addon reaches it" was written three times in this document and it was
false each time: the bridge writes every byte ISA reads, the manifest and the
fragments both.

The one difference between the track ISA 21.5.22 plays and the track it
destroys was measured early and then explained away:

    VIDEO 146   saiz default=0  count=160  sizes[:4]=[16, 16, 16, 16]
    AUDIO 150   saiz default=8  count=430

Sixteen bytes per video sample is an IV plus a subsample count plus an entry:
subsample encryption. Eight bytes per audio sample is the IV alone -- the whole
sample encrypted, which CENC spells as a subsample count of zero. Reading ISA
21's `DecryptSampleData` showed it handling a count of zero correctly, and that
reading was taken as ruling the difference out. It ruled out one function, not
the difference.

CENC can say the same thing explicitly: one subsample per sample, zero clear
bytes, the whole sample encrypted. `mp4.explicit_subsamples` rewrites `saiz`
and `saio` into a `senc` that says it that way, leaving the ciphertext and the
mdat untouched, and the bridge applies it to audio as it serves it. Measured
the same evening, on the same box that had failed a dozen times:

    Kodi 21.3, inputstream.adaptive 21.5.22, rewrite on
      0 aac errors, 0 stalls, 48 audio segments, 103.9s of playback
    Kodi 22.0, inputstream.adaptive 22.3.20, rewrite on
      0 aac errors, 0 stalls, 23 audio segments, 50.9s of playback

So ISA 21.5.22 mishandles whole-sample CENC audio somewhere other than the
function that was read, ISA 22 handles both spellings, and the addon can
simply choose the spelling that works. It is on by default, and `addon.xml`
asks for inputstream.adaptive 21.5.22 again rather than 22.3.20.

Two lessons worth keeping. Reading one function is not ruling out a mechanism.
And when a fault is in a component you do not control, the question to ask is
what you *do* control that feeds it -- here, every byte of it.

### The clear fragment was not the fault, and that settles it

The one lead the source review turned up was fragment-shaped, so the bridge
was given a per-itag fragment offset and told to serve audio one fragment on,
so ISA 21 would never see the clear one. It engaged:

    sabr bridge: serving audio itag 150 one fragment on, so ISA never sees
    the clear first one.

And the audio broke **immediately** rather than at 9.5 seconds:

    17:44:22.451  Creating audio thread
    17:44:23.833  [aac] channel element 2.10 is not allocated
    17:44:25.521  CVideoPlayerAudio::Process - stream stalled

The error count fell from ~2500 to 106 only because playback stalled in three
seconds instead of flooding for a minute. Same messages, same corruption,
starting at the first encrypted sample ISA was handed.

So the clear-to-encrypted transition is **not** the fault, and neither is the
synthetic `senc` difference between the versions -- the only thing this
media's shape reached differently. **ISA 21.5.22 does not decrypt this audio
at all**, and the 9.5 seconds that used to play was the clear fragment.

Three independent lines now agree:

1. all three AAC renditions fail identically when each is offered alone, with
   fragment sizes differing five-fold;
2. the failure lands on the encryption boundary, not on any byte or bitrate
   boundary;
3. removing the clear fragment moves the failure from the tenth second to the
   first.

Nothing in this addon reaches it. Kodi 21 needs inputstream.adaptive 22.3.20,
and that is a finding now rather than an inference.

### What the media actually is

Measured with a probe in the addon rather than inferred. Every init segment:

    ftyp moov mvhd pssh pssh mvex trex trak tkhd mdia mdhd hdlr minf dinf
    stbl stsd enca esds sinf frma schm schi tenc ...

`enca` for audio and `encv` for video, a complete sinf/schm/schi/tenc chain, and
two pssh boxes of YouTube's own. The fragments:

    #0  moof mfhd traf tfhd tfdt trun mdat                 -- no saiz, no saio
    #1  moof mfhd traf tfhd tfdt trun saiz saio mdat       -- 8 byte IVs

**The first fragment of each audio track is a clear lead.** The 9.5 seconds that
always played is the part that needs no key; audio had never once been
decrypted. `saio` offsets are 1001 and 1861 against fragments at 64783 and
324973, so they are relative to their own moof, and `tfhd` sets
default-base-is-moof and no base_data_offset. The file is impeccable.

### Ruled out, each by a measurement

* **Wrong key ids** -- read from each track's own tenc, identical to what the
  CDM then reported.
* **A shared CDM session** -- split so audio had its own; two sessions and two
  licences in the log, same failure at the same instant.
* **Resolution and decoder load** -- 640x360 died exactly where 1280x720 did,
  to the same PTS.
* **The licence request** -- matches the browser's captured body field for
  field, including the sessionId from drmParams field 5.
* **The `pot`** -- fragment 1 is a valid moof with it and without it.
* **Absolute `saio` offsets** -- measured relative.
* **The PSSH source** -- ISA prefers a manifest pssh and stops looking
  (`if (!sessionPsshset.pssh_.empty()) initData = sessionPsshset.pssh_`), so
  inlining ours suppressed `ExtractStreamProtectionData`. Removing it let the
  stream's own pssh and tenc through, and changed nothing.

### The answer

Kodi 22.0-BETA1 with **inputstream.adaptive 22.3.20**: zero AAC errors, zero
stalls, zero PosTime re-seeks, zero kNoKey, and ninety seconds of on-demand
playback with audio, ended by the user rather than by a stall. The capability
split is unchanged between the two versions -- `Single decrypt possible` for
audio, `Single decrypt failed, secure path only` for video, the same
output-restricted video keys -- so nothing about the account, the licence or
the manifest differs. ISA 21.5.22 could not decrypt those audio fragments;
22.3.20 can.

`addon.xml` therefore requires inputstream.adaptive 22.3.20. On Kodi 21 the
video track plays and the audio track is silent or noise, and no change to this
addon fixes it.

### What the session's fixes were worth

All of them still matter and all are visible in the working log: key ids read
from init segments for 12 of 12 representations on a title's first play, per
-Representation ContentProtection, the stream's own PSSH, separate CDM sessions,
and the playback heartbeat. The heartbeat in particular is independent of the
ISA bug and proven in the same run -- `heartbeat 0 acknowledged (OK)` and
`heartbeat 1 acknowledged (OK)` across ninety seconds, where the player
response's `intervalMilliseconds` 30000 and `maxRetries` 3 had been ending
playback at 1:26.


## Live has no init segment, so the manifest must carry the PSSH

On-demand played clean on Kodi 22 in the same session where live failed
immediately, on the same account, with the same build. The live attempt
(KDKA-TV, `z0sfuXTVx8g`) got as far as a parsed manifest and then lost its
video track:

```
Manifest successfully parsed (Periods: 1, Streams in first period: 4, Type: live)
Created AdaptiveStream [AS-0] video / [AS-1] audio / [AS-2] audio / [AS-3] subtitle
OpenStream(1001)
UpdateSampleDescription: Codec fourcc: avc1 (1635148593)
Initialize crypto session
SelectDRM: Selected DRM key system: com.widevine.alpha
error: CreateSession: Cannot request license, PSSH init data has unexpected size (0)
error: InitializeSession: Failed to create the DRM session
error: CVideoPlayerVideo::OpenStream: Codec id 27 require extradata.
warning: OpenStream - Unsupported stream 1001. Stream disabled.
```

Audio opened; with no video, the player closed everything.

### Where the init data comes from in ISA 22

ISA 22 replaced the DRM code this project had been reading. There is no
`ExtractStreamProtectionData` any more, and no `sessionPsshset.pssh_`.
`CSession::PrepareStream` now collects two lists and hands both to the engine
(`src/Session.cpp`):

```cpp
std::vector<DRM::DRMInfo> manifestDrmInfo = repr->DrmInfos();
std::vector<DRM::DRMInfo> mediaDrmInfo = stream.GetReader()->GetInitDRMInfo();
... m_drmEngine.InitializeSession(manifestDrmInfo, mediaDrmInfo, drmMediaType, ...)
```

`GetInitDRMInfo` (`src/samplereader/FragmentedSampleReader.cpp`) reads the
track it has parsed: it produces nothing unless the sample description is
`TYPE_PROTECTED`, and one `DRMInfo` per `pssh` box in the moov.
`DrmInfosUnion` puts the media entries first and appends the manifest ones,
and `InitializeSession` walks that list and stops at the first session that
opens. So where a stream carries its own `pssh`, the file still wins —
a manifest PSSH does not suppress it, which is what ISA 21 did and what the
comments in `manifest.py` used to describe.

### Live carries neither

An on-demand Representation names its init segment
(`<Initialization range="0-1729"/>`) and ISA fetches it. A live one names
nothing: its `<SegmentList>` is a run of `<SegmentURL media="sq/N/lmt/M"/>`
and no more. In the log ISA's first download for AS-0 is `sq/3263606`, a
1.4 MB media segment — there is no init fetch, and the moov ISA parsed came
out of the head of that segment. Whatever is in it, it yielded no `DRMInfo`:
`mediaDrmInfo` was empty, so the only entry left was ours from the manifest,
and after the change below it carried no `<cenc:pssh>`. Empty init data
reaches `CWVCencSingleSampleDecrypter::CreateSession`, which rejects anything
under 4 bytes — the "unexpected size (0)".

The addon's own logs say the same from the other side. On demand:

```
init segments: read key ids for 12 of 12 representation(s): 142=4d3521e2, ...
```

On live that line is absent entirely, and so is its failure counterpart:
`init_targets` returned nothing to fetch, because it only understood
`<Initialization range=>` and `<Initialization sourceURL=>`.

### What changed

* The PSSH is inlined in the manifest again, unconditionally, and the
  `manifest_pssh` setting is gone. On demand it costs nothing (the media
  entry is still first); live has nowhere else to get init data.
* `init_targets` gained a third shape: with no `<Initialization>`, the first
  `<SegmentURL media=>` stands in, read as a 16 kB range rather than whole.
  The first entry is the oldest in the DVR window and is served — the segment
  probe fetched `sq/3263606` with HTTP 200 while the newest was `sq/3265103`.
  This gives live per-track key ids from the media, as on demand.
* `_read_kids` now logs the two cases it used to pass over in silence: no
  Representation names an init segment, and every fetch succeeded but no moov
  carried a `tenc`. The second is the one that would say the live media is not
  signalling cenc where we look.
* The video key id is no longer blanked. That existed to stop ISA 21 putting
  audio and video on one CDM session, since `HasLicenseKey` matched on the key
  alone and YouTube returns all four keys in one licence. ISA 22 reuses a
  session only when the key id matches, or when the licence holds the key
  **and** the media type is the same (`InitializeSession`) — audio and video
  agree on neither, so they are already separate. Blanking now only draws
  "Cannot get default KID from DRM info, decryption can fail". The
  `split_sessions` setting is gone with it; each Representation's PSSH names
  its own key, which is what a conformant packager emits anyway.

Still unmeasured: whether the moov at the head of a live segment declares
`encv`/`enca` and a `tenc` at all. If it does not, a licence alone will not
be enough, because `m_protectedDesc` stays null and the sample reader never
decrypts. The new `init segments:` log lines answer it on the next live play.

## Live plays, then ISA dereferences an empty optional on the first MPD update

The manifest PSSH did it. On the `livepssh` build KDKA-TV opened a Widevine
session, took a licence carrying twelve keys, opened both a video and an audio
decoder, and rendered. It also confirmed the third init-target shape works:

```
init segments: read key ids for 10 of 11 representation(s): 142=43991063,
  143=43991063, 144=43991063, 145=eef784dd, 146=eef784dd,
  148:ChAKBWFjb250EgdwcmltYXJ5=c190a14d, ... 161=43991063
```

So the moov at the head of a live segment does carry `enca`/`encv` and a
`tenc` — the question left open above is answered, and live Representation
ids are not bare itags but `<itag>:<base64 audio track>`. ISA agreed:

```
ParseMoofPssh: Found 2 PSSH on media segment
ParseTrafSgpd: Found TRAF/SGPD/SEIG boxes with 1 entries
ParseTrafSgpd: Protected SEIG box entry have 1 key sets
```

`SEIG` is a sample-group entry — live rotates keys per fragment, which is why
twelve keys arrive for eleven Representations.

Then, about five seconds in, Kodi died. The log has no shutdown sequence, no
error and no final line: the last entry is our proxy answering ISA's **first
live manifest refresh**, six milliseconds earlier.

### DASHTree.cpp:1666

`CDashTree::OnUpdateSegments` merges the refreshed MPD into the live tree.
The first thing it does with each matched Representation is:

```cpp
auto repr = (*itRepr).get();

if (!repr->GetSegmentTemplate()->HasTimeline() || repr->Timeline().IsEmpty())
```

`GetSegmentTemplate()` returns `std::optional<CSegmentTemplate>&`
(`src/common/CommonSegAttribs.h:30`), and a Representation is only given one
when the manifest carries a `<SegmentTemplate>` on it or above it
(`DASHTree.cpp:818`). YouTube's live MPD carries `<SegmentList>` — that is
what we push a timescale onto, eleven times, on every fetch — so the optional
is empty for every Representation and `operator->` walks off an
uninitialised object. The identical call thirty lines further down is
guarded (`if (!rep->HasSegmentTemplate() || rep->GetSegmentTemplate()->
HasTimeline())`, line 1897), which is what makes this a bug rather than an
assumption: ISA knows a Representation may have no template and forgot here.

It fires on the first refresh, so any SegmentList-based live stream gets a
few seconds of playback and then takes Kodi down with it.

### The same missing template also starts us 2.5 hours late

The segment probe listed 1796 segments, oldest `sq/3263606`, newest
`sq/3265401`. ISA's first video download is `itag/145 sq/3263606` and it
walks forward from there — it began at the *oldest* segment in the DVR
window, about two and a half hours behind the live edge.

That is the same cause. ISA's live-edge machinery is gated on the template:

```cpp
// Generate segments to templated representation with no defined timeline only
if (!rep->HasSegmentTemplate() || rep->GetSegmentTemplate()->HasTimeline())
  continue;
...
const uint64_t liveEdgeScaled = liveEdgeMs * rep->GetSegmentTemplate()->GetTimescale() / 1000;
rep->Timeline().PruneToTime(liveEdgeScaled);
```

With no template there is no `PruneToTime`, no `GenerateTemplatedSegments`,
and nothing positions the stream at the live edge.

### The fix both point at

Rewrite the live `<SegmentList>` as `<SegmentTemplate>` + `<SegmentTimeline>`
in the proxy. The media urls are `sq/$Number$/lmt/<lmt>` under a
per-Representation `<BaseURL>`, which is exactly what `$Number$` expresses, so
the two forms carry the same information — but only one of them is the shape
ISA's live path is written for. That engages the optional (no crash) and lets
the live-edge code run (no 2.5 hour delay).

### What the manifest actually said, and what the conversion had to be

The saved `last-manifest.mpd` (856 KB) settles the shape:

```xml
<MPD type="dynamic" availabilityStartTime="2026-08-27T19:24:02"
     timeShiftBufferDepth="PT14400.000S" minimumUpdatePeriod="PT5.000S" ...>
 <Period start="PT16270868.153S">
  <SegmentList presentationTimeOffset="16293497486" startNumber="3258715" timescale="1000">
   <SegmentTimeline><S d="5005"/><S d="4971"/><S d="4938"/>... 1879 entries
  </SegmentList>
  <AdaptationSet id="0" mimeType="audio/mp4">
   <Representation id="148:ChAKBWFjb250EgdwcmltYXJ5">
    <BaseURL>https://...googlevideo.com/videoplayback/.../</BaseURL>
    <SegmentList><SegmentURL media="sq/3258715/lmt/702"/>... 1879 entries
```

`minimumUpdatePeriod="PT5.000S"` is the five seconds. Eleven Representations,
each listing the same contiguous 1879 segments, `lmt` constant within a
Representation, and the subtitle track spelling its media `sq/N` with no
`lmt` at all. No `<Initialization>` anywhere, and no `<SegmentTemplate>`.

**The cheap workaround does not exist.** The first idea was to add an empty
`<SegmentTemplate>` purely to engage the optional and leave the SegmentList
to do the work. It would have broken every url:

```cpp
if (rep->HasSegmentTemplate())
  streamUrl = segTpl->FormatUrl(segTpl->GetMedia(), ...);
else
  streamUrl = seg.url;
```

(`AdaptiveStream.cpp:216`.) The moment a Representation has a template, url
construction stops reading the SegmentList's `media` and formats the
template's instead.

**And the SegmentList was already producing nothing.** ISA parses a
`<SegmentTimeline>` only from an *AdaptationSet's* SegmentList
(`DASHTree.cpp:612`). YouTube states it once on the Period, where nothing
reads it, and a Representation's SegmentList inherits from its AdaptationSet,
which here has none. So `duration` fell through to `segList.GetDuration()` —
zero. Every one of the 1879 segments came out with duration 0, `startPTS_` 0
and `m_number` 0. That is the real reason playback began at the oldest
segment of a four-hour window: there was no timeline to seek in, only a list
walked from the front.

So the conversion is the whole fix, not a workaround for the crash. `patch()`
now restates the live manifest as one Period-level

```xml
<SegmentTemplate timescale="1000" startNumber="3258715"><SegmentTimeline>...</SegmentTimeline></SegmentTemplate>
```

which ISA copies down to every AdaptationSet and Representation (lines 578,
818), plus one line per Representation naming only its own media:

```xml
<SegmentTemplate media="sq/$Number$/lmt/702" startNumber="3258715"/>
```

A template node with no `<SegmentTimeline>` child leaves the inherited one
intact (`ParseSegmentTemplate`), so each Representation ends up with the
timeline, real durations, and numbers counting from its own first `sq`.

Checked rather than assumed: replaying ISA's generation (`DASHTree.cpp:1013`)
and `FormatUrl` over the converted manifest reproduces all 1879 urls of all
eleven Representations **exactly**, character for character, against the
`<SegmentURL>` list they replaced. The manifest also drops from 856 KB to
72 KB, which is 20,669 elements ISA no longer parses five times a minute.

`to_segment_template` refuses and leaves the manifest alone if the timeline
does not describe exactly as many segments as a Representation lists — a
template is equivalent to its list only while the two agree, and a manifest
that plays for five seconds beats one that fetches the wrong urls.

**Known unknown.** The `<S>` elements carry no `t=`, so segment PTS run from
zero plus the Period start. That is stable while YouTube keeps `startNumber`
fixed and appends — which is what two captures 25 minutes apart show
(oldest `sq/3263606` in both, newest moving 3265103 -> 3265401). Once the
window reaches its 4 hour `timeShiftBufferDepth` and segments start being
dropped, `startNumber` will advance and every PTS will shift with it, which
would break the update's `segment.startPTS_ == segStartPTS` lookup. Fixing
that means anchoring the first `<S t=>`, and choosing the anchor needs a
manifest from a window that has actually begun dropping. Not guessed at here.

## YouTube TV accepts an OAuth device-code token, as TVHTML5_UNPLUGGED

This was open for as long as the addon has existed. The notes said, honestly,
that the web player authenticates with `SAPISIDHASH` in every capture and
never with a bearer token -- all sixty-nine authenticated requests -- and that
this said what the web player does rather than what the surface allows.

It allows it. The device-code flow the regular YouTube addon uses works
against YouTube TV:

```
POST accounts.google.com/o/oauth2/device/code
     client_id, scope=https://www.googleapis.com/auth/youtube
  -> device_code, user_code, verification_url, interval
POST www.googleapis.com/oauth2/v4/token
     client_id, client_secret, code, grant_type=http://oauth.net/grant_type/device/1.0
  -> access_token, refresh_token
```

then `Authorization: Bearer <token>` on InnerTube.

**The identity is half the credential.** The first attempt was refused:

```
browse -> HTTP 400 as WEB_UNPLUGGED v1.20260825.04.00, 0 cookies / 0 bytes:
          INVALID_ARGUMENT: Request contains an invalid argument.
```

That reads like a rejected credential and is not one. Two things were wrong
with it at once, and both were already written down here. `INVALID_ARGUMENT`
is this surface's complaint about a malformed *request* -- the note above
`context()` records the mobile and TV clients answering exactly this when
sent the web player's `visitorData`/`rolloutToken`/`configInfo` -- and with no
cookie jar the web context loses that same block, so the one identity tried
was also the one most likely to be malformed. Beyond that, a device-code token
is minted for a limited-input client, and `WEB_UNPLUGGED` is the least likely
identity to be accepted for one.

Asked again across all six identities, TV first:

```
oauth probe: TVHTML5_UNPLUGGED answered with 150 station(s)
```

That is the same pairing plugin.video.youtube uses: it sends every bearer
request as `TVHTML5` (client id 7, a Samsung SmartTV on Tizen), swaps in a
Cobalt user agent via `_auth_user_agent`, and drops the `key` API-key
parameter when authorised. We send no `key` at all, and
`TVHTML5_UNPLUGGED` (client id 65) already carries a Cobalt user agent.

So the accepted identity is stored with the token and every later call is
made as that client -- a token without it is a valid credential that fails
every request. Sign-in still proves itself before the token is kept: it asks
each identity for the account's own lineup and keeps the token only for one
that answers.

What this does not yet establish is whether `player` returns a DASH manifest
for a `TVHTML5_UNPLUGGED` bearer session, or only a SABR endpoint. Browsing
is proven; playback is not, and the addon's client survey exists to answer
exactly that.

## SABR serves media, and the request shape that gets it

The SABR endpoint is not a wall. Asked correctly, on a cookie session, it
answered **HTTP 200 with 142,062 bytes**, of which 141,369 were media:

    STREAM_PROTECTION_STATUS   2 bytes
    PLAYBACK_START_POLICY      12 bytes
    LIVE_METADATA              42 bytes
    NEXT_REQUEST_POLICY        54 bytes
    MEDIA_HEADER               91 bytes      <- audio
    MEDIA_HEADER               147 bytes     <- video
    MEDIA_END                  1 byte  (x2)
    MEDIA (total)              141,369 bytes

Getting there took three corrections, each of which had been a guess:

**Fields 16 and 17 are the audio and the video selection.** They were built
as "the format I want" (16) and "the others I know about" (17). Every
captured body puts audio in 16 and video in 17, one repeated entry per
selected track; the browser sends two 16s, primary and secondary.

**An audio FormatId carries xtags, and must.** YouTube TV lists itag 148
twice and itag 149 twice -- `acont=primary` and `acont=secondary` -- and
xtags is the only field distinguishing them:

    f16 itag=149 lmt=0 xtags='ChAKBWFjb250EgdwcmltYXJ5'    -> {1:{1:"acont",2:"primary"}}
    f16 itag=149 lmt=0 xtags='ChIKBWFjb250EglzZWNvbmRhcnk' -> {1:{1:"acont",2:"secondary"}}
    f17 itag=279 lmt=0 xtags=''

It travels as the base64 string the player response carries, verbatim, not
decoded. A selection without it names a track the server cannot resolve,
and the answer is a 76-byte UMP body whose SABR_ERROR reads
`sabr.no_audio_selected`.

**ClientAbrState field 29 is 2.** It was 3 here, beside a comment of mine
reading "media type: audio+video". The captured request that was served
15 MB carries 2.

`lastModified` is absent from every YouTube TV format and the captured
requests send 0 for it, so the field is omitted.

### What the endpoint says when it is unhappy

Its errors are specific and worth reading rather than guessing at:

| body | meaning |
| --- | --- |
| `sabr.malformed_config` (31 bytes) | field 5 missing or unusable -- the config is required |
| `sabr.no_audio_selected` (76 bytes) | field 16 named no resolvable audio track |

Both arrive as **HTTP 200** with `Content-Type: application/vnd.yt-ump`. A
200 from this endpoint means "I parsed you", not "here is your media".

### The ustreamer config is per session and cannot be baked in

Field 5 is `playerConfig.mediaCommonConfig.mediaUstreamerRequestConfig
.videoPlaybackUstreamerConfig`, base64url-decoded. Three captures:

| capture | field 5 | sha1 |
| --- | --- | --- |
| 0a16582a | 1852 B | `dc0bdbc4…` |
| 3443c249 | 1334 B | `02ced566…` |
| b204e270 | 1762 B | `14a5b7f8…` |

Different every time. It has to come back from a player response.

### Which sessions are given one

| | cookie jar | bearer token |
| --- | --- | --- |
| WEB_UNPLUGGED | OK, dash=True, **config ~2340 chars**, abr=True | HTTP 400 |
| TVHTML5_UNPLUGGED @ 7.20260826.15.00 | HTTP 400 | OK, sabr=True, **config 2332**, abr=True |
| TVHTML5_UNPLUGGED @ 6.36 | HTTP 400 | OK, sabr=True, **config NONE**, abr=False |
| ANDROID / IOS_UNPLUGGED | HTTP 400 | HTTP 400 |
| TV_UNPLUGGED_ANDROID | HTTP 403 | HTTP 403 |
| TV_UNPLUGGED_CAST | HTTP 404 | HTTP 404 |

**The client version decides whether the config is served.** Five separate
runs concluded "a token session is never handed a config", and all five
were measured at 6.36 -- a value copied across three clients in the table
and never checked. Swept against 7.20260826.15.00 on one run, same token,
same account, same video: the older version is served no config and
`useServerDrivenAbr` unset, the newer one a 2332-character config with it
set. Nothing else differed.

The table still cannot separate identity from credential -- each credential
reaches exactly one client -- but it no longer needs to. A bearer token
holds a config, and `bearer-as-web` is closed for other reasons: the web
identity refuses a token with the full context, without
visitorData/rolloutToken/configInfo, and with a bare client name and
version alike. None of the 400s carry `error.details`.

### Cookie-free playback, measured end to end

One run, one video, both credentials, each asked as the identity it is
accepted as:

| | cookie jar / WEB_UNPLUGGED | bearer token / TVHTML5_UNPLUGGED |
| --- | --- | --- |
| player | dash=True sabr=True, 25 formats, config 2340 | dash=False sabr=True, 25 formats, config 2332 |
| get_drm_license | 400 -- credential accepted | 400 -- credential accepted |
| SABR, n as minted | 403, 0 bytes | 403, 0 bytes |
| SABR, n solved | **200, 142,061 bytes** | **200, 142,057 bytes** |
| of which media | 141,369 | 141,369 |

Both arms return two MEDIA_HEADERs, MEDIA_END twice, LIVE_METADATA and a
NEXT_REQUEST_POLICY. Nothing in the media path needs a cookie: the token
session is served the same bytes.

Two things this does not yet prove. The licence exchange has only ever been
tested with a placeholder challenge -- a 400 means the credential was
accepted, not that a real Widevine exchange completes on a token. And
InputStream Adaptive cannot speak SABR, so playing this requires a bridge
that serves it synthetic DASH.

## Driving a SABR session: the continuation token

A SABR request is not a segment fetch. Sent on its own it returns the same
bytes every time -- the same sequence numbers, six seconds apart, whatever
position or buffer it claims. That is not a cache and not a clamp. The
request says "I have nothing, I am starting" every time, and the server
answers it correctly every time.

What makes a session a session is an echo. Every response carries a
`NEXT_REQUEST_POLICY` (UMP part 35) whose **field 7** is an opaque blob, and
the next request sends that blob verbatim as **streamerContext field 3**.
Taken from response 1 of a captured session and compared against request 2
of the same session, byte for byte:

    response 1, NEXT_REQUEST_POLICY.7 : 08bf843d10003a0308e702421d0895... (44 bytes)
    request 2,  streamerContext.3     : 08bf843d10003a0308e702421d0895... (44 bytes)

Identical. With it, sequence numbers climb -- 2220056, 2220057 -- and a
round that claims a track the server considers complete gets the other
track alone in reply. Without it, five separate attempts to make the stream
advance all failed, and every explanation offered for that was wrong.

### The request, field by field, from four consecutive captured requests

    1  ClientAbrState
         18 = 2140, 19 = 1204 in every captured request
         28   position: 9007199254740991 (MAX_SAFE_INTEGER) for the live
              edge on the first request, an absolute media timestamp after
         29   a counter: 2, 3, 490, 1579 across one session. Annotated here
              twice as a media-type enum, in both directions, from reading
              a single request. It is not one.
         59   max height
    3  BufferedRange, repeated per track -- what we already hold
         1: FormatId  2: startTimeMs  3: durationMs
         4: firstSequence  5: lastSequence
    5  the ustreamer config
    16 audio FormatIds (itag, lastModified, xtags)
    17 video FormatIds
    19 StreamerContext
         1: ClientInfo {1 locale, 16 client id, 17 version, 18 os}
         2: a poToken, 85 bytes -- not sent by us, and media arrives anyway
         3: the echo above

A BufferedRange claim must only ever grow. Responses repeat a sequence
already held and arrive out of order between tracks, so taking the newest
header as "last" walks the claim backwards and asks for the same segment
again.

### What this means for a bridge

Segments are addressable, but by claim rather than by index: holding up to
N-1 is how you ask for N. That is enough to map InputStream Adaptive's
"fetch segment N" onto a SABR session, which is the piece a synthetic-DASH
bridge needs.

### The walk, measured

With the echo sent and the claim held correctly, a token session walks:

    round 1: 2220135              holding 2220135..2220135    92 KB
    round 2: 2220134 (backfill)   holding 2220134..2220135   112 KB
    round 3: 2220136              holding 2220134..2220136   105 KB
    round 4: 2220137              holding 2220134..2220137   109 KB
    round 5: 2220138              holding 2220134..2220138   113 KB
    round 6: nothing, 131 bytes, 0 media

The server answers the live edge with N and then backfills N-1 before
walking forward, so a claim that only grows forwards discards the backfill,
stops changing, and the response stops changing with it -- four identical
rounds, which looked like a protocol wall and was a bookkeeping bug.

Round 6 is not a failure. At the live edge there is no next segment until
one exists; the round that waited three seconds got 2220146. A bridge has
to treat an empty response as "wait", not as "end of stream".

### SABR delivers the initialisation segment inline

The first MEDIA part of a session opens with an `ftyp` box:

    0000001c 66747970 64617368 00000000     ....ftypdash....

So a segment arrives with its own initialisation rather than needing one
fetched separately, which is one thing a bridge does not have to solve.

### Minting a browser session from the token: refused so far

`OAuthLogin`, given the addon's bearer token, answers **HTTP 403
`Error=badauth`** -- the accounts service's terse refusal, naming neither a
scope nor a client. The token carries `.../auth/youtube` alone while that
route wants `https://www.google.com/accounts/OAuthLogin`, so the open
question is whether the client may request that scope at all; the
device-code endpoint answers it without anyone signing in.

### An initialisation segment has its own header

On demand, the first response carries a MEDIA_HEADER per track with **no
sequence number** and **field 8 set to 1**:

    {1: 0, 2: 11, 3: 150, 4: 1786514309843222, 6: 0, 8: 1, 10: 32512, ...}
    {1: 1, 2: 11, 3: 810, 4: 1786828651044300, 6: 0, 8: 1, 10: 424064, ...}

Field 3 is the itag, field 1 the header id the MEDIA parts reference. A
reader that requires a sequence number drops these silently, and then has
no initialisation segment to serve.

Live is different again: some renditions arrive with ftyp prepended to
their first media (148, 161) and some arrive with no initialisation at all
(317), which is consistent with the DASH path, where live has no init
segment and ISA parses a moov out of the first media segment.

### On demand starts at zero, not at the live edge

ClientAbrState field 28 is MAX_SAFE_INTEGER for "the live edge". Sent for a
recording it is past the end, and the server answers with the two
initialisation headers and no media, indefinitely -- which is the correct
answer to the question asked. On-demand sessions send 0.


## The signature timestamp names the player, and it has to be the right one

Every media url went to `HTTP 403` with an empty body -- both paths at once,
minutes after the DASH path had played seventy-six seconds cleanly. The
symptom is worth recording because it is so easy to misread:

* every variant of the url was refused, `as-is`, `no n`, `with pot`, `no n +
  pot`, `query style`, `no range`;
* pasting the url into a browser on the same machine and the same IP gave
  the same `403 Forbidden, Content-Length: 0`;
* a proof-of-origin token five minutes old, lifted from a request that had
  returned 16 MB, changed nothing;
* the manifest was clean -- diffing the itag 150 BaseURL of the run that
  played against the run that failed, `sparams` was character for character
  the same and the only new parameter, `pcm2cms=yes`, sits in `lsparams`
  and is signed by `lsig`;
* the player responses were the same: forty parameters each, matching but
  for `expire`, `ei`, `sig`, `spc`, `ns`, `n` and CDN routing;
* and `n` verified correct against the browser's own request -- YouTube
  minted `2YLDnv4vx-5yo8ccc44`, the browser sent `-AcdKn1WC2CNPg`, and the
  addon's transform of the minted value gives `-AcdKn1WC2CNPg`.

All of that is consistent, and all of it is beside the point. The player
request declares `signatureTimestamp`, which names the build the client
promises to unscramble with. The addon read it from the bootstrap page,
which said **20690**, while the player it actually fetches and extracts `n`
from, `e937390a`, carries `signatureTimestamp:20684` in its own source --
and 20684 is what the browser sends. So the urls we were minted expected a
transform we never applied. They were refused for everyone, the browser
included, which is exactly how that mismatch looks from outside.

It also explains why the transform kept checking out: it was right for the
browser's values, because those were minted for the build the browser
declared, and wrong for ours.

Read it out of the player, not the page. The player cannot disagree with the
transform by construction. One line fixed both paths at once.

A second bug hid inside the same log line. `_client_version` treats any
setting differing from the constant as a deliberate pin, and `settings.xml`
declared the previous version as its *default* -- so an untouched setting
outvoted both the page and the constant, and the addon kept claiming
`1.20260825.04.00` after the constant moved on. Settings defaults are not
"unset".


## AV1 is not a rendition this path can play

Audio through the bridge died at the same instant every time: the clear lead
played, 9.9 seconds of it, and every encrypted fragment after that came back
`kDecryptError` on a key the CDM reported usable. Everything measurable
about the media was correct --

* the fragments are byte for byte the file's own, initialisation 1712 bytes
  and fragments 321489 and 324709, compared against the same track fetched
  as a file;
* `saiz` counts the samples `trun` counts, 430 of them, `saio` points eight
  bytes past the moof where the mdat payload starts, and `trun`'s
  `data_offset` lands exactly where the IV table ends;
* the PSSH is byte-identical to the one YouTube publishes for that
  Representation;
* the same credential decrypts the same title over DASH.

The difference was the *other* track. Audio never gets a CDM session of its
own: ISA opens one for the video key, finds it needs the secure path, and
the audio track reuses that session. The bridge was picking itag 810 -- AV1
at 1080p, carrying the SD tier's key -- because it sorted candidates on
bitrate alone, and YouTube's own manifest for the title contains no AV1 at
all: twelve Representations, every one `avc1`.

Fields 16 and 17 are a *set* the server picks from, not a ranked list.
Preferring H.264 in the ordering changed nothing; the server chose 810
again. Offering H.264 alone got itag 223, and audio decrypted -- 31 seconds
and counting, no `kDecryptError`, no stall, where every previous run stopped
at 9.9.

Honest caveat: the signature timestamp fix landed in the same build, so the
two are not fully separated. What is not confounded is that audio failed
this way for many runs while media was flowing normally, and that the CDM
session is opened for the same KID either way -- `4d3521e2`, the SD key --
so it is the codec that changed, not the key or the session.


## The proof-of-origin token can be minted, and has to be

Every media url wants a `pot`, and it is bound to the `visitorData` that
minted it and lives hours. Pasting one from a browser capture worked exactly
as long as the capture was fresh -- a token five minutes old played, one from
half an hour earlier did not -- and when it lapsed it took *both* playback
paths down at once, which is a failure that reads like anything but an
expired token.

It can be minted, with nothing but the JavaScript runtime the addon already
needs for `n`:

    POST jnn-pa.googleapis.com/$rpc/…/Waa/Create  [requestKey]
         -> [null, "<scrambled challenge>"]

The second element is scrambled, not a program: base64-decode and add 97 to
every byte and it becomes JSON carrying BotGuard's interpreter, the real
program, the global name and the hash. Run the interpreter -- it registers
`globalThis[globalName]`, `trayride` in every capture -- then

    vm.a(program, setup, true, undefined, telemetry, [[],[]], undefined,
         false, loggers)
    asyncSnapshotFunction(cb, [binding, signedTimestamp, signalOutput, skip])
                                              -> "$…"
    POST …/Waa/GenerateIT [requestKey, "$…"]  -> [null, 43200, null, "<token>"]

and that token is the `pot`. There is no separate minting step: the
`signalOutput` array stays empty because nothing is meant to fill it.

BotGuard checks for a browser before it will run, but not a very convincing
one -- about a hundred and twenty lines of `document`, `navigator`,
`location`, `screen` and storage, assembled by running the VM and adding only
what it asked for, one error at a time. No jsdom. The snapshot it produced
matched a real browser's byte for byte after the leading nonce.

Two things will bite. The snapshot fails about one run in three with `E:v is
not a function`, and GenerateIT answers a failed snapshot with a token
regardless -- so a broken run is indistinguishable from a good one unless the
response is checked for its leading `$`. And the interpreter is not fixed:
its hash, its size and the name of its program export all change between
challenges, so none of it can be pinned.

Minting also repairs the pairing problem it used to cause. The token is
cached against its binding, so when Google rotates `visitorData` in an
`X-Goog-Visitor-Id` header the next lookup misses and mints a matching token
by itself, where before a rotation silently unpaired a pasted one.

## Adaptive switching on the bridge cannot be done, and here is the proof

DASH addresses media by Representation. SABR lets the server choose. Both
cannot be true at once, and two different attempts to make them true both
failed on the same box against the same title.

**Narrowing the offered set.** Fields 16 and 17 are what the client can
play; offer only the wanted itag and the server has nothing else to pick.
The endpoint answers `sabr.no_video_selected`. Measured twice, the second
time with every key id in place and the manifest naming nine renditions --
so it is the shape of the request the endpoint objects to, not the
rendition.

**Moving the height cap instead.** `ClientAbrState` field 59 is a height
cap and the browser sends 1080 in it, so: name every rendition in the
manifest, turn the itag ISA fetches into a cap, leave the whole set on
offer. ISA fetched itag 224, the cap moved to 720 -- and the server went on
serving 223, because a cap is a constraint and not a request. `/sabr/segment`
then had no bytes for the itag ISA had asked for:

    the player fetched itag 224 (720p), so the abr state asks for 720p
    ISA asked itag 224 for 1 -> 0 bytes
    Download failed, HTTP error 503   (x3)
    CreateStreamReader: No MOOV atom in stream
    OpenStream - Unsupported stream 1001. Stream disabled.

-- and the video track was gone for the rest of playback. Audio carried on
alone for forty-five segments.

So the manifest names exactly the renditions the session holds, which is
one per track, and that is not a limitation to be worked around: a
Representation the bridge cannot serve does not degrade to a lower quality,
it removes the track.

### The bridge was stuck at 480p because the request named no height

A capture of the browser's own quality selector -- the first one carrying
the POST bodies -- decodes to this:

    ClientAbrState 16 / 21   video itags offered
    720 / 720                [812, 811, 552]   all 1280x720, HD tier
    480 / 480                [810, 809, 551]   all  854x480
    360 / 360                [550]             one format
    1080 / 1080              [814, 813, 553]   all 1920x1080, HD tier

Fields 16 and 21 are the height being asked for. Field 59, which this
addon did send, is the ceiling -- the browser holds it at 1080 throughout
while 16 and 21 move between 360 and 1080.

This addon sent no 16 at all and a hardcoded `21 = 0`. So every request it
ever made asked for no particular height, and the endpoint answered with
480p or, when the offer contained nothing at 480p, `sabr.no_video_selected`.

That retires three conclusions recorded here, each of which fitted the
evidence available at the time:

* **"A single video format is refused."** The browser offers exactly one
  (itag 550, at 360/360) and is served.
* **"An offer of three HD renditions is refused, so it is the tier."** The
  browser offers exactly three (814, 813, 553, at 1080/1080) and is served.
* **"initialAuthorizedDrmTrackTypes caps the bridge at the SD tier."** The
  browser's session is authorised `AUDIO,SD` like every other, and is
  served HD-tier renditions. The field does not describe what the endpoint
  will serve.

  It does not describe what the *licence* will grant either, which took
  longer to act on. The addon kept using it as the resolution ceiling on
  the first play of a title, before any licence had been recorded, so every
  title's first play was 480p. Measured across three titles and both
  credentials, the hint read `AUDIO,SD` every time and the licence that
  followed granted `AUDIO, SD, HD, UHD1` every time. It is no longer
  consulted: with no licence recorded there is no ceiling, and the quality
  setting is the only limit. Once a licence has been seen, its grant is the
  ceiling as before -- an SD-only licence still caps at 480p.

  **One title has since matched the hint**, and the claim above needs
  narrowing rather than keeping. A *purchased* film played from the Library
  on 2026-08-29 20:14 hinted `AUDIO,SD` and its licence granted exactly
  `AUDIO,SD` -- two formats, where every subscription title's licence has
  granted four:

      play joldJiP04hk: live=False authorized=DRM_TRACK_TYPE_AUDIO,DRM_TRACK_TYPE_SD
      licence granted: 697 bytes, 2 formats [DRM_TRACK_TYPE_SD=…, DRM_TRACK_TYPE_AUDIO=…]

  So the hint is not always wrong; it is unreliable, and nothing in the
  player response distinguishes the two cases before the licence arrives.
  Treating it as a ceiling would still cost every subscription title its HD
  stream in order to protect the occasional purchased one, so the behaviour
  stands. The bridge offered 1080p (itag 227, `DRM_TRACK_TYPE_HD`) for a
  licence that granted only an SD key; that play was stopped after five
  seconds, so whether it decrypted is **not established**. The second play
  of the same title is capped at 480p by the recorded licence regardless.

The correlation behind all three was real but backwards: every offer that
was accepted happened to contain 480p renditions, because 480p is what a
request naming no height gets. Nothing was being refused for its tier or
its size.

Naming the height turned out to be necessary and not sufficient: 1080p
and 720p were still refused. Two more fields settle it, and a matrix run
in one playback says so plainly:

    avc1 at 1080p, height named                     REFUSED
    avc1 at 1080p, + fields 72 and 79               SERVED itag 146
    av01 at 1080p, + fields 72 and 79               SERVED itag 814
    vp9  at 1080p, + fields 72 and 79               SERVED itag 360
    ... plus a bandwidth in field 23                byte for byte the same

Field 79 is a repeated `{1: n, 2: 0}` for n = 3, 4, 2, 1 -- a capability
list. Field 72 carries the height in two slots; both are the height, since
a 1920x1080 rendition would put 1920 in one of them if either were a
width, and `viewport(1080)` reproduces the captured bytes exactly. Neither
is optional: without them the endpoint serves 480p and refuses anything
taller, whatever the codec.

So every request carries 16, 21, 72 and 79 now, and the bridge asks for
one height at a time, tallest first, stepping down on a refusal.

A post-licence probe ruled the licence out on the way: with one in hand,
an HD offer that named no height was refused exactly as before.

## The guide's info panel

An `epgAiringRenderer` carries eight keys -- `beginTimeMs`, `endTimeMs`,
`primaryText`, `navigationEndpoint`, `infoPanel`, `entitiesDvrStatus`,
`entitiesBellFollowStatus`, `trackingParams` -- and **not one of them is a
picture, a synopsis, a genre or a rating.** 0 of the 989 airings in the
2026-08-30 guide carry a `thumbnail` key at all.

All of it is one level down, in `infoPanel/epgInfoPanelRenderer`, present on
989 of the 989:

| key | holds |
| --- | --- |
| `thumbnail` | a 2560x1440 still |
| `primaryContainer` | `"KDKA+ * 2 hr * R"`, or `"Sat, Aug 29, 11:00 PM * KDKA+ * Aired Mar 1, 2025 * S38 E20 * The Hit-and-Run Homicide of Davis McClendon * TV-14"` |
| `secondaryContainer` | the synopsis |
| `tertiaryContainer` | `"Animated * Sitcom * Comedy"` |

Each container wraps its text two renderers deep --
`unpluggedBadgedTextRenderer` holding `unpluggedTextRenderer` holding the
text -- so a reader that flattens one node reads nothing from it.

The primary line comes in **two to seven** bullet-separated parts (489 of
six, 192 of five, 173 of four, 80 of three, 32 of two, 15 of one, 9 of
seven), and which parts are present varies by programme, so it must be read
part by part rather than by position. Coverage across the 989: a still on
989, a synopsis on 929, genres on 985, a rating on 738.

## What a show's page does not carry

A film's `unpluggedContentDetailsHeaderRenderer` says `"PG-13 * 2016"`. A
show's says neither: Rick and Morty's `secondaryText` is `"2013 - Present"`
and there is no rating anywhere above it. Its **episodes** carry both --
each `unpluggedCompactVideoRenderer`'s `secondaryText` reads `"Adult Swim *
TV-14 * 13d ago"`, and each has an `unpluggedTextBadgeRenderer` of `type:
"COUNTER"` holding `"24:03"`. A `type: "VIDEO_VERSION"` badge sits beside it
holding `"VOD"`, which is not a runtime.

An episode's `primaryText` is `"S9 E10 * Field of Dreams"` -- season,
number and name in one string.

A show's About carries an unlabelled `"2013 - Present"` / `"1994 - 2004"`
line where a film carries `Released 2016`.

**A show's page carries no portrait image at all.** Every thumbnail on Rick
and Morty's page, banner included, is 3840x2160, so there is nothing to put
in a poster slot.

## Android uses a different decrypter, and the desktop patch does not apply

Every finding in this document about InputStream Adaptive was made against a
`Platform: Linux x86 64-bit` box. ISA picks its Widevine decrypter by
platform, not by setting, and the two files differ in ways that matter.
Read against ISA `Omega` at v21.5.23:

**1. Android never decrypts in process.**
`src/decrypters/widevineandroid/WVCencSingleSampleDecrypter.cpp:185` returns
`SSD_SECURE_PATH | SSD_ANNEXB_REQUIRED` unconditionally and **ignores its
`media` argument**, so an audio track gets the same flags as video and
`SSD_SUPPORTS_DECODING` is never reported. The desktop decrypter
(`src/decrypters/widevine/WVCencSingleSampleDecrypter.cpp:150-215`) runs a
real trial decrypt and normally reports `SSD_SUPPORTS_DECODING |
SSD_SINGLE_DECRYPT`; for audio it sets `SSD_INVALID` rather than a secure
path when that trial fails. On Android both tracks are therefore handed to
the platform decoder as ciphertext plus a crypto descriptor.

**2. The `explicit_subsamples` patch is a no-op on Android.**
`WVCencSingleSampleDecrypter.cpp:792-794` already does it internally: when
`subsampleCount` is zero it sets the count to 1, clear bytes to 0 and
encrypted bytes to the whole sample -- exactly what `mp4.explicit_subsamples`
writes into the fragment. So the fix for whole-sample audio on desktop
cannot be the fix for anything on Android, and porting it is pointless.

**3. Android shares one CDM session across every stream.**
`WVCencSingleSampleDecrypter.cpp:178` returns `true` from `HasLicenseKey`
without looking at the key id, and `Session.cpp:517` uses that to hand a
later stream the earlier stream's decrypter. The comment in ISA's own source
says returning `false` "fixes pixaltion issues on some devices when manifest
has multiple encrypted streams". YouTube TV gives audio and video **different
key ids** -- a real session logged `key ids {146: 'd29e68442f8d5df6', 381:
'3b60e66196425c9d'}` -- so this is the one place where a track's key
identity is deliberately not checked.

Which of these actually bites has not been established: no Android kodi.log
exists. The addon now logs its platform and ISA version beside the session
id so the next one says outright which decrypter ran.

## No JavaScript runtime, anywhere in the addon

Two things needed one, and neither does now.

**`n`** was ported to the bundled Python interpreter and is solved there
first, with a runtime only as a fallback that never fires.

**The proof-of-origin token** needs BotGuard's VM run, and that VM cannot be
ported: it is 63 KB of obfuscated bytecode interpreter that arrives fresh
with every challenge, different hash, size and export name each time. But it
does not need a JavaScript *runtime*, only a JavaScript *engine*, and js2py
is one written in Python. The addon vendors it (952 KB trimmed of babel and
the npm importer, which this path never takes) and runs the VM inside it.

The snapshot it produces is byte for byte the one V8 produces from the same
challenge -- checked by running both against one cached challenge with the
randomness pinned and diffing. GenerateIT answers it with an ordinary token,
43200s ttl.

Four corrections to js2py were needed and are applied at run time by
`lib/js2py_fixes.py` rather than by forking it: `Date.now()` returned a Date
object rather than a number, `eval` took its scope from a fixed stack depth
where an indirect eval has to run in global scope, `Math.round` rounded half
to even, and property enumeration order was alphabetical rather than
integer-keys-first. `tools/js2py/README.md` has how each was found; two of
them only the differential tracer could have found.

js2py itself only runs on Python 3.11 and older as released: it splices
arguments into its native methods by rewriting CPython bytecode, and 3.12
raises from the remapping while 3.13 refuses at import. The vendored copy
replaces that with a wrapper built by exec -- same signature, no bytecode --
and `js2py_fixes` gives the translator a `randrange` that still takes a
float. Checked on 3.10, 3.11, 3.12 and 3.13: all four run the VM to a
snapshot.

Running the VM costs a few seconds, so the service mints one at start and
playback finds it waiting. If it has not landed yet, playback cold starts --
pure arithmetic, instant, good for thirty minutes rather than twelve hours --
and the minted token replaces it when it arrives. Neither path asks for
anything to be installed.
