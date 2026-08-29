_S={}
class Addon(object):
    def __init__(self,*a): pass
    def getSetting(self,k): return _S.get(k,"")
    def getSettingBool(self,k): return bool(_S.get(k))
    def setSetting(self,k,v): _S[k]=v
    def setSettingBool(self,k,v): _S[k]=v
    def getAddonInfo(self,k): return {"id":"plugin.video.youtubetv","profile":"/tmp/ytvprofile","path":"."}.get(k,"")
    def getLocalizedString(self,i): return ""

# Other addons, keyed by id, for the credential-borrowing test.
OTHERS = {}
_real_Addon = Addon
class Addon(_real_Addon):
    def __init__(self, addon_id=None):
        if addon_id and addon_id not in OTHERS:
            raise RuntimeError("Unknown addon id: %s" % addon_id)
        self._other = OTHERS.get(addon_id) if addon_id else None
        _real_Addon.__init__(self)
    def getSetting(self, k):
        if self._other is not None:
            return self._other.get(k, "")
        return _S.get(k, "")
