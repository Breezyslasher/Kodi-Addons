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

## The minter does not exist any more

`signalOutput` comes back empty because nothing is meant to be written into
it: **what GenerateIT returns is the token**. Verified by playing a recording
with a token minted here and nothing from a browser anywhere in the loop --
no 403s, no decrypt errors, fifty seconds clean.

So the local mint step in older descriptions of this flow is gone. Everything
probed looking for it -- the signal array in every snapshot slot, both slots
of the sixth argument to `vm.a`, the program export (`FrQ_` today, `grQ_` in
the capture, which does hand back a function that answers with another
snapshot rather than a token) -- was hunting a step this API no longer has.

The response moved with it: a capture from earlier the same day answered
`["<token>", 43200, 100]`, token at index 0 in standard base64, and it now
answers `[null, 43200, null, "<token>"]` at index 3, websafe. Take the first
string rather than a fixed index.

## Two traps

The interpreter is not fixed. A fresh challenge gave hash `Fg54iyAt...`
against the capture's `Gwp_J7rW...`, 63605 bytes against 63019, and the
program export renamed following the program's first three characters.
Nothing here can be pinned; fetch it per challenge.

Node 19 puts `crypto` on the global and node 18 does not, and BotGuard calls
`getRandomValues`: without the shim's fallback the snapshot returns the
*string* `"E:v is not a function"`, and GenerateIT answers that with a token
anyway. So a broken run looks exactly like a working one unless the response
is checked for its leading `$`. It fails about one run in three regardless,
which is why the addon retries.

## Can this be pure Python? Measured, not guessed

A pure-Python JavaScript engine exists -- js2py, ES5.1, no native code, so it
would install on LibreELEC where node will not. It gets a long way:

* it parses and runs the 63 KB obfuscated interpreter (0.7 s),
* `globalThis.trayride` is registered,
* `vm.a(program, setup, ...)` completes its setup (2.4 s),
* `asyncSnapshotFunction` returns.

It returns a failure, every time, and the shim is not the reason. The same
ES5 shim the js2py driver uses was run under node against the same endpoint
and minted a `$...` snapshot, so the fake browser is adequate; the
interpreter is the difference.

The difference is `Function.prototype.toString`. js2py discards the source
text when it translates JS to Python, so every function -- the VM's own
included -- answers

    function name(args) { [python code] }

where V8 answers the real source, and native functions answer
`{ [native code] }`. BotGuard reads that.

The control run, back to back against the same endpoint:

    unblinded                         $duk56bNRAAY6jxFaGo_eWOHFqwkr..   works
    Function.prototype.toString
      blinded to "{ [python code] }"  E:...                            fails

That is V8 -- everything else identical, the same shim, the same program --
failing the moment it can no longer show a function its own source. So this
is not a shim gap that can be filled one error at a time. It is structural:
an engine that cannot hand back the source it was given cannot pass BotGuard,
and js2py throws that source away at translation time.

Patching js2py to carry each function's source through the translator is the
only route left, and it is a real change to a vendored, unmaintained library
-- against which the cold-start token already plays, in pure Python, with
nothing vendored at all.
