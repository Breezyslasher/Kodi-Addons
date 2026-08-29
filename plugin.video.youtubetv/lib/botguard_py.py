"""BotGuard's own interpreter, run in Python.

The proof-of-origin exchange is three calls to jnn-pa.googleapis.com and one
run of a 63 KB obfuscated bytecode VM that Google sends with the challenge.
The VM is JavaScript and there is no way around that -- it is different on
every challenge, hash and export name included -- but it does not need a
JavaScript *runtime*: js2py is an ES5 interpreter written in Python, and
with lib/js2py_fixes.py applied it runs the VM to a snapshot byte for byte
identical to the one V8 produces from the same challenge.

The flow, all of it measured against captures:

* `Waa/Create [requestKey]` answers `[null, "<scrambled>"]`.
* Descrambling is base64, then +97 to every byte. Out comes
  `[messageId, wrappedScript, wrappedUrl, interpreterHash, program,
    globalName, , clientExperimentsStateBlob]`.
* The interpreter registers itself as `globalThis[globalName]`; `vm.a` sets
  it up and hands back a snapshot function.
* `asyncSnapshotFunction(cb, [contentBinding, signedTimestamp,
  webPoSignalOutput, skipPrivacyBuffer])` answers a string beginning `$`.
  A failed run answers one beginning `E:` -- and GenerateIT will answer
  *that* with a worthless token, so the leading character is checked.
* `GenerateIT [requestKey, "$..."]` answers the token itself. There is no
  local minting step in this version of the API.
"""
import base64
import json
import os
import sys
import time

from . import kodiutils

REQUEST_KEY = "O43z0dpjhgX20SCx4KAo"
ENDPOINT = "https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa"
API_KEY = "AIzaSyDyT5W0Jh49F30Pqqtyfdf7pDLFKLJoAnw"
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 "
      "Firefox/154.0")
HEADERS = {
    "content-type": "application/json+protobuf",
    "x-goog-api-key": API_KEY,
    "x-user-agent": "grpc-web-javascript/0.1",
    "user-agent": UA,
    "origin": "https://tv.youtube.com",
    "referer": "https://tv.youtube.com/",
}
TIMEOUT = 30
# Enough for the VM's getRandomValues calls; it falls back to Math.random
# if it ever wants more.
ENTROPY = 4096

_context = None


class BotGuardError(Exception):
    """The exchange did not produce a snapshot."""


def _vendor():
    """Put the vendored js2py on the path, behind anything installed.

    Appended rather than inserted: Kodi runs every addon in one Python
    process and sys.path is shared, so putting `six` at the front of it
    would hand our copy to every other addon on the box. At the back,
    ours is only reached when nothing else provides the name.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "vendor")
    if path not in sys.path:
        sys.path.append(path)


def _read(name):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, name), "r", encoding="utf-8") as handle:
        return handle.read()


def _fresh_context():
    """A JavaScript global with a browser in it, ready to take the VM.

    A new one per mint: the interpreter defines itself onto the global and
    a second challenge's interpreter would land on top of the first.
    """
    _vendor()
    from . import js2py_fixes
    js2py_fixes.apply()
    import js2py

    context = js2py.EvalJs()
    context.execute("var window = this; var self = this; var globalThis = this;")
    context.execute(js2py_fixes.FIXES_JS)
    context.execute(_read("botguard_shim.js"))
    context.execute("document.defaultView = this; var top = this;"
                    " var parent = this; var frames = this;")
    context.execute("__seedEntropy(%s);"
                    % json.dumps(list(bytearray(os.urandom(ENTROPY)))))
    return context


def _rpc(method, payload):
    import requests
    reply = requests.post("%s/%s" % (ENDPOINT, method), headers=HEADERS,
                          json=payload, timeout=TIMEOUT)
    if reply.status_code != 200:
        raise BotGuardError("%s answered HTTP %d" % (method, reply.status_code))
    return reply.json()


def descramble(text):
    """Google's own: base64, then plus 97 to every byte."""
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    return bytes((b + 97) & 0xFF for b in raw).decode("utf-8", "replace")


def mint(binding=""):
    """One exchange. Returns (token, ttl_seconds)."""
    started = time.time()
    created = _rpc("Create", [REQUEST_KEY])
    scrambled = next((v for v in created if isinstance(v, str)), "")
    if not scrambled:
        raise BotGuardError("Create returned no challenge")
    parts = json.loads(descramble(scrambled))
    wrapped, program, global_name = parts[1], parts[4], parts[5]
    interpreter = next((v for v in (wrapped or [])
                        if isinstance(v, str) and len(v) > 1000), "")
    if not interpreter:
        raise BotGuardError("the challenge carried no interpreter")
    # The source map is a comment on its own line and the parser has no use
    # for a 244 character data: url.
    interpreter = "\n".join(line for line in interpreter.split("\n")
                            if not line.startswith("//#"))

    context = _fresh_context()
    context.execute(interpreter)
    context.execute("var vm = this[%s];" % json.dumps(global_name))
    context.execute("if (!vm || !vm.a) throw 'the interpreter did not "
                    "register ' + %s;" % json.dumps(global_name))
    context.execute("var program = %s;" % json.dumps(program))
    # No event loop: the VM's callbacks are driven by __drain, which runs
    # whatever the shim's setTimeout queued until nothing is left.
    context.execute("""
var setupResult = null, snapshot = null;
function noop() {}
vm.a(program, function (a, s, p, c) { setupResult = {a: a, s: s, p: p, c: c}; },
     true, undefined, noop, [[], []], undefined, false,
     [noop, noop, noop, noop, noop]);
__drain();
if (!setupResult) throw 'the vm never set itself up';
setupResult.a(function (r) { snapshot = r; },
              [%s, undefined, [], undefined]);
__drain();
""" % (json.dumps(binding) if binding else "undefined"))
    response = str(context.snapshot)
    # A failed run answers a string beginning "E:", and GenerateIT hands
    # back a token for one of those too -- so a broken run looks exactly
    # like a working one unless this is checked.
    if not response.startswith("$"):
        raise BotGuardError("the snapshot failed: %s" % response[:120])

    answer = _rpc("GenerateIT", [REQUEST_KEY, response])
    token = next((v for v in answer if isinstance(v, str)), "")
    ttl = next((v for v in answer if isinstance(v, int)), 0)
    if not token:
        raise BotGuardError("GenerateIT returned no token")
    kodiutils.log("botguard: minted a token in %.1fs with no JavaScript "
                  "runtime (%d chars, good for %d hours)"
                  % (time.time() - started, len(token), (ttl or 43200) // 3600))
    return token, int(ttl or 43200)
