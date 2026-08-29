"""The audio fragment offset, without a network or a CDM."""
import os, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs"); sys.argv = ["x", "1", ""]
sys.path.insert(0, "/home/user/Kodi-Addons/plugin.video.youtubetv")
from lib import sabr_session

class S(sabr_session.Session):
    def __init__(self):            # bypass the network constructor
        self.segments = {150: {1: b"clear-fragment-1",
                               2: b"encrypted-2", 3: b"encrypted-3"}}
        self.skip = {}
        self.respell = set()
    def fetch(self):
        return False

s = S()
print("  no offset,  ISA asks 1 ->", s.segment(150, 1))
print("  no offset,  ISA asks 2 ->", s.segment(150, 2))
s.skip[150] = 1
print("  offset 1,   ISA asks 1 ->", s.segment(150, 1))
print("  offset 1,   ISA asks 2 ->", s.segment(150, 2))
assert s.segment(150, 1) == b"encrypted-2", "the clear fragment must not be served"
# A second session must not inherit the first one's offset.
t = S()
assert t.skip == {}, "skip must be per session, not shared"
print("  a second session starts with no offset:", t.skip == {})
print("ok")
