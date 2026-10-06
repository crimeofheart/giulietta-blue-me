"""Read and write candump -l logs.

Using can-utils' own format rather than something bespoke means every capture
this project takes can also be replayed with ``canplayer``, inspected with
``candump``, and diffed with ordinary text tools. The sidecar ``.meta.json``
carries the context that the log format has nowhere to put.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

LINE = re.compile(
    r"^\((?P<ts>\d+\.\d+)\)\s+(?P<iface>\S+)\s+"
    r"(?P<id>[0-9A-Fa-f]+)(?P<kind>##?)(?P<data>[0-9A-Fa-f]*)\s*$"
)


@dataclass(frozen=True)
class LogFrame:
    timestamp: float
    interface: str
    can_id: int
    extended: bool
    data: bytes

    @property
    def hex_id(self) -> str:
        return f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"

    def format(self) -> str:
        return (
            f"({self.timestamp:.6f}) {self.interface} "
            f"{self.hex_id}#{self.data.hex().upper()}"
        )


class LogFormatError(ValueError):
    pass


def parse_line(line: str) -> LogFrame | None:
    """Parse one candump line. Returns None for blank lines and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    m = LINE.match(stripped)
    if not m:
        raise LogFormatError(f"not a candump -l line: {line.rstrip()!r}")
    raw_id = m.group("id")
    # candump prints 3 nibbles for 11-bit IDs and 8 for 29-bit. That width is
    # the only place the log records which addressing mode was in use, and
    # resolving 11-bit vs 29-bit is decision gate D1, so it is load-bearing.
    extended = len(raw_id) > 3
    data = bytes.fromhex(m.group("data"))
    if len(data) > 8 and m.group("kind") == "#":
        raise LogFormatError(f"classic CAN frame with {len(data)} data bytes: {line!r}")
    return LogFrame(
        timestamp=float(m.group("ts")),
        interface=m.group("iface"),
        can_id=int(raw_id, 16),
        extended=extended,
        data=data,
    )


def read(path: "str | Path", *, strict: bool = False) -> Iterator[LogFrame]:
    """Stream frames from a candump log. Malformed lines are skipped unless strict."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            try:
                frame = parse_line(line)
            except LogFormatError:
                if strict:
                    raise LogFormatError(f"{path}:{lineno}: bad line {line.rstrip()!r}")
                continue
            if frame is not None:
                yield frame


def write(path: "str | Path", frames: Iterable[LogFrame]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for frame in frames:
            fh.write(frame.format() + "\n")
            count += 1
    return count


@dataclass
class CaptureMeta:
    """Sidecar context for a capture. Written next to the log as .meta.json."""

    label: str = ""
    scenario: str = ""
    vehicle: str = "Alfa Romeo Giulietta 940 (2011, 2.0 JTDm 140)"
    interface: str = "can0"
    bitrate: int = 50000
    tap_point: str = ""
    """Where the bus was tapped: 'blue&me connector pins 30/14/15' or 'OBD2 3/11'."""
    started_at: str = ""
    duration_s: float = 0.0
    frame_count: int = 0
    unique_ids: int = 0
    id_widths: dict = field(default_factory=dict)
    """Counts of 11-bit vs 29-bit frames seen. This is the raw evidence for D1."""
    error_frames: int = 0
    notes: str = ""
    tool_version: str = "blueandme-capture/1"

    @staticmethod
    def path_for(log_path: "str | Path") -> Path:
        p = Path(log_path)
        return p.with_suffix(p.suffix + ".meta.json")

    def save(self, log_path: "str | Path") -> Path:
        target = self.path_for(log_path)
        target.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, log_path: "str | Path") -> "CaptureMeta | None":
        target = cls.path_for(log_path)
        if not target.exists():
            return None
        return cls(**json.loads(target.read_text(encoding="utf-8")))


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
