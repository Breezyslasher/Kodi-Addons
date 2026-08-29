"""Solving the ``n`` parameter.

Every googlevideo URL carries an ``n`` the player mints scrambled and the page
rewrites before fetching. Measured on a URL known to be served: rotating one
character of ``n`` turns 15,010,219 bytes into an empty-bodied 403, and
dropping it does the same. So it has to be computed.

The transform is in the player, but only in some builds of it. The two the
page points at hide it behind an opcode VM; the two ``tce`` builds of the same
release still contain it as an ordinary function, reached through the value
handed to ``set("n", ...)``:

    a.D&&(eO(a),b=a.j.n||null)&&(b=Yma(b),a.set("n",b))

It also opens with a guard on a sentinel global::

    if(typeof Xma==="undefined")return a;

where ``Xma`` is a bare number declared elsewhere in the player. Without it the
function returns its input untouched -- no error and no clue, which is a far
worse failure than an exception, because the URL then looks transformed and is
not. Both are carried into the program we run.

Running it needs a real JavaScript engine. The vendored yt-dlp interpreter in
jsinterp.py cannot: it stops on an unbraced ``if`` body and again on ``typeof``,
and patching past those two only reached the transform's own catch block.
That matches yt-dlp's own conclusion -- as of 2026.8 every YouTube n-challenge
provider it ships shells out to deno, node, bun or quickjs, and nothing
pure-Python remains. jsinterp stays because it is sound for the simpler
signature work, but it is not on this path.

Verified end to end before being wired in: on player 06ab6907, the captured
``UQpyO2dm0XQSunbyNa`` transforms to ``ygW6YjigTA7D-Q``, which is byte for byte
what the browser sent.
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

# Engines worth looking for, best first. deno and bun need no package manager
# and run from a single binary, which matters on LibreELEC where there is no
# package manager at all.
_RUNTIMES = ("deno", "node", "bun", "qjs", "quickjs")
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


def in_flatpak():
    """Whether we are inside a flatpak sandbox."""
    return os.path.exists("/.flatpak-info")


def _runtime_on_path():
    """Find a JavaScript engine, and say how it has to be invoked.

    Returns (name, argv-prefix). Three places to look, because Kodi is rarely
    installed the way a developer's shell is:

    * the setting, for anywhere the other two fail;
    * PATH and the usual directories; and
    * the host, through flatpak-spawn.

    That last one is the case that actually bit: a flatpak cannot see
    /usr/bin/node however plainly the user's terminal can, so a runtime that
    passes the standalone check still leaves the addon reporting none found.
    flatpak-spawn --host runs it on the host properly, rather than borrowing
    the host binary into a sandbox whose libraries it was not built against.
    """
    configured = kodiutils.get_setting("js_runtime", "")
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return os.path.basename(configured), [configured]
        kodiutils.log("nsig: configured runtime %r is not executable, "
                      "looking elsewhere" % configured)

    places = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    places += ["/usr/bin", "/usr/local/bin", "/bin", "/opt/homebrew/bin",
               "/var/lib/flatpak/exports/bin", "/snap/bin", "/storage"]
    for name in _RUNTIMES:
        for directory in places:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return name, [candidate]

    # Why it failed matters as much as that it did. A run that reported "no
    # runtime" in under half a second turned out to be a machine where the
    # flatpak had never been granted --talk-name=org.freedesktop.Flatpak, so
    # the host probes could not run at all -- which the message could not say
    # because it recorded nothing about how it looked.
    trail = []
    sandboxed = in_flatpak()
    spawn = "/usr/bin/flatpak-spawn"
    trail.append("flatpak=%s spawn=%s" % (sandboxed, os.path.exists(spawn)))
    if sandboxed and os.path.exists(spawn):
        for name in _RUNTIMES:
            try:
                probe = subprocess.run([spawn, "--host", name, "--version"],
                                       capture_output=True, timeout=20)
            except Exception as exc:
                trail.append("%s:%s" % (name, type(exc).__name__))
                continue
            if probe.returncode == 0:
                kodiutils.log("nsig: using the host's %s through "
                              "flatpak-spawn" % name)
                return name, [spawn, "--host", name]
            trail.append("%s:rc=%d" % (name, probe.returncode))
    kodiutils.log("nsig: no runtime found -- %s" % " ".join(trail))
    return None, None


def _solve_with_runtime(js, value):
    """Run the player's own transform in a real JavaScript engine.

    Only the transform: the function sliced out of the player, plus the
    sentinel globals it checks for. Evaluating two and a half megabytes of
    YouTube to rewrite one query parameter would be slower and far more
    fragile.
    """
    runtime, argv = _runtime_on_path()
    if not runtime:
        if in_flatpak():
            raise NsigError(
                "no javascript runtime reachable from inside the flatpak "
                "(tried %s). Kodi cannot see the host's, so grant it the host "
                "runner: flatpak override --user "
                "--talk-name=org.freedesktop.Flatpak tv.kodi.Kodi -- then "
                "restart Kodi. Failing that, put the full path to a runtime "
                "in the addon's settings." % ", ".join(_RUNTIMES))
        raise NsigError(
            "no javascript runtime found (tried %s). Install one -- on Debian "
            "or Ubuntu, 'sudo apt install nodejs'; on LibreELEC unpack a node "
            "or deno build under /storage and set its full path in the "
            "addon's settings." % ", ".join(_RUNTIMES))

    name, program = build_program(js, value)
    # quickjs has no console.log-to-stdout convention worth relying on.
    emit = "print(__r);" if runtime in ("qjs", "quickjs") else "console.log(__r);"
    # The script goes in the addon's own profile directory, not /tmp.
    # /tmp inside a flatpak is the sandbox's own, so the host's node -- reached
    # through flatpak-spawn -- is handed a path that does not exist for it and
    # reports "Cannot find module". The profile directory is a real host path
    # visible under the same name on both sides, which is exactly what is
    # needed to pass a filename across that boundary.
    try:
        directory = kodiutils.profile_dir()
    except Exception:
        directory = tempfile.gettempdir()
    script = os.path.join(directory, "nsig_%d.js" % os.getpid())
    try:
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(program + "\n" + emit + "\n")
        result = subprocess.run(argv + [script], capture_output=True,
                                timeout=60)
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
    if result.returncode != 0:
        raise NsigError("%s exited %d: %s"
                        % (runtime, result.returncode,
                           result.stderr.decode("utf-8", "replace")[:300]))
    return runtime, name, result.stdout.decode("utf-8", "replace").strip()


_KEPT = set()


def _keep(js, player_id):
    """Save the player and the program built from it, once per release."""
    if not player_id or player_id in _KEPT:
        return
    _KEPT.add(player_id)
    try:
        import os
        where = kodiutils.profile_dir()
        player = os.path.join(where, "player-%s.js" % player_id)
        if not os.path.exists(player):
            with open(player, "w", encoding="utf-8") as handle:
                handle.write(js)
        name, program = build_program(js, "0123456789abcdefghij")
        built = os.path.join(where, "nsig-%s.js" % player_id)
        with open(built, "w", encoding="utf-8") as handle:
            handle.write(program)
        kodiutils.log("nsig: kept %s (%d bytes) and %s (%s(), %d bytes)"
                      % (player, len(js), built, name, len(program)))
    except Exception as exc:
        kodiutils.log("nsig: could not keep the player for %s: %s"
                      % (player_id, exc))


def solve(js, value, player_id=""):
    """Transform one ``n``.

    Cached per player release and value: ISA asks for many segments and they
    share the value the player minted, so the engine runs once rather than once
    per request.
    """
    key = "%s:%s" % (player_id, value)
    if key in _MEMO:
        return _MEMO[key]
    stored = kodiutils.read_json(CACHE_FILE, default={}) or {}
    if key in stored:
        _MEMO[key] = stored[key]
        return stored[key]

    # A wrong extraction is indistinguishable from a right one here: the
    # program runs, returns a plausible string, and the server answers the
    # url with an empty-bodied 403. Player e937390a did exactly that, on
    # both the SABR endpoint and the plain file url. So keep the evidence:
    # the player and the program built from it, once per release, named by
    # release, where they can be read afterwards.
    _keep(js, player_id)
    runtime, name, result = _solve_with_runtime(js, value)
    if not result:
        raise NsigError("%s produced nothing for %s" % (runtime, name))
    if result == value:
        # The transform bails to its input when a sentinel global is missing,
        # silently and without erroring. Treating that as success would put a
        # url that looks transformed and is not in front of the player.
        raise NsigError("%s returned the input unchanged -- the transform "
                        "bailed, most likely a sentinel global it checks for "
                        "was not carried across" % name)

    kodiutils.log("nsig: %s solved %s -> %s via %s()/%s"
                  % (player_id or "?", value, result, name, runtime))
    _MEMO[key] = result
    stored[key] = result
    kodiutils.write_json(CACHE_FILE, stored)
    return result


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
