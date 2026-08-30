"""Every url the addon draws has somewhere to go.

Written for a bug that shipped in 2026.8.31.32: a context menu offered
"Suggested titles", pointing at action=browse_suggested, and the route that
was meant to answer it never reached the file -- an edit failed and only the
label was reapplied. The action fell through main()'s chain to route_root,
so the entry silently opened the addon's front page.

Nothing else here catches that. import_all sees a file that imports,
check_attrs sees no missing attribute, and test_pages tests the readers, not
the wiring. What is missing is a *string* on one side of a url and no match
for it on the other, which is only visible by comparing the two sets.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(os.path.dirname(HERE)) + "/plugin.video.youtubetv"

failures = []


def check(what, got, want):
    if got == want:
        print("  ok   %s == %r" % (what, want))
    else:
        failures.append(what)
        print("  FAIL %s: got %r, wanted %r" % (what, got, want))


source = open(ADDON + "/default.py", encoding="utf-8").read()
# The service and the IPTV bridge build plugin urls of their own.
elsewhere = ""
for name in ("service.py", "lib/iptv.py", "lib/kodiutils.py", "lib/manifest.py"):
    try:
        elsewhere += open(ADDON + "/" + name, encoding="utf-8").read()
    except OSError:
        pass

emitted = set(re.findall(r'action="([a-z_]+)"', source + elsewhere))
# _list_sections(rows, "home_row") names the action as an argument rather
# than as a keyword, and those are the row routes -- the half of the wiring
# most easily left dangling, since the label and the action are written far
# apart.
emitted |= set(re.findall(r'_list_sections\([^,]+,\s*"([a-z_]+)"', source))
# The IPTV bridge writes its channel urls by hand, as "?action=play_channel".
emitted |= set(re.findall(r'\?action=([a-z_]+)', source + elsewhere))

# IPTV Manager calls these; nothing in the addon draws a url for them.
CALLED_FROM_OUTSIDE = {"iptv_channels", "iptv_epg"}
# main() dispatches on action, one branch per name, plus one that takes two.
handled = set(re.findall(r'action == "([a-z_]+)"', source))
for pair in re.findall(r'action in \(([^)]*)\)', source):
    handled |= set(re.findall(r'"([a-z_]+)"', pair))

check("every action a url names is dispatched",
      sorted(emitted - handled), [])
check("and every action dispatched is one a url names",
      sorted(handled - emitted - CALLED_FROM_OUTSIDE), [])
check("the actions found are not an empty set by accident",
      len(emitted) > 10, True)

# A route named in the dispatch must exist to be called.
called = set(re.findall(r'\n        (route_[a-z_]+)\(', source))
defined = set(re.findall(r'\ndef (route_[a-z_]+)\(', source))
check("every route the dispatch calls is defined", sorted(called - defined), [])

print("failures:", len(failures))
sys.exit(1 if failures else 0)
