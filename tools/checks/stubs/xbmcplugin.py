ITEMS = []
SORTS = []

# The handful this addon uses. Values are Kodi's own.
SORT_METHOD_NONE = 0
SORT_METHOD_LABEL = 1
SORT_METHOD_UNSORTED = 42


def addDirectoryItem(handle, url, li, isFolder=False):
    # The ListItem is kept, not just its label: whether a film ends up
    # playable, and what its context menu offers, is the thing worth
    # checking and neither is visible in a label.
    ITEMS.append((url, li.label, isFolder, li))


def addSortMethod(handle, sortMethod, *a, **k):
    # Recorded, because the *first* one added is the one Kodi defaults to,
    # and a listing whose order matters -- seasons, a channel's schedule --
    # is wrong if anything but NONE gets there first.
    SORTS.append(sortMethod)


def endOfDirectory(h, *a, **k): pass
def setContent(h, c): pass
def setResolvedUrl(h, ok, li): pass
