"""Receive-side fan-out: named frames to subscribed handlers.

Deliberately not another EventBus. The two reference projects between them
contain three different ad-hoc event buses with untyped string topics; this one
dispatches on FrameSpec names that are validated against the profile at
subscription time, so a typo fails at startup instead of silently never firing.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Callable

from ..protocol.frame import Profile
from .bus import ReceivedFrame

log = logging.getLogger("blueandme.rx")

Handler = Callable[[ReceivedFrame], None]


class Dispatcher:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self._handlers: dict[str, list[Handler]] = {}
        self._unmatched: list[Handler] = []
        self.counts: Counter[str] = Counter()
        self.unknown_ids: Counter[str] = Counter()

    def on(self, frame_name: str, handler: Handler) -> None:
        self.profile.require(frame_name)  # fail fast on typos
        self._handlers.setdefault(frame_name, []).append(handler)

    def on_unmatched(self, handler: Handler) -> None:
        """Called for every frame the profile does not recognise.

        During Phase 2 discovery this is where most traffic goes, which is the
        point: an unrecognised frame is a research lead, not an error.
        """
        self._unmatched.append(handler)

    def dispatch(self, frame: ReceivedFrame) -> None:
        if frame.spec is None:
            self.unknown_ids[frame.hex_id] += 1
            handlers: list[Handler] = self._unmatched
        else:
            self.counts[frame.spec.name] += 1
            handlers = self._handlers.get(frame.spec.name, ())
        for handler in handlers:
            try:
                handler(frame)
            except Exception:
                log.exception("handler %r failed on %r", handler, frame)

    def pump(self, bus, timeout: float | None = 1.0) -> None:
        for frame in bus.stream(timeout):
            self.dispatch(frame)
