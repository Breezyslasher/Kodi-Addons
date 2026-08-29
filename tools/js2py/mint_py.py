"""Mint a proof-of-origin token with no JavaScript runtime installed.

The VM runs under js2py -- a pure-Python ES5 interpreter -- and every
network call is Python's. If this works there is nothing left that needs
node.
"""
import base64, json, os, sys, time, warnings
warnings.filterwarnings("ignore")
sys.setrecursionlimit(30000)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = ["/tmp/js2py_pkg", "/tmp/pyjsparser-2.7.1"]
sys.path.insert(0, SP)
import js2py_fixes
js2py_fixes.apply()
import js2py, requests

# Every JS error js2py raises, with the line of translated Python that
# raised it. The VM catches them all and reports a bare object, so this is
# the only place the real cause is visible.
import js2py.base as _base
_seen = []
_make = _base.MakeError


# The interpreter arrives inside an eval, so its translated Python is
# compiled as "<string>" and no traceback can show its source. Keep it.
_translated = {}
import js2py.host.jseval as _jseval
_inner = _jseval.Eval


def _keep_eval(this, args):
    return _inner(this, args)


_real_exec = None


def _watch(name, message=""):
    import traceback
    frames = [f for f in traceback.extract_stack()[:-1]
              if f.filename in ("<string>", "<EvalJS snippet>")]
    where = ""
    chain = []
    for f in frames[-5:]:
        seen = _translated.get(f.filename) or []
        text = ""
        for lines in seen:
            if 0 < f.lineno <= len(lines) and lines[f.lineno - 1].strip():
                text = lines[f.lineno - 1].strip()
        chain.append("%s:%d %s" % (f.filename, f.lineno, text[:150]))
    if frames:
        f = frames[-1]
        seen = _translated.get(f.filename) or []
        for lines in seen:
            if 0 < f.lineno <= len(lines) and lines[f.lineno - 1].strip():
                where = lines[f.lineno - 1].strip()
        where = "%s:%d (%d translation(s))  %s" % (f.filename, f.lineno,
                                                   len(seen), where)
    _seen.append("%s: %s\n      %s" % (name, message,
                                          "\n      ".join(chain)))
    return _make(name, message)


_base.MakeError = _watch

# One experiment: js2py throws where V8 would too, but the VM's own error
# is opaque, so let a property read on undefined answer undefined and see
# whether the snapshot then succeeds. If it does, one divergence is the
# whole difference; if it does not, there is more than one.
if os.environ.get("LENIENT"):
    _undef_get = _base.PyJsUndefined.get if hasattr(_base, "PyJsUndefined") \
        else None
    _orig_get = _base.PyJs.get

    def _lenient_get(self, prop):
        if self.Class in ("Undefined", "Null"):
            return _base.undefined
        return _orig_get(self, prop)

    _base.PyJs.get = _lenient_get

# Capture the Python js2py translates each piece of JS into, so a traceback
# line number can be turned back into something readable. eval'd code is
# exec'd from a string, so it lands under "<string>".
_exec = _jseval.executor


def _remember(code):
    _translated.setdefault("<string>", []).append(code.split("\n"))
    return _exec(code)


_jseval.executor = _remember

KEY = 'O43z0dpjhgX20SCx4KAo'
GOOG = 'https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa'
UA = ('Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 '
      'Firefox/154.0')
HEAD = {"content-type": "application/json+protobuf",
        "x-goog-api-key": "AIzaSyDyT5W0Jh49F30Pqqtyfdf7pDLFKLJoAnw",
        "x-user-agent": "grpc-web-javascript/0.1", "user-agent": UA,
        "origin": "https://tv.youtube.com",
        "referer": "https://tv.youtube.com/"}


def rpc(method, payload):
    reply = requests.post("%s/%s" % (GOOG, method), headers=HEAD,
                          json=payload, timeout=30)
    reply.raise_for_status()
    return reply.json()


def descramble(text):
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    return bytes((b + 97) & 0xFF for b in raw).decode("utf-8", "replace")


def step(label, fn):
    start = time.time()
    try:
        out = fn()
    except Exception as exc:
        print("  %-34s FAILED after %5.1fs  %s: %s"
              % (label, time.time() - start, type(exc).__name__,
                 str(exc).replace("\n", " ")[:220]))
        raise
    print("  %-34s ok after %5.1fs" % (label, time.time() - start))
    return out


print("minting with js2py, no runtime installed")
created = step("Create", lambda: rpc("Create", [KEY]))
raw = [v for v in created if isinstance(v, str)][0]
parts = json.loads(descramble(raw))
_id, wrapped, wrapped_url, _hash, program, global_name = parts[:6]
interpreter = [v for v in (wrapped or [])
               if isinstance(v, str) and len(v) > 1000][0]
interpreter = "\n".join(l for l in interpreter.split("\n")
                        if not l.startswith("//#"))
# Diagnosis only: the VM reports a failure as "E:" + e.message + ":" +
# e.stack, and js2py's errors carry neither -- so the report says
# "E:undefined:undefined" and names nothing. Make it say what was thrown.
import re as _re
interpreter, hits = _re.subn(
    r'\+(\w+)\.message\+":"\+\1\.stack',
    r'+"|"+__why(\1)+"|"+\1.message+":"+\1.stack', interpreter)
print("  patched the error report in %d place(s)" % hits)
print("  interpreter %d bytes, program %d chars, global %s"
      % (len(interpreter), len(program), global_name))

context = js2py.EvalJs()
step("js2py fixes", lambda: context.execute(js2py_fixes.FIXES_JS))
step("shim", lambda: context.execute(open(SP + "/shim_es5.js").read()))
step("window/self", lambda: context.execute(
    "var window = this; var self = this; var globalThis = this;"
    "var top = this; var parent = this; var frames = this;"
    "document.defaultView = this; document.parentWindow = this;"
    "window.document = document; window.navigator = navigator;"
    "window.location = location; window.screen = screen;"))
step("interpreter", lambda: context.execute(interpreter))

context.execute("var program = %s;" % json.dumps(program))
step("the vm registered itself", lambda: context.execute(
    "var vm = this[%r]; if (!vm || !vm.a) throw 'no ' + %r;"
    % (global_name, global_name)))

step("vm.a(program, setup)", lambda: context.execute("""
var setupResult = null, snapshot = null, said = [];
function noop() {}
function say(tag) {
  return function () {
    var out = [tag], i;
    for (i = 0; i < arguments.length; i++) {
      try { out.push(String(arguments[i] && arguments[i].message
                            || arguments[i])); }
      catch (e) { out.push('?'); }
    }
    said.push(out.join(' | '));
  };
}
vm.a(program, function (a, s, p, c) { setupResult = {a: a, s: s, p: p, c: c}; },
     true, undefined, say('telemetry'), [[], []], undefined, false,
     [say('log0'), say('log1'), say('log2'), say('log3'), say('log4')]);
__drain();
if (!setupResult) throw 'the vm never set itself up';
"""))

step("asyncSnapshotFunction", lambda: context.execute("""
setupResult.a(function (r) { snapshot = r; },
              [undefined, undefined, [], undefined]);
__drain();
if (snapshot === null) throw 'the snapshot never returned';
"""))
for line in list(context.said or []):
    print("  vm said: %s" % str(line)[:200])
for line in list(context.__drainErrors or []):
    print("  timer threw: %s" % str(line)[:200])
for line in _seen[:4]:
    print("  js2py raised %s" % line)
print("  %d JS error(s) raised in total" % len(_seen))
response = str(context.snapshot)
print("  snapshot: %d chars, starts %r" % (len(response), response[:24]))
if not response.startswith("$"):
    sys.exit("the snapshot failed: %s" % response[:120])

it = step("GenerateIT", lambda: rpc("GenerateIT", [KEY, response]))
token = [v for v in it if isinstance(v, str)][0]
ttl = ([v for v in it if isinstance(v, int)] or [43200])[0]
print("\ntoken: %s... (%d chars), ttl %ds" % (token[:24], len(token), ttl))
open(os.path.join(SP, "js2py_token.txt"), "w").write(token)
