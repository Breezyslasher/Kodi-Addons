"""Repairing YouTube's DASH manifest for InputStream Adaptive.

YouTube declares the segment timeline once, on the Period, and leaves each
Representation's ``<SegmentList>`` bare::

    <Period>
      <SegmentList presentationTimeOffset="16293497486"
                   startNumber="3258715" timescale="1000">
        <SegmentTimeline><S d="5005"/>...</SegmentTimeline>
      </SegmentList>
      <AdaptationSet>
        <Representation>
          <BaseURL>https://rr2---sn-....googlevideo.com/videoplayback/.../</BaseURL>
          <SegmentList>
            <SegmentURL media="sq/3258715/lmt/702"/>
            ...

DASH cascades those attributes from Period to AdaptationSet to Representation,
so the manifest is legal. ISA does not inherit them: ``ParseTagRepresentation``
finds no timescale, takes it as zero, and divides by it working out the
timeline duration. That is not a caught error -- it is SIGFPE, and it takes
Kodi down with "Floating point exception (core dumped)".

Only ``timescale`` is copied. That is precisely what ISA said was missing, and
it is the whole of the fix. An earlier version also pushed
``presentationTimeOffset`` down, which was more than the evidence asked for and
actively harmful: ISA applies the Period-level offset already, so repeating it
on the Representation double-counts it. The stream then resolved to the far end
of a four-hour DVR window and ISA requested sq/3258715 -- the oldest segment in
the manifest rather than the live edge -- which the CDN answers with 403.

The timeline itself is not duplicated either: ISA gets far enough to complain
about the timescale specifically, so it has the timeline, and copying it into
every Representation would multiply an already 850 KB manifest.

Regex rather than ElementTree on purpose: these manifests are large, run to
several namespaces including YouTube's own, and a parse/serialise round trip
would rewrite prefixes and risk changing something that currently works. The
edit wanted here is exact and local.
"""

import re

from . import kodiutils

# The Period-level list, recognised by carrying a timescale.
_PARENT = re.compile(r"<SegmentList\b([^>]*\btimescale\s*=\s*[\"'][^\"']+[\"'][^>]*)>")
# A Representation-level list: the bare tag, no attributes at all.
_BARE = re.compile(r"<SegmentList\s*>")

# Only timescale. presentationTimeOffset double-counts and startNumber would
# renumber segments that already name their own sequence.
_INHERITED = ("timescale",)


def _attributes(tag_body):
    found = {}
    for name in _INHERITED:
        match = re.search(r"\b%s\s*=\s*[\"']([^\"']+)[\"']" % name, tag_body)
        if match:
            found[name] = match.group(1)
    return found


# BaseURL is not always a bare tag: YouTube's on-demand manifests annotate it
# as <BaseURL yt:contentLength="65569922">. Matching only the bare form meant
# both the pot injection and the segment probe silently did nothing on
# on-demand, and the log said so only by the absence of their lines.
_BASEURL_TAG = re.compile(r"(<BaseURL\b[^>]*>)([^<]+)(</BaseURL>)")


def add_po_token(xml, token):
    """Append an unsigned ``pot`` to every BaseURL.

    Only useful once a token is known to work; it is what turns the probe
    result into actual playback, because ISA fetches segments itself and this
    is the only place we can reach those URLs.
    """
    if not token:
        return xml
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml

    def rewrite(match):
        url = match.group(2)
        if "pot=" in url or "/pot/" in url:
            return match.group(0)
        joined = _add_param(url.rstrip("/") if "?" not in url else url,
                            "pot", token)
        # A SegmentList BaseURL is a prefix and must keep its trailing slash.
        if url.endswith("/") and not joined.endswith("/") and "?" not in joined:
            joined += "/"
        # We are writing back into XML, where a bare "&" is not legal. The URL
        # already carries its separators as &amp;, so the one _add_param just
        # introduced has to match -- otherwise ISA fails to parse the manifest
        # and the injection breaks more than it fixes.
        if "&amp;" in url or "?" in joined:
            joined = joined.replace("&amp;", "&").replace("&", "&amp;")
        return match.group(1) + joined + match.group(3)

    patched, count = _BASEURL_TAG.subn(rewrite, text)
    if count:
        kodiutils.log("manifest: added pot to %d BaseURLs" % count)
    return patched.encode("utf-8") if was_bytes else patched


def patch(xml):
    """Push the Period's segment attributes onto every bare SegmentList.

    Returns the manifest unchanged if there is nothing to inherit or nothing
    to fix, so a future YouTube that emits these properly costs nothing.
    """
    if isinstance(xml, bytes):
        text = xml.decode("utf-8", "replace")
        was_bytes = True
    else:
        text, was_bytes = xml, False

    parent = _PARENT.search(text)
    if not parent:
        kodiutils.log("manifest: no SegmentList carries a timescale, "
                      "leaving it alone")
        return xml

    inherited = _attributes(parent.group(1))
    if "timescale" not in inherited:
        return xml

    attrs = ' timescale="%s"' % inherited["timescale"]

    patched, count = _BARE.subn("<SegmentList%s>" % attrs, text)
    if count:
        kodiutils.log("manifest: pushed%s onto %d Representation SegmentLists"
                      % (attrs, count))
    return patched.encode("utf-8") if was_bytes else patched


# -- diagnostics ---------------------------------------------------------

_BASE_URL = re.compile(r"<BaseURL\b[^>]*>([^<]+)</BaseURL>")
_SEGMENT_URL = re.compile(r'<SegmentURL\s+media="([^"]+)"')


def _unescape(url):
    return (url.replace("&amp;", "&").replace("&lt;", "<")
               .replace("&gt;", ">").replace("&quot;", '"'))


def segment_urls(xml, limit=None):
    """Fetchable URLs for the first Representation, in manifest order.

    Two layouts, and YouTube uses one for live and the other for on-demand:

    * SegmentList -- an absolute BaseURL with relative SegmentURLs beneath it,
      which concatenate. This is the live manifest.
    * SegmentBase -- one BaseURL holding the whole file, read with ranged
      requests. This is on-demand, and it has no SegmentURL at all, so an
      earlier version of this reported "no BaseURL/SegmentURL pair found" and
      skipped every check.

    In the SegmentBase case the BaseURL is itself the URL to test.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    base = _BASE_URL.search(xml)
    if not base:
        return []
    # Stop at the next Representation so we do not pair one BaseURL with
    # another stream's segments.
    next_base = _BASE_URL.search(xml, base.end())
    end = next_base.start() if next_base else -1
    region = xml[base.end():end if end != -1 else len(xml)]
    prefix = base.group(1).strip()
    urls = [prefix + m.group(1) for m in _SEGMENT_URL.finditer(region)]
    if not urls:
        urls = [prefix]
    # These came out of XML, where the query separators are written &amp;.
    # Fetching them without unescaping would test a URL nobody serves.
    urls = [_unescape(u) for u in urls]
    return urls[:limit] if limit else urls


def first_segment_url(xml):
    urls = segment_urls(xml, limit=1)
    return urls[0] if urls else ""


def _strip_param(url, name):
    """Drop an unsigned parameter, in whichever spelling the URL uses.

    Live hands us path style (``/n/VALUE/``), on-demand query style
    (``&n=VALUE``). Both are unsigned when the name is absent from sparams.
    """
    url = re.sub(r"/%s/[^/?]+" % re.escape(name), "", url, count=1)
    url = re.sub(r"([?&])%s=[^&]*&?" % re.escape(name),
                 lambda m: m.group(1), url, count=1)
    return url.rstrip("?&")


# Kept under the old name for the path-style callers.
_strip_path_param = _strip_param


def _add_param(url, name, value):
    """Append an unsigned parameter in the URL's own spelling."""
    if "?" in url:
        return "%s&%s=%s" % (url, name, value)
    if "/sq/" in url:
        return url.replace("/sq/", "/%s/%s/sq/" % (name, value), 1)
    return "%s/%s/%s" % (url.rstrip("/"), name, value)


def _to_query_style(url):
    """Rewrite ``/videoplayback/a/1/b/2`` as ``/videoplayback?a=1&b=2``.

    The browser's working segment requests use the query form; the manifest
    hands us the path form. The signature covers parameter values, not which
    spelling carries them, so this is a legal restatement of the same URL.
    """
    marker = "/videoplayback/"
    if marker not in url:
        return url
    head, _, tail = url.partition(marker)
    parts = tail.split("/")
    pairs = []
    for i in range(0, len(parts) - 1, 2):
        pairs.append("%s=%s" % (parts[i], parts[i + 1]))
    return head + "/videoplayback?" + "&".join(pairs)


def probe_variations(url, headers, cookie_header=""):
    """Try the same segment several legal ways and log which the CDN accepts.

    Every position in the segment list is refused identically, so the timeline
    is not the problem and the request itself is. These are the differences
    between our URL and the one the browser gets a 200 for, tried one at a
    time: the unsigned throttling parameter, the missing playback nonce, the
    path-versus-query spelling, and a ranged request.
    """
    import requests
    cpn = "".join(__import__("random").choice(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        for _ in range(16))
    # A ranged request on everything: on-demand BaseURLs are the whole file
    # (65 MB in one capture), and we only want to know the status code.
    ranged = {"Range": "bytes=0-131071"}
    no_n = _strip_param(url, "n")
    variants = [
        ("as-is", url, ranged),
        ("no n", no_n, ranged),
        ("with cpn", _add_param(url, "cpn", cpn), ranged),
        ("query style", _to_query_style(url), ranged),
        ("no range", url, {}),
    ]
    # Every media request the browser makes carries a proof-of-origin token,
    # and none of ours does. pot is absent from sparams, so it can be added
    # without breaking the signature. If this is the one that returns 200, the
    # whole 403 wall is PO token enforcement and nothing else.
    token = kodiutils.get_setting("po_token", "")
    if token:
        variants.extend([
            ("with pot", _add_param(url, "pot", token), ranged),
            ("no n + pot", _add_param(no_n, "pot", token), ranged),
        ])
    else:
        kodiutils.log("segment variant [pot]: skipped, no po_token configured")
    for name, candidate, extra in variants:
        attempt = dict(headers)
        attempt.update(extra)
        if cookie_header:
            attempt["Cookie"] = cookie_header
        try:
            response = requests.get(candidate, headers=attempt, timeout=20,
                                    stream=True)
            kodiutils.log("segment variant [%-18s]: HTTP %d, %s bytes"
                          % (name, response.status_code,
                             response.headers.get("Content-Length", "?")))
            response.close()
        except Exception as exc:
            kodiutils.log_error("segment variant [%s] failed: %s" % (name, exc))


def probe_segments(xml, headers, cookie_header=""):
    """Fetch the oldest, middle and newest segments and log each result.

    One probe cannot tell a rejected URL from a segment that has aged out of
    the live window -- both are 403. Three positions can: if only the newest
    succeeds the problem is where in the timeline we start, and if all three
    fail it is the URL or the account.

    The newest is then retried with the session cookies attached. The browser
    sends none to googlevideo, but the browser never fetches this manifest
    either -- it uses SABR -- so the DASH path may not follow the same rule.
    """
    urls = segment_urls(xml)
    if not urls:
        kodiutils.log("segment probe: no BaseURL/SegmentURL pair found")
        return
    positions = [("oldest", 0), ("middle", len(urls) // 2),
                 ("newest", len(urls) - 1)]
    kodiutils.log("segment probe: %d segments listed for the first stream"
                  % len(urls))
    for name, index in positions:
        url = urls[index]
        tail = url.rsplit("/videoplayback", 1)[-1]
        # The sq/... tail is the part that differs; the signed prefix is noise.
        sq = tail.rsplit("/sq/", 1)[-1] if "/sq/" in tail else "?"
        try:
            import requests
            response = requests.get(url, headers=headers, timeout=20,
                                    stream=True)
            interesting = {k: v for k, v in response.headers.items()
                           if k.lower() in ("content-length", "content-type",
                                            "x-restrict-formats-hint",
                                            "x-walltime-ms", "server")}
            kodiutils.log("segment probe [%s #%d sq/%s]: HTTP %d %s"
                          % (name, index, sq, response.status_code,
                             interesting))
            response.close()
        except Exception as exc:
            kodiutils.log_error("segment probe [%s] failed: %s" % (name, exc))

    # Every position was refused identically, so the request is what differs
    # from the browser's, not the timeline position. Try the differences.
    probe_variations(urls[-1], headers, cookie_header)


def probe_first_segment(xml, headers):
    """Kept for callers that only want the quick check."""
    probe_segments(xml, headers)


def base_urls(xml):
    """Every BaseURL in the manifest, XML entities already resolved."""
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    return [_unescape(m.group(2)) for m in _BASEURL_TAG.finditer(text)]


def drop_param(xml, name):
    """Remove a query parameter from every BaseURL.

    Written for ``n``. The player mints n scrambled and the web page's JS
    rewrites it before fetching -- comparing the player's serverAbrStreamingUrl
    against the request the browser actually made shows every other parameter
    byte-identical and n alone changed, 18 characters in and 14 out. n is not
    listed in sparams, so removing it does not invalidate sig; whether the edge
    will serve a request without it is the question this exists to answer.
    """
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml

    def rewrite(match):
        url = match.group(2)
        stripped = re.sub(r"(&amp;|&|\?)%s=[^&\"'<]*" % re.escape(name),
                          lambda m: "?" if m.group(1) == "?" else "", url)
        # Removing the first parameter can leave a dangling separator.
        stripped = stripped.replace("?&amp;", "?").replace("?&", "?")
        return match.group(1) + stripped.rstrip("?") + match.group(3)

    text = _BASEURL_TAG.sub(rewrite, text)
    return text.encode("utf-8") if was_bytes else text


def rewrite_n(xml, solve):
    """Replace ``n`` in every BaseURL with the value ``solve`` returns.

    One transform per distinct n rather than per URL: a manifest carries a
    dozen BaseURLs sharing the value the player minted, so solving once and
    substituting is the difference between one pass over a megabyte of
    JavaScript and twelve.
    """
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml
    solved = {}

    def rewrite(match):
        url = match.group(2)
        found = re.search(r"(?:[?&]|&amp;)n=([^&\"'<]+)", url)
        if not found:
            return match.group(0)
        original = found.group(1)
        if original not in solved:
            solved[original] = solve(original)
        replacement = solved[original]
        if not replacement:
            return match.group(0)
        rebuilt = url[:found.start(1)] + replacement + url[found.end(1):]
        return match.group(1) + rebuilt + match.group(3)

    text = _BASEURL_TAG.sub(rewrite, text)
    if solved:
        kodiutils.log("manifest: rewrote n on %d distinct value(s): %s"
                      % (len(solved),
                         ", ".join("%s -> %s" % i for i in solved.items())))
    return text.encode("utf-8") if was_bytes else text


# YouTube's manifest declares exactly one ContentProtection per AdaptationSet,
# and it is not one ISA reads:
#
#     <ContentProtection schemeIdUri="http://youtube.com/drm/2012/10/10">
#       <yt:SystemURL type="playready">...</yt:SystemURL>
#       <yt:SystemURL type="widevine">...</yt:SystemURL>
#     </ContentProtection>
#
# A YouTube-proprietary scheme carrying licence urls -- no cenc:default_KID, no
# PSSH, and nothing ISA recognises as DRM. Naming a key on that element does
# nothing at all, which is why "Cannot convert KID" survived doing exactly
# that. What ISA wants is the standard pair: mp4protection naming the key, and
# the Widevine system id carrying the init data.
_ADAPTATION = re.compile(r"<AdaptationSet\b[^>]*>.*?</AdaptationSet>", re.S)
_REPRESENTATION = re.compile(r"(<Representation\b[^>]*>)(.*?)(</Representation>)", re.S)
_YT_SCHEME = re.compile(
    r'<ContentProtection\b[^>]*schemeIdUri\s*=\s*"http://youtube\.com/drm[^"]*"'
    r'[^>]*(?:/>|>.*?</ContentProtection>)', re.S)
_MIME = re.compile(r'mimeType\s*=\s*"([^"]+)"')
_HEIGHT = re.compile(r'\bheight\s*=\s*"(\d+)"')

WIDEVINE_URN = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"


def _as_uuid(key_id):
    """A 16-byte key id as the dashed form cenc:default_KID wants."""
    raw = key_id if isinstance(key_id, bytes) else None
    if raw is None:
        import base64
        text = key_id.strip().replace("-", "+").replace("_", "/")
        try:
            raw = base64.b64decode(text + "=" * (-len(text) % 4))
        except Exception:
            try:
                raw = bytes.fromhex(key_id)
            except ValueError:
                return None
    if len(raw) != 16:
        return None
    hexed = raw.hex()
    return "%s-%s-%s-%s-%s" % (hexed[:8], hexed[8:12], hexed[12:16],
                               hexed[16:20], hexed[20:])


def _track_for(block):
    """Which licensed track a chunk of manifest corresponds to."""
    mime = _MIME.search(block)
    if mime and mime.group(1).startswith("audio"):
        return "DRM_TRACK_TYPE_AUDIO"
    heights = [int(h) for h in _HEIGHT.findall(block)]
    tallest = max(heights) if heights else 0
    if tallest > 1080:
        return "DRM_TRACK_TYPE_UHD1"
    if tallest > 576:
        return "DRM_TRACK_TYPE_HD"
    return "DRM_TRACK_TYPE_SD"


def _protection(uuid, pssh):
    """The ContentProtection pair ISA actually reads."""
    out = ('<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"'
           ' value="cenc" cenc:default_KID="%s"/>' % uuid)
    if pssh:
        out += ('<ContentProtection schemeIdUri="%s">'
                '<cenc:pssh>%s</cenc:pssh></ContentProtection>'
                % (WIDEVINE_URN, pssh))
    return out


def set_key_ids(xml, key_ids, pssh=""):
    """Declare Widevine properly, naming the key each track needs.

    The audio set takes one key for all its Representations; a video set spans
    several licensed tiers, so each Representation is given the key for its own
    height rather than the set's tallest -- the account here is licensed for SD
    and the set runs to 1080p.
    """
    if not key_ids:
        return xml
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml
    if "xmlns:cenc" not in text:
        text = re.sub(r"(<MPD\b)", r'\1 xmlns:cenc="urn:mpeg:cenc:2013"', text, 1)

    applied = []

    def rewrite(block):
        body = block.group(0)
        track = _track_for(body)
        uuid = _as_uuid(key_ids.get(track, ""))
        if not uuid:
            return body

        if track == "DRM_TRACK_TYPE_AUDIO":
            applied.append("AUDIO")
            # Keep YouTube's own element -- it is harmless and ISA ignores it --
            # and add the standard pair in front of it.
            return _YT_SCHEME.sub(
                lambda m: _protection(uuid, pssh) + m.group(0), body, count=1)

        def per_representation(rep):
            head, inner, tail = rep.group(1), rep.group(2), rep.group(3)
            own = _track_for(head)
            own_uuid = _as_uuid(key_ids.get(own, ""))
            if not own_uuid:
                return rep.group(0)
            applied.append(own.replace("DRM_TRACK_TYPE_", ""))
            return head + _protection(own_uuid, pssh) + inner + tail

        return _REPRESENTATION.sub(per_representation, body)

    text = _ADAPTATION.sub(rewrite, text)
    if applied:
        counts = {}
        for name in applied:
            counts[name] = counts.get(name, 0) + 1
        kodiutils.log("manifest: declared widevine for %s"
                      % ", ".join("%s x%d" % kv for kv in sorted(counts.items())))
    return text.encode("utf-8") if was_bytes else text
