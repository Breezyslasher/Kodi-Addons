import os, sys, importlib
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs")
sys.argv = ["plugin://plugin.video.youtubetv/", "1", ""]
ROOT = "/home/user/Kodi-Addons/plugin.video.youtubetv"
sys.path.insert(0, ROOT)
bad = 0
for name in sorted(os.listdir(ROOT + "/lib")):
    if not name.endswith(".py") or name == "__init__.py":
        continue
    mod = "lib." + name[:-3]
    try:
        importlib.import_module(mod)
        print("  ok   %s" % mod)
    except Exception as exc:
        bad += 1
        print("  FAIL %s: %s: %s" % (mod, type(exc).__name__, exc))
for mod in ("default", "service"):
    try:
        importlib.import_module(mod)
        print("  ok   %s" % mod)
    except SystemExit:
        print("  ok   %s (exited)" % mod)
    except Exception as exc:
        bad += 1
        print("  FAIL %s: %s: %s" % (mod, type(exc).__name__, exc))
print("failures:", bad)
