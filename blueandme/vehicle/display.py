"""Text out to the instrument-panel and radio displays.

The encoding is shared across FCA platforms (see protocol/text.py, evidence
level LIKELY_SHARED). The frame ID that carries it is not, and lives in the
profile.
"""

from __future__ import annotations

import logging

from ..protocol import text as textcodec
from ..protocol.frame import Profile

log = logging.getLogger("blueandme.display")

TX_TEXT = "bm_text_message"
TX_TEXT_ALT = "bm_display_text"


class Display:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.frame_name = (
            TX_TEXT if profile.get(TX_TEXT) else
            TX_TEXT_ALT if profile.get(TX_TEXT_ALT) else None
        )

    @property
    def available(self) -> bool:
        return self.frame_name is not None

    def payloads(self, message: str, target: textcodec.Display) -> list[bytes]:
        """CAN payloads for a message, or an empty list if we cannot send text."""
        if not self.available:
            return []
        return textcodec.encode_frames(message, target)

    def track(self, artist: str, title: str) -> list[bytes]:
        """Artist and title, joined by the field separator as both references do."""
        text = f"{artist}\n{title}" if artist else title
        return self.payloads(text, textcodec.Display.RADIO)

    def clear(self, target: textcodec.Display = textcodec.Display.DASHBOARD) -> list[bytes]:
        if not self.available:
            return []
        spec = self.profile.require(self.frame_name)
        return [bytes(spec.dlc or 8)]
