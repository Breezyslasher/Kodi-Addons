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

## Adaptive switching on the bridge: the player picks, the server obeys

SABR chooses server-side. Fields 16 and 17 of a `VideoPlaybackAbrRequest`
are *sets* — every rendition the client can play — and the server picks one
out of each and names its pick in the `MEDIA_HEADER` it answers with. That
is why the bridge log has always read "the server chose audio 150, video
223": nothing in the addon chose those.

So a bridge cannot ask for a quality. It can only stop offering the others.
`Session.want(itag)` records what the player asked for and `_entries()`
offers that alone; the manifest lists every eligible rendition as a
`Representation` and each one's `media=` url carries its own `itag`, so the
itag InputStream Adaptive fetches *is* its choice, and the next exchange
offers nothing else.

Three constraints, each of which cost a rendition:

* **Same AdaptationSet, same decoder.** `siblings()` keeps only renditions
  with the same container and the same codec family as the served one, so
  AV1 never lands beside H.264 — a switch across that boundary is one the
  decoder cannot carry through, and the AV1 rendition is the one that would
  not decrypt anyway.
* **One timeline for the set.** `startNumber` and `duration` are taken from
  the served rendition and used for every Representation in its set. There
  is nothing else to take them from — a rendition the server has not served
  yet holds no segments — and there should be nothing else: renditions of
  one track are cut at the same instants, which is what `segmentAlignment`
  claims.
* **A Representation with no key id is a manifest ISA refuses whole**, not
  a rendition it skips. A rendition is only listed once its key can be
  named: read out of its own `tenc`, which needs the server to have served
  it, or out of a licence this title has already been granted. On a fresh
  protected title that leaves the served rendition alone in its set, and
  the alternatives appear on a later play.

The setting is off by default. A narrowed set was answered
`sabr.no_video_selected` once, and if the endpoint refuses one again the
session clears `wanted`, sets `narrowing = False` and offers the whole set
for the rest of playback rather than refusing once per segment.

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

Running the VM costs a few seconds, so the service mints one at start and
playback finds it waiting. If it has not landed yet, playback cold starts --
pure arithmetic, instant, good for thirty minutes rather than twelve hours --
and the minted token replaces it when it arrives. Neither path asks for
anything to be installed.
