#!/usr/bin/env python3
"""Check whether this machine can solve YouTube TV's n parameter.

Run it on the Kodi box, in a plain terminal:

    python3 youtube_tv_check_nsig.py

It finds a JavaScript runtime, downloads the player build that still names the
transform, applies it to a value captured from a real browser session, and
compares the answer with what that browser actually sent. A match means this
machine can do everything the addon needs; anything else says which step failed.

Self-contained on purpose -- no addon imports -- so it can be run anywhere,
including inside the Kodi flatpak, where the host's PATH may not be visible.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

# Captured from a signed-in browser session on player 06ab6907: the value the
# player minted, and the value the browser actually sent after transforming it.
SAMPLE_IN = "UQpyO2dm0XQSunbyNa"
SAMPLE_OUT = "ygW6YjigTA7D-Q"
PLAYER = ("https://tv.youtube.com/s/player/%s/"
          "player_ias_tce.vflset/en_US/base.js")
DEFAULT_PLAYER_ID = "06ab6907"
RUNTIMES = ("deno", "node", "bun", "qjs", "quickjs")

_SET_N_CALL = re.compile(
    r'(?P<nfunc>[\w$]+)\(\s*(?P<arg>[\w$]+)\s*\)\s*,\s*'
    r'[\w$.]+\.set\("n"\s*,\s*(?P=arg)\s*\)')
_SENTINEL = re.compile(r'typeof\s+([\w$]+)\s*===?\s*"undefined"')


def find_runtime():
    for name in RUNTIMES:
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = os.path.join(directory, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return name, path
    return None, None


def regex_allowed(js, index):
    j = index - 1
    while j >= 0 and js[j] in " \t\n":
        j -= 1
    if j < 0 or js[j] in "(,=:[!&|?{};+-*%~^<>":
        return True
    return any(js[max(0, j - len(w) + 1):j + 1] == w for w in
               ("return", "typeof", "case", "in", "of", "new", "delete", "void"))


def slice_function(js, name):
    match = re.search(r'\b%s\s*=\s*function\s*\(([^)]*)\)\s*\{' % re.escape(name), js)
    if not match:
        raise SystemExit("could not find function %s" % name)
    args = [a.strip() for a in match.group(1).split(",") if a.strip()]
    start = js.index("{", match.end() - 1)
    depth, k = 0, start
    while k < len(js):
        ch = js[k]
        if ch in "\"'`":
            quote, k = ch, k + 1
            while k < len(js):
                if js[k] == "\\":
                    k += 2
                    continue
                if js[k] == quote:
                    break
                k += 1
        elif ch == "/" and js[k + 1:k + 2] == "/":
            k = js.find("\n", k)
            if k < 0:
                break
        elif ch == "/" and js[k + 1:k + 2] == "*":
            k = js.index("*/", k) + 1
        elif ch == "/" and regex_allowed(js, k):
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return args, js[start + 1:k]
        k += 1
    raise SystemExit("unbalanced braces reading %s" % name)


def main():
    # A player id, or a path to a base.js already on disk.
    player_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PLAYER_ID

    runtime, path = find_runtime()
    if not runtime:
        print("FAIL  no JavaScript runtime found on PATH")
        print("      looked for: %s" % ", ".join(RUNTIMES))
        print("      install one, e.g.  sudo apt install nodejs")
        print("      PATH was: %s" % os.environ.get("PATH", ""))
        return 1
    print("ok    runtime: %s (%s)" % (runtime, path))

    # A local path works too, for checking a build already downloaded, or on a
    # machine that cannot reach youtube but can still run the transform.
    if os.path.exists(player_id):
        url = player_id
        with open(url, encoding="utf-8", errors="replace") as handle:
            js = handle.read()
    else:
        url = PLAYER % player_id
        try:
            with urllib.request.urlopen(url, timeout=60) as reply:
                js = reply.read().decode("utf-8", "replace")
        except Exception as exc:
            print("FAIL  could not download %s\n      %s" % (url, exc))
            return 1
    print("ok    player: %d bytes from %s" % (len(js), url))

    match = _SET_N_CALL.search(js)
    if not match:
        print('FAIL  no set("n", ...) call in this build -- wrong variant?')
        return 1
    name = match.group("nfunc")
    args, body = slice_function(js, name)
    print("ok    transform: %s(%s), %d bytes" % (name, ",".join(args), len(body)))

    preamble = []
    for sentinel in dict.fromkeys(_SENTINEL.findall(body)):
        found = re.search(
            r'var\s+%s\s*=\s*(-?[\d.]+|"[^"]*")\s*[;,]' % re.escape(sentinel), js)
        if found:
            preamble.append(found.group(0).rstrip(",;") + ";")
    print("ok    sentinels: %s" % (" ".join(preamble) or "none needed"))

    emit = "console.log" if runtime != "qjs" else "print"
    program = "%s\nfunction %s(%s){%s}\n%s(%s(%s));\n" % (
        "\n".join(preamble), name, ",".join(args), body,
        emit, name, json.dumps(SAMPLE_IN))
    handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8")
    try:
        handle.write(program)
        handle.close()
        result = subprocess.run([path, handle.name], capture_output=True,
                                timeout=120)
    finally:
        os.unlink(handle.name)

    if result.returncode != 0:
        print("FAIL  %s exited %d\n      %s"
              % (runtime, result.returncode,
                 result.stderr.decode("utf-8", "replace")[:400]))
        return 1

    got = result.stdout.decode("utf-8", "replace").strip()
    print("      %s -> %s" % (SAMPLE_IN, got))
    if got == SAMPLE_OUT:
        print("PASS  matches what the browser sent. This machine can solve n.")
        return 0
    if got == SAMPLE_IN:
        print("FAIL  returned its input unchanged -- a sentinel global is")
        print("      missing, so the transform bailed without erroring.")
    else:
        print("FAIL  expected %s" % SAMPLE_OUT)
        print("      A newer player would explain this: the sample pair was")
        print("      captured on %s. Pass the current player id as an" % DEFAULT_PLAYER_ID)
        print("      argument only if you also have a fresh pair to check.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
