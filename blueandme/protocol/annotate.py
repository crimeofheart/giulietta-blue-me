"""Human-readable annotations for known frames.

Annotators are keyed by FrameSpec *name*, not by CAN ID, which is why the
profiles use the same names for the same functions across platforms. Once
giulietta940.py names a captured frame ``steering_wheel_buttons``, every
annotation, tool and test below starts working on it with no further changes.
"""

from __future__ import annotations

from typing import Callable

from . import text as textcodec
from .frame import FrameSpec, Profile
from .profiles import alfa939, doblo263

Annotator = Callable[[FrameSpec, bytes, Profile], str]

_STATUS_NIBBLE = {
    doblo263.STATUS_WAKING: "powering on",
    doblo263.STATUS_WORKING: "working correctly",
    doblo263.STATUS_SLEEPING: "going to sleep",
}

_AUDIO_CHANNEL = {
    doblo263.AUDIO_MUTED: "audio out muted",
    doblo263.AUDIO_PHONE: "audio out active: phone",
    doblo263.AUDIO_VOICE: "audio out active: voice",
    doblo263.AUDIO_NAVIGATION: "audio out active: navigation",
    doblo263.AUDIO_MEDIA_PLAYER: "audio out active: media player",
}


def _buttons(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    pressed = profile.decode_buttons(data)
    return "BUTTON: " + ", ".join(p.upper() for p in pressed) if pressed \
        else "no button pressed"


_NODE_LABELS = {
    "bm_status_response": "Blue&Me",
    "instrument_panel_status": "instrument panel",
    "radio_status": "radio unit",
}


def _node_status(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if not data:
        return "empty status"
    nibble = data[-1] & 0x0F
    who = _NODE_LABELS.get(spec.name, spec.name)
    return f"{who}: {_STATUS_NIBBLE.get(nibble, f'unknown state 0x{nibble:X}')}"


def _network_control(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if len(data) < 2:
        return "short network control frame"
    nibble = data[1] & 0x0F
    return {
        doblo263.STATUS_WAKING: "BC: nodes, please wake up",
        doblo263.STATUS_WORKING: "BC: nodes, what is your status?",
        doblo263.STATUS_SLEEPING: "BC: nodes, please shut down",
    }.get(nibble, f"BC: unknown network command 0x{nibble:X}")


def _proxi_request(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    return "BC: nodes, what is your PROXI value?"


def _proxi_value(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    return f"PROXI value {data.hex().upper()}"


def _audio_channel(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if not data:
        return "empty audio channel frame"
    return _AUDIO_CHANNEL.get(data[-1], f"audio channel byte 0x{data[-1]:02X}")


def _track_time(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if len(data) < 2:
        return "short track time frame"
    # BCD-ish: fiatcan formats the position as "0x{mm:02d}{ss:02d}...", so the
    # nibbles read back directly as decimal digits.
    mm, ss = data[0], data[1]
    return f"track position {mm >> 4}{mm & 0xF}:{ss >> 4}{ss & 0xF}"


def _text(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if len(data) < 2:
        return "short text frame"
    total = (data[0] >> 4) + 1
    index = data[0] & 0x0F
    target = data[1] >> 4
    where = {1: "dashboard", 2: "radio"}.get(target, f"display {target}")
    body = textcodec.decode_text(data[2:], stop_at_terminator=False)
    if data[2:] == bytes(len(data) - 2):
        return f"clear {where} display"
    return f"text -> {where} [{index + 1}/{total}] {body!r}"


def _station_name(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    # An all-zero name with the trailing 0x80 flag is how the Doblo radio reports
    # a tuner that is off, which fmntf's decoded traces confirm.
    if not data.strip(b"\x00\x80"):
        return "FM tuner is off"
    return f"FM station {textcodec.decode_text(data)!r}"


def _radio_frequency(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if len(data) < 6:
        return "short frequency frame"
    if data[0] == 0xE3:
        return "Blue&Me playing" if data[4] == 0x02 else "Blue&Me muted"
    if data == bytes.fromhex("000000000800"):
        return "radio unit powered down"
    # fmntf part 4 gives the layout as 0x**ABCD000000: the frequency in tenths
    # of a MHz sits in bytes 1-2, not 0-1. Byte 0 is something else.
    tenths = (data[1] << 8) | data[2]
    return f"FM tuned to {tenths / 10:.2f} MHz"


def _clock(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    h = data.hex().upper()
    if len(h) < 12:
        return "short clock frame"
    return f"clock {h[0:2]}:{h[2:4]} on {h[4:6]}/{h[6:8]}/{h[8:12]}"


def _watchdog(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    return "Blue&Me watchdog"


def _bm_node_status_939(spec: FrameSpec, data: bytes, profile: Profile) -> str:
    if len(data) < 2:
        return "short node status"
    return {
        alfa939.STATUS_WORKING: "Blue&Me working",
        alfa939.STATUS_WORKING_ALT: "Blue&Me working (alt encoding)",
        alfa939.STATUS_SLEEPING: "Blue&Me sleeping",
    }.get(data[1], f"node status 0x{data[1]:02X}")


ANNOTATORS: dict[str, Annotator] = {
    "steering_wheel_buttons": _buttons,
    "body_network_control": _network_control,
    "bm_status_response": _node_status,
    "instrument_panel_status": _node_status,
    "radio_status": _node_status,
    "body_proxi_request": _proxi_request,
    "bm_proxi_response": _proxi_value,
    "instrument_panel_proxi": _proxi_value,
    "node_401a_proxi": _proxi_value,
    "bm_audio_channel": _audio_channel,
    "radio_audio_channel": _audio_channel,
    "bm_track_time": _track_time,
    "bm_text_message": _text,
    "bm_display_text": _text,
    "radio_station_name": _station_name,
    "radio_frequency": _radio_frequency,
    "clock": _clock,
    "bm_watchdog": _watchdog,
    "bm_node_status": _bm_node_status_939,
}


def annotate(spec: FrameSpec | None, data: bytes, profile: Profile) -> str:
    """One-line explanation of a frame, or an empty string if we cannot explain it."""
    if spec is None:
        return ""
    fn = ANNOTATORS.get(spec.name)
    if fn is None:
        return spec.description
    try:
        return fn(spec, data, profile)
    except Exception as exc:  # a malformed frame must not kill a decode run
        return f"<annotator error: {exc}>"
