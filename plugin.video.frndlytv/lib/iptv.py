"""IPTV Manager integration.

service.iptv.manager turns an addon's channel list and schedule into a real
Kodi PVR source, so Friendly TV's channels appear in the TV section with a
proper guide rather than only inside this addon's own menus.

How it works, from the addon's side (service.iptv.manager wiki,
"Integration"): the addon advertises ``iptv.enabled``, ``iptv.channels_uri``
and ``iptv.epg_uri``. IPTV Manager binds a socket on a free localhost port,
merges ``port=`` into the advertised plugin url (so an url that already has a
query keeps it, which is why this addon's ``?action=`` routing is enough), and
calls it with RunPlugin. The addon connects back to that port and writes JSON.

Two details from its source shape the code here. It waits ten seconds for the
*connection*, then as long as the connection stays open for the data -- so the
socket is opened before the guide is fetched, not after. And it reads until
the connection closes, so nothing is framed or terminated.

The wiki is explicit that an addon must never prompt for credentials here: a
guide refresh happens on IPTV Manager's schedule, and a sign-in dialog
appearing out of nowhere is worse than no data. So a failure logs and closes
the socket, which is the documented way to say "nothing this time".
"""

import json
import socket
import time

from urllib.parse import quote

from . import api, auth, kodiutils, parse

PLUGIN = "plugin://plugin.video.frndlytv/"

# How far ahead the exported guide reaches. The endpoint takes an arbitrary
# window, but it is asked for a day at a time in batches of twelve channels,
# which is the shape the web player uses and the only one observed answering.
EPG_HOURS = 24
EPG_BATCH = 12


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
        and unlimited time once it has one, and fetching a day of guide for a
        full lineup takes longer than ten seconds often enough to matter.
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

    def send_channels(self):
        self._send(self._channels)

    def send_epg(self):
        self._send(self._epg)

    # -- the lineup --------------------------------------------------------

    def _lineup(self, client):
        """The joined lineup, with anything Live Now missed filled in.

        The join itself lives in Api.lineup; this adds the per-channel
        fallback, which is worth its extra requests here because IPTV Manager
        writes a playlist that is meant to be complete.
        """
        channels = client.lineup()
        matched = sum(1 for c in channels if c["path"])
        kodiutils.log("iptv manager: %d channel(s), %d with a playable path"
                      % (len(channels), matched))
        if matched < len(channels):
            self._resolve_missing(client, channels)
        return channels

    def _resolve_missing(self, client, channels):
        """Fill in channels the Live Now join did not cover.

        The guide overlay names the channel a programme is on, so one short
        guide fetch gives the programme on the air and one overlay call per
        unresolved channel gives its slug. Bounded by how many are missing,
        and skipped entirely when none are -- this is a safety net, not the
        route the lineup normally takes.
        """
        missing = [c for c in channels if not c["path"]]
        if not missing:
            return
        now = int(time.time() * 1000)
        on_air = {}
        ids = [c["id"] for c in missing]
        for index in range(0, len(ids), EPG_BATCH):
            batch = ids[index:index + EPG_BATCH]
            try:
                rows = client.guide(batch, now, now + 3600 * 1000,
                                    page=index // EPG_BATCH)
            except api.ApiError as exc:
                kodiutils.log("iptv manager: could not look up what is on "
                              "channels %s: %s" % (batch, exc))
                continue
            for row in rows:
                for raw in (row.get("programs") or []):
                    prog = parse.programme(raw)
                    if prog["path"] and prog["start_ms"] <= now < prog["end_ms"]:
                        on_air[str(row.get("channelId") or "")] = prog["path"]
                        break
        found = 0
        for channel in missing:
            programme_path = on_air.get(channel["id"])
            if not programme_path:
                continue
            try:
                channel["path"] = client.watch_live_path(programme_path)
            except api.ApiError as exc:
                kodiutils.log("iptv manager: overlay failed for channel %s: %s"
                              % (channel["id"], exc))
                continue
            if channel["path"]:
                found += 1
        kodiutils.log("iptv manager: the guide overlay resolved %d more "
                      "channel(s) of %d unmatched" % (found, len(missing)))

    def _channels(self):
        """JSON-STREAMS: one entry per channel.

        The stream url names the *channel*, not the programme on the air when
        the list was built. IPTV Manager writes these into a playlist that
        outlives any one programme, so anything narrower would be a channel
        that plays the wrong thing an hour later and a dead link after that.
        """
        client = api.Api()
        streams = []
        for channel in self._lineup(client):
            if not channel["name"] or not channel["path"]:
                continue
            streams.append({
                "id": channel["id"],
                "name": channel["name"],
                "logo": channel["logo"],
                "stream": PLUGIN + "?action=play&path=%s"
                          % quote(channel["path"]),
            })
        kodiutils.log("iptv manager: offering %d channel(s)" % len(streams))
        return {"version": 1, "streams": streams}

    def _epg(self):
        """JSON-EPG: the schedule, keyed by the same channel id."""
        client = api.Api()
        channels = self._lineup(client)
        wanted = [c["id"] for c in channels if c["path"]]
        names = {c["id"]: c["name"] for c in channels}

        now = int(time.time() * 1000)
        end = now + EPG_HOURS * 3600 * 1000
        guide = {}
        airings = 0
        for index in range(0, len(wanted), EPG_BATCH):
            batch = wanted[index:index + EPG_BATCH]
            try:
                rows = client.guide(batch, now, end,
                                    page=index // EPG_BATCH)
            except api.ApiError as exc:
                kodiutils.log_error("iptv manager: guide batch %d failed: %s"
                                    % (index // EPG_BATCH, exc))
                continue
            for row in rows:
                channel_id = str(row.get("channelId") or "")
                if not channel_id:
                    continue
                programmes = []
                for raw in (row.get("programs") or []):
                    prog = parse.programme(raw)
                    if not prog["start_ms"] or not prog["end_ms"]:
                        continue
                    entry = {
                        "start": _iso(prog["start_ms"]),
                        "stop": _iso(prog["end_ms"]),
                        "title": prog["title"] or names.get(channel_id, ""),
                    }
                    image = client.image(prog["image"])
                    if image:
                        entry["image"] = image
                    # No "stream" on any programme, deliberately. JSON-EPG
                    # calls it a catch-up facility -- "to directly play a
                    # program from the EPG" -- and this addon has none: the
                    # only thing behind a Friendly TV programme is its
                    # channel, live. Kodi draws a marker on every entry given
                    # one, so filling it in would claim a day of schedule was
                    # replayable when none of it is.
                    programmes.append(entry)
                if programmes:
                    guide.setdefault(channel_id, []).extend(programmes)
                    airings += len(programmes)
        kodiutils.log("iptv manager: offering %d airing(s) across %d "
                      "channel(s)" % (airings, len(guide)))
        return {"version": 1, "epg": guide}
