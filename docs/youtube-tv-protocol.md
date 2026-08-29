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
