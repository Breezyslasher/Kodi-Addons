#!/usr/bin/env python3
"""Shrink and de-secret an A24 HAR so it is safe and small to share.

Usage:
    python3 sanitize_har.py capture.har a24-sanitized.har [--host SUFFIX ...]

Adapted from the Apple TV addon's tool. Differences:
  * No built-in host allow-list: A24's API, CDN and licence hosts are not
    known yet (docs/recon.md), and a guessed list silently drops the one call
    that matters. Pass --host to narrow once they are known.
  * Base64-encoded response bodies (content.encoding == "base64") are decoded
    before redaction, so JSON hidden that way is still kept and scrubbed.
  * JWT-shaped strings (eyJ...) are redacted wherever they appear.
  * Binary bodies -- the Widevine challenge and licence -- are replaced by a
    note of their size, which is all the recon needs.

Keeps URLs, query-parameter *names*, header *names* and JSON *keys*, so the
shape of every request and response survives. Read the output before
sharing; when in doubt, redact more.
"""

import base64
import json
import os
import re
import sys

DROP_PATH_RE = re.compile(
    r"\.(js|css|png|jpe?g|webp|gif|svg|ico|woff2?|ttf|mp4|m4s|m4v|m4a|ts|"
    r"cmfv|cmfa|aac|vtt)$", re.I)

SECRET_HEADERS = {
    "cookie", "set-cookie", "authorization", "proxy-authorization",
    "x-auth-token", "x-access-token", "x-api-key", "x-session-token",
    "x-device-id", "x-csrf-token", "x-xsrf-token",
}
# Any header whose name contains one of these is redacted too.
SECRET_HEADER_HINTS = ("token", "auth", "session", "secret", "key")

SECRET_JSON_KEYS = {
    "password", "token", "access_token", "refresh_token", "id_token",
    "accesstoken", "refreshtoken", "idtoken", "authtoken", "auth_token",
    "session", "sessionid", "session_id", "client_secret", "secret",
    "deviceid", "device_id", "license", "licence", "challenge", "code",
    "email", "username", "playbacktoken", "playback_token", "drmtoken",
    "jwt", "assertion", "otp", "user_id", "userid", "first_name", "last_name",
    "firstname", "lastname", "full_name", "name", "nexus_id", "instagram_handle",
    "phone", "birth_day", "birth_month", "address", "deviceid", "authid", "view_id", "viewid",
}

SECRET_QUERY_PARAMS = SECRET_JSON_KEYS | {
    "token", "access_token", "id_token", "auth", "authtoken", "sig",
    "signature", "hdnts", "hdnea", "policy", "key-pair-id", "password",
    "code", "email", "jwt",
}

REDACTED = "REDACTED"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
JWT_RE = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
# IPv4 addresses, which turn up inside JSON-in-a-string values (cookie consent
# records) where key-based redaction cannot see them.
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def secret_header(name):
    name = name.lower()
    return name in SECRET_HEADERS or any(h in name for h in SECRET_HEADER_HINTS)


def redact_headers(headers):
    for h in headers or []:
        if secret_header(h.get("name", "")):
            h["value"] = REDACTED


def redact_cookies(entry):
    for side in ("request", "response"):
        for cookie in entry.get(side, {}).get("cookies", []) or []:
            if cookie.get("value"):
                cookie["value"] = REDACTED


def scrub_string(text):
    text = JWT_RE.sub(REDACTED, text)
    text = IP_RE.sub(REDACTED, text)
    return EMAIL_RE.sub(REDACTED, text)


def scrub_url(url):
    """Blank credential values in a URL, keeping every parameter name."""
    if "?" not in url:
        return scrub_string(url)
    base, query = url.split("?", 1)
    parts = []
    for pair in query.split("&"):
        name, sep, value = pair.partition("=")
        if sep and name.lower() in SECRET_QUERY_PARAMS and value:
            value = REDACTED
        parts.append(name + sep + scrub_string(value))
    return scrub_string(base) + "?" + "&".join(parts)


# Any JSON key containing one of these is secret too (playSessionToken,
# captchaResponse, emailAddress, ...): exact-name lists miss service-specific
# spellings.
SECRET_KEY_HINTS = ("token", "password", "secret", "captcha", "email",
                    "signature", "apikey", "session", "otp", "code")


def secret_key(key):
    key = key.lower()
    return key in SECRET_JSON_KEYS or any(h in key for h in SECRET_KEY_HINTS)


def redact_json_values(obj):
    if isinstance(obj, dict):
        return {k: (REDACTED if secret_key(k) else redact_json_values(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_json_values(v) for v in obj]
    if isinstance(obj, str):
        if "://" in obj:
            return scrub_url(obj)
        return scrub_string(obj)
    return obj


def redact_text(text, mime=""):
    """JSON is scrubbed key by key; text manifests/XML have credentials
    stripped from URLs; anything else is replaced with a size note."""
    if not text:
        return text
    try:
        return json.dumps(redact_json_values(json.loads(text)))
    except (ValueError, TypeError):
        pass
    mime = (mime or "").lower()
    if "x-www-form-urlencoded" in mime:
        # Keep field names (grant_type, client_id...) and redact secret values.
        return scrub_url("?" + text)[1:]
    if any(m in mime for m in ("xml", "dash", "mpegurl", "text/plain")):
        # Manifests: keep them (they are the recon), scrub signed URLs.
        return re.sub(r"https?://[^\s\"'<>]+", lambda m: scrub_url(m.group(0)),
                      scrub_string(text))
    return "%s (%d chars, %s)" % (REDACTED, len(text), mime or "unknown type")


def decode_body(content):
    """Return the body as text, decoding base64 if the HAR stored it so."""
    text = content.get("text")
    if text and content.get("encoding") == "base64":
        raw = base64.b64decode(text + "=" * (-len(text) % 4))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Binary: a licence response, a segment. Size is all we keep.
            content.pop("encoding", None)
            return "%s (%d binary bytes)" % (REDACTED, len(raw)), False
        content.pop("encoding", None)
    return text, True


def wanted(entry, hosts):
    url = entry.get("request", {}).get("url", "")
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("/")[2].split(":")[0].lower()
    if hosts and not host.endswith(tuple(hosts)):
        return False
    if DROP_PATH_RE.search(url.split("?", 1)[0]):
        return False
    mime = (entry.get("response", {}).get("content") or {}).get("mimeType", "")
    if mime.startswith(("image/", "font/", "video/", "audio/")):
        return False
    return True


def sanitize(entry):
    req, resp = entry.get("request", {}), entry.get("response", {})
    redact_headers(req.get("headers"))
    redact_headers(resp.get("headers"))
    redact_cookies(entry)
    if req.get("url"):
        req["url"] = scrub_url(req["url"])
    for q in req.get("queryString", []) or []:
        if q.get("name", "").lower() in SECRET_QUERY_PARAMS:
            q["value"] = REDACTED
        else:
            q["value"] = scrub_string(q.get("value", ""))

    post = req.get("postData")
    if post:
        if "text" in post:
            text = post["text"] or ""
            # A Widevine challenge is binary; browsers store it mangled.
            if text and not text.isprintable() and not text.strip().startswith(("{", "[")):
                post["text"] = "%s (%d chars, likely binary challenge)" % (
                    REDACTED, len(text))
            else:
                post["text"] = redact_text(text, post.get("mimeType", ""))
        for p in post.get("params", []) or []:
            if secret_key(p.get("name", "")):
                p["value"] = REDACTED
            else:
                p["value"] = scrub_string(p.get("value", ""))

    content = resp.get("content")
    if content and content.get("text"):
        text, is_text = decode_body(content)
        content["text"] = redact_text(text, content.get("mimeType", "")) \
            if is_text else text


def main(argv):
    args, hosts = [], []
    it = iter(argv)
    for a in it:
        if a == "--host":
            hosts.append(next(it).lower())
        else:
            args.append(a)
    if len(args) != 2:
        print(__doc__)
        return 1
    with open(args[0], "r", encoding="utf-8") as fh:
        har = json.load(fh)
    entries = har.get("log", {}).get("entries", [])
    kept = [e for e in entries if wanted(e, hosts)]
    for entry in kept:
        sanitize(entry)
    har["log"]["entries"] = kept
    with open(args[1], "w", encoding="utf-8") as fh:
        json.dump(har, fh, separators=(",", ":"))
    print("Kept %d of %d entries. %.1f MB -> %.1f MB (%s). Review before sharing."
          % (len(kept), len(entries), os.path.getsize(args[0]) / 1e6,
             os.path.getsize(args[1]) / 1e6, args[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
