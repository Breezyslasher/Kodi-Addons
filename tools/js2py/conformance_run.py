"""Run conformance2 one case at a time, printing as it goes.

The whole file translated once, then each case run on its own, so a case
that never returns names itself in the output rather than taking the run
down with it.
"""
import io, json, sys, time
sys.path.insert(0, "plugin.video.youtubetv")
sys.path.insert(0, "/tmp/claude-0/-home-user-Kodi-Addons/"
                   "44ede4c1-ce08-56c0-abb1-ae12637b556f/scratchpad/kodistub")
from lib import botguard_py

SP = ("/tmp/claude-0/-home-user-Kodi-Addons/"
      "44ede4c1-ce08-56c0-abb1-ae12637b556f/scratchpad")
# The environment the addon actually mints in: the corrected engine, the
# shim and its ES5 typed arrays, not a bare context.
context = botguard_py._fresh_context()
start = time.time()
context.execute(io.open(SP + "/conform2.js").read())
print("translating the file took %.1fs" % (time.time() - start), flush=True)
context.execute("""
function __runOne(i) {
  try { return String(CASES2[i][1]()); }
  catch (e) { return 'THREW ' + (e && e.message || e); }
}
var __count = CASES2.length;
var __names = [];
for (var i = 0; i < __count; i++) __names.push(CASES2[i][0]);
""")
names = [str(n) for n in context.__names]
out = []
for i, name in enumerate(names):
    began = time.time()
    print("  %2d/%d %s ..." % (i + 1, len(names), name), flush=True)
    context.execute("var __one = __runOne(%d);" % i)
    took = time.time() - began
    out.append("%s => %s" % (name, str(context.__one)))
    print("      %s%s" % (str(context.__one)[:70],
                          "   (%.1fs)" % took if took > 1 else ""), flush=True)
io.open(SP + "/conform2_js2py.txt", "w").write("\n".join(out) + "\n")
print("all %d cases done" % len(names), flush=True)
