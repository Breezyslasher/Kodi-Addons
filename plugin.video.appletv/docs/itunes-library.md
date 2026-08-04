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

So `isEntitledToPlay` is what grants access, not the library listing, and the
addon's ordinary playback path already prefers entitled playables. A
family-shared film should therefore play if it is found through search --
with none of the store sign-in below.

Their resume positions do come through: the adam id above is one of the five
in the bookkeeper, at 3669 seconds. The store tracks where you are in a shared
title even though it will not list it.

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

So the library **listing** is blocked at the account door, and probably stays
blocked without attestation. What is not blocked is everything reached through
the ordinary UTS API, which needs none of this -- see family sharing above:
an iTunes title found by search reports `isEntitledToPlay` and plays.

Also unverified: whether a purchased stream decrypts under Kodi's Widevine
CDM. Purchases use the same `fpsRequest` licence endpoint as Apple TV+, which
the addon already proxies, so the machinery is in place — but no purchased
title has been played through it.

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
request and its session tokens everywhere after. Run
`tools/sanitize_har.py` over anything before sharing it, and treat an
unsanitised capture as the account itself.
