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

class NsigError(Exception):
    """The transform could not be found, or could not be run."""


CACHE_FILE = "nsig_cache.json"
VARIANTS_FILE = "player_variants.json"
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


# A transform that scrambles a string almost always splits it into characters
# and joins it back, so a function containing both is a candidate whatever it
# is called. This is how the transform gets found in a player that carries none
# of the landmarks above -- the YouTube TV player has no enhanced_except, no
# String.prototype.split.call, no fromCharCode(110) and no .set("n", anywhere
# in either of its builds.
_JOIN = re.compile(r'\.join\(\s*(""|\'\')\s*\)')
_FUNC_HEAD = re.compile(
    r'(?:(?P<assigned>[\w$.]+)\s*=\s*function\s*\(\s*(?P<arg1>\w+)\s*\)'
    r'|function\s+(?P<named>[\w$]+)\s*\(\s*(?P<arg2>\w+)\s*\))\s*\{')


def describe_candidates(js, look_back=1600, width=260, limit=12):
    """Functions that split a string apart and join it back together.

    Reported so a pattern can be written from the player at hand rather than
    from the shapes older players happened to use.
    """
    seen, lines = set(), []
    for join in _JOIN.finditer(js):
        window = js[max(0, join.start() - look_back):join.start()]
        heads = list(_FUNC_HEAD.finditer(window))
        if not heads:
            continue
        head = heads[-1]
        name = head.group("assigned") or head.group("named")
        if not name or name in seen:
            continue
        seen.add(name)
        start = max(0, join.start() - look_back) + head.start()
        lines.append("  %-14s %s" % (name, js[start:start + width]))
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "  nothing splits and rejoins a string here"


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
        kodiutils.log("nsig: no pattern matched.\nLandmarks:\n%s"
                      "\nFunctions that split and rejoin a string:\n%s"
                      % (describe_sites(js), describe_candidates(js)))
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


# How the tce builds reach the transform:
#
#     a.D&&(eO(a),b=a.j.n||null)&&(b=Yma(b),a.set("n",b))
#
# The name is whatever is applied to n immediately before set("n", ...), and
# that is the only shape that has ever actually matched a YouTube TV player.
_SET_N_CALL = re.compile(
    r'(?P<nfunc>[\w$]+)\(\s*(?P<arg>[\w$]+)\s*\)\s*,\s*'
    r'[\w$.]+\.set\("n"\s*,\s*(?P=arg)\s*\)')

# The transform refuses to work unless a sentinel global exists:
#
#     if(typeof Xma==="undefined")return a;
#
# Xma is a bare number declared elsewhere in the player. Miss it and the
# function returns its input untouched -- no error, no clue, and a url that
# looks transformed but is not. Node reproduced exactly that until the global
# was supplied, so the sentinel is carried with the function.
_SENTINEL = re.compile(r'typeof\s+([\w$]+)\s*===?\s*"undefined"')


def _regex_allowed(js, index):
    """Whether a '/' at this position starts a regex rather than a division."""
    j = index - 1
    while j >= 0 and js[j] in " \t\n":
        j -= 1
    if j < 0:
        return True
    if js[j] in "(,=:[!&|?{};+-*%~^<>":
        return True
    for word in ("return", "typeof", "case", "in", "of", "new", "delete", "void"):
        if js[max(0, j - len(word) + 1):j + 1] == word:
            return True
    return False


def slice_function(js, name):
    """Cut a named function out of minified JavaScript, braces balanced.

    Counting braces naively runs past the end, because a brace inside a string,
    a regex literal or a comment is not a brace -- a first attempt overshot by
    16 KB into unrelated code. This skips all three.
    """
    match = re.search(r'\b%s\s*=\s*function\s*\(([^)]*)\)\s*\{' % re.escape(name), js)
    if not match:
        match = re.search(r'\bfunction\s+%s\s*\(([^)]*)\)\s*\{' % re.escape(name), js)
    if not match:
        raise NsigError("no function named %s in the player" % name)
    args = [a.strip() for a in match.group(1).split(",") if a.strip()]
    start = js.index("{", match.end() - 1)
    depth, k = 0, start
    while k < len(js):
        char = js[k]
        if char in "\"'`":
            quote, k = char, k + 1
            while k < len(js):
                if js[k] == "\\":
                    k += 2
                    continue
                if js[k] == quote:
                    break
                k += 1
        elif char == "/" and js[k + 1:k + 2] == "/":
            k = js.find("\n", k)
            if k < 0:
                break
        elif char == "/" and js[k + 1:k + 2] == "*":
            k = js.index("*/", k) + 1
        elif char == "/" and _regex_allowed(js, k):
            k += 1
            while k < len(js):
                if js[k] == "\\":
                    k += 2
                    continue
                if js[k] == "[":
                    while k < len(js) and js[k] != "]":
                        k += 2 if js[k] == "\\" else 1
                elif js[k] == "/":
                    break
                k += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return args, js[start + 1:k]
        k += 1
    raise NsigError("unbalanced braces reading %s" % name)


def build_program(js, value):
    """A standalone script that applies the player's n transform to one value.

    Self-contained on purpose: the transform plus the sentinel globals it
    checks for, and nothing else of the player. Two and a half megabytes of
    YouTube do not need to be evaluated to rewrite one query parameter.
    """
    match = _SET_N_CALL.search(js)
    if not match:
        raise NsigError("no set(\"n\", ...) call found in the player")
    name = match.group("nfunc")
    args, body = slice_function(js, name)

    preamble = []
    for sentinel in dict.fromkeys(_SENTINEL.findall(body)):
        declaration = re.search(
            r'var\s+%s\s*=\s*(-?[\d.]+|"[^"]*")\s*[;,]' % re.escape(sentinel), js)
        if declaration:
            preamble.append(declaration.group(0).rstrip(",") .rstrip(";") + ";")
    return name, "%s\nfunction %s(%s){%s}\nvar __r=%s(%s);" % (
        "\n".join(preamble), name, ",".join(args), body,
        name, json.dumps(value))


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


# Builds Google publishes for one player id. The page asks for player_es6 and
# that one is an opcode VM, but it is not the only build of the same release:
# the embed player is a sixth of the size, and the tce and tv variants are
# compiled differently again. A build that still names its functions would make
# the transform readable without a JavaScript engine.
PLAYER_VARIANTS = (
    ("tv.youtube.com", "player_es6.vflset/en_US/base.js"),
    ("tv.youtube.com", "player_ias.vflset/en_US/base.js"),
    ("tv.youtube.com", "player_ias_tce.vflset/en_US/base.js"),
    ("tv.youtube.com", "player_es6_tce.vflset/en_US/base.js"),
    ("tv.youtube.com", "tv-player-ias.vflset/tv-player-ias.js"),
    ("tv.youtube.com", "tv-player-es6.vflset/tv-player-es6.js"),
    ("www.youtube.com", "player_embed_es6.vflset/en_US/base.js"),
    ("www.youtube.com", "player_ias.vflset/en_US/base.js"),
    # Small siblings served beside the player. unplugged.js is the only one
    # written for YouTube TV specifically, so it is worth a look even at 11 KB.
    ("tv.youtube.com", "player_es6.vflset/en_US/unplugged.js"),
    ("tv.youtube.com", "player_es6.vflset/en_US/heartbeat.js"),
)


def survey_variants(session, player_id, user_agent):
    """Report which builds of this player exist and what each one shows.

    A landmark count per file, so the answer is which build to read rather than
    a guess about which might be friendlier. Sizes alone would not say: a small
    file that is equally obfuscated is no use, and a large one that still names
    its functions is exactly what is wanted.
    """
    lines = ["player variants for %s:" % player_id]
    for host, path in PLAYER_VARIANTS:
        url = "https://%s/s/player/%s/%s" % (host, player_id, path)
        try:
            # The caller's session, not a fresh requests.get: it carries the
            # connection pool the player js was already fetched over, and a
            # function that takes a session and then ignores it is a function
            # that cannot be tested without going to the network.
            reply = session.get(url, timeout=25,
                                headers={"User-Agent": user_agent})
        except Exception as exc:
            lines.append("  %-46s %s" % (path, exc))
            continue
        if reply.status_code != 200:
            lines.append("  %-46s HTTP %d" % (path, reply.status_code))
            continue
        js = reply.text
        found = [name for name, pattern in (
            ("enhanced_except", "enhanced_except"),
            ("fromCharCode(110)", "String.fromCharCode(110)"),
            ('set("n"', '.set("n",'),
            ("split.call", "String.prototype.split.call"),
        ) if pattern in js]
        lines.append("  %-46s %-9s %8d bytes  %s"
                     % (path, host.split(".")[0], len(js),
                        ", ".join(found) or "no landmarks"))
    return "\n".join(lines)
