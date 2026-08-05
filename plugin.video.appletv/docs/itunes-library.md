# The iTunes library

Apple TV+ and iTunes purchases are two different services behind one app, and
this addon speaks the first fluently. Purchases were the one thing it could
never reach: **tv.apple.com does not expose them at all**, so no amount of
browser capture would have found them.

They were found instead in Apple's **Windows** client, whose traffic decrypts
with an ordinary proxy — no root, no device patching, no certificate fight.

## The chain

Four calls, in order. All observed; none guessed.

### 1. Sign in to the store

```
POST buy.itunes.apple.com/WebObjects/MZFinance.woa/wa/authenticate
     Content-Type: application/x-apple-plist

     appleId, password, guid, createSession=true, rmp=0
  →  passwordToken, clearToken, altDsid, dsPersonId,
     accountInfo, subscriptionStatus, accountFlags
```

Note what this is **not**: the web sign-in this addon already implements does
`authorize → SRP → two-factor → trust`. This endpoint takes the password
directly, in a plist, with a machine `guid`. Two separate services; a session
for one is not a session for the other.

`status` in the response body says whether it worked — a refusal still comes
back as HTTP 200.

### 2. List what the account owns

```
POST pd.itunes.apple.com/WebObjects/MZPurchaseDaap.woa/purchase/databases/101/items
  →  gzipped DMAP  (application/x-dmap-tagged)
```

DMAP is the old iTunes sharing format: a four-character tag, a big-endian
length, then that many bytes; some tags are containers holding more of the
same. There is a content-code dictionary that says which tags are strings and
which are integers — this does not fetch it, and guesses from the payload
instead, which is enough for the tags below.

```
adbs
  mstt  status          200
  mtco  total count       1
  mrco  returned count    1
  mlcl
    mlit                    one per owned title
```

Per title, the fields that matter:

| Tag | Meaning |
|-----|---------|
| `minm` | title |
| `ajci` | **UTS content id** (`umc.cmc.*`) — hands straight to the rest of the addon |
| `aeSI` | adam id — also the key the resume store uses |
| `ajhu` | an HLS playlist url, already carrying its token |
| `asyr` `asgn` `ascn` `aslc` | year, genre, short and long synopsis |
| `aeCR` | certificate, as `mpaa\|PG\|200\|reason` |
| `aecv` `aeat` `aeCa` | artwork |
| `assn` `asal` | show and collection titles |
| `ajEU` | iTunes Extras, when the title has any |

`ajci` is the important one: a purchase is a normal catalogue item from there
on, so it opens, describes and plays through paths that already exist.

### 3. Resume positions

```
POST upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/getAll
     domain = com.apple.upp
  →  values[]:  key = adam id
                value = raw-deflate → binary plist
```

The inner plist is the state:

```
bktm   position in seconds        4993.663
hbpl   has been played            true / false
plct   play count
tstm   timestamp, Apple epoch (2001-01-01)
```

Purchases do **not** appear in the now-playing service Apple TV+ reports to.
This is a separate key-value store, and the whole domain comes back in one
request rather than one per title — cheaper than anything on the TV+ side.

Only titles with playback state appear here. A film owned and never opened is
absent, so this is a viewing history, not a second way to list the library.

### 4. Mark watched, or save a position

```
POST upp.itunes.apple.com/WebObjects/MZBookkeeper.woa/wa/put
     domain = com.apple.upp, key = adam id, value = <same encoding>
  →  version, domain-version   (both increment, ordering concurrent writes)
```

Marking watched and unwatched differ only in `hbpl` and `plct`.

## Family sharing is not in the library

The DAAP listing holds what **this account bought**. A film shared by a family
member is absent from it, which matches Apple's own Windows client: it does
not show family purchases in its sidebar either.

Those titles are still reachable, and by an easier route. Searching for one
and opening it returns a playable on the iTunes channel:

```
playable  tvs.sbd.9001:1324419603:8804f8d9
   channelId          tvs.sbd.9001
   isItunes           True
   isEntitledToPlay   True
   entitlementReason  Unknown        (a purchase reads "Purchase" here)
```

`isEntitledToPlay` says the account may watch it, and that much is readable
with no store session at all. It does **not** hand over a stream:

```
playables: tvs.sbd.9001:1324419603:8804f8d9
           isEntitledToPlay = True
           assets           = {}        <- empty
```

An Apple TV+ playable carries `assets.hlsUrl` on this same response, which is
how the addon resolves playback. An iTunes one carries nothing. The stream for
a purchase comes from the redownload below, which needs the store session --
so entitlement is visible without signing in, and playback is not.

## Getting a purchase back

There is no endpoint called "redownload". The cloud-download button is an
ordinary purchase with the price set to zero:

```
POST p18-buy.itunes.apple.com/WebObjects/MZBuy.woa/wa/buyProduct
     Content-Type: application/x-apple-plist

     salableAdamId, productType=V, pricingParameters=STDRDL, price=0,
     ownerDsid, guid, kbsync, machineName, supportsGpuContentProtection
  →  status=0, authorized, keybag, songList[]
```

`ownerDsid` takes a **family member's** dsid, and the capture this was read
from redownloads a film the signed-in account does not own itself. So family
sharing is not a listing quirk — shared titles are genuinely fetchable.

Each `songList` entry carries two routes and a description:

| key | what it is |
|-----|------------|
| `URL` | a progressive `.m4v`, with an `accessKey` in the query string |
| `hls-playlist-url` | `play-edge…/MZPlayLocal.woa/hls/playlist.m3u8?a=<adam id>&…` |
| `hls-key-server-url` | `play-edge…/MZPlayLocal.woa/wa/fpsRequest` |
| `hls-key-cert-url` | `s.mzstatic.com/skdtool_2021_certbundle.bin` |
| `sinfs` | FairPlay key blobs, wrapped to the device that asked |
| `metadata` | the fullest description the store gives of a title |
| `has-4k` `has-hdr` `has-dolby-vision` | what editions exist |

The progressive file needs no cookies — its access key is the whole
authorisation — but it is FairPlay encrypted, and the `sinf` boxes bind the
keys to the requesting device. It also carries the account holder's real name
in cleartext, which is worth knowing before sharing one.

**The HLS route offers Widevine, and it was tested.** The suspicion was that
a purchase would be FairPlay-only, since the key certificate Apple hands the
Windows client is the FairPlay bundle, which Kodi cannot use. That is not
what the playlist serves. Pasted into this addon's manifest override and
played in Kodi:

```
[plugin.video.appletv] Collected 2 Widevine key(s)
[plugin.video.appletv] DRM property set (pre_init=True)
[plugin.video.appletv] Manifest proxy: master served, 24 variant(s) dropped
inputstream.adaptive: Manifest successfully parsed (Streams: 3)
```

Two things that matters for. The licence host is `MZPlayLocal/fpsRequest` —
the very one this addon already proxies Widevine through for Apple TV+, so
the machinery needs nothing new. And **the playlist itself needs no store
session**: it was fetched with no cookies, no `X-Token` and no dsid beyond
the one already in its query string. The FairPlay certificate is what
Apple's own client asked for, not the only thing on offer.

### The licence is requested, and refused

Run again on a machine with a Widevine CDM, the exchange completes and Apple
answers:

```
Manifest proxy: variant served, 1 KEYID added, 1 init segment(s) routed
Init segment …_video_gr205_sdr_470x352: patched 1 tenc KID(s)
  -> 0000000021ca6c756330202020202020
License request: challenge=1708 bytes, wants KID=0000000021ca6c756330…,
  known KIDs=[…6330…, …6336…]
fpsRequest failed: no licence in response
  {"streaming-response":{"streaming-keys":[{"id":1,"status":-1020}]}}
```

Worth reading carefully, because it is not a generic failure:

- Apple **accepted and parsed** the challenge. The reply is a well-formed
  `streaming-response`, not an HTTP error or an auth rejection.
- The key id is **Apple's own**, read out of the PSSH in its manifest, and
  the challenge asked for one this addon had already seen. Nothing was
  invented or mismatched.
- The refusal is per key: `status: -1020` on key `id: 1`. What that number
  means is not documented anywhere reachable and is not guessed at here.

Signing in to Apple TV+ and repeating it changes nothing:

```
License identity: bearer=yes, media-user-token=yes, adamId=252260696
… status: -1020
```

**An Apple TV+ identity does not authorise a purchase.** That is a tested
result, not an inference.

### What Apple's own client sends instead

Captures of the Windows client streaming a purchase — the `hls/playlist.m3u8`
with `?a=<adamId>` form, not the `subscription/` one — carry its licence
request, and it is a different animal:

```
POST play-edge…/MZPlayLocal.woa/wa/fpsRequest
     X-Dsid: <dsid>          X-Token: AwIAAAEC…
     Cookie: <store session>
     User-Agent: AMPLibraryAgent/1.6.4 …
     X-Apple-Store-Front: 143441-1,42

{"fairplay-streaming-request":{
   "streaming-keys":[{"guid":"16D0454F…","id":"1","adamId":"387548805",
                      "uri":"skd://itunes.apple.com/p1136803310/c1","spc":"…"}],
   "kbsync":"…","dsid":"…","version":1}}

→ {"fairplay-streaming-response":{"streaming-keys":[
     {"id":"1","status":"0","ckc":"…","renew-after":"780"}]}}
```

Four differences, all of them load-bearing:

| | Apple TV+ (works today) | a purchase |
|---|---|---|
| authorises with | Bearer + media-user-token | `X-Dsid` + `X-Token` + store cookies |
| user agent | the web player's | `AMPLibraryAgent` |
| key system | Widevine (`challenge` → `license`) | FairPlay (`spc` → `ckc`) |
| extra | — | `kbsync`, a device keybag |

So the licence is gated by the **store** session, the same door the sign-in
and the locker are behind — and Apple's client asks for FairPlay even though
the manifest carries Widevine keys, which this addon collected two of.

The addon now sends the store shape when a store session has been pasted in
and the stream came from the override: store headers, the library agent's
user agent, and the `guid` and `dsid` at the levels Apple puts them. What it
cannot send is `kbsync`.

## The stream is in the detail response after all

That whole FairPlay detour turns out to be Apple's Windows *iTunes* client,
not its TV app — and it is not how a purchase has to be played. Apple TV on
Android has no FairPlay at all and plays purchases, so a Widevine path
exists. Finding it needed one more look at captures already in hand.

**A purchase's stream is not in `assets`.** That field is empty on every
iTunes playable, whatever the caller. It is in `itunesMediaApiData`, which
the same `/movies/{id}` detail request the addon already makes returns, split
into two lists:

```
data.playables["tvs.sbd.9001:<adamId>:8804f8d9"].itunesMediaApiData
   offers[]              kind=buy | rent    price=19990   ← everyone gets these
   personalizedOffers[]  kind=redownload    price=0       ← only the store caller
      hlsUrl  …/hls/playlist.m3u8?cc=US&a=<adamId>&id=<programId>&aec=SD
              &l=en&dsid=<dsid>
```

`personalizedOffers` is the account's own copy, and its `hlsUrl` is the exact
url Apple's TV app then played. The difference is the caller:

| | `caller=web` on tv.apple.com | `caller=wlk` on uts-api.itunes.apple.com |
|---|---|---|
| `itunesMediaApiData` | yes | yes |
| `offers` (buy/rent) | yes | yes |
| **`personalizedOffers`** | **no** | **yes** |

So the website is not refused a purchase — it is never offered one. That is
why tv.apple.com cannot play what an Apple TV app on any other device can,
and why every earlier reading of "Apple sends no stream for these" was
looking at the wrong field on the wrong caller.

The addon now falls back to that endpoint whenever an iTunes playable comes
back with no assets, and plays the redownload offer if Apple names one.

### Where this stands, tested

Everything up to the licence works, with **no store session at all** — the
Apple TV+ bearer and media-user-token are enough:

```
iTunes playable tvs.sbd.9001:1324419603:8804f8d9 carries no assets;
  asking the store app's endpoint for a redownload offer
iTunes redownload offer found: redownload SD
iTunes key-server parameters: {adamId 1324419603, isExternal True,
                               svcId tvs.vds.9023}
Collected 3 Widevine key(s)
Manifest parsed (Streams: 30)          ← a real feature, 138 variants
Init segment P544700800_A1324419603_FF_video_…
License request: … adamId=1324419603, svcId=tvs.vds.9023
  → status -1020
```

`personalizedOffers` also turns out to be a dependable **ownership test**.
Asked for a title nobody in the family owns it returns nothing, and asked for
the family-shared one it returns the redownload — so "do I own this" is
answerable without the locker, and therefore without a store session.

What is left is the licence alone. Every field that can be read off Apple's
own responses now matches what its granted requests carry: the key id out of
its PSSH, the adam id, and an svcId taken from the title's own document
rather than assumed. It is still refused.

**A pasted store session was then tried, and refused identically.** With
`X-Dsid`, `X-Token` and the store cookies on the licence request — the exact
credentials Apple's own client uses — the answer is still `-1020`. So the
refusal is not about who is asking. Both identities the account has were
offered and both were declined.

What the captures say instead is that the key system splits cleanly by
playlist, without a single exception:

| playlist | licensed with |
|---|---|
| `hls/subscription/…` — Apple TV+ | **Widevine**, in 4 captures |
| `hls/playlist.m3u8?a=…` — a purchase | **FairPlay**, in 3 captures |

Kodi has no FairPlay CDM, and cannot have one. **So an iTunes purchase does
not play in this addon**, and that is the conclusion rather than a step
towards one.

Two things keep it from being flatly impossible, both worth recording:

- The purchase manifest **carries Widevine keys** — three were collected from
  it. Apple would not provision Widevine PSSHs into a manifest nothing is
  ever meant to decrypt under Widevine, so the content is prepared for it and
  the licence decision is being made per request, not per title.
- Apple TV on **Android** plays purchases, and Android has no FairPlay. Either
  Apple issues Widevine to that client where it refuses this one, or that app
  is given a different playlist entirely. A capture from it would say which.
  That capture does not exist here: the Android TV app's native layer rejects
  any substitute CA, which is where that route ended.

Everything short of the licence works, and is worth keeping: store search
finds purchases, `personalizedOffers` says whether the account owns one,
family-shared titles resolve like any other, and the stream that comes back
is the same feature manifest Apple's own client plays.

The differences that remain, beyond the key system, are ones nothing here can
supply:

- the **store session** — `X-Dsid` and `X-Token`, which the addon will now
  send on a purchase's licence request if one has been pasted in
- **`kbsync`**, the device keybag, which is not reproducible
- **the key system itself.** Every granted Widevine licence observed is for
  an Apple TV+ title. No capture exists of Apple granting a *Widevine*
  licence for a purchase — its own clients on Windows and macOS ask in
  FairPlay. That Apple TV on Android plays purchases, and Android has no
  FairPlay, is good reason to believe the path exists; it is not the same
  thing as having seen it.

One thing capture cannot settle: Apple's client changed **two** things at
once — it asks as `wlk` *and* authenticates with `X-DSID` plus store cookies
rather than a bearer. Whether the caller alone is enough, or the store
session is needed too, only a live request answers. The addon asks with what
it holds and adds a pasted store session when one is set, and logs which of
the two happened.

### Family sharing needs nothing extra here

The title that produced that offer — The Greatest Showman, adam id
1324419603 — is **not owned by the account that asked**. Cross-referencing
the lockers settles it:

| locker | contains 1324419603 |
|---|---|
| `spDsid=12305910250` (own, 2 films) | no |
| `spDsid=210495396` (family, 93 films) | **yes** |

Yet its `personalizedOffers` came back priced at zero, marked `redownload`,
with `dsid=12305910250` — the asking account's own number — in the url.

So Apple resolves the family entitlement on its side and issues the offer to
whoever is signed in. Playing a family-shared purchase needs no `spDsid`, no
family member's number, and no separate code path: it is the same request as
playing one's own. `spDsid` and `ownerDsid` matter for *listing* a library
and for the store's own redownload call, not for this.

The obstacle is `kbsync`: a device keybag blob on the request, the same class
of thing as the attestation headers below. The addon sends the request
without it and reports what Apple says rather than assuming.

### `metadata` is the best description of a purchase

Fuller than the lookup, and the only place an owned episode says which show
and season it belongs to:

```
kind              tv-episode
itemName          High Rise Hair Raiser
show-name         The Scooby-Doo Show
playlistName      The Scooby-Doo Show, Season 1
season-number     1
episode-number    S1E1        episode-sort-id  1
network-name      CBS         trackCount       16
duration          1463505     (milliseconds)
rating            {label: TV-G, system: us-tv, rank: 300}
longDescription   …
releaseDate       1976-09-11T07:00:00Z
```

Note the naming: this block is hyphenated where the lookup is camel-cased,
and a season is `playlistName` rather than a collection. They are two
different schemas for the same thing and cannot share a mapper.

Their resume positions are in the bookkeeper -- the adam id above is one of
the five, at 3669 seconds -- but reading that store also needs the session.

## A second, simpler route: the JSON locker

iTunes for Windows lists the same library as plain JSON rather than DMAP:

```
GET se-edge.itunes.apple.com/WebObjects/MZStoreElements.woa/wa/purchases
    ?dataOnly=true&mt=6&restoreMode=false&spDsid=<dsid>
  → {"lockerData":{"content":{"1080487524":[1080487524], ...}}}
```

`mt` selects the media type, and the numbers are not what the tab names
suggest. Each was checked by looking up what the locker returned:

| mt | what comes back |
|----|-----------------|
| 1 | songs |
| 4 | **television**, as `tvEpisode` rows |
| 6 | films |
| 3 | nothing — 200 with an empty locker on every account tried |

`mt=3` was the earlier guess for television, taken from watching the client
ask for it while the TV Shows tab was open. It is wrong: that request returns
an empty locker even for the account that owns fifteen episodes, and those
fifteen arrive under `mt=4` instead.

**Television is listed as episodes, never as seasons or series.** The fifteen
owned episodes span five seasons of five different shows, and no season row
appears anywhere in the locker. Each episode does say where it belongs —
`collectionId`, `collectionName` ("Treasure Quest, Season 1"),
`episodeSeasonNumber`, `episodeNumber` — so seasons can be grouped from the
episodes without another request, which is what this addon does.

One thing television does not carry: a synopsis. Under the
`redownload-image` profile the field is absent rather than empty on both
`tvSeason` and `tvEpisode` rows, while films have `itunesNotes.standard`. The
fuller description of an episode exists, but it arrives with a redownload
rather than a lookup.

`artistName` also means two different things by kind — the director of a
film, the series of an episode — so it cannot be read without checking
`kind` first.

The values are store ids, sometimes several per title where a film exists in
more than one edition. **Not every key is an id**: `content` also holds an
`orderedKeys` entry giving the display order, and counting it as a title asks
Apple to look up an id called "orderedKeys", which it rejects -- taking the
real ids down with it, since they travel in the same batch. Only the film
lockers carry it; the music and television ones do not. Titles then come from
`client-api.itunes.apple.com/.../MZStorePlatform.woa/wa/lookup?id=<ids>`,
asked with `p=redownload-image`, which is the profile the library view uses;
`p=lockup` is what store browsing sends.

### Where the family dsids come from

There is no endpoint for this. The bag lists `commerce/family/permission/get`
and the sharing toggles, but nothing that enumerates members. The roster is
embedded in the purchases **page** instead, as JSON:

```json
{"iCloudDsid":"…","iTunesPreferredDsid":"…","accountName":"…",
 "displayName":"…","sharingPurchases":true,"isMe":false}
```

`iTunesPreferredDsid` is what `spDsid` wants and `sharingPurchases` says
whether asking will return anything. Parsing one real page gives four
members, all sharing — and two of those dsids are the ones the client was
seen asking with, which is corroboration rather than coincidence. So the
number never has to be typed in by hand, given a store session.

**`spDsid` says whose purchases to list, and that is how family sharing
works.** One capture makes it plain: signed in as one account throughout,

| spDsid | items |
|--------|-------|
| the signed-in account's own dsid | 0 |
| a family member's dsid | 511 music, 93 films, 15 episodes |

So the account in that capture owns one film of its own -- which is exactly
what the DAAP call returned -- and sees 93 through the family. Apple's TV app
offers no such switch in its sidebar, which is why family purchases never
appear there; the limitation is that client's, not the API's.

This route is easier to consume than DMAP and is the one to implement if the
authentication below is ever solved. It is what the addon uses when a store
session has been pasted in.

### The locker works; naming what is in it does not

With a pasted session the locker answers properly — the roster parses, four
members with three sharing, and the film locker returns its ids. Turning
those ids into titles is where it stops, and all three routes were tried:

| route | answer |
|---|---|
| `MZStorePlatform/lookup` (Apple's own) | **403** |
| `itunes.apple.com/lookup` (public) | **works, but not for everything** |
| `uts/v3/contents/play-metadata/vod` | **460**, content does not match condition |

#### What the token actually is

The storefront bundle is in the captures — `di6-storefront-bootstrap_modern.js`,
2.2 MB — and it contains the whole construction:

```js
_setSignedRequestQueryParams: function (url) {
  var n = Math.round(new Date().getTime() / 1e3)
  var whitelist = its.serverData.properties["SF6.StorePlatform.whitelistParams"]
  var o = ""                                  // sort the query, concatenate
  ...  u = url.split("?")[1].split("&").sort() //   the VALUES of whitelisted keys
       if (whitelist[key]) o += value
  var f = [n, iTunes.storefront, decodeURIComponent(o)].join("")
  return url + "&X-JS-SP-TOKEN=" + encodeURIComponent(
                 iTunes.signStorePlatformRequestData(f))
             + "&X-JS-TIMESTAMP=" + n
}
```

The whitelist is served alongside it: `["caller", "dsid", "id", "p"]`. So the
signed string is fully known — timestamp, storefront, then the values of those
four parameters in sorted-key order.

**What is not known is the signing.** `signStorePlatformRequestData` appears
exactly once in 2.2 MB, as a call on the native `iTunes` bridge, and is never
defined in JavaScript. It is in the client binary.

Its output is 16 bytes, which is MD5-shaped, so that was worth testing: 720
combinations of MD5, SHA-1, SHA-256, SHA-512 and BLAKE2s over six storefront
spellings, four separators, three field orders and two encodings. None
reproduce a captured token. It is keyed or proprietary, not a bare hash of a
string anyone can assemble.

That is as far as captures go. Recovering it would mean reversing the native
function or running a real client, which is a different kind of work from
anything else here.

The first is refused. `X-JS-SP-TOKEN` is the obvious suspect — 142 captured
lookups carry 142 distinct tokens across only 26 timestamps, so it signs each
request rather than the session — but that is a suspicion, not a finding. The
request was also being sent as the TV app rather than iTunes, with a plist
content type and a client header the real one does not have; those are fixed
now, and the device values Apple's client carries (`X-Apple-I-MD` and its
companions, `X-Apple-Cuid`) are still missing. Blaming the token before
accounting for those would be guessing.

**The second carries the library, and one measurement of it was badly
misleading.** Asked for a single id it answered `resultCount: 0`, which read
like a service that cannot do this at all. Asked for the real library — 94
ids across four lockers — it resolved **89**. It works; that first id is
simply not in its index, along with four others.

The id that misled was *Despicable Me*, which the addon's own search finds
without difficulty. Both facts hold at once: the public service and the UTS
catalogue are different indexes, and a title can sit in one and not the
other. A sample of one said "impossible" where a sample of ninety-four says
"mostly".

The third maps a store id to its canonical one, which would be ideal, and it
negotiates: without a duration it says `hlsAssetDurationInSeconds is
required`, and with a nominal one it says the content does not match a
condition — most likely that the duration must match the real asset, which is
not knowable before the title is resolved. Apple's client also sends a
`playablePassthrough` carrying an internal leg id that cannot be derived for
an arbitrary store id.

So the library works. It lists what the account owns and what its family
shares — 89 of 94 in the run this describes, across four lockers — logs the
handful no lookup will name, and its entries open: a title picked from it
resolves its redownload offer and reaches the licence exactly as one picked
from search does.

Sixteen ids across the two lockers resolve through neither public route --
five films and one complete season of eleven episodes. That is **not**
because Apple has dropped them. Its own lookup names every one of them:

```
387548805   Despicable Me            302596216  The Merchant Royal
1538303219  Greenland                303292916  Pirates!
1140582200  Kubo and the Two Strings 303885799  The Legend
779516213   Last Vegas               …           Treasure Quest, Season 1
826848074   Mr. Peabody & Sherman       (all eleven episodes)
```

That endpoint is the one refused with a 403, and its profile name says what
it is for: `redownload-image-tracklist-item`. It is the **library** service,
and it describes what an account owns whether or not the title is still
published. The public lookup and the catalogue describe what is currently on
sale, which is a different question and a smaller set.

So this is one blocker wearing two faces, not two problems. An iPhone lists
these titles because Apple's client can call that service; this cannot,
because each call is signed per request.

Which is why the five films could be rescued and the eleven episodes could
not, and the difference is about **ids, not availability**:

| | asked with a store id | answer |
|---|---|---|
| `/movies/<id>` | accepted | the five films, in full |
| `/episodes/<id>` | **400, malformed** | never gets as far as looking |

So `/movies/<episode id>` answering 404 says only *"that id is not a movie"*,
which is true and useless. It is not evidence the episode is unpublished, and
reading it that way was a mistake. Whether those episodes are in the current
catalogue is simply **not known** -- no endpoint here accepts an episode's
store id to ask with.

A season would be the way in, since an episode's lookup names its
`collectionId`. That id comes from the lookup that is refused, so it is out of
reach for exactly the titles that need it.

**A locker holds one person's purchases**, which is easy to miss and was
missed here for a while: asking only for the signed-in account returned a
single film while the family between them own ninety-odd. The roster is read
anyway, so every member who shares is asked and the ids merged.

It stops where everything else stops, at the licence. What needs no store
session at all, and is the more useful half: **search finds purchases, and
opening one says whether the account owns it** — the `personalizedOffers`
test.

## Borrowing a session instead of minting one

Since Apple's own software proves itself in ways that cannot be reproduced,
the way round is not to reproduce it. Sign in with a real Apple client, take
the session it established, and hand it to the addon: two advanced settings
accept the cookie header and, optionally, a family member's dsid.

That needs no attestation, because a genuine client already did that part.
What it costs is that the session is captured by hand and expires on Apple's
schedule rather than the addon's.

## What is verified, and what is not

Verified by replaying real captures through this code:

- the DMAP parse — one owned title, `mtco` of 1, every field resolving,
  including the UTS content id
- the resume decode — five positions recovered from a live `getAll`, with a
  finished title correctly skipped
- the resume encode — round-trips through deflate and back
- the runtime — `astm` is inside `aeif`, not on the title, which was
  established by parsing `aefl`'s payload and finding it consumes exactly as
  DMAP; the film's 5,678,891 ms comes out as its real 1:34:38

**Tried, and refused: the store sign-in.** Apple answers `403`. The guid was
the suspected obstacle; the capture shows something harder. The real request
carries three headers generated on the device:

```
X-Apple-ActionSignature   a signed blob, ~200 bytes
X-Apple-AMD               attestation data
X-Apple-AMD-M             a longer companion value
```

These are Apple's device attestation. They are computed by Apple's own client
from material it holds, and nothing here can produce them -- this is the same
family of values other projects obtain from an "anisette" provider running on
genuine Apple software. Aligning everything else that differed (the pod host
`p18-buy`, the guid in the query string as well as the body, the `com.apple.TV`
client header, the `t:tv1` storefront suffix, the TV user agent) is done, and
is worth having, but it is unlikely to be enough on its own.

This blocks more than the listing. Everything on the store side is behind the
same door:

| | needs the store session |
|---|---|
| listing what the account owns | yes |
| the stream for a purchase (`buyProduct`) | yes |
| resume positions (`MZBookkeeper`) | yes |
| **knowing a title is owned** (`isEntitledToPlay`) | **no** |
| **the file a redownload points at** (`accessKey`) | **no** |
| **the HLS playlist a redownload points at** | **no** |

Only the last is reachable, because it rides the ordinary UTS detail response.
Search does return store films -- "Green Book" comes back with 14 results
across Top Results, Movies and TV Shows -- but the results carry no
entitlement or channel at all, only id, title, type, genres, duration,
release date and artwork. Whether one is owned shows only once it is opened.

**That is true of the store app's caller, and not of the website's.** The
same search asked as tv.apple.com returns Apple TV+ originals and nothing
else: "ted" gives Ted Lasso, Shrinking, Wolfs and Ghosted, where the store
caller for "green book" gives Green Book, Oppenheimer, Hidden Figures and
BlacKkKlansman -- none of which are on Apple TV+ at all. So the caller
decides what search will admit exists, exactly as it decides whether
`personalizedOffers` is sent, and a purchase cannot be opened from a search
that will not list it. The addon asks as the store app and keeps the
website's search as a fallback.

So an iTunes title can be found, described and shown as owned. It cannot be
played, and that is unlikely to change without device attestation.

Also unverified: whether a purchased stream *decrypts* under Kodi's Widevine
CDM. It is now known that one is *offered* a Widevine key — the addon
collected two from a real purchase's playlist — and that the playlist needs
no store session. What remains untested is the licence exchange itself,
which stopped at a machine with no CDM installed.

Verified since, by replaying a redownload capture through this code:

- the redownload parse — one film's two routes, both certificate urls, and
  every edition flag
- the metadata mapper — an owned episode resolving to show, season, episode
  number, network, certificate and a 1463-second runtime
- the television grouping — fifteen locker episodes gathering into their
  five real seasons, with the right episode counts

## How the capture was taken

Worth recording, because two things made the difference after a long detour
through an Android TV emulator that ended at a pinned native layer:

1. **The Windows client does not pin.** Fiddler Classic decrypts it with its
   CA in the Windows trust store, and Store apps need
   `CheckNetIsolation.exe LoopbackExempt` — or Fiddler's WinConfig button —
   before a loopback proxy sees them at all.
2. **Rules → Performance → Disable Caching.** Without it the interesting
   canvases return `304 Not Modified` with no body, which reads exactly like
   an endpoint that does not work.

`init.itunes.apple.com/bag.xml` is Apple's own service-discovery document —
744 keys of endpoint urls, including several the client never calls. That is
where `commerce/account/purchases` and the whole `MZBookkeeper` family were
found by name. It is a far more durable source of endpoints than scraping
markup, and worth re-reading whenever something breaks.

## Security

A capture of this chain contains the account's password in the sign-in
request, its session tokens everywhere after, and — in a redownload — the
account holder's name inside the `sinf` blobs. Nothing here is ever written
into the repository: sessions live only in Kodi's settings at runtime, and
are pasted by hand rather than minted or stored by this addon. Run
`tools/sanitize_har.py` over anything before sharing it, and treat an
unsanitised capture as the account itself.
