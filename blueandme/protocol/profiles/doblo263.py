"""Fiat Doblo 263 B-CAN profile. REFERENCE ONLY -- never transmitted.

Transcribed from fmntf/fiatcan (FiatProtocol.py, BodyComputerManager.py,
CanOneHertzLoop.py, initproxi/initproxi.c) and the author's four-part write-up
on medium.com/@fmntf, July-August 2019.

Every constant here is OTHER_VEHICLE. The Doblo uses 29-bit extended IDs with a
``topic << 16 | node`` layout; the Alfa 159 reimplementation of the same
functions uses 11-bit standard IDs and a completely different addressing scheme.
The two cannot both describe the Giulietta, and neither has been checked against
it. This profile exists so that captures can be *compared* against a known FCA
platform, not so that its frames can be replayed into a car.
"""

from __future__ import annotations

from ..confidence import Confidence as C
from ..frame import ButtonMask, Direction as D, Evidence, FrameSpec, Profile

SRC = "fmntf/fiatcan @ master (FiatProtocol.py) + medium.com/@fmntf part 2-4"

NODE_BODY_COMPUTER = 0x4000
NODE_INSTRUMENT_PANEL = 0x4003
NODE_RADIO = 0x4005
NODE_PARKING_SENSORS = 0x4018
NODE_UNKNOWN_401A = 0x401A
NODE_BLUE_AND_ME = 0x4021


def _id(topic: int, node: int) -> int:
    """Doblo arbitration layout: high 16 bits topic, low 16 bits sender node."""
    return (topic << 16) | node


TOPIC_NODE_STATUS = 0x0E09
TOPIC_PROXI = 0x1E11
TOPIC_BUTTONS = 0x0635
TOPIC_AUDIO_CHANNEL = 0x0631
TOPIC_TRACK_TIME = 0x0809
TOPIC_TEXT = 0x0A39
TOPIC_WATCHDOG = 0x0A01
TOPIC_RADIO_STATION = 0x0A19
TOPIC_RADIO_FREQUENCY = 0x0A11
TOPIC_CLOCK = 0x0C21

# Node status payload nibble, topic 0x0E09. fmntf part 4.
STATUS_WAKING = 0x0C
STATUS_WORKING = 0x0E
STATUS_SLEEPING = 0x0A

# Audio channel selector, low byte of the 0x0631 payload. fmntf part 4:
# anything that is not 0x8x makes the radio answer "No source available".
AUDIO_MUTED = 0x80
AUDIO_PHONE = 0x81
AUDIO_VOICE = 0x82
AUDIO_NAVIGATION = 0x83
AUDIO_MEDIA_PLAYER = 0x84

#: Bytes 2 onward of the track-time frame. fmntf part 3 explicitly leaves these
#: undecoded ("Other bytes must be further investigated"); fiatcan transmits
#: 0x4878 there while playing and traces show 0x4078. Carried as an opaque
#: trailer rather than pretended to be understood.
TRACK_TIME_TRAILER = bytes.fromhex("487800000000")

FRAMES = [
    FrameSpec(
        "body_network_control", _id(TOPIC_NODE_STATUS, NODE_BODY_COMPUTER), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=6, period_ms=1000,
        description="BC wake/status/shutdown. 001C=wake, 001E=status query, 001A=shut down",
    ),
    FrameSpec(
        "bm_status_response", _id(TOPIC_NODE_STATUS, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=2,
        description="Blue&Me answers 000C booting / 000E working / 000A sleeping",
    ),
    FrameSpec(
        "instrument_panel_status", _id(TOPIC_NODE_STATUS, NODE_INSTRUMENT_PANEL), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=2,
        description="Node 0x4003 status; initproxi mirrors its value. "
                    "fmntf hedged this node's identity; adamsanta confirmed it "
                    "by isolating the cluster (fiatcan issue #4)",
    ),
    FrameSpec(
        "radio_status", _id(TOPIC_NODE_STATUS, NODE_RADIO), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=2,
    ),
    FrameSpec(
        "body_proxi_request", _id(TOPIC_PROXI, NODE_BODY_COMPUTER), True,
        D.RX, C.OTHER_VEHICLE, SRC,
        description="BC: nodes, what is your PROXI value? ~1s after wake, 3 retries",
    ),
    FrameSpec(
        "bm_proxi_response", _id(TOPIC_PROXI, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=6,
        description="Blue&Me PROXI answer; all nodes share one value, so it can be echoed",
    ),
    FrameSpec(
        "instrument_panel_proxi", _id(TOPIC_PROXI, NODE_INSTRUMENT_PANEL), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=6,
        description="Peer PROXI value, echoed verbatim by fiatcan's initproxi.c",
    ),
    FrameSpec(
        "node_401a_proxi", _id(TOPIC_PROXI, NODE_UNKNOWN_401A), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=6,
    ),
    FrameSpec(
        "steering_wheel_buttons", _id(TOPIC_BUTTONS, NODE_BODY_COMPUTER), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=2,
        description="Bitmask, 0000 when nothing is pressed",
    ),
    FrameSpec(
        "bm_audio_channel", _id(TOPIC_AUDIO_CHANNEL, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=8, period_ms=1000,
        description="Declares the Blue&Me audio source state to the radio",
    ),
    FrameSpec(
        "radio_audio_channel", _id(TOPIC_AUDIO_CHANNEL, NODE_RADIO), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=8,
        description="Radio's own source state; bit 0x10000000 set = media player active",
    ),
    FrameSpec(
        "bm_track_time", _id(TOPIC_TRACK_TIME, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=8, period_ms=1000,
        description="Playback position, BCD MM SS in bytes 0-1",
    ),
    FrameSpec(
        "bm_text_message", _id(TOPIC_TEXT, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=8,
        description="6-bit encoded text for dashboard or radio display",
    ),
    FrameSpec(
        "bm_watchdog", _id(TOPIC_WATCHDOG, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE, SRC, dlc=8, period_ms=1000,
        description="Pair of frames 4000..02 then 5000..02, ~20ms apart, 1Hz",
    ),
    FrameSpec(
        "radio_station_name", _id(TOPIC_RADIO_STATION, NODE_RADIO), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=7,
        description="FM station name in the same 6-bit character map",
    ),
    FrameSpec(
        "radio_frequency", _id(TOPIC_RADIO_FREQUENCY, NODE_RADIO), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=6,
        description="E30000000200 B&M playing, E30000000000 B&M muted, else BCD frequency",
    ),
    FrameSpec(
        "clock", _id(TOPIC_CLOCK, NODE_INSTRUMENT_PANEL), True,
        D.RX, C.OTHER_VEHICLE, SRC, dlc=6, period_ms=1000,
        description="Hex-encoded HHMMDDMMYYYY wall clock",
    ),
]

# Both reference projects use the same masks over the first two payload bytes,
# under different frame IDs. See alfa939.py.
BUTTONS = [
    ButtonMask("volume_up", 0x8000),
    ButtonMask("volume_down", 0x4000),
    ButtonMask("mute", 0x2000),
    ButtonMask("up", 0x1000),
    ButtonMask("down", 0x0800),
    ButtonMask("source", 0x0400),
    ButtonMask("menu", 0x0080),
    ButtonMask("windows", 0x0040),
]

ID_WIDTH_EVIDENCE = Evidence(
    C.OTHER_VEHICLE,
    "fmntf, medium.com/@fmntf part 2: candump output shows 8-nibble IDs and "
    "upstream cansniffer cannot parse them because they are 29-bit",
)

PROFILE = Profile(
    key="doblo263",
    vehicle="Fiat Doblo 263 (2010-2015)",
    bitrate=50000,
    extended_ids=True,
    id_width_evidence=ID_WIDTH_EVIDENCE,
    frames=FRAMES,
    buttons=BUTTONS,
    notes=(
        "Reference only. 29-bit IDs, topic<<16|node. fiatcan's README claims these "
        "findings probably apply to the Alfa 159; karolkrupa's working 159 code "
        "shows they do not. Treat all compatibility claims as unverified."
    ),
)
