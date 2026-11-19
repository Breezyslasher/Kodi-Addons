# Skip Buttons (`script.module.skipbuttons`)

Kodi has no "Skip intro" button of its own. This module adds the ones
streaming apps have, for any video add-on that knows where those parts of
a video are:

- **Skip recap** and **Skip intro** in the bottom-right corner while they
  play; they jump to the end of that part, and hide themselves after 10
  seconds (or on Back, or a click anywhere else) so Kodi's own controls
  stay reachable.
- At the credits, **Next episode** counting down from 10, and **Watch
  credits** to stay.

The buttons are a dark, see-through rounded box; the focused one turns white.

## Using it

In your `addon.xml`:

```xml
<import addon="script.module.skipbuttons" version="1.0.0"/>
```

In your service (it must stay running while the video plays):

```python
import time
import xbmc
from skipbuttons import SkipController


class Player(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)
        self.skip = SkipController(self, play_next=self.play_next_episode)

    def onAVStarted(self):
        # Seconds into the video; leave out what you don't know.
        self.skip.reset({"recap": (10, 61), "intro": (62, 152), "credits": 3432},
                        has_next=True)

    def onPlayBackStopped(self):
        self.skip.reset()            # closes any button

    def play_next_episode(self):
        xbmc.executebuiltin("PlayMedia(plugin://plugin.video.example/?play=next)")


player = Player()
monitor = xbmc.Monitor()
while not monitor.waitForAbort(1):
    if player.isPlayingVideo():
        player.skip.tick(player.getTime(), time.time())
```

Options:

- `SkipController(player, play_next=None, labels=None, autoplay=None)`:
  without `play_next` there is no Next episode button; `labels` overrides
  any of `"recap"`, `"intro"`, `"next"` (with `%d` for the seconds left) and
  `"credits"`; `autoplay` is a callable, e.g. your add-on's setting, that
  turns the credits countdown off when it returns False.
- `reset(marks, has_next=False, credits=True)`: `credits=False` leaves the
  credits to someone else, e.g. the Up Next add-on's pop-up.

Used by [Starz](../plugin.video.starz/), which takes the times from Starz's
own metadata.
