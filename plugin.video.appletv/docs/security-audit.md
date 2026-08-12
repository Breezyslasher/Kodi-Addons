# Security audit — plugin.video.appletv

Scope: the whole addon, with emphasis on the handling of the user's Apple
account credentials (these are the real account's tokens, tied to the user's
phone/2FA). Reviewed: `default.py`, `service.py`, `lib/api.py`, `lib/auth.py`,
`lib/license_proxy.py`, `lib/kodiutils.py`, `lib/srp_client.py`,
`lib/hashcash.py`, `resources/settings.xml`.

Method: read every network boundary, every credential store, and every place
network- or user-controlled data reaches a filesystem path, a URL that is
fetched, or a code-execution primitive. Findings are grounded in specific
lines, not assumed.

## What is handled safely (verified)

- **Password never stored, never sent in the clear.** Sign-in is SRP-6a
  (`lib/srp_client.py`, `lib/auth.py`); Apple never receives the password, and
  it is not written to disk or logs.
- **TLS is never weakened.** No `verify=False`, no disabled cert checks, no
  `http://` to any remote host anywhere. Every Apple endpoint is `https://`.
- **No dangerous execution or deserialization.** No `eval`/`exec`/`os.system`/
  `subprocess`/`pickle`/`marshal`/`yaml.load`. Network payloads are parsed only
  with `json`. On-disk state is JSON only.
- **Credential values are not logged.** Token/cookie values are never
  interpolated into a log line; the license path deliberately logs only
  `bearer=yes/no`, not the value.
- **The license proxy binds to localhost only** (`127.0.0.1`), not `0.0.0.0`.

## Findings and fixes

### 1. Localhost license/manifest proxy had no request authentication — FIXED (high)

`lib/license_proxy.py` runs a small HTTP server on `127.0.0.1` for InputStream
Adaptive. Its `/manifest` handler fetched an arbitrary `u=<url>` from the
request and, for a master playlist (`m=1`), attached the account's Apple
`Authorization: Bearer` + `media-user-token` headers to that fetch — with no
check on who was asking and no allow-list on the target host.

"Localhost" is not a trust boundary on a media box: it includes every other
local process **and any web page open in a browser on the same machine**. A
malicious page could issue a cross-origin `GET
http://127.0.0.1:57812/manifest?m=1&u=<attacker-url>`; the browser sends it (a
simple GET), the proxy fetches `<attacker-url>` **with the account tokens
attached**, and the attacker's own server logs them. The browser cannot read
the response (CORS), but it does not need to — the credentials are in the
outbound request. Result: theft of the `media-user-token` (account credential)
to an arbitrary server, exploitable any time the service is running and a
playback context is loaded.

**Fix:** every proxy request now must carry a per-session secret (`k=`). The
service mints `secrets.token_hex(16)` at start and publishes it in
`license_proxy.json` — a file only same-user local code (the plugin) can read;
a remote web page cannot. Requests without the exact secret get `403`
(`hmac.compare_digest`). The plugin builds `/manifest`, `/init` and `/widevine`
URLs with the secret; the proxy self-built variant/init URLs carry it too. This
closes both the credential leak and the open-forwarder/SSRF behaviour, without
depending on a host allow-list that could break playback on odd CDN hosts.

### 2. Account credentials stored in cleartext on disk — ACCEPTED (low, by platform)

`session.json` (tokens + all session cookies incl. `myacinfo`,
`media-user-token`) and `playback_context.json` (bearer + media-user-token) are
written unencrypted to the addon profile dir (`lib/auth.py:103`,
`lib/api.py` playback-context write). Any local process or backup that can read
the profile dir obtains working account credentials.

This is standard for Kodi addons: Kodi exposes no keychain/secret store, so
there is no portable place to encrypt to (a key kept beside the data buys
little). The realistic mitigation is OS-level file permissions on the Kodi
userdata dir. Documented, not "fixed", because encrypting-in-place here would be
security theatre. `clear()` (sign-out) wipes the account tokens and cookies.

### 3. Auth headers forwarded to hosts named by an Apple response — ACCEPTED (low)

The HLS master manifest and subtitle fetches send the full auth headers, and
the Widevine cert fetch uses the cookie-bearing session, to URLs taken from
Apple's playback response (`hlsUrl`, `wideVineCertificateUrl`, sub-playlist
URIs). If Apple's TLS-protected response named a non-Apple host, the tokens
would go there. Gated behind TLS-to-Apple (the response itself is authentic),
so low; noted so a future change keeps auth headers off response-derived hosts.

### 4. Path-traversal hardening on profile-dir writes — FIXED (low, defense in depth)

`read_json`/`write_json`/`delete_file` join their filename argument onto the
profile dir, and a few callers build that name from ids that originate in Kodi
routing or an Apple response; the subtitle writer built a `<code>.vtt` name from
a manifest `LANGUAGE` attribute. None was exploitable in practice (traversal
needs control of an Apple TLS response, and `/` was already replaced in cache
keys), but the names were not robustly sanitized.

**Fix:** `kodiutils._safe_name` collapses any filename to a bare basename and
rejects `..`/separators/NUL, applied in all three helpers; the subtitle code is
reduced to `[a-z]` letters before it can form a filename.

### 5. Account PII (Apple ID emails, DSIDs) in the debug log — NOTED (low)

The Family-Sharing members response is dumped to `kodi.log` (member
`accountName` emails, `dsid`/`altDsid`). Not credentials, but PII in a log that
users routinely paste when asking for help. Consider trimming to counts if the
diagnostic value is not needed. Left as-is for now (it has been load-bearing for
diagnosing family-sharing issues), flagged for awareness.

## Summary

The one issue that could actually leak the account token off-box — the
unauthenticated localhost proxy — is fixed. The remaining items are low-severity
and either accepted as inherent to the Kodi platform (cleartext at rest) or
hardened defensively (path names). No credential logging, no TLS weakening, no
code-exec or deserialization sinks were found.
