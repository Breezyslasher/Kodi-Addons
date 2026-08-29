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

_BASE_URL = re.compile(r"<BaseURL>([^<]+)</BaseURL>")
_SEGMENT_URL = re.compile(r'<SegmentURL\s+media="([^"]+)"')


def segment_urls(xml, limit=None):
    """Every segment URL of the first Representation, in manifest order.

    YouTube writes an absolute BaseURL per Representation with relative
    SegmentURLs beneath it, so the two concatenate.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    base = _BASE_URL.search(xml)
    if not base:
        return []
    # Stop at the next Representation so we do not pair one BaseURL with
    # another stream's segments.
    end = xml.find("<BaseURL>", base.end())
    region = xml[base.end():end if end != -1 else len(xml)]
    prefix = base.group(1).strip()
    urls = [prefix + m.group(1) for m in _SEGMENT_URL.finditer(region)]
    return urls[:limit] if limit else urls


def first_segment_url(xml):
    urls = segment_urls(xml, limit=1)
    return urls[0] if urls else ""


def _strip_path_param(url, name):
    """Drop an unsigned ``/name/value/`` pair from a path-style URL."""
    return re.sub(r"/%s/[^/]+" % re.escape(name), "", url, count=1)


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
    variants = [
        ("as-is", url, {}),
        ("no n", _strip_path_param(url, "n"), {}),
        ("with cpn", url.replace("/sq/", "/cpn/%s/sq/" % cpn, 1), {}),
        ("no n + cpn",
         _strip_path_param(url, "n").replace("/sq/", "/cpn/%s/sq/" % cpn, 1), {}),
        ("query style", _to_query_style(url), {}),
        ("query style, no n", _to_query_style(_strip_path_param(url, "n")), {}),
        ("ranged", url, {"Range": "bytes=0-1048575"}),
    ]
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
