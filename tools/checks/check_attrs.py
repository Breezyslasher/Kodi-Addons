"""Every `module.attribute` reference in the addon, checked against the module.

Deleting a function leaves callers importing fine and failing at the call, so
imports alone prove nothing. This resolves each attribute for real.
"""
import ast, os, sys, importlib
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs")
sys.argv = ["plugin://plugin.video.youtubetv/", "1", ""]
ROOT = "/home/user/Kodi-Addons/plugin.video.youtubetv"
sys.path.insert(0, ROOT)

mods = {}
for name in os.listdir(ROOT + "/lib"):
    if name.endswith(".py") and name != "__init__.py":
        stem = name[:-3]
        try:
            mods[stem] = importlib.import_module("lib." + stem)
        except Exception as exc:
            print("could not import lib.%s: %s" % (stem, exc))

ALIASES = {"manifest_mod": "manifest", "epg_mod": "epg", "sabr_bridge": "sabr_bridge"}
bad = 0
for base, _, files in os.walk(ROOT):
    if "vendor" in base or "__pycache__" in base:
        continue
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(base, fn)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)):
                continue
            mod = ALIASES.get(node.value.id, node.value.id)
            if mod not in mods:
                continue
            if not hasattr(mods[mod], node.attr):
                bad += 1
                print("  MISSING  %s:%d  %s.%s"
                      % (os.path.relpath(path, ROOT), node.lineno,
                         node.value.id, node.attr))
print("dangling references:", bad)
