# Offline checks for plugin.video.youtubetv

Kodi is not importable outside Kodi, so these run the addon against the small
`stubs/` package instead. They exist because of three bugs that reached a real
box, each of which the previous check would have missed:

| check | what it catches | the bug it was written for |
| --- | --- | --- |
| `import_all.py` | a module that no longer imports | -- |
| `check_attrs.py` | `module.attr` where `attr` was deleted | callers of removed functions after the cookie path was cut |
| `test_proxy.py` | the licence proxy's HTTP routes | `do_POST` deleted with the `/manifest` handler beside it: ISA reported `License server returned failure (HTTP error 501)`, the CDM session never opened, and the video stream was dropped with `Codec id 27 require extradata` -- three messages, none naming the missing handler, and neither check above sees it because nothing *references* `do_POST` |
| `test_bootstrap.py` | the player-js lookup across candidate pages | signing out took the ytcfg with it, so `n` could not be solved and every media url was a 403 |
| `test_subsamples.py` | re-signalling whole-sample audio encryption as one subsample | ISA 21.5.22 mishandles `saiz default=8`; on-demand died at 9.5 s |
| `test_pages.py` | the Home and Library readers | four traps at once: the Library's filter/sort grid is a flattened cross product, so pairing selector *i* with content *i* names the wrong row; a scheduled recording that is on the air carries its videoId only inside a popup dialog and was dropped; two airings of one show deduplicated to one because they share a destination; and Home's first `nextContinuationData` belongs to its first shelf, not to the page |

Run them all:

    for f in tools/checks/*.py; do python3 "$f" || echo "FAILED: $f"; done

They need no network and no credentials.

`test_credentials.py` covers where the Google API project comes from: this
addon's settings, then plugin.video.youtube's if that addon is installed, then
one baked into the build -- and that half a pair is skipped rather than sent.

Each check in `test_pages.py` was verified by breaking the fix it covers and
watching it fail: removing the popup lookup fails three, the start-time
dedupe key two, the page-level continuation one, the row-major cell index two.
