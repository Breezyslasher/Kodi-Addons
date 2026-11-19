"""Skip recap / Skip intro / Next episode buttons over the video.

Written to be add-on independent (to become a shared module): the caller
passes the markers, the labels and what "next episode" does. Nothing here
knows about Starz except markers(), which reads Starz's fields.

Every episode's detail (v8/content) carries them, as the Starz apps use:
"recap": {"timeIn", "timeOut"}, "intro": {"timeIn", "timeOut"} and
"creditTimeIn" (seconds; e.g. Power S1E2: recap 10-61, intro 62-152,
credits 3432). Kodi has no skip button of its own, so this shows a small
dialog over the video (resources/skins/default/1080i/script-starz-skip.xml),
as Up Next does for its pop-up.
"""

import xbmc
import xbmcgui


PRIMARY, SECONDARY = 3001, 3002
BACK = (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU)
SKIP_SHOW_SECS = 10     # a skip button hides itself after this long
NEXT_DELAY_SECS = 10    # the countdown before the next episode starts


def markers(content):
    """{"recap": (in, out), "intro": (in, out), "credits": t} from a title's
    detail; only sensible ranges."""
    out = {}
    runtime = _num(content.get("runtime"))
    for name in ("recap", "intro"):
        seg = content.get(name) or {}
        t_in, t_out = _num(seg.get("timeIn")), _num(seg.get("timeOut"))
        if t_in is not None and t_out and t_out - t_in >= 5:
            out[name] = (t_in, t_out)
    credits = _num(content.get("creditTimeIn"))
    # Ignore a credits time at (or past) the very end (previews: = runtime).
    if credits and credits > 60 and (not runtime or credits < runtime - 5):
        out["credits"] = credits
    return out


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        self.choice = "primary" if control_id == PRIMARY else "secondary"
        self.close()

    def onAction(self, action):
        if action.getId() in BACK:
            self.choice = "back"
            self.close()


def _log(msg):
    xbmc.log("[skip] %s" % msg, xbmc.LOGINFO)


def new_dialog(skin_path, primary, secondary=""):
    d = SkipDialog("script-starz-skip.xml", skin_path, "default", "1080i")
    d.primary_label, d.secondary_label = primary, secondary
    return d


class SkipController(object):
    """Driven once a second by the service with the playing position."""

    def __init__(self, player, play_next, labels, skin_path, autoplay=lambda: True):
        """labels: {"recap", "intro", "next" (with %d for seconds left),
        "credits"}; skin_path: the add-on folder holding resources/skins;
        autoplay: callable, whether the credits countdown is wanted."""
        self.player = player
        self.play_next = play_next   # callable: start the next episode
        self.labels = labels
        self.skin_path = skin_path
        self.autoplay = autoplay
        self.reset()

    def reset(self, marks=None, has_next=False, next_title="", own_credits=True):
        self.close()
        self.marks = marks or {}
        self.has_next = has_next
        self.next_title = next_title
        self.own_credits = own_credits   # False when Up Next does the credits
        self.done = set()                # segments dismissed or used
        self.dialog = None
        self.segment = None
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
        if (self.marks.get("credits") and self.has_next and self.own_credits
                and pos >= self.marks["credits"]
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
        self.dialog = new_dialog(self.skin_path, primary, secondary)
        self.dialog.show()

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
