"""Starz catalogue and playback-info calls.

Every method that would talk to Starz raises NotReadyError until the request
is read out of a capture (docs/recon.md rows C, D, K). No host or path is
invented here on purpose: a made-up URL is worse than a blank.

Shapes returned to default.py (keep these stable as the real calls land):

    item     {"id", "type": "movie"|"series"|"season"|"episode",
              "title", "plot", "year", "duration" (secs), "season",
              "episode", "art": {"thumb", "poster", "fanart"}}
    row      {"id", "title", "items": [item, ...]}
    playback {"manifest_url", "manifest_type": "mpd"|"hls",
              "license_url", "license_headers": {name: value},
              "stream_headers": {name: value},
              "subtitles": [url, ...]}
"""

import requests

from lib import kodiutils
from lib.auth import NotReadyError

TIMEOUT = 20


class StarzApi(object):

    def __init__(self, auth):
        self.auth = auth
        self.session = requests.Session()

    def _headers(self):
        """Headers every API call needs.

        TODO(recon C, K): auth header name/format, plus any required
        User-Agent, Origin, device id or x-* headers.
        """
        raise NotReadyError("API headers not known yet (recon rows C, K).")

    def _get(self, url, params=None):
        resp = self.session.get(url, params=params, headers=self._headers(),
                                timeout=TIMEOUT)
        if resp.status_code == 401 and self.auth.signed_in:
            kodiutils.log("401 from API, refreshing token")
            self.auth.refresh()
            resp = self.session.get(url, params=params,
                                    headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # -- catalogue (recon C) ----------------------------------------------

    def home_rows(self):
        raise NotReadyError("Home rows not built yet (recon row C).")

    def row_items(self, row_id, page=1):
        raise NotReadyError("Row listing not built yet (recon row C).")

    def seasons(self, series_id):
        raise NotReadyError("Series drill-down not built yet (recon row C).")

    def episodes(self, series_id, season_id):
        raise NotReadyError("Season drill-down not built yet (recon row C).")

    def search(self, query):
        raise NotReadyError("Search not built yet (recon row C).")

    # -- playback (recon D-H, J) -------------------------------------------

    def playback(self, content_id):
        """Resolve a title to a playback dict (see module docstring).

        TODO(recon D): the request made on "play"; where the manifest URL,
        licence URL and any per-play token live in its response.
        """
        raise NotReadyError("Playback info not built yet (recon row D).")
