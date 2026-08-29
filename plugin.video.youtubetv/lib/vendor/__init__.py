"""Third-party Python, vendored so the addon needs nothing installed.

* js2py 0.74 (MIT, Piotr Dabkowski) -- a JavaScript engine written in
  Python. It is what runs BotGuard's interpreter, so a proof-of-origin
  token can be minted on a box that has no JavaScript runtime at all.
  Three of its modules are not the released ones. `es6` is babel
  (3.7 MB) for a translation path this never takes and `node_import`
  installs npm packages at run time -- both are stubs. `utils/injector`
  is rewritten: js2py's own splices arguments into its natives by
  rewriting CPython bytecode, which raises on Python 3.12 and refuses to
  import at all on 3.13, and the job needs no bytecode. Everything else
  is the released library, unmodified; the corrections this addon needs
  are applied at run time by lib/js2py_fixes.py rather than by forking.
* pyjsparser 2.7.1 (MIT, Piotr Dabkowski) -- the parser js2py uses.
* six 1.x (MIT, Benjamin Peterson) -- js2py imports it.
"""
