"""Alfa Romeo Giulietta 940 (2011, 2.0 JTDm) B-CAN profile.

This profile holds only what captures from this car show. There is no
published CAN dump for the 940 -- no ID list, no PROXI payload, no frame
layouts -- and the two available FCA reimplementations contradict each other,
so nothing may be copied in on faith. Every frame below cites the capture it
was measured in, and none is transmitted yet: the frames the Pi sends at the
car were supervised experiments (experiments/, outside TxGate) until
vehicle/node.py took over what the car accepted from them.

Identifier width (decision gate D1)
-----------------------------------
**29-bit extended**, measured in capture A and every capture since. First
stated by the author of the
Alfa 159 implementation, in reply to a Giulietta owner who asked whether his
project would work:

    "Unfortunately, this project will not work with giulietta because giulietta
     has CAN network with extended IDs (29 bits) and 159 has 11 bit IDs. I think
     after changing the identifiers this project should work for Giulietta as
     well, as these modules don't seem to be much different except for the extra
     features and other identifiers."
    -- karolkrupa, 2021-01-27
       https://github.com/karolkrupa/alfa-blue-me/issues/1#issuecomment-768146196

This is testimony from someone who has worked hands-on with FCA B-CAN of this
era, specific to this model, and it is corroborated by the platform timeline:
the Giulietta 940 is contemporary with the Fiat Doblo 263, which fiatcan shows
using 29-bit IDs with ``topic << 16 | node`` addressing, while the 159 is an
older generation.

It was recorded as REPORTED_940 until capture A counted the nibble widths
candump prints: 4157 of 4157 frames 29-bit, now CONFIRMED_940. The captures
also bore out the Doblo's ``topic << 16 | node`` layout, and, for the wheel
buttons, even the Doblo's topic, node and masks.

Order of discovery, from tools/diff.py output (the write-up is linked in README.md):

  D1  ID width          capture A: confirm 29-bit by counting printed widths
  D2  PROXI challenge   capture H minus capture E: which frame appears once the
                        Body Computer expects a Blue&Me node again?
  ..  node inventory    capture A: which 0x40xx nodes exist on this car
  ..  buttons           capture C: one button at a time, 3 presses each
  ..  radio state       capture F: source changes, volume, mute
  ..  sleep handshake   capture B: ignition ON -> OFF
"""

from __future__ import annotations

from ..confidence import Confidence as C
from ..frame import ButtonMask, Direction as D, Evidence, FrameSpec, Profile

#: Agreed by both reference platforms and by the car's other B-CAN nodes being
#: FCA-standard. Verified in Phase 1 by checking the interface error counters at
#: this rate against the live car before anything else is believed.
CANDIDATE_BITRATE = 50000

ID_WIDTH_EVIDENCE = Evidence(
    C.CONFIRMED_940,
    "captures/A-first-contact.log (2026-09-13T16:31:37Z): all 4157 frames 29-bit, "
    "0 bus errors (sidecar id_widths); likewise C, E and every capture since. "
    "Reported first by karolkrupa (author of alfa-blue-me), "
    "github.com/karolkrupa/alfa-blue-me/issues/1#issuecomment-768146196, "
    "2021-01-27: Giulietta uses 29-bit extended IDs, the 159 uses 11-bit",
)


def _id(topic: int, node: int) -> int:
    """Arbitration layout seen in every capture: topic << 16 | sending node."""
    return (topic << 16) | node


NODE_BODY_COMPUTER = 0x4000
NODE_RADIO = 0x4005
NODE_BLUE_AND_ME = 0x4021
TOPIC_BUTTONS = 0x0635
TOPIC_NODE_STATUS = 0x0E09
TOPIC_PROXI = 0x1E11
TOPIC_AUDIO_CHANNEL = 0x0631
TOPIC_WATCHDOG = 0x0A01
TOPIC_TRACK_TIME = 0x0809
TOPIC_TEXT = 0x0A39

M = "captures/M-module-present-obd.log (2026-09-21, this car's own module plugged in, OBD tap)"
ZB = ("captures/ZB-working-4021.log (2026-09-23, the Pi as node 4021 in a supervised "
      "run, experiments/node4021/node4021_full.py; the car accepted it: no blink, the "
      "radio offered and selected Blue&Me)")

# -- what node 4021 says, as this car's module said it (capture M) or as the car
# accepted it from the Pi (capture ZB). vehicle/node.py acts on these.

#: The Body Computer's status poll, steady while the network is up...
POLL_STEADY = bytes.fromhex("001E0500046F")
#: ...and the first two bytes of every poll a node answers. A 001E poll that is
#: not the steady one is the wind-down (001E05000429, 001E04000029,
#: 001E00000029), answered "sleeping". 0018/001A/001B polls get no answer.
POLL_ANSWERED = bytes.fromhex("001E")
STATUS_BOOTING = bytes.fromhex("000C")   # M: after wake; ZB: the Pi, first 10 s
STATUS_WORKING = bytes.fromhex("001E")   # ZB: a working module (the Doblo's value;
                                         # this car's dead module said 000E, capture M)
STATUS_SLEEPING = bytes.fromhex("000A")  # M and ZB: during the wind-down
#: Answered right after the network wakes; 400A, 4003 and 401A send the same.
#: The value itself is one car's configuration: a setting (can.proxi_value in
#: the config file), not a constant. Answer with your own car's value.
PROXI_VALUE_BYTES = 6
WATCHDOG = (bytes.fromhex("4000000000000002"), bytes.fromhex("5000000000000002"))
WATCHDOG_GAP_S = 0.020
TRACK_IDLE = (bytes.fromhex("0000400000000000"), bytes.fromhex("0000800000000000"))
TRACK_TRAILER = bytes.fromhex("407800000000")   # after BCD minutes, seconds
AUDIO_MUTED = bytes.fromhex("0000000000000080")
AUDIO_MEDIA_PLAYER = bytes.fromhex("0000000000000084")
#: Not seen on this car; the Doblo's values, sent on the owner's decision
#: (2026-09-26). fiatcan (FiatProtocol.py, CanOneHertzLoop.py) sends phone +
#: locked, ...0481, for as long as a call is up (guide E8). ...0482 is the
#: Doblo's "voice" plus locked, used here while the phone's assistant is open:
#: an inference, since fiatcan had no assistant.
AUDIO_PHONE = bytes.fromhex("0000000000000481")
AUDIO_VOICE = bytes.fromhex("0000000000000482")
#: Byte 0 of the radio's request: 0C hands the audio to the media player; these
#: take it back (24 leaving, 20, 00 and 02 off or shutting down, 0F).
RADIO_OPEN_MEDIA_PLAYER = 0x0C
RADIO_LEAVES_MEDIA_PLAYER = (0x00, 0x02, 0x0F, 0x20)
#: 0x24 is not "leave" (sniff 2026-09-27, captures/mute-sniff-20260927.txt): the
#: radio sends it on mute (wheel 2000) and on the first SRC press, keeps byte 4
#: 0x10 (Blue&Me selected) and shows Blue&Me muted (0A114005 byte 4 00); only
#: after our "…80" did it fall back to the previous source. So on 0x24 the
#: node keeps "…84"; the radio has left when byte 4 loses 0x10.
RADIO_MUTES = 0x24
RADIO_BM_SELECTED = 0x10   # byte 4 of radio_media_request
RADIO_PLAYING = 0x04       # byte 0 while a source plays; byte 4 says which
#: The radio's arrow pad on Blue&Me, as bits of radio_media_request (byte,
#: mask): held = set, one frame at press and one at release, no repeats
#: (sniff 2026-09-27, captures/arrows-sniff-20260927.txt; left/right from the
#: held presses, up/down from the owner's order).
RADIO_KEYS = {"up": (0, 0x01), "down": (1, 0x80), "left": (1, 0x40), "right": (1, 0x20)}
#: Report booting this long after the bus wakes, as the module does (capture M).
BOOT_S = 10.0
#: Stop the periodic frames after this much silence: never hold the car awake.
IDLE_S = 3.0

# -- text, as the Doblo's working module sent it (tests/fixtures/doblo-02-driving.log).
# Not seen on this car: the owner's decision is to assume it is the same.
#: Once a second while the media player is not playing: radio, nothing to show.
TEXT_IDLE_RADIO = bytes.fromhex("0028000000000000")
#: The same for the instrument cluster, by analogy (target 1); never seen.
TEXT_CLEAR_DASHBOARD = bytes.fromhex("0018000000000000")
#: A message's frames go out this far apart.
TEXT_GAP_S = 0.019
#: The radio's three fields and their lengths: "+" and the artist, the album
#: (or the artist again), the title.
RADIO_TEXT_FIELDS = (14, 14, 18)
#: The radio's two lines (car, 2026-09-27): top = field 3 (title), first 12
#: characters, static; bottom = field 1 ("+" artist). Every new message starts
#: its display cycle again, so it never reaches its own time display, and its
#: own scroll was too slow for the owner. So the Pi runs "artist - title"
#: through field 3 (top), one character every 0.25 s, and writes the playing
#: time as field 1 (bottom) (owner's layout, 2026-09-27).
TEXT_SCROLL_S = 0.25
RADIO_SCROLL_WIDTH = 12

FRAMES: list = [
    FrameSpec(
        "steering_wheel_buttons", _id(TOPIC_BUTTONS, NODE_BODY_COMPUTER), True,
        D.RX, C.CONFIRMED_940,
        "captures/C-buttons.log (2026-09-13T17:57:41Z): each wheel button pressed 3x "
        "in the order vol+ vol- next prev src mute phone voice; one bit each, first "
        "presses at t+19.4, 24.1, 31.2, 38.1, 45.4, 52.4, 59.1, 64.6 s "
        "(vol+ at 1789322280.754724, voice at 1789322325.905330)",
        description="Wheel buttons, 2 bytes, one bit per button held; 2 Hz plus a "
                    "frame on every change. Only short presses recorded so far.",
        dlc=2, period_ms=500,
    ),
    # -- received -------------------------------------------------------------
    FrameSpec(
        "bc_status_poll", _id(TOPIC_NODE_STATUS, NODE_BODY_COMPUTER), True,
        D.RX, C.CONFIRMED_940,
        f"{M}: steady 001E0500046F from 1790006160.080609; wind-down 001E05000429 at "
        "1790006213.086468, then 001E04000029, 001E00000029, 001A00000029",
        description="Body Computer's status poll, about 1.4 Hz; nodes answer on topic 0E09",
        dlc=6,
    ),
    FrameSpec(
        "bc_proxi_challenge", _id(TOPIC_PROXI, NODE_BODY_COMPUTER), True,
        D.RX, C.CONFIRMED_940,
        f"{ZB}: 1E114000# at 1790185189.151365, answered by 400A, 4003, 401A and the Pi; "
        "also captures A and H",
        description="PROXI challenge, empty, about 1 s after the network wakes",
        dlc=0,
    ),
    FrameSpec(
        "radio_media_request", _id(TOPIC_AUDIO_CHANNEL, NODE_RADIO), True,
        D.RX, C.CONFIRMED_940,
        f"{ZB}: 06314005#0C00000000000000 at 1790185228.098552 (open the media player), "
        "0400000010000000 from 1790185228.584047 (playing), 2400000010000000 at "
        "1790185240.010731 (leaving)",
        description="The radio's audio source request; byte 0 says what it wants",
        dlc=8,
    ),
    # -- sent by node 4021 ---------------------------------------------------------
    FrameSpec(
        "bm_status_response", _id(TOPIC_NODE_STATUS, NODE_BLUE_AND_ME), True,
        D.TX, C.CONFIRMED_940,
        f"{M}: 000C at 1790006162.149270, 000E at 1790006163.124751, 000A at "
        f"1790006213.126832; {ZB}: 001E from 1790185200.235288",
        description="Node status: 000C booting, 001E working, 000A sleeping",
        dlc=2,
    ),
    FrameSpec(
        "bm_proxi_response", _id(TOPIC_PROXI, NODE_BLUE_AND_ME), True,
        D.TX, C.CONFIRMED_940,
        f"{ZB}: 1E114021# with the car's value at 1790185189.159230, 8 ms after the challenge; "
        "the same value as 1E11400A at 1790185189.153881",
        description="PROXI answer, the shared configuration value",
        dlc=6,
    ),
    FrameSpec(
        "bm_watchdog", _id(TOPIC_WATCHDOG, NODE_BLUE_AND_ME), True,
        D.TX, C.CONFIRMED_940,
        f"{M}: 4000000000000002 at 1790006160.226684, 5000000000000002 at "
        "1790006160.246742, about 1 Hz",
        description="A pair, 20 ms apart, once a second",
        dlc=8, period_ms=1000,
    ),
    FrameSpec(
        "bm_track_time", _id(TOPIC_TRACK_TIME, NODE_BLUE_AND_ME), True,
        D.TX, C.CONFIRMED_940,
        f"{M}: zeros from 1790006160.177392 (the dead module); {ZB}: idle "
        "0000400000000000 / 0000800000000000 from 1790185189.213584, playing "
        "0000407800000000 at 1790185228.207316, then 0001..., 0002...",
        description="Playback position, BCD minutes and seconds, then 407800000000",
        dlc=8, period_ms=1000,
    ),
    FrameSpec(
        "bm_text_message", _id(TOPIC_TEXT, NODE_BLUE_AND_ME), True,
        D.TX, C.OTHER_VEHICLE,
        "tests/fixtures/doblo-02-driving.log (fiatcan, a working Blue&Me on a Doblo 263): "
        "0028... once a second, then at 1517155333.361769 seven frames 602A... to "
        "662AFC..., '+LO STATO SOCI / LO STATO SOCIA / QUELLO CHE LE DONN'; "
        "protocol/text.py makes the same frames bit for bit (tests/test_text.py). "
        "Not yet seen or sent on this car",
        description="6-bit text: header (total-1)<<4|index, target<<4|A (2 radio, 1 cluster)",
        dlc=8,
        owner_exception="2026-09-26, owner: assume the Doblo's text format holds on the "
                        "Giulietta and send it; change it if the car shows otherwise",
    ),
    FrameSpec(
        "bm_audio_channel", _id(TOPIC_AUDIO_CHANNEL, NODE_BLUE_AND_ME), True,
        D.TX, C.CONFIRMED_940,
        f"{M}: ...80 from 1790006166.149748; {ZB}: ...84 at 1790185228.103066, 5 ms "
        "after the radio's 0C",
        description="Audio channel: ...80 muted, ...84 media player",
        dlc=8, period_ms=1000,
    ),
]

# Names follow the Giulietta Blue&Me manual (604_38_194, p. 9): "phone" is the
# handset / MENU button, "voice" the voice-recognition key the owner calls the
# Windows key, "mute" the mute / ESC button, next and prev the arrow keys. The
# masks are the Doblo's and the 159's, now measured on this car.
BUTTONS: list = [
    ButtonMask("volume_up", 0x8000),
    ButtonMask("volume_down", 0x4000),
    ButtonMask("mute", 0x2000),
    ButtonMask("next", 0x1000),
    ButtonMask("prev", 0x0800),
    ButtonMask("source", 0x0400),
    ButtonMask("phone", 0x0080),
    ButtonMask("voice", 0x0040),
]

PROFILE = Profile(
    key="giulietta940",
    vehicle="Alfa Romeo Giulietta 940 (2011, 2.0 JTDm 140)",
    bitrate=CANDIDATE_BITRATE,
    extended_ids=True,
    id_width_evidence=ID_WIDTH_EVIDENCE,
    frames=FRAMES,
    buttons=BUTTONS,
    notes=(
        "ONLY FRAMES MEASURED ON THIS CAR, each citing its capture. Copying an ID from doblo263 or alfa939 without a "
        "capture citation defeats the entire safety model of this project."
    ),
)
