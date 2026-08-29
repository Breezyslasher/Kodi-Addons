class ListItem(object):
    def __init__(self, label="", **kw): self.label=label; self._p={}
    def setArt(self,*a,**k): pass
    def setProperty(self,k,v): self._p[k]=v
    def getVideoInfoTag(self):
        class T:
            def __getattr__(self,n): return lambda *a,**k: None
        return T()
class Dialog(object):
    def __getattr__(self,n): return lambda *a,**k: None
class DialogProgress(Dialog): pass
NOTIFICATION_INFO="info"; NOTIFICATION_WARNING="warn"; NOTIFICATION_ERROR="error"
INPUT_ALPHANUM=0
class WindowXMLDialog(object):
    def __init__(self,*a,**k): pass
class WindowXML(object):
    def __init__(self,*a,**k): pass
