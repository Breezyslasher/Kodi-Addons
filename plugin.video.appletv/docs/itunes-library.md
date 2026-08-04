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

The untested variable is **identity**. That request carried an Apple TV+
developer token and whatever media-user-token the addon held, which on the
machine tested was none — so Apple was asked to license a purchase without
being told who was asking. Whether a signed-in account changes the answer is
the next thing to find out, and the licence proxy now logs which credentials
it sent so the next refusal can be read against them.

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
more than one edition. Titles then come from
`client-api.itunes.apple.com/.../MZStorePlatform.woa/wa/lookup?id=<ids>`,
asked with `p=redownload-image`, which is the profile the library view uses;
`p=lockup` is what store browsing sends.

**`spDsid` says whose purchases to list, and that is how family sharing
works.** One capture makes it plain: signed in as one account throughout,

| spDsid | items |
|--------|-------|
| the signed-in account's own dsid | 0 |
| a family member's dsid | 511 music, 94 films, 15 other |

So the account in that capture owns one film of its own -- which is exactly
what the DAAP call returned -- and sees 94 through the family. Apple's TV app
offers no such switch in its sidebar, which is why family purchases never
appear there; the limitation is that client's, not the API's.

This route is easier to consume than DMAP and is the one to implement if the
authentication below is ever solved. It is what the addon uses when a store
session has been pasted in.

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
