"""Background service: the licence proxy, and the live heartbeat loop.

Two jobs that have to outlive a plugin invocation. The plugin process exits as
soon as it resolves a URL, but ISA keeps fetching licences for as long as the
stream plays, and YouTube expects a heartbeat every 30 seconds while it does.
"""

import json
import time

import xbmc

from lib import api, auth, kodiutils, license_proxy

HEARTBEAT_DEFAULT_MS = 30000


class Monitor(xbmc.Monitor):
    def __init__(self):
        super(Monitor, self).__init__()
        self.settings_changed = False

    def onSettingsChanged(self):
        self.settings_changed = True


class Heartbeat(object):
    """Polls player/heartbeat for as long as something is playing.

    YouTube's heartbeat carries HEARTBEAT_CHECK_TYPE_YPC, the entitlement
    check, and the player response says how long a client may go quiet:
    ``intervalMilliseconds`` 30000 with ``maxRetries`` 3. Ninety seconds.

    This used to run only for live, on the assumption that on-demand had
    nothing to keep alive. The 2026-08-28 03:10 capture says otherwise -- two
    minutes of *on-demand* playback in the web player carries a heartbeat POST
    every thirty seconds, sequence numbers 0 and 1, each echoing the previous
    response's heartbeatServerData. So it runs for everything now.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.video_id = None
        self.cpn = None
        self.sequence = 0
        self.token = None
        self.server_data = None
        self.next_due = 0
        self.interval = HEARTBEAT_DEFAULT_MS
        self._client = None

    def adopt(self, context):
        """Pick up the playback the plugin just armed.

        The token and the first server data come from the player response's
        heartbeatParams and are not optional: the web player quotes both on its
        very first call. Sending neither is how a session goes unrecognised.
        """
        if not context.get("video_id"):
            return
        if context.get("video_id") == self.video_id:
            return
        self.reset()
        params = context.get("heartbeat") or {}
        self.video_id = context.get("video_id")
        self.cpn = context.get("cpn")
        self.token = params.get("heartbeatToken")
        self.server_data = params.get("heartbeatServerData")
        try:
            self.interval = max(int(params.get("intervalMilliseconds") or
                                    HEARTBEAT_DEFAULT_MS), 5000)
        except (TypeError, ValueError):
            self.interval = HEARTBEAT_DEFAULT_MS
        self.next_due = time.time() + self.interval / 1000.0
        kodiutils.log("heartbeat: following %s every %ds, token %s"
                      % (self.video_id, self.interval / 1000,
                         "yes" if self.token else "MISSING"))

    def tick(self):
        if not self.video_id or time.time() < self.next_due:
            return
        if self._client is None:
            try:
                self._client = api.Api()
            except auth.AuthError:
                self.reset()
                return
        try:
            response = self._client.heartbeat(self.video_id, self.cpn,
                                              self.sequence, self.token,
                                              self.server_data)
        except (auth.AuthError, api.ApiError) as exc:
            kodiutils.log_error("heartbeat failed: %s" % exc)
            self.next_due = time.time() + self.interval / 1000.0
            return

        self.sequence += 1
        self.server_data = response.get("heartbeatServerData") or self.server_data
        status = (response.get("playabilityStatus") or {}).get("status")
        if status and status != "OK":
            reason = (response.get("playabilityStatus") or {}).get("reason")
            kodiutils.log_error("heartbeat says %s: %s" % (status, reason))
            kodiutils.notify(reason or status, "Stream stopped")

        try:
            delay = int(response.get("pollDelayMs") or self.interval)
        except (TypeError, ValueError):
            delay = self.interval
        self.next_due = time.time() + max(delay, 5000) / 1000.0
        kodiutils.log("heartbeat %d acknowledged (%s), next in %ds"
                      % (self.sequence - 1, status or "no status", delay / 1000))


def main():
    kodiutils.log("service starting")
    proxy = license_proxy.LicenseProxy()
    if not proxy.start():
        kodiutils.log_error("licence proxy did not start -- playback of "
                            "protected streams will fail")

    monitor = Monitor()
    heartbeat = Heartbeat()

    while not monitor.abortRequested():
        if monitor.settings_changed:
            monitor.settings_changed = False
            if license_proxy._port() != proxy.port:
                kodiutils.log("proxy port changed, restarting it")
                proxy.stop()
                proxy = license_proxy.LicenseProxy()
                proxy.start()

        if xbmc.Player().isPlayingVideo():
            heartbeat.adopt(kodiutils.read_json(
                license_proxy.CONTEXT_FILE, default={}) or {})
            heartbeat.tick()
        elif heartbeat.video_id:
            kodiutils.log("heartbeat: playback ended")
            heartbeat.reset()

        if monitor.waitForAbort(2):
            break

    kodiutils.log("service stopping")
    proxy.stop()


if __name__ == "__main__":
    main()
