"""Pull a cookie jar out of a HAR -- only from a request that provably worked.

Written after baking a jar that came from a capture containing no authenticated
call at all: the browser was mid sign-in, and "take the longest Cookie header"
happily picked a pre-sign-in jar that 401'd on every build for hours. So the
rule is now structural rather than a heuristic. A jar is only taken from a
tv.youtube.com InnerTube request that returned 200 on a real endpoint, and
log_event does not count -- it answers 200 signed out.
"""
import json, sys, zipfile

SKIP = {"log_event"}


def entries(path):
    if path.endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = [n for n in z.namelist() if n.endswith(".har")][0]
        return json.loads(z.read(name))["log"]["entries"]
    return json.load(open(path))["log"]["entries"]


def authenticated(path):
    """Every (endpoint, started, cookie header) that InnerTube answered 200."""
    found = []
    for entry in entries(path):
        url = entry["request"]["url"]
        if "tv.youtube.com/youtubei/v1/" not in url:
            continue
        if entry["response"]["status"] != 200:
            continue
        endpoint = url.split("youtubei/v1/")[1].split("?")[0]
        if endpoint in SKIP:
            continue
        header = next((h["value"] for h in entry["request"]["headers"]
                       if h["name"].lower() == "cookie"), None)
        if header:
            found.append((endpoint, entry["startedDateTime"], header))
    return found


if __name__ == "__main__":
    hits = authenticated(sys.argv[1])
    if not hits:
        sys.exit("no authenticated InnerTube call in this capture -- it cannot "
                 "supply a working jar, whatever cookies it contains")
    endpoint, started, header = hits[-1]
    sys.stderr.write("from %s at %s, %d cookies\n"
                     % (endpoint, started, header.count("=")))
    sys.stdout.write(header)
