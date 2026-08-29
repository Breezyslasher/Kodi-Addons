"""Third-party Python, vendored so the addon needs nothing installed.

* js2py 0.74 (MIT, Piotr Dabkowski) -- a JavaScript engine written in
  Python. It is what runs BotGuard's interpreter, so a proof-of-origin
  token can be minted on a box that has no JavaScript runtime at all.
  Two of its modules are replaced by stubs rather than vendored: `es6`
  is babel (3.7 MB) for a translation path this never takes, and
  `node_import` installs npm packages at run time. Everything else is
  the released library, unmodified; the corrections this addon needs are
  applied at run time by lib/js2py_fixes.py rather than by forking.
* pyjsparser 2.7.1 (MIT, Piotr Dabkowski) -- the parser js2py uses.
* six 1.x (MIT, Benjamin Peterson) -- js2py imports it.
"""
