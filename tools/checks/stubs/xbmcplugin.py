ITEMS=[]
def addDirectoryItem(handle,url,li,isFolder=False): ITEMS.append((url,li.label,isFolder))
def endOfDirectory(h,*a,**k): pass
def setContent(h,c): pass
def setResolvedUrl(h,ok,li): pass
