"""The licence proxy over real HTTP.

Importing the module and resolving its attributes both passed while do_POST
was missing, because nothing referenced it -- BaseHTTPRequestHandler dispatches
by method name. Only speaking to the socket finds that.
"""
import os, sys, threading, urllib.request, urllib.error
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs")
sys.argv = ["plugin://plugin.video.youtubetv/", "1", ""]
sys.path.insert(0, "/home/user/Kodi-Addons/plugin.video.youtubetv")
from http.server import ThreadingHTTPServer
from lib import license_proxy, kodiutils

kodiutils.log = lambda m: None
kodiutils.log_error = lambda m: None
license_proxy._SECRET = "testsecret"

server = ThreadingHTTPServer(("127.0.0.1", 0), license_proxy._Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % port

def ask(path, data=None):
    req = urllib.request.Request(base + path, data=data,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

fails = 0
def check(label, got, allowed):
    global fails
    ok = got in allowed
    if not ok: fails += 1
    print("  %-52s -> %s %s" % (label, got, "" if ok else "EXPECTED one of %s" % (allowed,)))

status, _ = ask("/license?k=testsecret", data=b"challenge-bytes")
check("POST /license with the secret", status, (200, 400, 403, 502))
if status == 501:
    print("     501 is the failure this test exists for: no do_POST on the handler")

check("POST /license with a wrong secret", ask("/license?k=nope", data=b"x")[0], (403,))
check("POST /license with no body", ask("/license?k=testsecret", data=b"")[0], (400,))
check("GET /license (ISA's probe)", ask("/license?k=testsecret")[0], (200,))
check("GET /sabr/manifest for an unknown session", ask("/sabr/manifest?id=nope&k=testsecret")[0], (404,))
check("GET /sabr/segment for an unknown session", ask("/sabr/segment?id=nope&itag=1&n=1&k=testsecret")[0], (404,))
check("GET with a wrong secret", ask("/anything?k=nope")[0], (403,))
server.shutdown()
print("failures:", fails)
sys.exit(1 if fails else 0)
