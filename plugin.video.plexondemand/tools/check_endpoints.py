#!/usr/bin/env python3
"""Does Plex still answer the way plugin.video.plexondemand expects?

The one entry for Plex On Demand in .github/workflows/endpoint-checks.yml. It
runs four checks in turn and fails if any of them fails:

  1. spec     - tools/endpoints.json through the shared engine
                (.github/endpoint-check/check_endpoints.py): token, PIN,
                providers, rows, genres and a genre's movies, store, search.
  2. addon    - tools/smoke_test.py: the addon's own lib/ code against the
                live service (playback incl. the DRM licence, watch state,
                watchlist, related, rentals, ...).
  3. service  - tools/test_service.py: service.py and the Skip Buttons wiring
                with Kodi stubbed; no network.
  4. offline  - tools/test_offline.py: failure paths (offline, revoked
                sign-in, ISA secure-decoder flag, channel without an id).

    python3 plugin.video.plexondemand/tools/check_endpoints.py

Needs `requests` (the addon needs it too). Anonymous only: no credentials.
"""
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))

PARTS = [
    ("spec", [os.path.join(REPO, ".github", "endpoint-check", "check_endpoints.py"),
              os.path.join(TOOLS, "endpoints.json")]),
    ("addon", [os.path.join(TOOLS, "smoke_test.py")]),
    ("service", [os.path.join(TOOLS, "test_service.py")]),
    ("offline", [os.path.join(TOOLS, "test_offline.py")]),
]


def main():
    failed = []
    for name, cmd in PARTS:
        print("\n===== %s: %s" % (name, os.path.relpath(cmd[-1], REPO)), flush=True)
        status = subprocess.call([sys.executable, "-u"] + cmd, cwd=REPO)
        print("===== %s: %s" % (name, "passed" if status == 0 else "FAILED (exit %d)" % status), flush=True)
        if status != 0:
            failed.append(name)
    print("\nPlex On Demand: %s" % ("all parts passed" if not failed else "failed: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
