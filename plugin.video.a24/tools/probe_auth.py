#!/usr/bin/env python3
"""Signed-out probe of A24's OAuth authorize endpoint (docs/recon.md row B2).

Usage:
    python3 probe_auth.py

Sends the web app's authorize request with no session at all, in a few
variants, and prints status, redirect target (host + path + query *names*)
and the start of any error body. No account, no cookie, no credentials:
it only shows how the endpoint answers the request shape.
"""

import base64
import hashlib
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH = "https://account.a24films.com/api/v1/auth/authorize/"
CLIENT_ID = "nHodnPAsF9MSYmRkVlFH1EsD142RZ9Nk1xNg1f1e"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def challenge():
    verifier = secrets.token_urlsafe(48)
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def probe(label, params, headers=None):
    url = AUTH + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
        status, hdrs, body = resp.status, resp.headers, resp.read(3000)
    except urllib.error.HTTPError as exc:
        status, hdrs, body = exc.code, exc.headers, exc.read(3000)
    loc = hdrs.get("Location") or ""
    if loc:
        p = urllib.parse.urlsplit(loc)
        loc = "%s%s ?%s" % (p.netloc, p.path, ",".join(sorted(urllib.parse.parse_qs(p.query))))
    print("== %s\n   HTTP %s  %s  Location: %s" % (label, status, hdrs.get("Content-Type"), loc or "-"))
    text = body.decode("utf-8", "replace").strip()
    if text:
        print("   body: " + " ".join(text.split())[:700])


def main():
    base = {"response_type": "code", "client_id": CLIENT_ID,
            "code_challenge": challenge(), "code_challenge_method": "S256",
            "redirect_uri": "https://app.a24films.com/tabs/home/",
            "back_to_site_title": "A24 App"}
    probe("web app params", base)
    probe("web app params, Accept json", base, {"Accept": "application/json"})
    probe("native redirect_uri", dict(base, redirect_uri="https://api.app.a24films.com/redirect/"))
    probe("no back_to_site_title", {k: v for k, v in base.items() if k != "back_to_site_title"})
    probe("bad client_id (control)", dict(base, client_id="x"))
    probe("bad redirect_uri (control)", dict(base, redirect_uri="https://example.com/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
