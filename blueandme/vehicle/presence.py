"""Node presence: convince the Body Computer that a Blue&Me module is alive.

The Body Computer wakes the network at key-on, then polls every node's status
once a second. A node that stops answering is eventually reported missing; a
node that keeps answering "working" after a shutdown request stops the Body
Computer from putting the network to sleep, which is a battery-drain bug, so the
sleep handshake must be honoured.

State machine, from fmntf part 4 (Doblo 263 semantics -- the Giulietta encoding
is UNKNOWN and the nibble values live in the profile once captured):

    OFF --wake--> BOOTING --ready--> WORKING --shutdown request--> SLEEPING --> OFF
"""

from __future__ import annotations

import logging
from enum import Enum

from ..can.bus import ReceivedFrame
from ..events import EventBus
from ..protocol.frame import Profile
from ..protocol.profiles import doblo263 as _doblo263

#: Profiles that have resolved the status-nibble encoding. The Giulietta profile
#: is absent from this map until capture A resolves it.
_MODULES = {"doblo263": _doblo263}

log = logging.getLogger("blueandme.presence")

RX_NETWORK_CONTROL = "body_network_control"
TX_STATUS = "bm_status_response"


class NodeState(Enum):
    OFF = "off"
    BOOTING = "booting"
    WORKING = "working"
    SLEEPING = "sleeping"


class Presence:
    """Tracks what the Body Computer asked for and what we should answer.

    Deliberately does not transmit. It computes the payload; the caller passes it
    to CanBus.send, which is where TxGate decides whether it may go out. Keeping
    the decision and the transmission apart is what makes this unit-testable
    without a bus.
    """

    def __init__(self, profile: Profile, bus: EventBus) -> None:
        self.profile = profile
        self.bus = bus
        self.state = NodeState.OFF
        self.ignition = False
        self._pending_reply: bytes | None = None

    # -- inbound -----------------------------------------------------------

    def on_frame(self, frame: ReceivedFrame) -> None:
        """Handle the Body Computer's network control frame."""
        command = self.decode_command(frame.data)
        if command is None:
            return
        if command is NodeState.BOOTING:
            self._wake()
        elif command is NodeState.WORKING:
            self._status_query()
        elif command is NodeState.SLEEPING:
            self._shutdown_request()

    def decode_command(self, payload: bytes) -> "NodeState | None":
        """Map the network-control payload onto the command it carries.

        Returns None when the profile has not defined the encoding yet, which is
        the state the Giulietta profile ships in.
        """
        codes = _profile_codes(self.profile)
        if codes is None or len(payload) < 2:
            return None
        nibble = payload[1] & 0x0F
        return codes.get(nibble)

    # -- transitions -------------------------------------------------------

    def _wake(self) -> None:
        if self.state in (NodeState.OFF, NodeState.SLEEPING):
            log.info("body computer woke the network")
            self.state = NodeState.BOOTING
            self.ignition = True
            self.bus.emit("ignition", True)

    def _status_query(self) -> None:
        if self.state is NodeState.BOOTING:
            self.state = NodeState.WORKING
            log.info("reporting working")
        elif self.state in (NodeState.OFF, NodeState.SLEEPING):
            # A status poll with no preceding wake still means the network is up.
            self.state = NodeState.WORKING
            if not self.ignition:
                self.ignition = True
                self.bus.emit("ignition", True)

    def _shutdown_request(self) -> None:
        if self.state is not NodeState.SLEEPING:
            log.info("body computer asked the network to shut down")
            self.state = NodeState.SLEEPING
            if self.ignition:
                self.ignition = False
                self.bus.emit("ignition", False)
            self.bus.emit("sleep_requested")

    def mark_ready(self) -> None:
        """Called once Bluetooth and audio are up, promoting BOOTING to WORKING."""
        if self.state is NodeState.BOOTING:
            self.state = NodeState.WORKING

    # -- outbound ----------------------------------------------------------

    def status_payload(self) -> bytes | None:
        """The status response to send, or None if the encoding is unknown.

        Answering "working" after a shutdown request is exactly the bug fmntf
        describes: the Body Computer loops forever asking the network to sleep.
        This method therefore never reports WORKING once SLEEPING is reached.
        """
        codes = _profile_codes(self.profile)
        if codes is None:
            return None
        inverse = {v: k for k, v in codes.items()}
        nibble = inverse.get(
            NodeState.BOOTING if self.state is NodeState.BOOTING
            else NodeState.SLEEPING if self.state in (NodeState.SLEEPING, NodeState.OFF)
            else NodeState.WORKING
        )
        if nibble is None:
            return None
        spec = self.profile.get(TX_STATUS)
        width = spec.dlc if spec and spec.dlc else 2
        return bytes(width - 1) + bytes([nibble])


def _profile_codes(profile: Profile) -> "dict[int, NodeState] | None":
    """Read the profile's status nibble encoding, if it has one.

    Only the Doblo profile currently defines these. The Giulietta profile returns
    None until capture A resolves the encoding, and Presence then decodes nothing
    and transmits nothing -- which is the correct behaviour for an unknown bus.
    """
    module = _MODULES.get(profile.key)
    if module is None:
        return None
    return {
        module.STATUS_WAKING: NodeState.BOOTING,
        module.STATUS_WORKING: NodeState.WORKING,
        module.STATUS_SLEEPING: NodeState.SLEEPING,
    }
