"""Configuration loading. YAML on disk, dataclasses in memory, validated on load."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .can.txgate import CanMode
from .protocol.confidence import Confidence
from .protocol.registry import DEFAULT_PROFILE, REFERENCE_ONLY, get

DEFAULT_PATHS = [
    Path("/etc/blueandme/config.yaml"),
    Path(__file__).parent / "config" / "default.yaml",
]


@dataclass
class CanConfig:
    interface: str = "can0"
    bitrate: int = 50000
    mode: CanMode = CanMode.LISTEN_ONLY
    profile: str = DEFAULT_PROFILE
    min_tx_confidence: Confidence = Confidence.CONFIRMED_940
    allowed_tx_frames: list[str] = field(default_factory=list)
    #: This car's PROXI value: 12 hex digits, quoted in the YAML. Empty: the
    #: node does not answer the Body Computer's PROXI challenge.
    proxi_value: str = ""

    def proxi_bytes(self) -> "bytes | None":
        """The value as bytes; None when it is unset or malformed."""
        value = self.proxi_value
        if isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{12}", value):
            return bytes.fromhex(value)
        return None


@dataclass
class BluetoothConfig:
    name: str = "Blue&Me"
    pairable: bool = True
    pairing_timeout_s: int = 0
    auto_reconnect: bool = True
    reconnect_interval_s: int = 10
    alsa_device: str = "default"


@dataclass
class PowerConfig:
    shutdown_on_sleep_request: bool = True
    shutdown_delay_s: int = 30
    stay_awake_after_key_off_s: int = 0
    # Standby while parked (decided 2026-09-25): lean once the bus has been
    # quiet this long with the ignition off, awake again on the next frame or
    # on ignition, and after the hold the Pi halts. See power/standby.py.
    standby: bool = True
    standby_after_quiet_s: int = 60
    standby_hold_h: float = 72.0
    #: BCM GPIO sensing ignition (pin 32, through an optocoupler); None until
    #: it is wired. 3 (header pin 5): pulled low, it also boots a halted Pi.
    #: An optocoupler pulls low when on.
    ignition_gpio: "int | None" = None
    ignition_active_low: bool = False
    #: BCM GPIO the kernel raises once the Pi has halted (dtoverlay=gpio-poweroff),
    #: telling a power latch to cut the supply; None: the latch is shelved and
    #: the hold ends in a halt.
    poweroff_gpio: "int | None" = None


@dataclass
class Config:
    can: CanConfig = field(default_factory=CanConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    log_level: str = "INFO"
    capture_dir: Path = Path("/var/lib/blueandme/captures")

    def validate(self) -> list[str]:
        """Return a list of problems. Empty means the config is safe to run."""
        problems: list[str] = []
        try:
            profile = get(self.can.profile)
        except KeyError as exc:
            return [str(exc)]

        if self.can.mode is not CanMode.LISTEN_ONLY and self.can.profile in REFERENCE_ONLY:
            problems.append(
                f"can.profile is {self.can.profile!r}, which describes a different "
                f"vehicle, and can.mode is {self.can.mode.value}. Reference profiles "
                f"may never be transmitted. Set mode: listen-only or switch profile."
            )
        unknown = set(self.can.allowed_tx_frames) - {s.name for s in profile}
        if unknown:
            problems.append(
                f"can.allowed_tx_frames names frames absent from profile "
                f"{self.can.profile!r}: {sorted(unknown)}"
            )
        if self.can.mode is CanMode.TX:
            problems.append(
                "can.mode is 'tx', which permits every frame meeting the confidence "
                "threshold. Bring-up should use 'gated-tx' with an explicit "
                "allowed_tx_frames list."
            )
        if (self.can.mode is not CanMode.LISTEN_ONLY
                and profile.id_width_confidence < Confidence.CONFIRMED_940):
            # Gate D1. A reported width is good enough to plan around and to
            # shape the profile, but not to transmit on: get it wrong and every
            # frame is malformed on a live vehicle bus.
            problems.append(
                f"profile {self.can.profile!r} identifier width is "
                f"{profile.describe_id_width()}. Decision gate D1 needs a "
                f"capture from this car before any transmitting mode. "
                f"Take capture A; the sidecar's id_widths counts settle it."
            )
        value = self.can.proxi_value
        if not isinstance(value, str):
            # Unquoted digits are a number to YAML, which may change them.
            problems.append(
                f"can.proxi_value was read as the number {value!r}: put the "
                f"12 hex digits in quotes."
            )
        elif value and self.can.proxi_bytes() is None:
            problems.append(
                f"can.proxi_value must be 12 hex digits (6 bytes), got {value!r}."
            )
        return problems


def _coerce(section: dict, cls):
    return {k: v for k, v in (section or {}).items() if k in cls.__dataclass_fields__}


def load(path: "str | Path | None" = None) -> Config:
    """Load config from an explicit path, $BLUEANDME_CONFIG, or the defaults."""
    import yaml

    candidates = (
        [Path(path)] if path
        else ([Path(os.environ["BLUEANDME_CONFIG"])] if "BLUEANDME_CONFIG" in os.environ
              else DEFAULT_PATHS)
    )
    raw: dict = {}
    for candidate in candidates:
        if candidate.exists():
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            break

    can_raw = _coerce(raw.get("can", {}), CanConfig)
    if "mode" in can_raw:
        can_raw["mode"] = CanMode.parse(can_raw["mode"])
    if "min_tx_confidence" in can_raw:
        can_raw["min_tx_confidence"] = Confidence.parse(can_raw["min_tx_confidence"])

    cfg = Config(
        can=CanConfig(**can_raw),
        bluetooth=BluetoothConfig(**_coerce(raw.get("bluetooth", {}), BluetoothConfig)),
        power=PowerConfig(**_coerce(raw.get("power", {}), PowerConfig)),
        log_level=raw.get("log_level", "INFO"),
        capture_dir=Path(raw.get("capture_dir", "/var/lib/blueandme/captures")),
    )
    return cfg
