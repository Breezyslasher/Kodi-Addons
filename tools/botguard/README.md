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

## It is pure Python now

js2py -- an ES5 interpreter written in Python, no native code, so it
installs where node will not -- runs the interpreter, registers the VM,
completes `vm.a`'s setup and returns a snapshot that GenerateIT answers with
a real token. The addon ships it vendored and needs nothing installed.

Four corrections to js2py were needed, each found by running the same thing
under node and under js2py and diffing rather than by reading the spec.
`tools/js2py/README.md` has the method and the two that only the
differential tracer could have found:

* **`Date.now()` returns a Date object**, not a number. BotGuard sets its
  clock baseline from `performance.timeOrigin`, so every reading after it
  was string concatenation.
* **`eval` takes its scope from `inspect.stack()[3]`**, a fixed depth.
  BotGuard calls `(0, eval)(code)`, and an indirect eval runs in *global*
  scope; the caller's scope leaks the VM's own minified locals, so a probe
  that has to throw `O is not defined` quietly succeeds and the VM skips
  the two instructions that follow it.
* **`Math.round` rounds half to even**, Python-style, where JS rounds half
  up: `round(-1.5)` was -2 and `round(2.5)` was 2.
* **Property order**: `for-in` sorted every key alphabetically and
  `Object.keys` used insertion order, where JS puts integer-like keys first
  and the rest in insertion order, both times. `for-in` also stopped at an
  object's own properties and never walked the prototype chain.
* **`%` poisoned every number it produced.** js2py interns the small
  integers and its `%` corrected the result's sign in place, so `-7 % 3`
  wrote -1 into the shared 2 and the literal `2` evaluated to -1
  thereafter -- `1 + 1` was -1. The VM's dispatch indexes with `(n + 1) % 3`.
* **`splice` with one argument** removed nothing, and **`''.split(',')`**
  answered `[]` rather than `['']`.

Two more the shim covers rather than js2py: there is no `Symbol`, `Map`,
`Set`, `Promise`, `Reflect` or `TextEncoder` in ES5, and
`Object.getOwnPropertyNames` handed JavaScript a Python view with no length
and no array methods on it.

**`Function.prototype.toString` is not among them**, though one run said it
was. js2py answers `function name(args) { [python code] }` where V8 answers
the real source, and blinding V8's toString exactly that way looked
decisive: unblinded minted, blinded failed. It was a single run of each, and
this flow fails about one run in three on its own. Eight runs each say the
distribution is the same -- 5/8 minted unblinded, 7/8 blinded. One run is
not a measurement here, which is the same trap the leading `$` check exists
for.
