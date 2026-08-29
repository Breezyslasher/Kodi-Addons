"""fix_js_args, without rewriting CPython bytecode.

Replaced rather than vendored. js2py's own version disassembles each of its
native methods and splices two extra parameters into the bytecode, turning
the `this` its bodies read as a global into a local. That works only for the
bytecode layout it was written against: Python 3.12 raises KeyError from the
LOAD_GLOBAL remapping, and 3.13 refuses at import time with "Your python
version made changes to the bytecode". Kodi 21 is on 3.11 and later Kodi is
not going to stay there.

Nothing about the job needs bytecode. js2py's natives read `this` and
`arguments` out of their own module's globals, so a wrapper with the right
signature can put them there for the duration of the call and take them
away again. The wrapper is built with exec so that co_argcount and
co_varnames come out exactly as js2py expects -- PyJsFunction reads both to
work out how many arguments the JavaScript function declares, and treats
"was it returned unchanged" as meaning it already carries a scope.

Save and restore rather than assign: natives call back into JavaScript,
which calls other natives, and the values have to unwind with the calls.
That makes it re-entrant but not thread-safe -- lib/botguard_py.py holds a
lock around the one place this library is used.
"""

__all__ = ['fix_js_args']

_MISSING = object()
# What a function that already carries them looks like: js2py's own natives
# end in (this, arguments), and translated JavaScript ends in
# (arguments, var).
_ALREADY = (('this', 'arguments'), ('arguments', 'var'))

_TEMPLATE = """
def _wrapper(%(params)sthis, arguments):
    _held_this = _scope.get('this', _MISSING)
    _held_args = _scope.get('arguments', _MISSING)
    _scope['this'] = this
    _scope['arguments'] = arguments
    try:
        return _func(%(args)s)
    finally:
        if _held_this is _MISSING:
            _scope.pop('this', None)
        else:
            _scope['this'] = _held_this
        if _held_args is _MISSING:
            _scope.pop('arguments', None)
        else:
            _scope['arguments'] = _held_args
"""


def fix_js_args(func):
    """Give `func` two more parameters, this and arguments, if it lacks them."""
    code = func.__code__
    count = code.co_argcount
    if code.co_varnames[max(count - 2, 0):count] in _ALREADY:
        return func
    names = list(code.co_varnames[:count])
    joined = ", ".join(names)
    namespace = {"_func": func, "_scope": func.__globals__,
                 "_MISSING": _MISSING}
    exec(_TEMPLATE % {"params": (joined + ", ") if names else "",
                      "args": joined}, namespace)
    wrapper = namespace["_wrapper"]
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper
