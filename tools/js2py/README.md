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

## What is measured, and what is not

The snapshot matching V8's byte for byte is checked against one cached
challenge with the randomness pinned. Tokens minted this way are accepted
by GenerateIT and carry the usual 43200s ttl. The exchange fails outright
about one run in three regardless of engine -- it does under node too --
which is why anything calling it retries, and why one run of anything here
is never a measurement.
