#!/usr/bin/env python3
"""Shrink and de-secret an Apple TV HAR so it is safe and small to share.

Usage:
    python3 sanitize_har.py capture.har appletv-sanitized.har

A browser HAR of tv.apple.com is mostly artwork and video segments; the API
calls are a tiny fraction of it. This keeps only the Apple API requests,
drops images, fonts, media segments and analytics beacons, and redacts
cookies, auth tokens and anything else sensitive -- while preserving URLs,
query-parameter *names*, header *names* and JSON *keys*, so the shape of
every request and response survives intact.

A 300 MB capture typically comes out a few hundred kilobytes, small enough
to attach directly. Read the output before sharing; when in doubt, redact
more.
"""

import json
import re
import sys

# Keep every Apple host by default. An allow-list of known API hosts looks
# tidier but silently drops whatever Apple adds next -- and it did: sibling
# store sign-ins (auth.music/podcasts/apps.apple.com), the account lookup on
# amp-api.music.apple.com and the storefront/ratings call on
# amp-api.videos.apple.com were all being thrown away.
KEEP_HOST_SUFFIX = ("apple.com",)

# ...but never these, whatever host they came from: analytics beacons, static
# assets and the media itself, which is the bulk of a capture and of no use.
DROP_HOSTS = ("xp.apple.com", "daf.xp.apple.com", "mzstatic.com", "vod-",
              "cdn-apple.com")
DROP_PATH_RE = re.compile(
    r"\.(js|css|png|jpe?g|webp|gif|svg|ico|woff2?|ttf|mp4|m4s|ts|cmfv|cmfa|aac)$"
    r"|/static|/assets/|/api/csp-report", re.I)

# Non-JSON bodies (page shells, scripts) are blanked rather than truncated:
# they are the bulk of a capture, may carry cookies, and truncating JSON
# would leave it unparseable. redact_text() already does exactly that.

# Header names whose values are secret and must be blanked.
SECRET_HEADERS = {
    "cookie", "set-cookie", "authorization", "x-apple-id-session-id",
    "x-apple-session-token", "scnt", "x-apple-twosv-trust-token",
    "x-apple-webauth-token", "media-user-token", "x-dsid", "x-token",
    "x-apple-music-user-token", "x-apple-gs-token",
}

# JSON keys whose values are secret.
SECRET_JSON_KEYS = {
    "m1", "m2", "a", "b", "salt", "cookie", "token", "trusttokens",
    "session_token", "access_token", "password", "svctoken", "authtoken",
    "dsid", "guid", "downloadkey", "license", "key",
}

# Query parameters whose values are credentials. Names are kept so the shape
# of the request stays readable; only the values go.
SECRET_QUERY_PARAMS = {
    "t", "utsk", "token", "keyinfo", "sig", "signature", "auth", "authtoken",
    "access_token", "id_token", "dsid", "guid", "password", "code",
}

REDACTED = "REDACTED"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact_headers(headers):
    for h in headers:
        if h.get("name", "").lower() in SECRET_HEADERS:
            h["value"] = REDACTED


def redact_cookies(entry):
    """A HAR keeps cookies in their own arrays as well as in the headers.

    Redacting only the headers leaves the Apple ID session cookie sitting in
    request.cookies[] in full, which is the one thing that must never leave
    the machine.
    """
    for side in ("request", "response"):
        for cookie in entry.get(side, {}).get("cookies", []) or []:
            if cookie.get("value"):
                cookie["value"] = REDACTED


def scrub_url(url):
    """Blank credential values in a URL, keeping every parameter name."""
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    parts = []
    for pair in query.split("&"):
        name, sep, value = pair.partition("=")
        if sep and name.lower() in SECRET_QUERY_PARAMS and value:
            value = REDACTED
        parts.append(name + sep + value)
    return base + "?" + "&".join(parts)


def redact_json_values(obj):
    if isinstance(obj, dict):
        return {k: (REDACTED if k.lower() in SECRET_JSON_KEYS else redact_json_values(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_json_values(v) for v in obj]
    if isinstance(obj, str):
        # Response bodies carry stream URLs whose t= parameter is a playback
        # credential, so scrub those the same way as a request URL.
        if "://" in obj and "?" in obj:
            obj = scrub_url(obj)
        return EMAIL_RE.sub(REDACTED, obj)
    return obj


def redact_text(text):
    if not text:
        return text
    try:
        return json.dumps(redact_json_values(json.loads(text)))
    except (ValueError, TypeError):
        # Not JSON: blank it rather than risk leaking a token/HTML with cookies.
        return REDACTED


def wanted(entry):
    """True for entries worth keeping: Apple API calls, not assets."""
    url = entry.get("request", {}).get("url", "")
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("/")[2] if "//" in url else ""
    if any(bad in host for bad in DROP_HOSTS):
        return False
    if not host.endswith(KEEP_HOST_SUFFIX):
        return False
    path = url.split("?", 1)[0]
    if DROP_PATH_RE.search(path):
        return False
    mime = (entry.get("response", {}).get("content") or {}).get("mimeType", "")
    if mime.startswith(("image/", "font/", "video/", "audio/")):
        return False
    return True


def sanitize(entry):
    req, resp = entry.get("request", {}), entry.get("response", {})
    redact_headers(req.get("headers", []))
    redact_headers(resp.get("headers", []))
    redact_cookies(entry)

    if req.get("url"):
        req["url"] = scrub_url(req["url"])

    # Query string names are kept; credential and email-like values scrubbed.
    for q in req.get("queryString", []):
        if q.get("name", "").lower() in SECRET_QUERY_PARAMS:
            q["value"] = REDACTED
        else:
            q["value"] = EMAIL_RE.sub(REDACTED, q.get("value", ""))

    post = req.get("postData")
    if post and "text" in post:
        post["text"] = redact_text(post["text"])
        for p in post.get("params", []):
            if p.get("name", "").lower() in SECRET_JSON_KEYS:
                p["value"] = REDACTED

    content = resp.get("content")
    if content and "text" in content:
        content["text"] = redact_text(content["text"])


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        har = json.load(fh)

    entries = har.get("log", {}).get("entries", [])
    kept = [e for e in entries if wanted(e)]
    for entry in kept:
        sanitize(entry)
    har["log"]["entries"] = kept

    # Compact, not indented: this file is for machines and wants to be small.
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(har, fh, separators=(",", ":"))
    import os
    before = os.path.getsize(sys.argv[1])
    after = os.path.getsize(sys.argv[2])
    print("Kept %d of %d entries. %.1f MB -> %.1f MB (%s). Review before sharing."
          % (len(kept), len(entries), before / 1e6, after / 1e6, sys.argv[2]))


if __name__ == "__main__":
    main()
