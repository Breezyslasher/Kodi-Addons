"""The MPD pieces the SABR bridge builds its manifest out of.

This module used to be the repair shop for YouTube's own DASH manifest --
copying the Period's timescale down onto each Representation so ISA would
not divide by zero and take Kodi down with it, rebuilding SegmentLists as
SegmentTemplates, pushing a proof-of-origin token into every BaseURL,
naming each track's key. All of that served the manifest a cookie session
was handed, and there is no such manifest any more: the bridge writes its
own, from the formats a SABR player response lists.

What is left is what the bridge still needs, and it is small:

* ``rewrite_n`` / ``carries_n`` -- the media url's ``n`` has to be solved
  through the player's own transform or the CDN answers 403 with an empty
  body. On-demand spells it as a query parameter and live as a path
  segment, which is why matching is done here rather than at either caller.
* ``_add_param`` -- appending a parameter in whichever of those two
  spellings a url uses.
* ``_protection`` -- the ``<ContentProtection>`` element, with the PSSH ISA
  needs to open a CDM session and the ``cenc:default_KID`` that tells it
  which of the licence's four keys belongs to this track.

See lib/sabr_bridge for the manifest these go into.
"""

import re

from . import kodiutils

# BaseURL is not always a bare tag: YouTube's on-demand manifests annotate it
# as <BaseURL yt:contentLength="65569922">. Matching only the bare form meant
# both the pot injection and the segment probe silently did nothing on
# on-demand, and the log said so only by the absence of their lines.
_BASEURL_TAG = re.compile(r"(<BaseURL\b[^>]*>)([^<]+)(</BaseURL>)")


# -- diagnostics ---------------------------------------------------------

def _add_param(url, name, value):
    """Append an unsigned parameter in the URL's own spelling."""
    if "?" in url:
        return "%s&%s=%s" % (url, name, value)
    if "/sq/" in url:
        return url.replace("/sq/", "/%s/%s/sq/" % (name, value), 1)
    return "%s/%s/%s" % (url.rstrip("/"), name, value)


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


WIDEVINE_URN = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"


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


