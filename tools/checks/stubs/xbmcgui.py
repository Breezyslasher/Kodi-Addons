class ListItem(object):
    def __init__(self, label="", **kw): self.label=label; self._p={}
    def setArt(self,*a,**k): pass
    def setProperty(self,k,v): self._p[k]=v
    def addContextMenuItems(self,items): self.menu=list(items)
    def getVideoInfoTag(self):
        # Records what was set rather than swallowing it. A listing whose
        # readers are all correct can still put nothing on screen, and that
        # is only visible by asking what the item ended up carrying.
        if not hasattr(self, "info"):
            self.info = _InfoTag()
        return self.info


class _InfoTag(object):
    def __init__(self): self.set = {}
    def __getattr__(self, name):
        if name.startswith("set"):
            def record(*args):
                self.set[name] = args[0] if len(args) == 1 else args
            return record
        raise AttributeError(name)
class Dialog(object):
    def __getattr__(self,n): return lambda *a,**k: None
class DialogProgress(Dialog): pass
NOTIFICATION_INFO="info"; NOTIFICATION_WARNING="warn"; NOTIFICATION_ERROR="error"
INPUT_ALPHANUM=0
class WindowXMLDialog(object):
    def __init__(self,*a,**k): pass
class WindowXML(object):
    def __init__(self,*a,**k): pass
