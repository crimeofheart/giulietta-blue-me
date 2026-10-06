"""PROXI: prove to the Body Computer that this node belongs in the car.

How PROXI works, per fmntf part 4 (Doblo 263, OTHER_VEHICLE evidence):

  * The Body Computer holds a list of expected nodes, written at the factory or
    by a PROXI alignment.
  * Shortly after wake-up it broadcasts a challenge. Nodes answer with their
    PROXI value.
  * A node that never answers is marked missing; an unexpected node also fails
    the check. Either way the odometer blinks.
  * The challenge is retried up to three times at roughly one-second intervals.
    The genuine Blue&Me module misses the first one -- it is still booting
    Windows Mobile -- and answers the second.

The peer-echo strategy
----------------------
On the Doblo every node answers with the *same* value, so a replacement can
listen for any other node's answer and repeat it under its own ID without ever
knowing what the value means. fiatcan's initproxi.c does exactly this.

Whether the Giulietta shares one value across nodes is decision gate D2 and is
answered by capture H. Until then this responder has no value to echo and no
frame to send it under, and correctly does nothing.

Timing
------
The 3-second window is decision gate D3. A Pi Zero W does not cold-boot inside
it. This class is deliberately cheap to construct and answers from whatever it
has already heard, so it is ready the instant the process starts; making the
process itself start sooner is a separate problem.
"""

from __future__ import annotations

import logging
import time

from ..can.bus import ReceivedFrame
from ..events import EventBus
from ..protocol.frame import Profile

log = logging.getLogger("blueandme.proxi")

RX_REQUEST = "body_proxi_request"
TX_RESPONSE = "bm_proxi_response"

#: Frames whose payload is another node's PROXI value, in preference order.
PEER_FRAMES = ("instrument_panel_proxi", "node_401a_proxi")


class ProxiResponder:
    def __init__(
        self,
        profile: Profile,
        bus: EventBus,
        static_value: bytes | None = None,
        clock=time.monotonic,
    ) -> None:
        self.profile = profile
        self.bus = bus
        self.static_value = static_value
        """Explicit PROXI value, e.g. read out with the ECU software. Overrides
        peer echo when set."""
        self._peer_value: bytes | None = None
        self._peer_source: str | None = None
        self._clock = clock
        self.challenges_seen = 0
        self.answers_given = 0
        self.last_challenge_at: float | None = None

    # -- inbound -----------------------------------------------------------

    def observe(self, frame: ReceivedFrame) -> None:
        """Feed every received frame in; the responder picks out what it needs."""
        if frame.spec is None:
            return
        if frame.spec.name in PEER_FRAMES and frame.data:
            if self._peer_value != frame.data:
                log.info(
                    "learned PROXI value %s from %s",
                    frame.data.hex().upper(), frame.spec.name,
                )
            self._peer_value = bytes(frame.data)
            self._peer_source = frame.spec.name
        elif frame.spec.name == RX_REQUEST:
            self.challenges_seen += 1
            self.last_challenge_at = self._clock()

    # -- outbound ----------------------------------------------------------

    @property
    def value(self) -> bytes | None:
        """The value we would answer with, or None if we have nothing to say."""
        return self.static_value if self.static_value is not None else self._peer_value

    @property
    def source(self) -> str:
        if self.static_value is not None:
            return "configured static value"
        if self._peer_source:
            return f"echoed from {self._peer_source}"
        return "none"

    def response_payload(self) -> bytes | None:
        """Payload for the PROXI answer, or None if we cannot answer.

        Returns None when the value is unknown *or* when the active profile has
        no response frame defined, which is the Giulietta case today. Answering a
        PROXI challenge with a guessed value is worse than not answering: a wrong
        value fails the check just as a missing node does, but also puts an
        unrecognised frame on a live bus.
        """
        value = self.value
        if value is None:
            return None
        spec = self.profile.get(TX_RESPONSE)
        if spec is None:
            return None
        if spec.dlc is not None and len(value) != spec.dlc:
            log.warning(
                "PROXI value is %d bytes, %s expects %d; not answering",
                len(value), spec.name, spec.dlc,
            )
            return None
        return value

    def answered(self) -> None:
        """Called after a successful transmission, for the counters and the event."""
        self.answers_given += 1
        value = self.value
        if value is not None:
            self.bus.emit("proxi_answered", value)

    def describe(self) -> str:
        value = self.value
        return (
            f"value={'?' if value is None else value.hex().upper()} "
            f"({self.source}), challenges={self.challenges_seen}, "
            f"answers={self.answers_given}"
        )
