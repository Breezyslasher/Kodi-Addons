#!/usr/bin/env python3
"""Strip secrets from an Apple TV HAR while keeping the structure I need.

Usage:
    python3 sanitize_har.py tv.apple.com.har appletv-sanitized.har

Keeps only Apple-relevant requests (idmsa / uts-api / itunes / apple), and
within them redacts cookies, auth tokens and any header/JSON value that looks
sensitive -- preserving URLs, query-param *names*, header *names* and JSON
*keys* so the request/response shape stays intact. Read the output before
sharing; when in doubt, redact more.
"""

import json
import re
import sys

# Only keep entries whose URL contains one of these (drops trackers, images…).
KEEP_HOSTS = ("idmsa.apple.com", "uts-api.itunes.apple.com", "tv.apple.com",
              "play.itunes.apple.com", "itunes.apple.com", "apple.com/WebObjects")

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

REDACTED = "REDACTED"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact_headers(headers):
    for h in headers:
        if h.get("name", "").lower() in SECRET_HEADERS:
            h["value"] = REDACTED


def redact_json_values(obj):
    if isinstance(obj, dict):
        return {k: (REDACTED if k.lower() in SECRET_JSON_KEYS else redact_json_values(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_json_values(v) for v in obj]
    if isinstance(obj, str):
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


def sanitize(entry):
    req, resp = entry.get("request", {}), entry.get("response", {})
    redact_headers(req.get("headers", []))
    redact_headers(resp.get("headers", []))

    # Query string names are kept; email-like values scrubbed.
    for q in req.get("queryString", []):
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
    kept = [e for e in entries if any(h in e.get("request", {}).get("url", "") for h in KEEP_HOSTS)]
    for entry in kept:
        sanitize(entry)
    har["log"]["entries"] = kept

    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(har, fh, indent=2)
    print("Kept %d of %d entries. Wrote %s. Review it before sharing."
          % (len(kept), len(entries), sys.argv[2]))


if __name__ == "__main__":
    main()
