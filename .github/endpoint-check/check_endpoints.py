#!/usr/bin/env python3
"""Generic endpoint checker for the addons' private APIs.

Usage:
    python3 check_endpoints.py path/to/spec.json

Each addon describes the chain of calls it depends on in a JSON spec; this
script runs them in order and exits non-zero if any check fails, so a daily
GitHub Actions run notices when a service renames or removes something.
Standard library only, so the workflow needs no pip install.

Spec format:

{
  "name": "Starz",
  "defaults": {"headers": {"User-Agent": "..."}, "timeout": 20},
  "vars": {"device_id": "{random_hex32}"},
  "checks": [
    {
      "id": "mint",                         # unique name
      "method": "GET",                      # default GET
      "url": "https://host/path/{device_id}",   # {var} substitution
      "params": {"q": "x", "multi": ["a", "b"]},
      "headers": {"X-Token": "{auth_token}"},
      "json": {...},                        # request body, sent as JSON
      "uses": ["mint"],                     # re-run these first (fresh tokens)
      "expect": {
        "status": 200,                      # int or list of ints
        "content_type": "image/",           # prefix
        "text_prefix": "PLAYAUTH",
        "json_paths": ["a.b[0].c"]          # must exist and be non-null
      },
      "save": {"auth_token": "text", "ep": "json:a.b[0].id"},
      "print": ["isAllowedAccess"],         # non-secret JSON fields to show
      "report_only": false                  # true: never fails the run
    }
  ]
}

Output never includes response bodies, and every saved value is masked in
GitHub's logs (they are tokens more often than not).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def builtin(name):
    if name == "random_hex32":
        return os.urandom(16).hex()
    return None


def substitute(value, env):
    if isinstance(value, str):
        def repl(m):
            key = m.group(1)
            if key not in env:
                raise KeyError(key)
            return str(env[key])
        return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)
    if isinstance(value, dict):
        return {k: substitute(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, env) for v in value]
    return value


def json_path(data, path):
    """a.b[0].c -> value, or raise KeyError."""
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            data = data[int(part[1:-1])]
        else:
            if not isinstance(data, dict) or part not in data:
                raise KeyError(path)
            data = data[part]
    if data is None:
        raise KeyError(path)
    return data


def request(check, defaults, env):
    method = check.get("method", "GET").upper()
    url = substitute(check["url"], env)
    params = substitute(check.get("params") or {}, env)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    headers = dict(defaults.get("headers") or {})
    headers.update(substitute(check.get("headers") or {}, env))
    body = None
    if "json" in check:
        body = json.dumps(substitute(check["json"], env)).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    timeout = check.get("timeout", defaults.get("timeout", 20))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type", ""), err.read()


def run_check(check, spec, env, by_id, depth=0):
    """Returns (ok, message)."""
    for dep in check.get("uses") or []:
        if depth > 5:
            return False, "dependency loop"
        ok, msg = run_check(by_id[dep], spec, env, by_id, depth + 1)
        if not ok:
            return False, "dependency %s failed: %s" % (dep, msg)
    try:
        status, ctype, raw = request(check, spec.get("defaults") or {}, env)
    except KeyError as missing:
        return False, "needs {%s}, which an earlier check did not save" % missing.args[0]
    except Exception as exc:
        return False, "request failed: %s" % exc
    summary = "HTTP %d, %s, %d bytes" % (status, ctype.split(";")[0] or "-", len(raw))
    expect = check.get("expect") or {}
    want = expect.get("status", 200)
    want = want if isinstance(want, list) else [want]
    if status not in want:
        return False, "%s (expected %s)" % (summary, "/".join(map(str, want)))
    if expect.get("content_type") and not ctype.startswith(expect["content_type"]):
        return False, "%s (expected content type %s)" % (summary, expect["content_type"])
    text = raw.decode("utf-8", "replace")
    if expect.get("text_prefix") and not text.strip().strip('"').startswith(expect["text_prefix"]):
        return False, "%s (body does not start with %r)" % (summary, expect["text_prefix"])
    data = None
    if expect.get("json_paths") or check.get("print") or any(
            str(v).startswith("json:") for v in (check.get("save") or {}).values()):
        try:
            data = json.loads(text)
        except ValueError:
            return False, "%s (not JSON)" % summary
    for path in expect.get("json_paths") or []:
        try:
            json_path(data, path)
        except (KeyError, IndexError, TypeError):
            return False, "%s (missing %s)" % (summary, path)
    for name, source in (check.get("save") or {}).items():
        try:
            value = (text.strip().strip('"') if source == "text"
                     else json_path(data, source[5:]))
        except (KeyError, IndexError, TypeError):
            return False, "%s (cannot save %s from %s)" % (summary, name, source)
        env[name] = value
        if IN_ACTIONS and len(str(value)) > 6:
            print("::add-mask::%s" % value)
    shown = []
    for field in check.get("print") or []:
        try:
            shown.append("%s=%s" % (field, json_path(data, field)))
        except (KeyError, IndexError, TypeError):
            shown.append("%s=<missing>" % field)
    return True, summary + (" [%s]" % ", ".join(shown) if shown else "")


def main(path):
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    env = {}
    for name, value in (spec.get("vars") or {}).items():
        m = re.fullmatch(r"\{(\w+)\}", str(value))
        env[name] = builtin(m.group(1)) if m and builtin(m.group(1)) else value
    by_id = {c["id"]: c for c in spec["checks"]}
    failures = 0
    print("%s endpoint check" % spec.get("name", path))
    for check in spec["checks"]:
        if check.get("helper"):
            continue  # only run when another check "uses" it
        ok, msg = run_check(check, spec, env, by_id)
        label = "OK  " if ok else ("NOTE" if check.get("report_only") else "FAIL")
        if not ok and not check.get("report_only"):
            failures += 1
        print("%s %-26s %s" % (label, check["id"], msg))
        if check.get("note") and not ok:
            print("     %s" % check["note"])
    print("%d check(s) failed" % failures if failures else "All checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
