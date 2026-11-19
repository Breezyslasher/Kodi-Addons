"""Skip recap / Skip intro / Next episode buttons over the video.

Kodi has no skip button of its own. This module shows a small dialog in the
bottom-right corner while a marked part of the video plays, as streaming
apps do. Any add-on that knows where those parts are can use it:

    from skipbuttons import SkipController

    class Player(xbmc.Player):
        def __init__(self):
            xbmc.Player.__init__(self)
            self.skip = SkipController(self, play_next=self.start_next_episode)

        def onAVStarted(self):
            self.skip.reset({"recap": (10, 61), "intro": (62, 152),
                             "credits": 3432}, has_next=True)

        def onPlayBackStopped(self):
            self.skip.reset()          # closes any button

    # and once a second, from the service loop, while playing:
    player.skip.tick(player.getTime(), time.time())

Markers are seconds into the video; any of them may be missing:

* "recap": (start, end)  -> Skip recap (jumps to end)
* "intro": (start, end)  -> Skip intro (jumps to end)
* "credits": start       -> Next episode, counting down; Watch credits

Skip buttons hide themselves after SKIP_SHOW_SECS so Kodi's own controls
stay reachable; Back, or a click anywhere but the buttons, hides any
button. At the credits the countdown calls
play_next() unless the viewer chooses Watch credits.
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

PRIMARY, SECONDARY, ELSEWHERE = 3001, 3002, 3000
BACK = (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU)
SKIP_SHOW_SECS = 10     # a skip button hides itself after this long
NEXT_DELAY_SECS = 10    # the countdown before the next episode starts
MODULE_ID = "script.module.skipbuttons"
SKIN_FILE = "script-skipbuttons.xml"


def _module():
    return xbmcaddon.Addon(MODULE_ID)


def default_labels():
    """The module's own, translatable button texts."""
    s = _module().getLocalizedString
    return {"recap": s(30000), "intro": s(30001), "next": s(30002), "credits": s(30003)}


def _log(msg):
    xbmc.log("[%s] %s" % (MODULE_ID, msg), xbmc.LOGINFO)


class SkipDialog(xbmcgui.WindowXMLDialog):

    def __init__(self, *args, **kwargs):
        xbmcgui.WindowXMLDialog.__init__(self)
        self.choice = None          # "primary", "secondary" or "back"
        self.primary_label = ""
        self.secondary_label = ""

    def onInit(self):
        self.getControl(PRIMARY).setLabel(self.primary_label)
        if self.secondary_label:
            self.setProperty("secondary", "1")
            self.getControl(SECONDARY).setLabel(self.secondary_label)
        else:
            self.clearProperty("secondary")
        self.setFocusId(PRIMARY)

    def set_primary(self, label):
        self.primary_label = label
        try:
            self.getControl(PRIMARY).setLabel(label)
        except RuntimeError:
            pass                    # not initialised yet: onInit sets it

    def onClick(self, control_id):
        # A click outside the buttons lands on ELSEWHERE: same as Back.
        self.choice = {PRIMARY: "primary", SECONDARY: "secondary"}.get(control_id, "back")
        self.close()

    def onAction(self, action):
        if action.getId() in BACK:
            self.choice = "back"
            self.close()


class SkipController(object):
    """Drive with tick(position, now) about once a second while playing."""

    def __init__(self, player, play_next=None, labels=None, autoplay=None):
        """player: the xbmc.Player (for seekTime); play_next: callable that
        starts the next episode (no Next episode button without it);
        labels: override any of {"recap", "intro", "next" (with %d for the
        seconds left), "credits"}; autoplay: callable returning whether the
        credits countdown is wanted (default: always)."""
        self.player = player
        self.play_next = play_next
        self.labels = default_labels()
        self.labels.update(labels or {})
        self.autoplay = autoplay or (lambda: True)
        self.skin_path = xbmcvfs.translatePath(_module().getAddonInfo("path"))
        self.reset()

    def reset(self, marks=None, has_next=False, credits=True):
        """New markers for a new video (none: just close). credits=False
        leaves the credits to someone else (e.g. Up Next's pop-up)."""
        self.close()
        self.marks = marks or {}
        self.has_next = has_next
        self.own_credits = credits
        self.done = set()                # segments dismissed or used
        self.shown_at = None

    def close(self):
        d = getattr(self, "dialog", None)
        if d is not None:
            try:
                d.close()
            except RuntimeError:
                pass
        self.dialog = None
        self.segment = None

    def _current(self, pos):
        for name in ("recap", "intro"):
            seg = self.marks.get(name)
            if seg and seg[0] <= pos < seg[1] - 2:
                return name
        if (self.marks.get("credits") and self.has_next and self.play_next
                and self.own_credits and pos >= self.marks["credits"]
                and self.autoplay()):
            return "credits"
        return None

    def tick(self, pos, now):
        d = self.dialog
        if d is not None and d.choice:
            self._chosen(d.choice)
            return
        seg = self._current(pos)
        if seg in self.done:
            seg = None
        if seg != self.segment:
            self.close()
            if seg:
                self._open(seg, now)
            return
        if not seg:
            return
        if seg == "credits":
            left = NEXT_DELAY_SECS - int(now - self.shown_at)
            if left <= 0:
                self.done.add("credits")
                self.close()
                self.play_next()
                return
            self.dialog.set_primary(self.labels["next"] % left)
        elif now - self.shown_at >= SKIP_SHOW_SECS:
            self.done.add(seg)       # out of the way; Kodi's OSD is back
            self.close()

    def _open(self, seg, now):
        if seg == "credits":
            primary = self.labels["next"] % NEXT_DELAY_SECS
            secondary = self.labels["credits"]
        else:
            primary, secondary = self.labels[seg], ""
        self.segment, self.shown_at = seg, now
        d = SkipDialog(SKIN_FILE, self.skin_path, "default", "1080i")
        d.primary_label, d.secondary_label = primary, secondary
        self.dialog = d
        d.show()

    def _chosen(self, choice):
        seg = self.segment
        self.done.add(seg)
        self.close()
        if choice != "primary":
            return                    # Back, or Watch credits: carry on
        if seg == "credits":
            self.play_next()
        else:
            try:
                self.player.seekTime(self.marks[seg][1])
                _log("skipped the %s" % seg)
            except RuntimeError as exc:
                _log("skip failed: %s" % exc)
