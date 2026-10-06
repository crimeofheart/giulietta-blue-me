"""Alfa Romeo 159 (939) B-CAN profile. REFERENCE ONLY -- never transmitted.

Transcribed from karolkrupa/alfa-blue-me (device/SteeringWheel.py, device/Radio.py,
module/Proxi.py, module/StatusManager.py, prototypes/proxi.py).

This profile is the counter-example that keeps the project honest: it implements
exactly the same Blue&Me functions as doblo263.py, on the same 50 kbit/s bus, and
agrees with it on almost nothing. 11-bit standard IDs, no visible topic/node
addressing, different frame for every function. The one thing the two do share is
the text payload codec -- see protocol/text.py.
"""

from __future__ import annotations

from ..confidence import Confidence as C
from ..frame import ButtonMask, Direction as D, Evidence, FrameSpec, Profile

SRC = "karolkrupa/alfa-blue-me @ master"

# module/Proxi.py: status byte sent at 1Hz on 0x707.
STATUS_WORKING = 0x1F
STATUS_SLEEPING = 0x1A
# prototypes/proxi.py uses 0x1E for working; the module uses 0x1F. Upstream
# disagrees with itself, which is itself a reason not to trust the value.
STATUS_WORKING_ALT = 0x1E

# module/Proxi.py: the literal PROXI answer, origin undocumented upstream.
PROXI_RESPONSE_PAYLOAD = bytes([0x21, 0x14, 0x00, 0x28, 0x65, 0x70])

# device/Radio.py display modes, byte 2 of the track-time frame.
DISPLAY_MODE_TEXT = 0x40
DISPLAY_MODE_FOLDER = 0x48
DISPLAY_MODE_ARTISTS = 0x50
DISPLAY_MODE_GENRES = 0x58
DISPLAY_MODE_ALBUMS = 0x60
DISPLAY_MODE_PLAYLISTS = 0x68

FRAMES = [
    FrameSpec(
        "steering_wheel_buttons", 0x3C4, False, D.RX, C.OTHER_VEHICLE, SRC, dlc=2,
        description="Bitmask over 2 bytes, same masks as the Doblo under a different ID",
    ),
    FrameSpec(
        "body_proxi_request", 0x740, False, D.RX, C.OTHER_VEHICLE, SRC,
        description="Triggers the PROXI answer; no payload inspection upstream",
    ),
    FrameSpec(
        "bm_proxi_response", 0x747, False, D.TX, C.OTHER_VEHICLE, SRC, dlc=6,
        description="Fixed payload 21 14 00 28 65 70",
    ),
    FrameSpec(
        "bm_node_status", 0x707, False, D.TX, C.OTHER_VEHICLE, SRC, dlc=2,
        period_ms=1000, description="00 1F working / 00 1A sleeping",
    ),
    FrameSpec(
        "bm_status", 0x3E7, False, D.TX, C.OTHER_VEHICLE, SRC, dlc=8, period_ms=1000,
        description="6 network-name bytes + status byte + media-player byte",
    ),
    FrameSpec(
        "radio_status_request", 0x545, False, D.RX, C.OTHER_VEHICLE, SRC, dlc=6,
        description="E0.. enables the media player, 58040C000200 disables it",
    ),
    FrameSpec(
        "radio_command", 0x405, False, D.RX, C.OTHER_VEHICLE, SRC, dlc=8,
        description="0C00.. opens the media player",
    ),
    FrameSpec(
        "bm_display_text", 0x5E7, False, D.TX, C.OTHER_VEHICLE, SRC, dlc=8,
        description="6-bit encoded text, header byte + 0x2A target byte",
    ),
    FrameSpec(
        "bm_track_time", 0x427, False, D.TX, C.OTHER_VEHICLE, SRC, dlc=8,
        period_ms=1000, description="MM SS display_mode 0x78 00 00 00 00",
    ),
]

# device/SteeringWheel.py buttons_binding. The four masks the Doblo also uses are
# identical; the 159 adds up/down on the low byte and renames 0x1000/0x0800 from
# up/down to next/prev.
#
# Upstream inconsistency worth carrying forward: the comment block at the top of
# SteeringWheel.py says "3C4 10 01 - down" and "3C4 08 02 - up", but the actual
# buttons_binding dict maps 0x0002 to up and 0x0001 to down. The dict is what runs.
BUTTONS = [
    ButtonMask("volume_up", 0x8000),
    ButtonMask("volume_down", 0x4000),
    ButtonMask("mute", 0x2000),
    ButtonMask("next", 0x1000),
    ButtonMask("prev", 0x0800),
    ButtonMask("source", 0x0400),
    ButtonMask("menu", 0x0080),
    ButtonMask("windows", 0x0040),
    ButtonMask("up", 0x0002),
    ButtonMask("down", 0x0001),
]

ID_WIDTH_EVIDENCE = Evidence(
    C.OTHER_VEHICLE,
    "karolkrupa, github.com/karolkrupa/alfa-blue-me/issues/1"
    "#issuecomment-768146196: 'the 159 has 11 bit IDs'; the source also sends "
    "every frame with extended_id=False",
)

PROFILE = Profile(
    key="alfa939",
    vehicle="Alfa Romeo 159 (939, 2005-2011)",
    bitrate=50000,
    extended_ids=False,
    id_width_evidence=ID_WIDTH_EVIDENCE,
    frames=FRAMES,
    buttons=BUTTONS,
    notes=(
        "Reference only. 11-bit IDs. Directly contradicts doblo263.py on every "
        "frame ID while implementing the same functions, which is why no "
        "cross-vehicle constant may be transmitted without a Giulietta capture."
    ),
)
