"""Ignition state, as the Body Computer announces it on CAN.

Presence emits the events from the Body Computer's wake and sleep frames; this
class is the policy layer that decides when "key off" becomes "shut down". It
needs the profile's status encoding (step 11). Pin 32 is also sensed, on a GPIO,
by power/standby.py, which wakes on either (decided 2026-09-25) so that both
are known and either can be relied on.
"""

from __future__ import annotations

import logging
import time

from ..events import EventBus

log = logging.getLogger("blueandme.ignition")


class IgnitionMonitor:
    def __init__(
        self,
        events: EventBus,
        stay_awake_s: int = 0,
        shutdown_delay_s: int = 30,
        clock=time.monotonic,
    ) -> None:
        self.events = events
        self.stay_awake_s = stay_awake_s
        """Seconds to keep running after key-off. Non-zero trades battery drain
        for a warm start next time -- see decision gate D3."""
        self.shutdown_delay_s = shutdown_delay_s
        self.on = False
        self.key_off_at: float | None = None
        self._clock = clock
        events.on("ignition", self._on_ignition)

    def _on_ignition(self, on: bool) -> None:
        self.on = on
        if on:
            if self.key_off_at is not None:
                log.info("ignition back on; cancelling pending shutdown")
            self.key_off_at = None
        else:
            self.key_off_at = self._clock()
            log.info(
                "ignition off; shutdown in %ds",
                self.stay_awake_s + self.shutdown_delay_s,
            )

    def should_shut_down(self) -> bool:
        if self.on or self.key_off_at is None:
            return False
        elapsed = self._clock() - self.key_off_at
        return elapsed >= self.stay_awake_s + self.shutdown_delay_s

    def seconds_until_shutdown(self) -> float | None:
        if self.on or self.key_off_at is None:
            return None
        remaining = (
            self.stay_awake_s + self.shutdown_delay_s
            - (self._clock() - self.key_off_at)
        )
        return max(remaining, 0.0)
