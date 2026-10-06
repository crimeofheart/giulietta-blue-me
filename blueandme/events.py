"""One small typed pub/sub, replacing the three ad-hoc event buses in the
reference projects.

Event names are declared up front. Subscribing to an undeclared event raises at
subscription time rather than silently never firing, which is the failure mode
both upstream buses have.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("blueandme.events")

Handler = Callable[..., None]

#: Every event this application can emit. Adding a publisher means adding a name.
EVENTS = frozenset({
    "button",            # (name: str) a steering-wheel button, debounced
    "button_released",   # (name: str, held_s: float) the same button let go
    "ignition",          # (on: bool)
    "sleep_requested",   # ()
    "proxi_answered",    # (value: bytes)
    "audio_channel",     # (channel: str) which source the radio selected
    "media_playing",     # (playing: bool)
    "media_track",       # (artist: str, title: str)
    "media_position",    # (seconds: int)
    "media_skipped",     # (action: str) the wheel sent next / previous / play_pause
    "radio_key",         # (name: str, pressed: bool) the radio's arrow pad on Blue&Me
    "bt_connected",      # (connected: bool, address: str)
})


class EventBus:
    def __init__(self, names: "frozenset[str]" = EVENTS) -> None:
        self._names = names
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event: str, handler: Handler) -> None:
        if event not in self._names:
            raise KeyError(
                f"unknown event {event!r}; declared events are "
                f"{', '.join(sorted(self._names))}"
            )
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        if event not in self._names:
            raise KeyError(f"cannot emit undeclared event {event!r}")
        for handler in self._handlers.get(event, ()):
            try:
                handler(*args)
            except Exception:
                log.exception("handler %r failed on event %r", handler, event)

    def handlers(self, event: str) -> int:
        return len(self._handlers.get(event, ()))
