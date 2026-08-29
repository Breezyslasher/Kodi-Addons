"""The cached challenge under js2py, traced the same way as under node."""
import io, json, os, sys, time, warnings
warnings.filterwarnings("ignore")
sys.setrecursionlimit(30000)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = ["/tmp/js2py_pkg", "/tmp/pyjsparser-2.7.1", SP]
import js2py_fixes
js2py_fixes.apply()
import js2py
import js2py.base as _base
import js2py.host.jseval as _jseval

# Turn the marker into the translated line that raised it.
_translated, _seen = {}, []
_make, _exec = _base.MakeError, _jseval.executor


def _remember(code):
    _translated.setdefault("<string>", []).append(code.split("\n"))
    return _exec(code)


_jseval.executor = _remember

# The interpreter eval()s code it builds at run time, so the stack lands in
# source no textual patch of the file could reach. Keep every piece.
_evalled = []
_translate = _jseval.translate_js


def _keep(code, header):
    _evalled.append(code)
    return _translate(code, header)


_jseval.translate_js = _keep


def _watch(name, message=""):
    if "__whereDidThisComeFrom" in str(message):
        import traceback
        chain = []
        for f in traceback.extract_stack()[:-1]:
            if f.filename not in ("<string>", "<EvalJS snippet>"):
                continue
            text = ""
            for lines in _translated.get(f.filename) or []:
                if 0 < f.lineno <= len(lines) and lines[f.lineno - 1].strip():
                    text = lines[f.lineno - 1].strip()
            chain.append("%s:%d %s" % (f.filename, f.lineno, text[:200]))
        _seen.append(chain[-8:])
    return _make(name, message)


_base.MakeError = _watch

challenge = json.load(open(SP + "/cached_challenge.json"))
context = js2py.EvalJs()
context.execute("var window = this; var self = this; var globalThis = this;")
context.execute(js2py_fixes.FIXES_JS)
context.execute(io.open(SP + "/shim_es5.js").read())
context.execute("document.defaultView = this; var top = this; var parent = this;")
context.execute(io.open(SP + "/trace.js").read())
context.execute(io.open(SP + "/cached_interpreter.js").read()
                .replace('if(T=v(b,420),T>=k)break;', 'if(T=v(b,420),__rec("op " + T),T>=k)break;')
                .replace('catch(B){l(b,451)?M6(22,B,b,N):F(451,b,B)}', 'catch(B){__rec("VM caught: " + (B && B.message !== undefined ? ("msg " + B.message) : ("bare " + __brief(B))));l(b,451)?M6(22,B,b,N):F(451,b,B)}')
                .replace('function(){return+new Date}', 'function(){var v = +new Date; __rec("fallback F -> " + (typeof v) + " " + __brief(v)); return v}', 1)
                .replace('return this.MF+window.performance.now()', 'return (__rec("MF is " + (typeof this.MF) + " " + __brief(this.MF)), this.MF+window.performance.now())', 1))
context.execute("var program = %s;" % json.dumps(challenge["program"]))
context.execute("var vm = this[%r];" % challenge["globalName"])

start = time.time()
context.execute("""
var setupResult = null, snapshot = null;
function noop() {}
__traceOn = true;
vm.a(program, function (a, s, p, c) { setupResult = {a: a, s: s, p: p, c: c}; },
     true, undefined, noop, [[], []], undefined, false,
     [noop, noop, noop, noop, noop]);
__drain();
if (setupResult) {
  setupResult.a(function (r) { snapshot = r; },
                [undefined, undefined, [], undefined]);
  __drain();
}
var traceText = __trace.join('\\n');
""")
io.open(SP + "/trace_js2py.txt", "w").write(str(context.traceText) + "\n")
for chain in _seen[:1]:
    print("where the string reached Math.floor:")
    for line in chain:
        print("   " + line)
for n, piece in enumerate(_evalled):
    io.open(SP + "/evalled_%d.js" % n, "w").write(piece)
print("kept %d eval'd pieces: %s"
      % (len(_evalled), [len(p) for p in _evalled]))
print("js2py: %d traced calls in %.1fs, snapshot %s"
      % (len(str(context.traceText).split("\n")), time.time() - start,
         str(context.snapshot)[:30]))
