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

# Only timescale is pushed onto a SegmentList. presentationTimeOffset
# double-counts, and startNumber would renumber segments that already name
# their own sequence. startNumber is read all the same, for the
# SegmentTemplate, where the numbers are what builds the url.
_INHERITED = ("timescale", "startNumber")


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


def _baked_po_token():
    """A token to attach: minted if it can be, baked if not.

    The DASH path needs one on every BaseURL exactly as the bridge needs one
    in its request, so both ask the same way -- mint against the visitorData
    we present, and fall back to whatever the build carries.
    """
    try:
        from . import api, potoken
        minted = potoken.token(api.visitor_data())
    except Exception as exc:
        kodiutils.log("manifest: no minted token (%s)" % exc)
        minted = ""
    if minted:
        return minted
    try:
        from . import baked_session
    except ImportError:
        return ""
    return getattr(baked_session, "PO_TOKEN", "") or ""


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

    patched = to_segment_template(patched, parent, inherited)
    return patched.encode("utf-8") if was_bytes else patched


_TIMELINE = re.compile(r"<SegmentTimeline\b.*?</SegmentTimeline>", re.S)
_S_ELEMENT = re.compile(r"<S\b([^>]*)/>")
_REPR_SEGLIST = re.compile(r"<SegmentList\b[^>]*>.*?</SegmentList>", re.S)
_SQ = re.compile(r"\bsq/(\d+)")


def _timeline_length(timeline):
    """How many segments a SegmentTimeline describes, counting r= repeats."""
    total = 0
    for attrs in _S_ELEMENT.findall(timeline):
        repeat = re.search(r'\br\s*=\s*"(-?\d+)"', attrs)
        if repeat and int(repeat.group(1)) < 0:
            return None  # open-ended; we cannot count it
        total += 1 + (int(repeat.group(1)) if repeat else 0)
    return total


def to_segment_template(text, parent, attrs):
    """Restate a live SegmentList as the SegmentTemplate ISA is written for.

    Two things go wrong with the SegmentList form, and both are the same
    omission in ISA.

    It crashes. The live manifest update opens each Representation with

        if (!repr->GetSegmentTemplate()->HasTimeline() || repr->Timeline().IsEmpty())

    (DASHTree.cpp:1666) and GetSegmentTemplate returns a std::optional that is
    empty unless a <SegmentTemplate> appeared on the Representation or above
    it. The same call thirty lines down is guarded, so this is an oversight,
    not a contract -- but minimumUpdatePeriod is PT5S, so live played for five
    seconds and took Kodi with it.

    And before that it was already running on nothing. ISA parses a
    <SegmentTimeline> only from an AdaptationSet's SegmentList
    (DASHTree.cpp:612); YouTube states it once on the Period, where nothing
    reads it. A Representation's SegmentList inherits from its AdaptationSet,
    which here has none, so every segment came out with duration 0, PTS 0 and
    number 0 -- 1879 segments all claiming to start at the same instant. That
    is why playback began at the oldest segment in a four-hour window rather
    than at the live edge: there was no timeline to seek in.

    The template form fixes both because it is the path ISA maintains. The
    Period element carries timescale, startNumber and the timeline, and ISA
    copies it down to each AdaptationSet and Representation (lines 578 and
    818); each Representation overrides only ``media``, and a template node
    with no SegmentTimeline child leaves the inherited one intact
    (ParseSegmentTemplate). ISA then builds real segments from it
    (line 1013) and formats each url as sq/<number>/lmt/<lmt> joined to the
    Representation's BaseURL -- character for character the urls the
    SegmentList spelled out, since $Number$ counts from the same startNumber
    (AdaptiveStream.cpp:216, SegTemplate.cpp FormatUrl).

    Bails out, leaving the manifest as it was, if the timeline does not
    describe exactly as many segments as the Representation lists. A template
    is only equivalent to the list it replaces while the two agree, and a
    manifest that plays for five seconds beats one that fetches wrong urls.
    """
    timeline = _TIMELINE.search(text, parent.end())
    if not timeline:
        kodiutils.log("manifest: the Period SegmentList states no "
                      "SegmentTimeline, so its SegmentLists stay as they are")
        return text
    length = _timeline_length(timeline.group(0))
    if not length:
        kodiutils.log("manifest: cannot count the Period SegmentTimeline, so "
                      "its SegmentLists stay as they are")
        return text

    inherited = ' timescale="%s"' % attrs["timescale"]
    if "startNumber" in attrs:
        inherited += ' startNumber="%s"' % attrs["startNumber"]

    converted = []
    refused = []

    def per_representation(rep):
        head, body, tail = rep.group(1), rep.group(2), rep.group(3)
        ident = _REP_ID.search(head)
        ident = ident.group(1) if ident else "?"
        seglist = _REPR_SEGLIST.search(body)
        if not seglist:
            return rep.group(0)
        urls = _SEGMENT_MEDIA.findall(seglist.group(0))
        if not urls:
            refused.append("%s: no SegmentURL" % ident)
            return rep.group(0)
        if len(urls) != length:
            refused.append("%s: %d urls vs %d timeline entries"
                           % (ident, len(urls), length))
            return rep.group(0)
        first = _SQ.search(urls[0])
        if not first:
            refused.append("%s: %s names no sq" % (ident, urls[0]))
            return rep.group(0)
        media = _SQ.sub("sq/$Number$", urls[0], count=1)
        # media comes straight out of an XML attribute and goes straight
        # back into one, so it is already escaped exactly right.
        element = ('<SegmentTemplate media="%s" startNumber="%s"/>'
                   % (media, first.group(1)))
        converted.append(ident)
        return head + body[:seglist.start()] + element + body[seglist.end():] + tail

    rewritten = _REPRESENTATION.sub(per_representation, text)
    if refused:
        kodiutils.log_error("manifest: left %d SegmentList(s) alone -- %s"
                            % (len(refused), "; ".join(refused[:4])))
    if not converted:
        return text

    element = ('<SegmentTemplate%s>%s</SegmentTemplate>'
               % (inherited, timeline.group(0)))
    rewritten = (rewritten[:parent.start()] + element
                 + rewritten[parent.start():])
    kodiutils.log("manifest: restated %d SegmentList(s) as SegmentTemplates "
                  "over a %d entry timeline" % (len(converted), length))
    return rewritten


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
    token = kodiutils.get_setting("po_token", "") or _baked_po_token()
    if token:
        variants.extend([
            ("with pot", _add_param(url, "pot", token), ranged),
            ("no n + pot", _add_param(no_n, "pot", token), ranged),
        ])
    else:
        kodiutils.log("segment variant [pot]: skipped, no po_token configured")
    # The player's own n, put back. If this one answers 200 the transform is
    # wrong for these urls however faithfully it was extracted; if it is
    # refused like the rest, n is not what the wall is made of.
    restored = url
    for solved_value, minted in LAST_N.items():
        if solved_value and solved_value in restored:
            restored = restored.replace(solved_value, minted)
    if restored != url:
        variants.append(("player's own n", restored, ranged))
        if token:
            variants.append(("player's n + pot",
                             _add_param(restored, "pot", token), ranged))
    else:
        kodiutils.log("segment variant [player's own n]: skipped, nothing "
                      "was rewritten in this url")
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


# Where n hides in a media url. On-demand spells it as a query parameter, live
# as a path segment; a check for only the first skips live entirely.
_N_IN_URL = re.compile(r"""(?:[?&]|&amp;)n=([^&"'<]+)|/n/([^/"'<]+)""")


def carries_n(url):
    """Whether a url has an n to rewrite, in either spelling."""
    return bool(_N_IN_URL.search(url))


# solved value -> the value the player minted, from the last rewrite_n.
LAST_N = {}


def rewrite_n(xml, solve):
    """Replace ``n`` in every BaseURL with the value ``solve`` returns.

    One transform per distinct n rather than per URL: a manifest carries a
    dozen BaseURLs sharing the value the player minted, so solving once and
    substituting is the difference between one pass over a megabyte of
    JavaScript and twelve.

    Both spellings, because YouTube uses both. On-demand puts n in the query as
    ``n=...``; live puts it in the path as ``/n/.../``. Matching only the query
    form left every live segment carrying the value the player minted, which is
    the one googlevideo refuses. The player's own o5_ exists to keep the two in
    step -- it rewrites a /n/ path segment to match the query n -- which is as
    direct a statement as one could ask for that both forms are real.
    """
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml
    solved = {}

    def rewrite(match):
        url = match.group(2)
        found = _N_IN_URL.search(url)
        if not found:
            return match.group(0)
        group = 1 if found.group(1) else 2
        original = found.group(group)
        if original not in solved:
            solved[original] = solve(original)
        replacement = solved[original]
        if not replacement:
            return match.group(0)
        rebuilt = (url[:found.start(group)] + replacement
                   + url[found.end(group):])
        return match.group(1) + rebuilt + match.group(3)

    text = _BASEURL_TAG.sub(rewrite, text)
    if solved:
        # Remember which value replaced which, so a probe can put the
        # player's own n back. Every url the addon sends carries a value it
        # computed, so "is our n the reason for the 403" has never actually
        # been asked -- and the extracted transform being byte-identical to
        # the player's own does not answer it if the url was minted by a
        # different build of the same release.
        LAST_N.clear()
        LAST_N.update((new, old) for old, new in solved.items() if new)
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
_MIME = re.compile(r'mimeType\s*=\s*"([^"]+)"')
_HEIGHT = re.compile(r'\bheight\s*=\s*"(\d+)"')

WIDEVINE_URN = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"


_HEX_KID = re.compile(r"\A[0-9a-fA-F]{32}\Z")


def _as_uuid(key_id):
    """A 16-byte key id as the dashed form cenc:default_KID wants.

    Hex is tried first, and the order matters: 32 hex characters are also
    valid base64, so decoding base64 first turns a hex key id into 24 bytes
    and this returns None for a key id that was perfectly good. The licence
    gives base64url; init segments give hex.
    """
    raw = key_id if isinstance(key_id, bytes) else None
    if raw is None:
        if _HEX_KID.match(key_id.strip()):
            raw = bytes.fromhex(key_id.strip())
        else:
            import base64
            text = key_id.strip().replace("-", "+").replace("_", "/")
            try:
                raw = base64.b64decode(text + "=" * (-len(text) % 4))
            except Exception:
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
    """The ContentProtection pair ISA actually reads.

    The key id goes on both elements, not just mp4protection. ISA picks the
    scheme matching the key system first and reads *its* kid, falling back to
    mp4protection's only when that one is empty (GetProtectionData in
    src/parser/DASHTree.cpp). Naming it twice takes the fallback out of the
    path, and is what conformant packagers emit anyway.

    A track whose key id is not known yet still gets both elements, without the
    attribute. That is what marks the Representation encrypted and hands ISA
    the init data -- and with the license_data property gone, this manifest is
    the only place either can now come from.
    """
    kid = ' cenc:default_KID="%s"' % uuid if uuid else ""
    out = ('<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"'
           ' value="cenc"%s/>' % kid)
    if pssh:
        out += ('<ContentProtection schemeIdUri="%s"%s>'
                '<cenc:pssh>%s</cenc:pssh></ContentProtection>'
                % (WIDEVINE_URN, kid, pssh))
    else:
        # The system id with no init data under it, which only marks the
        # Representation encrypted. ISA 22 will not open a session on this
        # alone -- an empty initData reaches CreateSession and it refuses with
        # "PSSH init data has unexpected size (0)" -- so a caller that leaves
        # pssh empty is relying on the stream carrying its own. On demand
        # does; live does not.
        out += ('<ContentProtection schemeIdUri="%s"%s/>' % (WIDEVINE_URN, kid))
    return out


_REP_ID = re.compile(r'\bid\s*=\s*"([^"]+)"')
_INIT_RANGE = re.compile(r'<Initialization\b[^>]*\brange\s*=\s*"([^"]+)"')
_INIT_SOURCE = re.compile(r'<Initialization\b[^>]*\bsourceURL\s*=\s*"([^"]+)"')
_SEGMENT_MEDIA = re.compile(r'<SegmentURL\b[^>]*\bmedia\s*=\s*"([^"]+)"')
_TEMPLATE_MEDIA = re.compile(r'<SegmentTemplate\b[^>]*\bmedia\s*=\s*"([^"]+)"')
_TEMPLATE_START = re.compile(
    r'<SegmentTemplate\b[^>]*\bstartNumber\s*=\s*"(\d+)"')

# How much of a live segment to pull when looking for its moov. On demand
# states the init segment's exact length (about 1.7 kB); live states nothing,
# so this is a head big enough to hold ftyp+moov and small enough that eleven
# of them cost less than one segment.
LIVE_HEAD_BYTES = 16384


_INDEX_RANGE = re.compile(r'<SegmentBase\b[^>]*\bindexRange\s*=\s*"(\d+)-(\d+)"')


def index_targets(xml):
    """(id, url, sidx first byte, sidx last byte) per Representation.

    Only the on-demand shape has a SegmentIndex to read; live lists its
    segments outright and needs none.
    """
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    out = []
    for rep in _REPRESENTATION.finditer(text):
        head, body = rep.group(1), rep.group(2)
        ident = _REP_ID.search(head)
        base = _BASEURL_TAG.search(body)
        index = _INDEX_RANGE.search(body)
        if ident and base and index:
            out.append((ident.group(1), _unescape(base.group(2).strip()),
                        int(index.group(1)), int(index.group(2))))
    return out


def init_targets(xml):
    """Where each Representation's moov lives: (id, url, byte range).

    Three shapes, because YouTube uses all three. On demand names the init
    segment as a range into the one BaseURL
    (``<Initialization range="0-1729"/>``). Some manifests give a relative
    ``sourceURL`` under an absolute BaseURL. Live gives neither: its
    ``<SegmentList>`` is nothing but ``<SegmentURL media="sq/N/lmt/M"/>``
    entries, and the moov travels at the head of each of them -- which is how
    ISA gets a sample description on live at all, having no init segment to
    fetch. So the first listed segment stands in, read by a range rather than
    whole.

    That first entry is the oldest in the DVR window, not the live edge, and
    it is served: the segment probe fetched sq/3263606 with HTTP 200 while
    the newest was sq/3265103.

    Call this after n has been rewritten -- these are real, fetchable urls and
    an unsolved n makes every one of them a 403.
    """
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    out = []
    for rep in _REPRESENTATION.finditer(text):
        head, body = rep.group(1), rep.group(2)
        ident = _REP_ID.search(head)
        base = _BASEURL_TAG.search(body)
        if not ident or not base:
            continue
        url = _unescape(base.group(2).strip())
        ranged = _INIT_RANGE.search(body)
        if ranged:
            out.append((ident.group(1), url, ranged.group(1)))
            continue
        source = _INIT_SOURCE.search(body)
        if source:
            out.append((ident.group(1), url + _unescape(source.group(1)), ""))
            continue
        segment = _SEGMENT_MEDIA.search(body)
        if not segment:
            # After to_segment_template there are no SegmentURLs left to read,
            # so take the template's own first segment: $Number$ at its
            # startNumber is exactly the url the list used to spell out.
            template = _TEMPLATE_MEDIA.search(body)
            number = _TEMPLATE_START.search(body)
            if template and number:
                out.append((ident.group(1),
                            url + _unescape(template.group(1)).replace(
                                "$Number$", number.group(1)),
                            "0-%d" % (LIVE_HEAD_BYTES - 1)))
            continue
        out.append((ident.group(1), url + _unescape(segment.group(1)),
                    "0-%d" % (LIVE_HEAD_BYTES - 1)))
    return out


def set_key_ids(xml, key_ids, pssh="", kid_by_rep=None, pssh_for=None):
    """Declare Widevine properly, naming the key each track needs.

    The audio set takes one key for all its Representations; a video set spans
    several licensed tiers, so each Representation is given the key for its own
    height rather than the set's tallest -- the account here is licensed for SD
    and the set runs to 1080p.

    A track with no key id yet is still declared, without the attribute. This
    used to return the manifest untouched, which was survivable only while ISA
    was getting its init data from the license_data property; now that the
    property is gone, an undeclared Representation is one ISA does not consider
    encrypted at all.

    ``kid_by_rep`` maps a Representation id to the key id read out of that
    track's own init segment, and wins over the per-track-type map when it has
    an answer. It is the better source twice over: it is the id the samples are
    actually encrypted with rather than one inferred from the picture height,
    and it is available on a title's first play, where a licence-derived id is
    not.

    ``pssh_for``, when given, is called with a Representation's key id as raw
    bytes (or None when it has none) and returns the PSSH to declare on that
    Representation, so each track's init data names its own key the way a
    conformant packager would.

    An earlier version of this also blanked the key id on video, to stop ISA
    putting audio and video on one CDM session. That was ISA 21's rule --
    reuse whenever an earlier decrypter already held the key (Session.cpp,
    HasLicenseKey) -- and YouTube returns all four keys in one licence, so the
    two tracks did land together. ISA 22 reuses a session only when the key id
    matches or the media type does as well (DrmEngine.cpp, InitializeSession),
    and audio and video agree on neither, so they are already separate. The
    blanking now only costs: ISA 22 warns "Cannot get default KID from DRM
    info, decryption can fail" and probes capabilities with an empty key.
    """
    if not key_ids and not pssh and not kid_by_rep:
        return xml
    key_ids = key_ids or {}
    kid_by_rep = kid_by_rep or {}
    was_bytes = isinstance(xml, bytes)
    text = xml.decode("utf-8", "replace") if was_bytes else xml
    if "xmlns:cenc" not in text:
        text = re.sub(r"(<MPD\b)", r'\1 xmlns:cenc="urn:mpeg:cenc:2013"', text, 1)

    applied = []

    def label(track, uuid):
        name = track.replace("DRM_TRACK_TYPE_", "")
        return name if uuid else name + "(no key yet)"

    def rewrite(block):
        body = block.group(0)
        track = _track_for(body)

        def per_representation(rep):
            head, inner, tail = rep.group(1), rep.group(2), rep.group(3)
            # A Representation inside an audio set carries neither mimeType nor
            # height, so asking _track_for about it alone answers SD. The set
            # already knows; only a video set spans more than one track type.
            own = (track if track == "DRM_TRACK_TYPE_AUDIO"
                   else _track_for(head))
            ident = _REP_ID.search(head)
            own_uuid = (_as_uuid(kid_by_rep.get(ident.group(1), "") if ident else "")
                        or _as_uuid(key_ids.get(own, "")) or "")
            own_pssh = pssh
            if pssh_for:
                own_pssh = pssh_for(bytes.fromhex(own_uuid.replace("-", ""))
                                    if own_uuid else None) or pssh
            applied.append(label(own, own_uuid))
            return head + _protection(own_uuid, own_pssh) + inner + tail

        return _REPRESENTATION.sub(per_representation, body)

    text = _ADAPTATION.sub(rewrite, text)
    if applied:
        counts = {}
        for name in applied:
            counts[name] = counts.get(name, 0) + 1
        kodiutils.log("manifest: declared widevine for %s"
                      % ", ".join("%s x%d" % kv for kv in sorted(counts.items())))
    return text.encode("utf-8") if was_bytes else text
