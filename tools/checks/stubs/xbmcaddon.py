_S={}
class Addon(object):
    def __init__(self,*a): pass
    def getSetting(self,k): return _S.get(k,"")
    def getSettingBool(self,k): return bool(_S.get(k))
    def setSetting(self,k,v): _S[k]=v
    def setSettingBool(self,k,v): _S[k]=v
    def getAddonInfo(self,k): return {"id":"plugin.video.youtubetv","profile":"/tmp/ytvprofile","path":"."}.get(k,"")
    def getLocalizedString(self,i): return ""
