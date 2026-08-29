# YouTube TV (tv.youtube.com) — protocol notes for a possible Kodi addon

Findings from four browser HAR captures of a signed-in YouTube TV session
(Firefox 154 / Linux, web client `WEB_UNPLUGGED`, August 2026). All identifying
values below — account IP, `sig`/`spc` signatures, visitor id, cookies, license
blobs — are redacted or truncated. Nothing here is secret: it is the shape of
the requests, which is what an addon needs.

Written to answer one question: can Kodi play YouTube TV through
InputStream Adaptive? Short answer: probably yes, and much more cleanly than
Apple TV+, but one thing is still unproven. See "The open question" below.

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

## Endpoints

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

### Search — `POST /youtubei/v1/search?alt=json`

```json
{ "query": "rick", "params": "6gMOCgASABoAIgAqADIAQgA%3D" }
```

With `POST /youtubei/v1/suggest?alt=json` behind the search box for
autocomplete. Both are cheap and behave like ordinary InnerTube search.

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
