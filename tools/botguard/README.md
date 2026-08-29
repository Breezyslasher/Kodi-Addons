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

The likeliest remaining explanation is staleness: every experiment above ran
against a challenge captured hours earlier, and the challenge is time-bound
(`c=1787980897&t=21600`). This script asks for a fresh one, which is the
difference worth testing next and cannot be tested from a machine without
access to Google.
