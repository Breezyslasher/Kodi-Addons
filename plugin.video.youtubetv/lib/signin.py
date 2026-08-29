"""Signing in from a device that has a keyboard.

The addon authenticates with a Google cookie jar (see auth). Getting one onto
a Kodi box meant either putting a file there or typing a three kilobyte
header on a remote control, and neither is something anyone does twice.

This serves a one-page form on the local network for as long as the sign-in
dialog is open. The phone or laptop that is already signed in to
tv.youtube.com opens it, pastes, and the session lands in the addon profile.

What this is not: it is not an OAuth flow. Every authenticated request in
every capture taken of the web player -- sixty-nine of them -- carries
``Authorization: SAPISIDHASH``, and not one carries a bearer token, so a
cookie jar is what the surface accepts and this makes fetching one bearable
rather than replacing it.

The page is reachable by anything on the same network while it is open, so:
it runs only while the dialog is up, the path carries a random token, and it
stops the moment a jar arrives or the dialog is cancelled. Anyone already on
your network could reach it during that window, which is the same trust as
Kodi's own web interface.
"""

import secrets
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs
except ImportError:  # pragma: no cover
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from urlparse import parse_qs

from . import auth, kodiutils

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in to YouTube TV for Kodi</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem;
      background:#111;color:#eee}
 main{max-width:40rem;margin:0 auto}
 h1{font-size:1.3rem} ol{padding-left:1.2rem} li{margin:.6rem 0}
 textarea{width:100%%;height:9rem;font:13px/1.4 ui-monospace,monospace;
          padding:.6rem;border-radius:.4rem;border:1px solid #444;
          background:#1c1c1c;color:#eee;box-sizing:border-box}
 button{margin-top:.8rem;font-size:1rem;padding:.6rem 1.2rem;border:0;
        border-radius:.4rem;background:#3ea6ff;color:#04121f;font-weight:600}
 code{background:#222;padding:.1rem .3rem;border-radius:.2rem}
 .note{color:#aaa;font-size:.9rem}
</style></head><body><main>
<h1>Sign in to YouTube TV for Kodi</h1>
%(message)s
<ol>
 <li>In this browser, sign in to <code>tv.youtube.com</code>.</li>
 <li>Open the developer tools (F12), the <b>Network</b> tab, and reload.</li>
 <li>Click any request to <code>tv.youtube.com</code>, find
     <b>Request Headers</b>, and copy the whole <code>Cookie:</code> value.</li>
 <li>Paste it below. Alternatively paste the contents of a
     <code>cookies.txt</code> export.</li>
</ol>
<form method="post" action="%(path)s">
 <textarea name="jar" autofocus placeholder="SID=...; SAPISID=...; ..."></textarea>
 <button type="submit">Sign in</button>
</form>
<p class="note">This page is served by Kodi on your own network and closes
as soon as it has a session.</p>
</main></body></html>"""

DONE = """<!doctype html><html><head><meta charset="utf-8">
<title>Signed in</title><style>body{font:16px/1.5 system-ui,sans-serif;
margin:0;padding:2rem;background:#111;color:#eee;text-align:center}</style>
</head><body><h1>%s</h1><p>%s</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    # Silence: BaseHTTPRequestHandler logs every request to stderr, which in
    # Kodi means the log gets a copy of nothing useful.
    def log_message(self, *args):
        pass

    def _html(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.split("?")[0] != self.server.token_path:
            self._html(404, DONE % ("Not here", "Check the address on the TV."))
            return
        self._html(200, PAGE % {"path": self.server.token_path, "message": ""})

    def do_POST(self):
        if self.path.split("?")[0] != self.server.token_path:
            self._html(404, DONE % ("Not here", "Check the address on the TV."))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode("utf-8", "replace")
        pasted = (parse_qs(raw).get("jar") or [""])[0].strip()

        problem = self.server.offer(pasted)
        if problem:
            self._html(200, PAGE % {
                "path": self.server.token_path,
                "message": '<p style="color:#ff8a80">%s</p>' % problem})
            return
        self._html(200, DONE % ("Signed in",
                                "You can close this and go back to Kodi."))


class SignInServer(object):
    """Serves the form until a usable jar arrives, or until it is stopped."""

    def __init__(self, verify=None):
        self._verify = verify
        self.cookies = None
        self.error = ""
        self._server = HTTPServer(("0.0.0.0", 0), _Handler)
        self._server.token_path = "/" + secrets.token_urlsafe(9)
        self._server.offer = self._offer
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.daemon = True

    @property
    def port(self):
        return self._server.server_address[1]

    def url(self, host):
        return "http://%s:%d%s" % (host, self.port, self._server.token_path)

    def _offer(self, pasted):
        """Take a paste. Returns a message to show, or "" when accepted."""
        if not pasted:
            return "Nothing was pasted."
        try:
            if "\t" in pasted or pasted.lstrip().startswith("# "):
                cookies = auth.parse_cookies_txt_text(pasted)
            else:
                cookies = auth.parse_cookie_header(pasted)
        except Exception as exc:
            return "Could not read that: %s" % exc

        missing = [n for n in auth.REQUIRED if n not in cookies]
        if missing:
            return ("That paste has no %s. Copy the whole Cookie header from a "
                    "request to tv.youtube.com, not just part of it."
                    % " or ".join(missing))
        if self._verify:
            ok, message = self._verify(cookies)
            if not ok:
                return message
        self.cookies = cookies
        return ""

    def start(self):
        self._thread.start()
        kodiutils.log("sign-in page listening on port %d" % self.port)

    def stop(self):
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
