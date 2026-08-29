"""Solving the ``n`` parameter.

Every googlevideo URL carries an ``n`` the player mints scrambled and the page
rewrites before fetching. Measured on a URL known to be served: rotating one
character of ``n`` turns 15,010,219 bytes into an empty-bodied 403, and
dropping it does the same. So it is not decoration and it cannot be omitted --
it has to be computed.

The transform lives in the player JavaScript and is regenerated with each
release, so there is no algorithm to reimplement, only a language to run.
Two ways to run it, tried in order:

* the vendored yt-dlp interpreter in jsinterp.py, which needs nothing
  installed; and
* a real JS runtime, if one is on PATH.

The order is deliberate but the fallback is not decoration either. yt-dlp
itself stopped using its own interpreter for this: as of 2026.8 every YouTube
n-challenge provider it ships shells out to deno, node, bun or quickjs, and
nothing pure-Python remains. That is the project with the most invested in this
problem saying the interpreter no longer keeps up with the obfuscation. We may
be luckier -- one player, one function -- but the log says which route
succeeded, so the answer is a measurement rather than a hope.
"""

import json
import os
import re
import subprocess
import tempfile

from . import kodiutils

CACHE_FILE = "nsig_cache.json"
# Keep the solved values, not just the player: n is per-URL, but a playback
# session reuses the same one across every segment request.
_MEMO = {}

# The page names its player build; the JS is served from the same host.
_PAGE_JS_URL = re.compile(r'"jsUrl"\s*:\s*"([^"]+base\.js)"')

# How the player reaches the n transform. Several shapes, because players in
# the wild disagree: n is read via .get("n") or via a variable built from
# String.fromCharCode(110), and the transform is either named outright or
# picked out of an array of candidates.
#
# Compiled here at import rather than at the point of use. The first version of
# this was assembled by hand into one alternation and never compiled -- it was
# malformed, and the failure surfaced on a user's machine as "unbalanced
# parenthesis at position 159" rather than on the machine that wrote it.
_N_FUNC_PATTERNS = tuple(re.compile(p) for p in (
    r'\.get\("n"\)\)&&\(b=(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\(',
    r'b=String\.fromCharCode\(110\),c=a\.get\(b\)\)&&\(c='
    r'(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\(',
    r'(?P<var>[a-zA-Z0-9_$.]+)&&\(b="nn"\[\+(?P=var)\][^)]*\),c='
    r'(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\(',
    r'\.set\("n",(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\(',
))

# Where n is touched at all, plus the landmarks the transform is known by.
# Only used to report what a player actually looks like when no pattern
# matches, so the next pattern is written from the player rather than from
# memory. The es6 build defeated every pattern and its report showed a single
# .get("n") belonging to a url-rewriting helper -- useful, but it would have
# been more useful still to know which of these landmarks were present.
_N_SITES = tuple(re.compile(p) for p in (
    r'\.get\("n"\)',
    r'String\.fromCharCode\(110\)',
    r'"nn"\[',
    r'\.set\("n",',
    # The transform's own fingerprints: it catches its errors and returns a
    # string starting "enhanced_except_", and it works on a split string.
    r'enhanced_except',
    r'String\.prototype\.split\.call',
    r'\.split\(""\)',
))


def describe_sites(js, width=220, limit=2):
    """What the player looks like around the places it touches n.

    Reports every landmark, present or absent: knowing that
    ``String.fromCharCode(110)`` appears nowhere is as informative as seeing
    where it does, and a report listing only what matched hides that.
    """
    lines = []
    for pattern in _N_SITES:
        found = list(pattern.finditer(js))
        if not found:
            lines.append("  %-28s absent" % pattern.pattern)
            continue
        lines.append("  %-28s %d occurrence(s)"
                     % (pattern.pattern, len(found)))
        for match in found[:limit]:
            start = max(0, match.start() - width // 3)
            lines.append("      %s" % js[start:start + width])
    return "\n".join(lines)


def resolve_function(js):
    """Find the n transform and return (source to run, function name).

    The source comes back because the transform is not always a named function:
    where the player stores it inline in an array, a wrapper is appended that
    gives it a name, rather than trying to hand the interpreter a fragment.
    """
    match = None
    for pattern in _N_FUNC_PATTERNS:
        match = pattern.search(js)
        if match:
            break
    if not match:
        kodiutils.log("nsig: no pattern matched. The player touches n here:\n%s"
                      % describe_sites(js))
        raise NsigError("no n transform found in the player js")
    name, index = match.group("nfunc"), match.groupdict().get("idx")
    if index is None:
        return js, name

    body = _array_body(js, name)
    if body is None:
        raise NsigError("the n transform is indexed but its array is missing")
    members = _split_top_level(body)
    try:
        member = members[int(index)]
    except (IndexError, ValueError):
        raise NsigError("the n transform index %s is out of range for %s"
                        % (index, name))
    if _IDENTIFIER.match(member):
        return js, member
    return ("%s\n;function %s(a){return (%s)(a)}"
            % (js, SYNTHETIC_NAME, member)), SYNTHETIC_NAME


def _solve_with_interpreter(js, name, value):
    from .jsinterp import JSInterpreter
    return JSInterpreter(js).call_function(name, value)


def _runtime_on_path():
    for name in _RUNTIMES:
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return name, candidate
    return None, None


def _solve_with_runtime(js, name, value):
    runtime, path = _runtime_on_path()
    if not runtime:
        raise NsigError("no javascript runtime on PATH (tried %s)"
                        % ", ".join(_RUNTIMES))
    # The player js is a module-scoped blob; wrap it so the function is
    # reachable and print only the result.
    script = "%s\nprocess.stdout.write(String(%s(%s)))" % (
        js, name, json.dumps(value))
    if runtime in ("qjs", "quickjs"):
        script = "%s\nconsole.log(String(%s(%s)))" % (js, name, json.dumps(value))
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False,
                                         encoding="utf-8")
    try:
        handle.write(script)
        handle.close()
        result = subprocess.run([path, handle.name], capture_output=True,
                                timeout=30)
        if result.returncode != 0:
            raise NsigError("%s failed: %s"
                            % (runtime, result.stderr.decode("utf-8", "replace")[:200]))
        return result.stdout.decode("utf-8", "replace").strip()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def solve(js, value, player_id=""):
    """Transform one ``n``, by whichever route works.

    Cached per player release and value, because ISA asks for many segments and
    each one would otherwise reparse a megabyte of JavaScript.
    """
    key = "%s:%s" % (player_id, value)
    if key in _MEMO:
        return _MEMO[key]
    stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
    if key in stored:
        _MEMO[key] = stored[key]
        return stored[key]

    js, name = resolve_function(js)
    kodiutils.log("nsig: player %s uses %s()" % (player_id or "?", name))
    errors = []
    for label, solver in (("interpreter", _solve_with_interpreter),
                          ("js runtime", _solve_with_runtime)):
        try:
            result = solver(js, name, value)
        except Exception as exc:
            errors.append("%s: %s" % (label, exc))
            continue
        if not result or result == value:
            errors.append("%s: returned the input unchanged" % label)
            continue
        kodiutils.log("nsig: %s solved %s -> %s via the %s"
                      % (player_id or "?", value, result, label))
        _MEMO[key] = result
        stored[key] = result
        kodiutils.write_json(CACHE_FILE, stored)
        return result
    raise NsigError("; ".join(errors))
