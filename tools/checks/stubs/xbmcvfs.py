import os
def translatePath(p): return p
def exists(p): return os.path.exists(p)
def mkdirs(p):
    os.makedirs(p, exist_ok=True); return True
