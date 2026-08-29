LOGINFO=1; LOGERROR=2; LOGDEBUG=0
def log(msg, level=0): pass
def translatePath(p): return p
class Monitor(object):
    def waitForAbort(self, t=0): return False
    def abortRequested(self): return False
