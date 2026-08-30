ITEMS = []


def addDirectoryItem(handle, url, li, isFolder=False):
    # The ListItem is kept, not just its label: whether a film ends up
    # playable, and what its context menu offers, is the thing worth
    # checking and neither is visible in a label.
    ITEMS.append((url, li.label, isFolder, li))


def endOfDirectory(h, *a, **k): pass
def setContent(h, c): pass
def setResolvedUrl(h, ok, li): pass
