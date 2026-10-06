"""SocketCAN wrapper with listen-only enforced at two layers.

Layer 1: when the configured mode is listen-only the socket itself is opened
with python-can's ``receive_own_messages=False`` and, where the kernel driver
supports it, the interface is expected to have been brought up with the
``listen-only`` control mode by install.sh. A listen-only CAN controller does
not even emit acknowledge bits, so it cannot perturb the bus.

Layer 2: `TxGate` refuses the call regardless. Layer 2 is the one that is
actually tested; layer 1 is defence in depth for the case where a future edit
changes the mode without changing the wiring.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator

from ..protocol.frame import Direction, FrameSpec, Profile
from .txgate import CanMode, TxGate

log = logging.getLogger("blueandme.bus")

Listener = Callable[["ReceivedFrame"], None]


class ReceivedFrame:
    """One frame off the bus, already matched against the active profile."""

    __slots__ = ("timestamp", "can_id", "extended", "data", "spec")

    def __init__(
        self,
        timestamp: float,
        can_id: int,
        extended: bool,
        data: bytes,
        spec: FrameSpec | None,
    ) -> None:
        self.timestamp = timestamp
        self.can_id = can_id
        self.extended = extended
        self.data = data
        self.spec = spec

    @property
    def hex_id(self) -> str:
        return f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"

    @property
    def name(self) -> str:
        return self.spec.name if self.spec else "UNKNOWN"

    def __repr__(self) -> str:
        return f"<{self.hex_id}#{self.data.hex().upper()} {self.name}>"


class CanBus:
    """Thin, testable wrapper over python-can's socketcan interface."""

    def __init__(
        self,
        interface: str,
        profile: Profile,
        gate: TxGate,
        bitrate: int | None = None,
        filter_to_profile: bool = False,
    ) -> None:
        self.interface = interface
        self.filter_to_profile = filter_to_profile
        self.profile = profile
        self.gate = gate
        self.bitrate = bitrate or profile.bitrate
        self._bus = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "CanBus":
        import can  # imported lazily so log-only tools need no python-can

        listen_only = self.gate.mode is CanMode.LISTEN_ONLY
        filters = None
        if self.filter_to_profile:
            # Let the kernel drop the ~116 frames/s this app does not act on.
            # Reading them all, a Zero W answered the radio in 78 ms median and
            # 156 ms worst case against a ~100 ms window (vcan0 with capture E
            # replaying, experiments/node4021).
            filters = [{"can_id": s.can_id, "can_mask": 0x1FFFFFFF if s.extended else 0x7FF,
                        "extended": s.extended}
                       for s in self.profile if s.direction is not Direction.TX]
        self._bus = can.Bus(
            interface="socketcan",
            channel=self.interface,
            receive_own_messages=False,
            can_filters=filters,
        )
        log.info(
            "opened %s at %d bit/s (%s), %s",
            self.interface, self.bitrate,
            "LISTEN-ONLY" if listen_only else self.gate.mode.value,
            self.gate.describe(),
        )
        return self

    def close(self) -> None:
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def __enter__(self) -> "CanBus":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- receive -----------------------------------------------------------

    def recv(self, timeout: float | None = 1.0) -> ReceivedFrame | None:
        if self._bus is None:
            raise RuntimeError("bus is not open")
        msg = self._bus.recv(timeout)
        if msg is None:
            return None
        return self._wrap(msg)

    def stream(self, timeout: float | None = 1.0) -> Iterator[ReceivedFrame]:
        while True:
            frame = self.recv(timeout)
            if frame is not None:
                yield frame

    def _wrap(self, msg) -> ReceivedFrame:
        extended = bool(msg.is_extended_id)
        return ReceivedFrame(
            timestamp=float(msg.timestamp),
            can_id=int(msg.arbitration_id),
            extended=extended,
            data=bytes(msg.data),
            spec=self.profile.by_id(int(msg.arbitration_id), extended),
        )

    # -- transmit ----------------------------------------------------------

    def send(self, frame_name: str, payload: bytes) -> None:
        """Transmit a named frame. The only way anything reaches the bus."""
        import can

        spec = self.profile.require(frame_name)
        self.gate.check(spec, payload)  # raises TransmitRefused
        if self._bus is None:
            raise RuntimeError("bus is not open")
        self._bus.send(
            can.Message(
                arbitration_id=spec.can_id,
                is_extended_id=spec.extended,
                data=payload,
            )
        )
        self.gate.record(spec, payload)
