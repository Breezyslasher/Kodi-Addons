"""Background service: runs the Widevine licence proxy for the Kodi session.

InputStream Adaptive talks to the proxy over localhost during playback, so it
must outlive the short-lived plugin process. This service starts it once and
keeps it up until Kodi shuts down.
"""

import xbmc

from lib import kodiutils
from lib.license_proxy import LicenseProxy


def main():
    proxy = LicenseProxy()
    try:
        proxy.start()
    except Exception as exc:
        kodiutils.log_error("Failed to start licence proxy: %s" % exc)
        return

    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if monitor.waitForAbort(5):
            break
    proxy.stop()
    kodiutils.log("License proxy stopped")


if __name__ == "__main__":
    main()
