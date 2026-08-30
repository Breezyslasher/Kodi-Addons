"""IPTV Manager integration.

service.iptv.manager turns an addon's channel list and schedule into a real
Kodi PVR source, so YouTube TV's channels appear in the TV section with a
proper guide rather than only inside this addon's own menus.

How it works, from the addon's side (add-ons/service.iptv.manager wiki,
"Integration"): the addon advertises three settings -- ``iptv.enabled``,
``iptv.channels_uri`` and ``iptv.epg_uri``. IPTV Manager binds a socket on a
free localhost port, merges ``port=`` into the advertised plugin url with
``update_qs`` (so an url that already has a query keeps it, which is why the
addon's existing ``?action=`` routing is enough), and calls it with
``RunPlugin``. The addon connects back to that port and writes JSON.

Two details from its source that shape the code here. It waits ten seconds
for the *connection*, then as long as the connection stays open for the data
-- so the socket is opened before the guide is fetched, not after. And it
reads until the connection closes, so nothing is framed or terminated.

The wiki is explicit that an addon must never prompt for credentials here: a
guide refresh happens on IPTV Manager's schedule, and a sign-in dialog
appearing out of nowhere is worse than no data. So a failure logs and closes
the socket, which is the documented way to say "nothing this time".
"""

import json
import socket
import time

from urllib.parse import quote

from . import api, auth, epg as epg_mod, kodiutils

# What the guide endpoint reaches for. IPTV Manager refreshes on its own
# schedule and Google caps the reachable range at a week, so this is a
# compromise between a useful guide and a response measured in megabytes.
#
# Asked for as several pages of six hours rather than one request for
# twenty-four. Six hours is what the web client's own live tab asks for,
# and what every capture measures; a single twenty-four hour request is a
# shape nothing has been observed answering, and this ran as one for long
# enough to be worth not repeating.
EPG_HOURS = 6
# Pagination does not bring back more *channels* -- every page of the
# 2026-08-29 capture carried the same 148 rows in the same order. It brings
# back more hours: eleven pages took one lineup from 962 airings to 6306, a
# median of 40 per channel. So the loop runs until a page adds nothing new,
# repeats its own token, or this cap is reached, and the cap only decides
# how far ahead the guide reaches. Eight pages is about 29 airings per
# channel, measured; the capture still had a token after eleven.
EPG_PAGES = 8
EPG_AIRINGS_PER_STATION = 40


def _iso(milliseconds):
    """ISO-8601 UTC, which is what the JSON-EPG format asks for."""
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                         time.gmtime(milliseconds / 1000.0))


class IPTVManager(object):
    """Answers one IPTV Manager request over its callback socket."""

    def __init__(self, port):
        self.port = int(port)

    def _send(self, build):
        """Connect first, then do the slow part, then write.

        The order matters: IPTV Manager gives ten seconds for the connection
        and unlimited time once it has one, and fetching a day of guide takes
        longer than ten seconds often enough to matter.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("127.0.0.1", self.port))
        except OSError as exc:
            kodiutils.log_error("iptv manager: could not connect back on "
                                "port %d: %s" % (self.port, exc))
            return
        try:
            payload = build()
        except (auth.AuthError, api.ApiError) as exc:
            # Closing without writing is how the protocol says "failed". A
            # dialog here would appear during an unattended guide refresh.
            kodiutils.log_error("iptv manager: no data this time: %s" % exc)
            return
        except Exception as exc:
            kodiutils.log_error("iptv manager: giving up on this request: %s"
                                % exc)
            return
        else:
            try:
                sock.sendall(json.dumps(payload).encode("utf-8"))
            except OSError as exc:
                kodiutils.log_error("iptv manager: could not send: %s" % exc)
        finally:
            sock.close()

    # -- the two endpoints -------------------------------------------------

    def send_channels(self):
        self._send(self._channels)

    def send_epg(self):
        self._send(self._epg)

    def _stations(self, hours, pages=1):
        """The lineup, following the guide's pagination ``pages`` deep.

        A later page describes no channel: it carries a stationId and more
        airings, which parse_epg reads and merge_airings folds in. Before
        that, every airing past the first page was dropped, and every
        airing on the first page that was not the one on the air with it --
        so the Kodi PVR guide showed one programme per channel.
        """
        client = api.Api()
        response = client.epg(hours=hours,
                              max_airings=EPG_AIRINGS_PER_STATION,
                              order=kodiutils.get_setting("epg.order", "") or None)
        stations = epg_mod.parse_epg(response)
        token = epg_mod.continuation_token(response) if pages > 1 else None
        fetched = 1
        while token and fetched < pages:
            page = client.continuation(token)
            fetched += 1
            added = epg_mod.merge_airings(stations, epg_mod.parse_epg(page))
            kodiutils.log("iptv manager: page %d added %d airing(s)"
                          % (fetched, added))
            following = epg_mod.continuation_token(page)
            if not added or not following or following == token:
                break
            token = following
        return stations

    def _channels(self):
        """JSON-STREAMS: one entry per station.

        The stream url names the *station*, not the airing playing when the
        guide was built. IPTV Manager writes these into a playlist that
        outlives any one programme, so a video id here would be a channel
        that plays the wrong thing an hour later, and a dead link after that.
        """
        streams = []
        for station in self._stations(hours=2):
            if not station.station_id or not station.name:
                continue
            streams.append({
                "id": station.station_id,
                "name": station.name,
                "logo": station.logo or "",
                "stream": ("plugin://plugin.video.youtubetv/"
                           "?action=play_channel&station_id=%s"
                           % quote(station.station_id)),
            })
        kodiutils.log("iptv manager: offering %d channel(s)" % len(streams))
        return {"version": 1, "streams": streams}

    def _epg(self):
        """JSON-EPG: the schedule, keyed by the same station id."""
        guide = {}
        airings = 0
        for station in self._stations(hours=EPG_HOURS, pages=EPG_PAGES):
            if not station.station_id:
                continue
            programmes = []
            for airing in station.airings:
                if not airing.start_ms or not airing.end_ms:
                    continue
                entry = {
                    "start": _iso(airing.start_ms),
                    "stop": _iso(airing.end_ms),
                    "title": airing.title or station.name,
                }
                if airing.description:
                    entry["description"] = airing.description
                if airing.art:
                    entry["image"] = airing.art
                if airing.video_id:
                    # Lets the guide play a programme directly. Only what is
                    # on now will actually resolve -- catch-up is a separate
                    # entitlement -- but the url is right either way.
                    entry["stream"] = ("plugin://plugin.video.youtubetv/"
                                       "?action=play&video_id=%s"
                                       % quote(airing.video_id))
                programmes.append(entry)
            if programmes:
                guide[station.station_id] = programmes
                airings += len(programmes)
        kodiutils.log("iptv manager: offering %d airing(s) across %d channel(s)"
                      % (airings, len(guide)))
        return {"version": 1, "epg": guide}
