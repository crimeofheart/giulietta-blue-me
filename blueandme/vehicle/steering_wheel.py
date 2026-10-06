"""Steering-wheel button decoding.

The button frame is a bitmask: a non-zero payload means one or more buttons are
held, an all-zero payload means everything is released. The frame repeats while
a button is held, so a debounce is needed to turn a stream of identical frames
into one press event.

Both reference projects debounce on a fixed timer (fiatcan 0.3s, alfa-blue-me
0.7s), which drops genuine repeat presses made faster than that. This decoder
instead edges on the release-to-press transition and keeps the timer only as a
guard against contact bounce, so deliberate double-taps still register.

The Giulietta's Blue&Me acted on release, and a press held for more than a
second meant something else (manual 604_38_194, p. 9), so each release is an
event too, carrying how long the button was held.
"""

from __future__ import annotations

import logging
import time

from ..can.bus import ReceivedFrame
from ..events import EventBus
from ..protocol.frame import Profile

log = logging.getLogger("blueandme.buttons")

FRAME = "steering_wheel_buttons"

#: Guard against contact bounce only. Not a repeat-rate limiter.
BOUNCE_S = 0.05


class SteeringWheel:
    def __init__(
        self,
        profile: Profile,
        bus: EventBus,
        bounce_s: float = BOUNCE_S,
        clock=time.monotonic,
    ) -> None:
        self.profile = profile
        self.bus = bus
        self.bounce_s = bounce_s
        self._clock = clock
        self._held: set[str] = set()
        self._last_press: dict[str, float] = {}
        self._pressed_at: dict[str, float] = {}

    def on_frame(self, frame: ReceivedFrame) -> None:
        self.feed(frame.data)

    def feed(self, payload: bytes) -> list[str]:
        """Decode one button frame. Returns the buttons newly pressed."""
        pressed = set(self.profile.decode_buttons(payload))
        now = self._clock()
        fired: list[str] = []

        for name in sorted(pressed - self._held):
            last = self._last_press.get(name)
            if last is not None and now - last < self.bounce_s:
                continue
            self._last_press[name] = now
            self._pressed_at[name] = now
            fired.append(name)
            log.info("BUTTON: %s", name.upper())
            self.bus.emit("button", name)

        for name in sorted(self._held - pressed):
            start = self._pressed_at.pop(name, None)
            if start is None:
                continue  # its press was a bounce
            held = now - start
            log.info("released: %s after %.2f s", name.upper(), held)
            self.bus.emit("button_released", name, held)

        self._held = pressed
        return fired

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self._held)
