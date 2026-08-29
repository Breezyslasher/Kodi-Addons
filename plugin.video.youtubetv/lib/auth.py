"""The addon's credential.

This used to be a cookie jar exported from a signed-in browser, signed with
the SAPISIDHASH scheme the web player uses. That worked, and it was the only
thing that worked for a long time, but it had a cost that never went away:
Google rotates those cookies constantly, an export goes stale in hours to
days, and the fix was always the same errand -- open a browser, export again,
paste it into a TV box. The addon absorbed rotations to slow that down and
still could not stop it.

What replaced it is the device-code token the regular YouTube addon uses,
accepted here as TVHTML5_UNPLUGGED. It refreshes itself, so there is nothing
to re-export. What made it usable was not the credential but the delivery: a
token session is never offered a DASH manifest, only YouTube's own SABR
endpoint, and until the bridge in lib/sabr_bridge could serve SABR to
InputStream Adaptive -- in HD, which took naming a height, a viewport and a
capability list -- dropping cookies would have meant dropping playback.

That is done and measured, so the jar is gone. See lib/oauth for the flow and
docs/youtube-tv-protocol.md for what the two credentials were each offered.

One thing this costs, and it is worth being plain about: the device-code flow
needs the client ID and secret of a Google API project, which the user has to
create and paste into the settings. The cookie route needed no project of
anyone's. That is the trade -- a one-off setup instead of a recurring one.
"""

from . import kodiutils


class AuthError(Exception):
    """Sign-in is missing or no longer accepted."""


def bearer():
    """The access token, refreshed if it needed to be.

    Raises rather than returning empty, because every caller of this wants a
    credential and none of them can proceed without one -- and a caller that
    quietly carried on with no Authorization header would be asking YouTube
    TV for a signed-out lineup and reporting whatever came back.
    """
    from . import oauth
    token = oauth.access_token()
    if not token:
        raise AuthError("not signed in")
    return token


def client_name():
    """The identity this credential is accepted as."""
    from . import api, oauth
    return oauth.load().get("client_name") or api.OAUTH_CLIENT_NAME


def signed_in():
    from . import oauth
    return bool(oauth.load().get("access_token"))


def sign_out():
    from . import oauth
    oauth.forget()
    kodiutils.log("signed out: the stored token is gone")
