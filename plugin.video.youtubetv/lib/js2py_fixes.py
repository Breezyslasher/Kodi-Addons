"""Where js2py answers differently from a JavaScript engine.

js2py is an ES5 interpreter written in Python, and BotGuard is a bytecode
VM that runs entirely on top of one. Without these it stops a third of the
way through its program and reports failure; with them it produces a
snapshot byte for byte identical to the one V8 produces from the same
challenge -- checked by running both against one cached challenge with the
randomness pinned and diffing the traces.

Each was found that way rather than by reading the spec: run the same
expression under node and under js2py, diff, fix what differs. tools/js2py
carries the conformance corpus and the differential tracer.

They are applied by monkeypatching so the vendored library stays the
released one, and apply() is safe to call more than once.
"""

import math
import os
import sys

_applied = False


def _vendor():
    """Put the vendored js2py on the path, behind anything installed.

    Appended rather than inserted: Kodi runs every addon in one Python
    process and sys.path is shared, so putting `six` at the front of it
    would hand our copy to every other addon on the box. At the back, ours
    is only reached when nothing else provides the name.

    Here rather than only in the caller, so that importing this module is
    enough -- apply() imports js2py, and an ordering where it ran first
    would fail with ModuleNotFoundError.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if path not in sys.path:
        sys.path.append(path)


def apply():
    """Correct js2py in place. Idempotent."""
    global _applied
    if _applied:
        return
    _applied = True
    _vendor()
    # -- Python 3.12 --------------------------------------------------
    # The translator names a try/catch's temporaries with
    # random.randrange(1e8), and 3.12 stopped taking a float there. Before
    # importing anything that translates, give that module a randrange
    # that takes one. (fix_js_args is the other 3.12 casualty, and that one
    # is replaced outright in lib/vendor/js2py/utils/injector.py.)
    import random as _random
    import js2py.translators.translating_nodes as _nodes

    class _Random(object):
        def __getattr__(self, name):
            return getattr(_random, name)

        def randrange(self, *args, **kwargs):
            return _random.randrange(*[int(a) for a in args], **kwargs)

    _nodes.random = _Random()

    import js2py.base as base
    import js2py.constructors.jsobject as jsobject
    from js2py.base import Js, undefined

    # -- % poisons every number it produces ------------------------------
    # js2py interns a PyJsNumber for every integer from -1024 to 16383 and
    # hands the same object out every time that value is wanted. Its %
    # builds the result with Js(a % b) -- which returns the interned object
    # -- and then corrects the sign *in place*:
    #
    #     pyres = Js(a % b)
    #     if a < 0 and pyres.value > 0:
    #         pyres.value -= abs(b)
    #
    # So `-7 % 3` takes the shared 2, writes -1 into it, and from then on
    # the JavaScript literal 2 evaluates to -1 everywhere in that context:
    # `1 + 1` is -1, `String(2)` is "-1", `Math.pow(2, 53)` is -1. The next
    # expression in the same statement already sees it -- `5.5 % 2` answers
    # 0.5, because by then the 2 is a -1.
    #
    # Correct the sign before wrapping, and nothing shared is touched.
    # This is the released library's bug, not something the addon does to
    # it, and it is not academic: the VM's dispatch indexes with (n + 1) % 3.
    def __mod__(self, other):
        a = self.to_number().value
        b = other.to_number().value
        if abs(a) == float("inf") or not b:
            return base.NaN
        if abs(b) == float("inf"):
            return Js(a)
        value = a % b
        # Python takes the sign of b, JavaScript the sign of a.
        if a < 0 and value > 0:
            value -= abs(b)
        elif a > 0 and value < 0:
            value += abs(b)
        return Js(value)

    base.PyJs.__mod__ = __mod__

    # -- key order -------------------------------------------------------
    # JS enumerates integer-like keys first, ascending, then the rest in
    # insertion order. js2py sorts every key alphabetically in for-in and
    # uses plain insertion order in Object.keys, so {b:1, 2:1, a:1, 1:1}
    # enumerates as 1,2,a,b and keys as b,2,a,1 where a browser says
    # 1,2,b,a both times. Anything that hashes an object's keys sees a
    # different object.
    def order(names):
        numeric, rest = [], []
        for name in names:
            if name.isdigit() and (name == "0" or name[0] != "0") \
                    and int(name) < 4294967295:
                numeric.append(name)
            else:
                rest.append(name)
        numeric.sort(key=int)
        return numeric + rest

    def __iter__(self):
        if not self.IS_CHILD_SCOPE:
            cands = order([name for name in self.own
                           if self.own[name]['enumerable']])
        else:
            cands = order(list(self.own))
        seen = set()
        for cand in cands:
            check = self.own.get(cand)
            if check and check['enumerable']:
                seen.add(cand)
                yield Js(cand)
        # And the prototype chain. js2py stopped at the object's own
        # properties, so `for (var k in child)` never saw what the
        # prototype declared -- an inherited enumerable property is
        # invisible where a browser lists it after the own ones. Built-in
        # methods live on prototypes too and are not enumerable, so they
        # stay out of it.
        if self.IS_CHILD_SCOPE:
            return
        proto, depth = self.prototype, 0
        while proto is not None and depth < 32:
            for name in order([n for n in getattr(proto, "own", {})
                               if proto.own[n].get("enumerable")]):
                if name not in seen:
                    seen.add(name)
                    yield Js(name)
            proto, depth = getattr(proto, "prototype", None), depth + 1

    base.PyJs.__iter__ = __iter__

    # -- Function.prototype.name -----------------------------------------
    # js2py names a hoisted function PyJsHoisted_f_ and an inline one after
    # its slot, so fn.name is its own translation artefact rather than the
    # name the source gave it.
    original_init = base.PyJsFunction.__init__

    def __init__(self, func, prototype=None, extensible=True, source=None):
        original_init(self, func, prototype, extensible, source)
        name = self.func_name or ''
        if name.startswith('PyJsHoisted_') and name.endswith('_'):
            name = name[len('PyJsHoisted_'):-1]
        elif name.startswith('PyJs_') and name.endswith('_'):
            name = name[len('PyJs_'):-1].rstrip('0123456789').rstrip('_')
            if name == 'anonymous':
                name = ''
        if name != self.func_name:
            self.func_name = name
            if self.own.get('name'):
                self.own['name']['value'] = Js(name)
            elif name:
                self.define_own_property('name', {
                    'value': Js(name), 'writable': False,
                    'enumerable': False, 'configurable': True})

    base.PyJsFunction.__init__ = __init__

    # -- eval, from anywhere ---------------------------------------------
    # js2py takes the calling scope from inspect.stack()[3] -- a fixed
    # depth. It is right only when eval is called directly from translated
    # code at exactly that depth; every other shape either picks somebody
    # else's scope or raises KeyError: 'var', a *Python* exception that no
    # JavaScript try/catch can catch. `(0, eval)(x)`, `var e = eval; e(x)`,
    # eval.call, eval inside a callback: all of them.
    #
    # BotGuard evals throughout its run. Fixing this is what takes the VM
    # from stopping a third of the way through to finishing.
    import inspect as _inspect
    import js2py.host.jseval as _jseval

    class _Stack(object):
        """inspect, with stack()[3] made to mean the global scope.

        The outermost js2py scope, not the innermost: BotGuard calls eval
        as `(0, eval)(code)`, and an indirect eval runs in *global* scope.
        Handing it the caller's scope instead leaks the VM's own minified
        locals into the snippet -- so `O` resolves, the probe that has to
        throw "O is not defined" quietly succeeds, and the VM skips the two
        instructions that follow it.
        """

        def __getattr__(self, name):
            return getattr(_inspect, name)

        def stack(self, *args):
            frames = _inspect.stack()
            found = [r for r in frames[1:] if "var" in r[0].f_locals]
            return [found[-1]] * 4 if found else frames

    _jseval.inspect = _Stack()

    # -- what an error says ----------------------------------------------
    # BotGuard throws on purpose and keeps what came back: a run collects
    # "Cannot read properties of undefined (reading 'String')",
    # "w.apply is not a function", "SH is not defined" and half a dozen
    # SyntaxErrors, and puts them in the snapshot. js2py words all of them
    # differently, so say them the way an engine says them.
    import re as _re
    _null_prop = _re.compile(
        r"Undefined and null dont have properties \(tried getting "
        r"property '(.*)'\)$")
    _not_fn = _re.compile(
        r"'(\w+)' is not a function \(tried calling property '(.*)' "
        r"of '(.*)'\)$")
    _line = _re.compile(r"^Line \d+: ")
    _original = base.MakeError

    def MakeError(name, message="", *rest):
        text = message or ""
        found = _null_prop.match(text)
        if found:
            text = "Cannot read properties of undefined (reading '%s')" \
                % found.group(1)
        else:
            found = _not_fn.match(text)
            if found:
                text = "%s is not a function" % found.group(2)
            elif name == "SyntaxError":
                text = _line.sub("", text)
        return _original(name, text, *rest)

    base.MakeError = MakeError
    for module in (base, jsobject):
        module.MakeError = MakeError

    # -- Object.getOwnPropertyNames ---------------------------------------
    # It answered obj.own.keys() -- a Python view, handed to JS unconverted,
    # with no length and no array methods on it. .sort() on the result was
    # "'undefined' is not a function", so nothing could use it. It has to be
    # replaced on the constructor itself: fill_prototype copied the original
    # onto Object when js2py was imported, so patching the class does
    # nothing.
    def getOwnPropertyNames(obj):
        if not obj.is_object():
            raise base.MakeError(
                'TypeError',
                'Object.getOwnPropertyNames called on non-object')
        return Js(order(list(obj.own)))

    jsobject.Object.put('getOwnPropertyNames',
                        base.PyJsFunction(getOwnPropertyNames,
                                          base.FunctionPrototype))


# The rest cannot be patched from Python: fill_prototype copies each method
# onto its constructor when js2py is imported, so changing the class after
# that changes nothing. They are replaced in JavaScript instead, on the
# context, before anything else runs.
FIXES_JS = """
// Math.round: JS rounds half up, always. Python rounds half to even, so
// js2py said round(-1.5) = -2 and round(2.5) = 2 where a browser says -1
// and 3.
(function () {
  var floor = Math.floor;
  Math.round = function (x) {
    x = Number(x);
    if (x !== x) return NaN;
    if (x === Infinity || x === -Infinity) return x;
    return floor(x + 0.5);
  };
})();

// Date.now returns a Date object rather than a number: js2py binds it to
// the same helper that builds a date. Everything downstream of it is then
// a string concatenation instead of arithmetic -- BotGuard sets
// MF = performance.timeOrigin, whose value is Date.now(), and its clock
// reads "Sat Aug 29 2026 08:37:17 GMT+0000 (UTC)12" instead of a number.
Date.now = function () { return new Date().getTime(); };

// An error with no stack. js2py has none at all, and the VM keeps
// message + ":" + stack for every throw it makes on purpose, so half of
// what it collects was the string "undefined".
(function () {
  try {
    Object.defineProperty(Error.prototype, 'stack', {
      configurable: true,
      get: function () {
        return String(this.name) + ': ' + String(this.message) +
               '\\n    at <anonymous>:1:1';
      }
    });
  } catch (e) {}
})();

// Array.prototype.splice with one argument removes everything from there
// to the end. js2py read the missing deleteCount as 0 and removed nothing,
// so [1,2,3,4].splice(2) answered [] and left the array untouched.
(function () {
  var native_ = Array.prototype.splice;
  Array.prototype.splice = function (start) {
    if (!arguments.length) return [];
    if (arguments.length > 1) return native_.apply(this, arguments);
    var length = this.length >>> 0;
    var from = Number(start) || 0;
    from = from < 0 ? Math.max(length + from, 0) : Math.min(from, length);
    return native_.call(this, start, length - from);
  };
})();

// ''.split(',') is [''], not []. js2py answered [] for every separator
// that does not match an empty string.
(function () {
  var native_ = String.prototype.split;
  String.prototype.split = function (separator, limit) {
    var s = String(this);
    if (s !== '' || separator === undefined)
      return native_.apply(this, arguments);
    if (limit === 0) return [];
    var matchesEmpty = separator instanceof RegExp
      ? separator.test('') : String(separator) === '';
    return matchesEmpty ? [] : [''];
  };
})();

// Object.keys and getOwnPropertyNames: js2py returned insertion order and,
// for getOwnPropertyNames, a Python list with no array methods on it at
// all -- .sort() on the result was "'undefined' is not a function".
// for-in is fixed in Python, so build both on it.
(function () {
  function order(names) {
    var numeric = [], rest = [], i, n;
    for (i = 0; i < names.length; i++) {
      n = names[i];
      if (/^(0|[1-9][0-9]*)$/.test(n) && +n < 4294967295) numeric.push(n);
      else rest.push(n);
    }
    numeric.sort(function (a, b) { return a - b; });
    return numeric.concat(rest);
  }
  Object.keys = function (o) {
    var out = [], k;
    for (k in o) if (Object.prototype.hasOwnProperty.call(o, k)) out.push(k);
    return order(out);
  };
})();

// (0.5).toString(2) was "0": js2py truncated to an integer first.
(function () {
  var digits = '0123456789abcdefghijklmnopqrstuvwxyz';
  var native_ = Number.prototype.toString;
  Number.prototype.toString = function (radix) {
    if (radix === undefined || radix === 10) return native_.call(this, radix);
    var v = Number(this);
    if (v !== v) return 'NaN';
    if (v === Infinity) return 'Infinity';
    if (v === -Infinity) return '-Infinity';
    var sign = v < 0 ? '-' : '';
    v = Math.abs(v);
    var whole = Math.floor(v), fraction = v - whole, out = '';
    while (whole >= 1) {
      out = digits.charAt(whole % radix) + out;
      whole = Math.floor(whole / radix);
    }
    out = out || '0';
    if (fraction) {
      var frac = '';
      for (var i = 0; i < 20 && fraction; i++) {
        fraction *= radix;
        var d = Math.floor(fraction);
        frac += digits.charAt(d);
        fraction -= d;
      }
      out += '.' + frac;
    }
    return sign + out;
  };
})();

// (1e21).toFixed(2) is "1e+21", not twenty-one digits and a point.
(function () {
  var native_ = Number.prototype.toFixed;
  Number.prototype.toFixed = function (n) {
    var v = Number(this);
    if (!(Math.abs(v) < 1e21)) return String(v);
    return native_.call(this, n);
  };
})();
"""


def unlock_globals(context):
    """Let JavaScript replace a global, the way a real engine does.

    js2py defines every built-in on the top scope with writable and
    configurable both False, so `Uint8Array = ours` at global scope does
    nothing at all -- silently, since sloppy mode does not throw. In a
    browser the global built-ins are writable and configurable, and the
    shim needs that: js2py's typed arrays are implemented with numpy,
    numpy is not on a Kodi box, and `new Uint8Array(4)` therefore raises a
    Python NameError. That is not a JavaScript exception, so no try/catch
    in the VM can catch it and it takes the whole mint down.
    """
    try:
        scope = context._context["var"]
    except Exception:
        return
    for descriptor in getattr(scope, "own", {}).values():
        if isinstance(descriptor, dict) and "value" in descriptor:
            descriptor["writable"] = True
            descriptor["configurable"] = True
