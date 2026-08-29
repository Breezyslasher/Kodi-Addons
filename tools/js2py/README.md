# Running BotGuard without a JavaScript runtime

The proof-of-origin token needs BotGuard's VM run, and the VM is JavaScript
that arrives fresh with every challenge -- different hash, different size,
different export name -- so it cannot be reimplemented. What it does not
need is a JavaScript *runtime*: js2py is an ES5 interpreter written in
Python, and with `lib/js2py_fixes.py` applied it runs the VM to a snapshot
byte for byte identical to the one V8 produces from the same challenge.

These are the tools that got it there. They need node, which the addon does
not: node is the reference the Python is measured against.

## conformance.js, conformance2.js

~160 expressions covering what the VM actually uses. Run under node, run
under js2py, diff. Everything `lib/js2py_fixes.py` corrects was found here
or by the tracer.

    node -e "eval(require('fs').readFileSync('conformance.js','utf8')); console.log(__run().join('\n'))"

## trace.js, trace_node.js, trace_js2py.py

The differential tracer. It pins the randomness (a fixed LCG for
Math.random and getRandomValues) and records every call into a built-in,
so one cached challenge can be run under both engines and the traces
diffed. The first line that differs is the divergence.

Two of the four fixes were found this way and nothing else would have
found them:

* 29,392 identical calls, then `Math.floor(1787992192291)` against
  `Math.floor("Sat Aug 29 2026 08:30:08 GMT+0000")`. **`Date.now()`
  returns a Date object in js2py, not a number** -- it is bound to the
  same helper that builds a date. BotGuard sets its clock baseline from
  `performance.timeOrigin`, so every later reading was string
  concatenation instead of arithmetic.
* With the dispatch instrumented to print the VM's own program counter:
  node runs ops 1900, 1908, 1916, 1924; js2py runs 1900 then 1924. Op
  1900 has to throw `O is not defined`, and under js2py it did not --
  because js2py takes the calling scope for `eval` from
  `inspect.stack()[3]`, a fixed depth, and BotGuard calls
  `(0, eval)(code)`. An **indirect eval runs in global scope**; handing
  it the caller's scope leaks the VM's own minified locals, so `O`
  resolves and the probe quietly succeeds.

## mint_py.py

The whole exchange under js2py with a running commentary, for when
something breaks again. `LENIENT=1` makes a property read on undefined
answer undefined instead of throwing, which is occasionally useful for
seeing how much further a run would get.

## The one that would have bitten hardest

`%` poisoned every number it produced. js2py interns a `PyJsNumber` for
every integer from -1024 to 16383 and hands the same object out each time
that value is wanted; its `__mod__` built the result with `Js(a % b)` --
returning the interned object -- and then corrected the sign **in place**:

    pyres = Js(a % b)
    if a < 0 and pyres.value > 0:
        pyres.value -= abs(b)

So `-7 % 3` takes the shared `2`, writes `-1` into it, and from then on the
JavaScript literal `2` evaluates to `-1` everywhere in that context:
`1 + 1` is `-1`, `String(2)` is `"-1"`, `Math.pow(2, 53)` is `-1`. The very
next expression in the same statement already sees it, which is why
`(-7 % 3) + ',' + (7 % -3) + ',' + (5.5 % 2)` answers `-1,1,0.5` where a
browser says `-1,1,1.5`.

This is the released library's bug, not something the addon does to it, and
it is not academic: the VM's own dispatch indexes with `(n + 1) % 3`.

It was found by the conformance corpus in an unexpected way -- a case
several rows later started answering wrongly, and bisecting the prefix
named `modulo negatives` as the culprit. A case that fails on its own is
easy; a case that poisons the ones after it is what a corpus run in
sequence is for.

## What still differs, and why it is left

Ten of 161 cases, all either deliberate or out of reach:

| case | what happens |
|---|---|
| `eval sees local` | **Deliberate.** js2py cannot tell a direct eval from an indirect one, and BotGuard needs indirect semantics -- global scope -- or the probe that must throw `O is not defined` quietly succeeds. Direct eval therefore does not see the caller's locals. |
| typed arrays (3 cases) | The shim implements them in ES5 because js2py's are numpy-only and numpy is not on a Kodi box. Values are masked when the array is built; a write afterwards is not truncated, and `Object.prototype.toString` says `[object Object]`. ES5 gives no way to do better, and the alternative is a Python `NameError` that no JavaScript `try/catch` can catch. |
| `this in call`, `call with primitive this` | Sloppy-mode boxing of a primitive `this`. |
| `arguments aliasing` | Writing a parameter does not show through `arguments[0]`. |
| `sparse forEach` | Visits holes. |
| `new on bound` | `new (f.bind(...))` throws. |
| `+'0b11'` | js2py is ES5-correct here and node is not: binary literals in `Number()` are ES6. |

## What is measured, and what is not

The snapshot matching V8's byte for byte is checked against one cached
challenge with the randomness pinned. Tokens minted this way are accepted
by GenerateIT and carry the usual 43200s ttl. The exchange fails outright
about one run in three regardless of engine -- it does under node too --
which is why anything calling it retries, and why one run of anything here
is never a measurement.
