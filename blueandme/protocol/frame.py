"""Frame specifications: the single place a CAN identifier may be written down.

A `FrameSpec` binds an arbitration ID to a human name, a direction, an evidence
level and a citation. `Profile` collects them. Nothing else in the codebase is
allowed to contain a raw CAN ID -- see tests/test_no_stray_ids.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .confidence import Confidence


class Direction(Enum):
    """Who puts this frame on the bus, from our point of view."""

    RX = "rx"
    """Sent by the vehicle. We only ever listen."""

    TX = "tx"
    """Sent by us, impersonating the Blue&Me node."""

    BOTH = "both"
    """Seen in both directions, e.g. a topic every node answers on."""


@dataclass(frozen=True)
class FrameSpec:
    """One CAN frame this project knows how to recognise or emit."""

    name: str
    """Stable identifier, e.g. ``steering_wheel_buttons``. Used in config allow-lists."""

    can_id: int
    extended: bool
    direction: Direction
    confidence: Confidence
    source: str
    """Where the knowledge came from. A capture path for CONFIRMED_940, a repo
    path and upstream file for OTHER_VEHICLE. Never empty."""

    description: str = ""
    dlc: int | None = None
    """Expected payload length, or None if variable/unknown."""

    period_ms: int | None = None
    """Nominal transmit period for cyclic frames, or None for event-driven."""

    owner_exception: str = ""
    """The owner's decision to send this frame below the evidence threshold,
    dated and in their words. Empty for every frame whose evidence carries it;
    TxGate lets a frame through on this alone, and still only in gated-tx with
    the frame on the allow-list. The confidence above stays what it is."""

    def __post_init__(self) -> None:
        if not self.name or not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise ValueError(f"frame name must be snake_case: {self.name!r}")
        if not self.source:
            raise ValueError(f"{self.name}: source citation is mandatory")
        limit = 0x1FFFFFFF if self.extended else 0x7FF
        if not 0 <= self.can_id <= limit:
            raise ValueError(
                f"{self.name}: id {self.can_id:#x} out of range for "
                f"{'29' if self.extended else '11'}-bit"
            )
        if self.dlc is not None and not 0 <= self.dlc <= 8:
            raise ValueError(f"{self.name}: dlc {self.dlc} out of range")

    @property
    def hex_id(self) -> str:
        """Rendered the way candump renders it: 8 nibbles extended, 3 standard."""
        return f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"

    def matches(self, can_id: int, extended: bool) -> bool:
        return can_id == self.can_id and extended == self.extended

    def __str__(self) -> str:
        return f"{self.hex_id} {self.name} [{self.confidence.name}]"


@dataclass(frozen=True)
class Evidence:
    """A claim plus its provenance, for facts that are not individual frames.

    The identifier width is the obvious case: it is a property of the whole bus,
    not of any one frame, but it needs the same "how do we know this?" treatment
    as everything else.
    """

    confidence: Confidence
    source: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("evidence requires a source citation")

    def __str__(self) -> str:
        return f"{self.confidence.name} ({self.source})"


@dataclass(frozen=True)
class ButtonMask:
    """One steering-wheel button, as a mask over the button frame payload.

    Both reference projects encode buttons as a bitmask in the first two payload
    bytes, and -- notably -- use the *same* masks under different frame IDs.
    """

    name: str
    mask: int
    width: int = 2
    """Payload bytes the mask covers, counted from byte 0."""

    def pressed(self, payload: bytes) -> bool:
        if len(payload) < self.width:
            return False
        value = int.from_bytes(payload[: self.width], "big")
        return value & self.mask == self.mask


class Profile:
    """A named collection of FrameSpecs for one vehicle platform."""

    def __init__(
        self,
        key: str,
        vehicle: str,
        bitrate: int,
        extended_ids: bool | None,
        frames: Iterable[FrameSpec],
        buttons: Iterable[ButtonMask] = (),
        notes: str = "",
        id_width_evidence: "Evidence | None" = None,
    ) -> None:
        self.key = key
        self.vehicle = vehicle
        self.bitrate = bitrate
        self.extended_ids = extended_ids
        """True = 29-bit, False = 11-bit, None = not yet determined."""
        self.id_width_evidence = id_width_evidence
        """How we know the width above. Required whenever extended_ids is set,
        because a wrong answer here makes the bus look dead and everything
        downstream meaningless."""
        if extended_ids is not None and id_width_evidence is None:
            raise ValueError(
                f"{key}: extended_ids is set but no evidence is cited for it"
            )
        self.notes = notes
        self._by_name: dict[str, FrameSpec] = {}
        self.buttons = tuple(buttons)
        for spec in frames:
            if spec.name in self._by_name:
                raise ValueError(f"{key}: duplicate frame name {spec.name}")
            self._by_name[spec.name] = spec

    # -- lookup ------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self):
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def get(self, name: str) -> FrameSpec | None:
        return self._by_name.get(name)

    def require(self, name: str) -> FrameSpec:
        spec = self._by_name.get(name)
        if spec is None:
            raise KeyError(
                f"profile {self.key!r} has no frame {name!r}. "
                f"If this is a Giulietta frame, it is still UNKNOWN -- capture it first."
            )
        return spec

    def by_id(self, can_id: int, extended: bool) -> FrameSpec | None:
        for spec in self._by_name.values():
            if spec.matches(can_id, extended):
                return spec
        return None

    def transmittable(self, threshold: Confidence) -> list[FrameSpec]:
        return [
            s
            for s in self._by_name.values()
            if s.direction is not Direction.RX and s.confidence >= threshold
        ]

    def decode_buttons(self, payload: bytes) -> list[str]:
        return [b.name for b in self.buttons if b.pressed(payload)]

    @property
    def id_width_confidence(self) -> Confidence:
        if self.id_width_evidence is None:
            return Confidence.UNKNOWN
        return self.id_width_evidence.confidence

    def describe_id_width(self) -> str:
        width = {True: "29-bit extended", False: "11-bit standard",
                 None: "UNRESOLVED"}[self.extended_ids]
        if self.id_width_evidence is None:
            return f"{width} (decision gate D1)"
        return f"{width} -- {self.id_width_evidence}"

    def __repr__(self) -> str:
        width = {True: "29-bit", False: "11-bit", None: "id width UNKNOWN"}[
            self.extended_ids
        ]
        return f"<Profile {self.key} {self.vehicle} {width} {len(self)} frames>"
