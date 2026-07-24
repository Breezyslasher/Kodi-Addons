"""
Watch Party background service.

Watches session.json (written by the UI in default.py) and reconciles the
running pieces with it:

    mode == 'host'   -> run the relay server locally AND join it over
                        localhost as a regular member
    mode == 'guest'  -> join a remote relay
    mode == 'off'    -> tear everything down

The session file's mtime is checked once a second, so UI actions take
effect almost immediately without any IPC.
"""
import os

import xbmc

import common
from engine import SyncEngine
from relay import RelayServer


class Service:
    def __init__(self):
        self.relay = None
        self.engine = None
        self.session_mtime = None

    # -- reconcile ---------------------------------------------------------

    def _session(self):
        return common.load_json(common.session_file(),
                                default={'mode': 'off'})

    def _teardown(self):
        if self.engine:
            common.log("stopping engine")
            try:
                self.engine.stop()
            except Exception as e:
                common.log(f"engine stop failed: {e}", xbmc.LOGERROR)
            self.engine = None
        if self.relay:
            common.log("stopping relay")
            try:
                self.relay.stop()
            except Exception as e:
                common.log(f"relay stop failed: {e}", xbmc.LOGERROR)
            self.relay = None

    def _start_host(self, session):
        port = int(session.get('port') or 8765)
        room = str(session.get('room') or '')
        try:
            self.relay = RelayServer(port, room)
            self.relay.start()
            common.log(f"relay listening on :{port} (room {room})")
        except OSError as e:
            common.log(f"cannot start relay on :{port}: {e}", xbmc.LOGERROR)
            common.notify(f"Cannot host on port {port}")
            self.relay = None
            return
        self._start_engine('127.0.0.1', port, room)
        if self.engine:
            common.notify(f"Party started — code {room}")

    def _start_guest(self, session):
        host = str(session.get('host') or '')
        port = int(session.get('port') or 8765)
        room = str(session.get('room') or '')
        self._start_engine(host, port, room)
        if self.engine:
            common.notify(f"Joined party at {host}")

    def _start_engine(self, host, port, room):
        engine = SyncEngine(host, port, room)
        try:
            engine.start()
            self.engine = engine
        except Exception as e:
            common.log(f"could not join party: {e}", xbmc.LOGERROR)
            common.notify(f"Could not join party: {e}")
            common.save_json(common.status_file(),
                             {'active': False, 'error': str(e)})

    def _reconcile(self):
        session = self._session()
        mode = session.get('mode') or 'off'
        self._teardown()
        if mode == 'host':
            self._start_host(session)
        elif mode == 'guest':
            self._start_guest(session)

    # -- main loop ---------------------------------------------------------

    def run(self):
        common.log("service starting")
        monitor = xbmc.Monitor()
        session_path = common.session_file()
        while not monitor.abortRequested():
            try:
                mtime = os.path.getmtime(session_path)
            except OSError:
                mtime = None
            if mtime != self.session_mtime:
                self.session_mtime = mtime
                self._reconcile()
            if monitor.waitForAbort(1):
                break
        self._teardown()
        common.log("service stopped")


if __name__ == '__main__':
    Service().run()
