"""Undoing YouTube's signature cipher.

Every format in a YouTube TV player response arrives as a ``signatureCipher``
rather than a usable URL::

    s=0EAAE0s2JYwRAIgJuxJ498XKJSEPQ...   (the scrambled signature)
    sp=sig                                (the parameter to write it back to)
    url=https://rr6---sn-....googlevideo.com/videoplayback?...   (no sig yet)

The scrambling is a short, fixed sequence of operations -- reverse the string,
swap two characters, drop a prefix -- chosen per player release and expressed
as JavaScript in the player's base.js. Unscrambling does not need a JavaScript
engine: the transform function is a list of calls into a small helper object,
and reading which helper method is which is enough to replay them in Python.
This is the same approach the regular Kodi YouTube addon and yt-dlp take, and
it is why they still work while a DASH-manifest approach does not.

Nothing here is copied from those projects; the technique is common knowledge
and the implementation below is written against the shapes described in
YouTube's own player script.
"""

import re

from . import kodiutils

# The player script referenced by a watch page, e.g.
# /s/player/1a2b3c4d/player_ias.vflset/en_US/base.js
PLAYER_JS = re.compile(r'"?(/s/player/[\w.-]+/[\w.-]+/[\w-]+/base\.js)"?')
PLAYER_BASE = "https://www.youtube.com"

CACHE_FILE = "player_cipher.json"


class CipherError(Exception):
    """The player script could not be read, or its transform not found."""


def player_url(html):
    """The base.js URL referenced by a watch page."""
    match = PLAYER_JS.search(html or "")
    if not match:
        raise CipherError("no player script referenced by the page")
    path = match.group(1)
    return PLAYER_BASE + path


# The scrambler always has the same skeleton, whatever its names:
#
#   <name> = function(<var>) { <var> = <var>.split(""); ... return <var>.join("") }
#
# so it is matched by that shape. Earlier versions of this pinned the parameter
# to "a", which is only the most common minifier output -- a player that named
# it anything else went unrecognised.
_TRANSFORM = re.compile(
    r"(?:function\s+(?P<n1>[\w$]+)|(?P<n2>[\w$]+)\s*=\s*function)"
    r"\s*\(\s*(?P<var>[\w$]+)\s*\)\s*\{"
    r"\s*(?P=var)\s*=\s*(?P=var)\.split\(\s*[\"']{2}\s*\)\s*;"
    r"(?P<body>.*?)"
    r"return\s+(?P=var)\.join\(\s*[\"']{2}\s*\)",
    re.DOTALL)


def _find_transform(js):
    """Locate the scrambler and return (name, parameter, body)."""
    match = _TRANSFORM.search(js)
    if not match:
        raise CipherError("could not locate the signature transform")
    name = match.group("n1") or match.group("n2")
    return name, match.group("var"), match.group("body")


def _find_transform_name(js):
    return _find_transform(js)[0]


def describe(js):
    """What the player script looks like where a transform should be.

    Written for the case this has already hit: the patterns above are drawn
    from player releases that have since changed, and guessing at a new one
    from nothing is how three rounds get wasted. Every scrambler splits the
    signature into characters and rejoins it, so the text around each
    `.split("")` is where the answer is.
    """
    notes = ["player script: %d bytes" % len(js)]
    for needle in ('.split("")', ".split(\"\")", "a.reverse()", "a.splice(",
                   "decodeURIComponent"):
        notes.append("  %-20s x%d" % (needle, js.count(needle)))

    for index, match in enumerate(re.finditer(r'\.split\(\s*""\s*\)', js)):
        if index >= 4:
            break
        start = max(0, match.start() - 140)
        end = min(len(js), match.end() + 200)
        snippet = js[start:end].replace("\n", " ")
        notes.append("  context %d: ...%s..." % (index + 1, snippet))
    return "\n".join(notes)


def _helper_operations(js, helper):
    """Map each helper method to what it does.

    The helper is an object literal of three or four short functions. They are
    told apart by shape rather than by name, because the names are minified and
    change every release:

        reverse : contains a.reverse()
        splice  : contains a.splice(
        swap    : assigns a[0] from a temporary, i.e. exchanges two characters
    """
    match = re.search(r"(?:var\s+)?%s\s*=\s*\{(?P<body>.*?)\}\s*;"
                      % re.escape(helper), js, re.DOTALL)
    if not match:
        raise CipherError("helper object %s not found" % helper)
    body = match.group("body")

    operations = {}
    for fn in re.finditer(r"(?P<name>[\w$]+)\s*:\s*function\s*\([^)]*\)\s*\{(?P<code>.*?)\}",
                          body, re.DOTALL):
        code = fn.group("code")
        if "reverse" in code:
            operations[fn.group("name")] = ("reverse", None)
        elif "splice" in code:
            operations[fn.group("name")] = ("splice", None)
        elif "%b" in code or "var c=a[0]" in code.replace(" ", "") or "a[b%a.length]" in code.replace(" ", ""):
            operations[fn.group("name")] = ("swap", None)
    if not operations:
        raise CipherError("helper object %s defined no recognised operations" % helper)
    return operations


def parse(js):
    """Read a player script into a replayable list of (operation, argument)."""
    name, var, body = _find_transform(js)

    helper = None
    steps = []
    # The argument is optional: reverse is called as f(a), swap and splice as
    # f(a, n). Requiring the number silently dropped every reverse step and
    # produced a plausible-looking but wrong signature.
    for call in re.finditer(
            r"(?P<obj>[\w$]+)\.(?P<fn>[\w$]+)\(\s*%s\s*(?:,\s*(?P<arg>\d+)\s*)?\)"
            % re.escape(var), body):
        helper = helper or call.group("obj")
        arg = call.group("arg")
        steps.append((call.group("fn"), int(arg) if arg is not None else 0))
    if not steps:
        raise CipherError("transform %s performed no operations" % name)

    operations = _helper_operations(js, helper)
    plan = []
    for fn, arg in steps:
        if fn not in operations:
            raise CipherError("helper method %s is not a known operation" % fn)
        plan.append((operations[fn][0], arg))
    return plan


def apply(plan, signature):
    """Replay a parsed plan over a scrambled signature."""
    chars = list(signature)
    for operation, arg in plan:
        if operation == "reverse":
            chars.reverse()
        elif operation == "splice":
            chars = chars[arg:]
        elif operation == "swap":
            index = arg % len(chars)
            chars[0], chars[index] = chars[index], chars[0]
        else:
            raise CipherError("unknown operation %r" % operation)
    return "".join(chars)


def descramble(js, signature):
    return apply(parse(js), signature)


def resolve(cipher, plan):
    """Turn a ``signatureCipher`` value into a usable URL.

    ``sp`` names the query parameter the unscrambled signature belongs in --
    normally "sig", but it has been renamed before, so it is read rather than
    assumed.
    """
    from urllib.parse import parse_qs, quote

    fields = parse_qs(cipher)
    url = (fields.get("url") or [""])[0]
    scrambled = (fields.get("s") or [""])[0]
    param = (fields.get("sp") or ["sig"])[0]
    if not url:
        raise CipherError("signatureCipher carried no url")
    if not scrambled:
        return url
    signature = apply(plan, scrambled)
    joiner = "&" if "?" in url else "?"
    return "%s%s%s=%s" % (url, joiner, param, quote(signature, safe=""))


def _save_script(js):
    try:
        import os
        path = os.path.join(kodiutils.profile_dir(), "player.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(js)
        kodiutils.log("cipher: player script saved to %s" % path)
    except Exception as exc:
        kodiutils.log_error("cipher: could not save the player script: %s" % exc)


def cached_plan():
    return kodiutils.read_json(CACHE_FILE, default=None)


def cache_plan(player, plan):
    kodiutils.write_json(CACHE_FILE, {"player": player, "plan": plan})


# -- fetching -----------------------------------------------------------

def fetch_plan(session, headers, watch_url):
    """Read the player script the watch page names, and parse its transform.

    Cached against the player URL: the transform changes only when Google
    ships a new player, and base.js is around a megabyte, so re-parsing it for
    every play would be wasteful and slow.
    """
    page = session.get(watch_url, headers=headers, timeout=30)
    if page.status_code != 200:
        raise CipherError("watch page returned HTTP %d" % page.status_code)
    url = player_url(page.text)

    cached = cached_plan()
    if cached and cached.get("player") == url and cached.get("plan"):
        kodiutils.log("cipher: reusing the cached plan for %s"
                      % url.rsplit("/", 4)[1])
        return url, [tuple(step) for step in cached["plan"]]

    script = session.get(url, headers=headers, timeout=30)
    if script.status_code != 200:
        raise CipherError("player script returned HTTP %d" % script.status_code)
    try:
        plan = parse(script.text)
    except CipherError:
        # Keep the script and describe it, so the next attempt is written
        # against the player that actually shipped rather than a guess.
        _save_script(script.text)
        kodiutils.log("cipher: %s" % describe(script.text))
        raise
    cache_plan(url, plan)
    kodiutils.log("cipher: parsed %d operations from %s"
                  % (len(plan), url.rsplit("/", 4)[1]))
    return url, plan
