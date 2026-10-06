"""Audio-source negotiation with the factory radio.

The radio will not unmute the Blue&Me analog input until the Blue&Me node
declares an active audio channel on the bus. That is the whole reason this
project needs CAN at all: the sound card can be wired perfectly and produce
silence until the right frame goes out.

Doblo semantics (OTHER_VEHICLE): the low byte of the audio-channel frame selects
the source, and anything that is not 0x8x makes the radio answer "No source
available". The Giulietta encoding is UNKNOWN until capture F.
"""

from __future__ import annotations

import logging
from enum import Enum

from ..can.bus import ReceivedFrame
from ..events import EventBus
from ..protocol.frame import Profile
from ..protocol.profiles import doblo263 as _doblo263

log = logging.getLogger("blueandme.radio")

TX_AUDIO_CHANNEL = "bm_audio_channel"
TX_TRACK_TIME = "bm_track_time"
RX_RADIO_FREQUENCY = "radio_frequency"
RX_RADIO_AUDIO_CHANNEL = "radio_audio_channel"

_TRACK_TIME_TRAILER = {"doblo263": _doblo263.TRACK_TIME_TRAILER}

_CHANNEL_BYTES = {"doblo263": {
    "muted": _doblo263.AUDIO_MUTED,
    "phone": _doblo263.AUDIO_PHONE,
    "voice": _doblo263.AUDIO_VOICE,
    "navigation": _doblo263.AUDIO_NAVIGATION,
    "media": _doblo263.AUDIO_MEDIA_PLAYER,
}}


class AudioChannel(Enum):
    MUTED = "muted"
    MEDIA = "media"
    PHONE = "phone"
    VOICE = "voice"
    NAVIGATION = "navigation"


class Radio:
    def __init__(self, profile: Profile, bus: EventBus) -> None:
        self.profile = profile
        self.bus = bus
        self.channel = AudioChannel.MUTED
        self.selected_source: str | None = None
        self.track_position_s = 0

    # -- outbound ----------------------------------------------------------

    def set_channel(self, channel: AudioChannel) -> None:
        if channel is not self.channel:
            log.info("audio channel -> %s", channel.value)
        self.channel = channel

    def audio_channel_payload(self) -> bytes | None:
        """Frame declaring our audio state, or None if the encoding is unknown."""
        table = _CHANNEL_BYTES.get(self.profile.key)
        spec = self.profile.get(TX_AUDIO_CHANNEL)
        if table is None or spec is None:
            return None
        selector = table.get(self.channel.value)
        if selector is None:
            return None
        width = spec.dlc or 8
        return bytes(width - 1) + bytes([selector])

    def track_time_payload(self) -> bytes | None:
        """Playback position, in the nibble-decimal form both references use."""
        spec = self.profile.get(TX_TRACK_TIME)
        if spec is None:
            return None
        minutes = min(self.track_position_s // 60, 99)
        seconds = self.track_position_s % 60
        packed = bytes([
            (minutes // 10) << 4 | (minutes % 10),
            (seconds // 10) << 4 | (seconds % 10),
        ])
        trailer = _TRACK_TIME_TRAILER.get(self.profile.key, b"")
        width = spec.dlc or 8
        return (packed + trailer).ljust(width, b"\x00")[:width]

    # -- inbound -----------------------------------------------------------

    def on_frame(self, frame: ReceivedFrame) -> None:
        """Track what the radio says it is doing, so playback follows the source.

        When the driver switches to FM with the radio's own buttons, nothing on
        the bus tells us directly to pause; we infer it from the radio's state
        frames, which is what fiatcan does with 0x0A114005.
        """
        if frame.spec is None:
            return
        if frame.spec.name == RX_RADIO_FREQUENCY and len(frame.data) >= 6:
            playing = frame.data[0] == 0xE3 and frame.data[4] == 0x02
            source = "bm" if playing else "fm"
            if source != self.selected_source:
                self.selected_source = source
                log.info("radio selected source: %s", source)
                self.bus.emit("audio_channel", source)
