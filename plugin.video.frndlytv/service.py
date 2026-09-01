"""Background service: give the stream slot back when playback stops.

Friendly TV counts concurrent streams per account and a slot stays taken
until something posts its poll key to ``stream/session/end``. The plugin
process exits as soon as it hands a url to Kodi, so it cannot be the thing
that does that -- it is already gone by the time the viewer stops watching.
This is.

Without it an account locks itself out of its own subscription after a few
plays, with "you are watching on too many devices" and no device actually
watching.
"""

import xbmc

from lib import api, auth, kodiutils, playback


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        self._playing_key = ""

    def run(self):
        kodiutils.log("service starting; %s" % kodiutils.platform())
        player = xbmc.Player()
        while not self.abortRequested():
            try:
                self._tick(player)
            except Exception as exc:
                # A raise here would end the service for the rest of the
                # Kodi session, and with it every future slot release.
                kodiutils.log_error("service tick failed: %s" % exc)
            if self.waitForAbort(2):
                break
        # Kodi closing mid-stream still leaves a slot held.
        self._release()
        kodiutils.log("service stopping")

    def _tick(self, player):
        if player.isPlayingVideo():
            if not self._playing_key:
                context = kodiutils.read_json(playback.CONTEXT_FILE,
                                              default={}) or {}
                key = context.get("poll_key") or ""
                if key:
                    self._playing_key = key
                    kodiutils.log("holding a stream slot for %s"
                                  % (context.get("path") or "?"))
        elif self._playing_key:
            self._release()

    def _release(self):
        key, self._playing_key = self._playing_key, ""
        if not key:
            return
        try:
            api.Api().end_session(key)
        except (api.ApiError, auth.AuthError) as exc:
            # Nothing to retry against: the session may already have been
            # reaped server-side, and a slot left behind times out on its own.
            kodiutils.log("could not release the stream slot: %s" % exc)
        kodiutils.delete_file(playback.CONTEXT_FILE)


if __name__ == "__main__":
    Service().run()
