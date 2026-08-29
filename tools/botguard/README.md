# Minting a proof-of-origin token

`mint.js` does the whole flow with nothing but node -- no npm packages, no
jsdom -- and prints a token:

    node mint.js <visitorData>      # session-bound
    node mint.js <videoId>          # content-bound

## What it does, read out of a browser capture

    POST jnn-pa.googleapis.com/$rpc/…/Waa/Create  [requestKey]
         -> [null, "<scrambled challenge>"]

The second element is **scrambled, not a program**: base64-decode it and add
97 to every byte and it becomes JSON --

    [messageId, wrappedScript, wrappedUrl, interpreterHash, program,
     globalName, , clientExperimentsStateBlob]

`wrappedScript` carries the BotGuard interpreter itself, or `wrappedUrl`
names it (`//www.google.com/js/th/<hash>.js`). Running it registers
`globalThis[globalName]` -- `trayride` in every capture so far. Then:

    vm.a(program, setup, true, undefined, telemetry, [[],[]], undefined,
         false, loggers)                       -> asyncSnapshotFunction, …
    asyncSnapshotFunction(cb, [binding, signedTimestamp, signalOutput, skip])
                                               -> "$…"
    POST …/Waa/GenerateIT [requestKey, "$…"]   -> ["<integrity token>", 43200, …]
    signalOutput[0](integrityTokenBytes)       -> mint
    mint(bindingBytes)                         -> the token

## What is verified

The interpreter runs in **bare node** with `shim.js`, a hand-built browser of
about a hundred and twenty lines -- document, navigator, location, screen,
storage -- assembled by running the VM and adding only what it asked for, one
error at a time. It needs no jsdom.

The snapshot produces a real BotGuard response. Against the one the browser
sent in the same capture:

    browser:  $7XI5chZR AAY6jxFaGo_eioUnUO0tRAqnAEABEArZ1KtzvuM3-LYVW99…
    ours:     $qTY5NmxR AAY6jxFaGo_eioUnUO0tRAqnAEABEArZ1KtzvuM3-LYVW99…

Only the leading nonce differs; the body is byte-identical, so the shim's
environment fingerprint matches what a real browser reports.

## What is not solved

`signalOutput` comes back empty, so there is no minter and the last two steps
are untested. Tried and ruled out: the signal array at every index of the
snapshot arguments, both slots of the sixth argument to `vm.a`, both values of
`skipPrivacyBuffer`, the program-specific `grQ_` export (it does return a
function for an integrity token, but that function mints nothing), and the
att/get challenge as an alternative to Create's.

Staleness was the theory and it was wrong. `jnn-pa.googleapis.com` turns out
to be reachable where `tv.youtube.com` is not, so the whole flow now runs
against a **fresh** challenge -- new interpreter hash, new program -- and the
signal output is still empty. What did change is the API:

* the capture answered `["<token>", 43200, 100]`, token at index 0, standard
  base64; today it answers `[null, 43200, null, "<token>"]`, index 3, websafe;
* the interpreter is not fixed -- a fresh challenge gave hash `Fg54iyAt…`
  against the capture's `Gwp_J7rW…`, 63605 bytes against 63019, and the
  program-specific export renamed from `grQ_` to `FrQ_`, following the
  program's first three characters. Nothing here can be pinned.

Two traps worth knowing. Node 19 puts `crypto` on the global and Node 18 does
not, and BotGuard calls `getRandomValues`: without the shim's fallback the
snapshot returns the *string* `"E:v is not a function"`. And GenerateIT
answers a failed snapshot with a token anyway, so that failure is silent
unless the response is checked for its leading `$` -- which `mint.js` now
does, refusing to hand back a token no one should trust.

The open question is whether the 103-character token GenerateIT returns is
itself usable as a proof-of-origin token. It carries the same `Mk`/`Ml`
prefix as every token seen in a capture. If it is, the minter is not needed
at all.

## Where the minter is not

Ruled out against a **fresh** challenge, with a **fresh** integrity token:

* the signal array in every slot of the snapshot arguments, and in both slots
  of the sixth argument to `vm.a` -- nothing is ever written into any of them;
* the program-specific export, `FrQ_` today and `grQ_` in the capture. It does
  take the integrity token and call back with a function, which looks exactly
  like the minter, and that function called as `f(callback, identifier)`
  answers with a 2100-character string beginning `$`. That is another
  snapshot, not a token.

So the only thing shaped like a proof-of-origin token is what GenerateIT
returns: 103 characters, websafe, beginning `Mk` -- the same prefix as every
token seen in a browser capture, though those were 114 characters.

## The snapshot fails about one run in three

Nondeterministically, on node 18 and node 22 alike, with `E:v is not a
function` or `E:O is not a function`. The shim is missing something the VM
reaches for only on some paths. Whatever finally mints a token needs to check
for the leading `$` and retry, which `mint.js` half does -- it refuses a bad
snapshot but does not yet retry.
