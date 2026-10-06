"""The single choke point through which any frame must pass to reach the bus.

Safety model (project brief section 23): a frame is transmitted only if all of
the following hold.

  1. The configured CAN mode permits transmission at all.
  2. The frame comes from the *vehicle* profile, not a reference profile.
  3. The frame's evidence level meets or exceeds ``min_tx_confidence``.
  4. The frame's name is on the explicit allow-list, when the mode is gated-tx.
  5. The frame's direction is not RX-only.

Every accepted frame is logged with its name, evidence level and citation.
Every rejection is logged too, and raises, so a mistake is loud rather than
silent.
"""

from __future__ import annotations

import logging
from enum import Enum

from ..protocol.confidence import Confidence
from ..protocol.frame import Direction, FrameSpec, Profile

log = logging.getLogger("blueandme.tx")


class CanMode(Enum):
    LISTEN_ONLY = "listen-only"
    """Nothing is transmitted, ever. The socket is also put in listen-only mode
    so the controller does not even send acknowledge bits."""

    GATED_TX = "gated-tx"
    """Transmit only frames named in ``allowed_tx_frames``. Bring-up mode."""

    TX = "tx"
    """Transmit anything that clears the confidence threshold."""

    @classmethod
    def parse(cls, value: "str | CanMode") -> "CanMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                f"unknown CAN mode {value!r}; expected one of "
                + ", ".join(m.value for m in cls)
            ) from None


class TransmitRefused(RuntimeError):
    """Raised instead of putting a frame on the bus. Never caught internally."""


class TxGate:
    def __init__(
        self,
        profile: Profile,
        mode: CanMode = CanMode.LISTEN_ONLY,
        min_confidence: Confidence = Confidence.CONFIRMED_940,
        allowed: "list[str] | None" = None,
    ) -> None:
        self.profile = profile
        self.mode = CanMode.parse(mode)
        self.min_confidence = Confidence.parse(min_confidence)
        self.allowed = set(allowed or ())
        self.sent = 0
        self.refused = 0
        unknown = self.allowed - {s.name for s in profile}
        if unknown:
            raise ValueError(
                f"allowed_tx_frames names frames absent from profile "
                f"{profile.key!r}: {sorted(unknown)}"
            )

    # -- policy ------------------------------------------------------------

    def check(self, spec: FrameSpec, payload: bytes) -> None:
        """Raise TransmitRefused unless every gate condition is satisfied."""
        why = self._refusal(spec, payload)
        if why is not None:
            self.refused += 1
            log.error("REFUSED %s: %s", spec, why)
            raise TransmitRefused(f"refusing to transmit {spec.name}: {why}")

    def _refusal(self, spec: FrameSpec, payload: bytes) -> str | None:
        if self.mode is CanMode.LISTEN_ONLY:
            return "CAN mode is listen-only"
        if self.profile.get(spec.name) is not spec:
            return (
                f"frame does not belong to the active vehicle profile "
                f"{self.profile.key!r} (source: {spec.source})"
            )
        if spec.direction is Direction.RX:
            return "frame is receive-only; the vehicle sends it, we do not"
        if spec.confidence < self.min_confidence:
            if not spec.owner_exception:
                return (
                    f"evidence level {spec.confidence.name} is below the required "
                    f"{self.min_confidence.name}; capture it from this car first"
                )
            if self.mode is not CanMode.GATED_TX or spec.name not in self.allowed:
                return (
                    f"evidence level {spec.confidence.name} is below the required "
                    f"{self.min_confidence.name}; the owner's exception holds only in "
                    f"gated-tx with the frame in allowed_tx_frames"
                )
        if self.mode is CanMode.GATED_TX and spec.name not in self.allowed:
            return "gated-tx mode and frame is not in allowed_tx_frames"
        if spec.dlc is not None and len(payload) != spec.dlc:
            return f"payload is {len(payload)} bytes, spec says {spec.dlc}"
        if len(payload) > 8:
            return f"payload is {len(payload)} bytes, CAN 2.0 allows 8"
        return None

    def refusal(self, spec: FrameSpec, payload: bytes) -> "str | None":
        """Why this frame may not go out, or None if it may. Does not count or log."""
        return self._refusal(spec, payload)

    def permits(self, spec: FrameSpec, payload: bytes = b"") -> bool:
        """Non-raising form, for status reporting."""
        return self._refusal(spec, payload if payload else bytes(spec.dlc or 0)) is None

    def record(self, spec: FrameSpec, payload: bytes) -> None:
        """Log an accepted transmission. Called by the bus after a successful send."""
        self.sent += 1
        exception = (" (owner exception)" if spec.owner_exception
                     and spec.confidence < self.min_confidence else "")
        log.info(
            "TX %s#%s  [%s%s] %s", spec.hex_id, payload.hex().upper(),
            spec.confidence.name, exception, spec.name,
        )

    def describe(self) -> str:
        allow = ", ".join(sorted(self.allowed)) or "(none)"
        return (
            f"profile={self.profile.key} mode={self.mode.value} "
            f"min_confidence={self.min_confidence.name} allowed=[{allow}]"
        )
