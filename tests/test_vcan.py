"""End-to-end tests over a real SocketCAN virtual interface.

Skipped automatically when vcan0 is absent, so the suite still runs on a laptop.
To enable::

    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan
    sudo ip link set up vcan0

These are the tests that prove the pieces work against the kernel's CAN stack
rather than against each other, including the one that matters most: that a
listen-only bus cannot be made to transmit.
"""

import subprocess
import time

import pytest

can = pytest.importorskip("can")

from blueandme.can.bus import CanBus
from blueandme.can.dispatcher import Dispatcher
from blueandme.can.txgate import CanMode, TransmitRefused, TxGate
from blueandme.events import EventBus
from blueandme.protocol.confidence import Confidence
from blueandme.protocol.registry import get
from blueandme.vehicle.presence import Presence
from blueandme.vehicle.steering_wheel import SteeringWheel

VCAN = "vcan0"


def vcan_available() -> bool:
    try:
        out = subprocess.run(
            ["ip", "link", "show", VCAN], capture_output=True, text=True, timeout=3
        )
        return out.returncode == 0 and "UP" in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(
    not vcan_available(),
    reason=f"{VCAN} not up; see this module's docstring to enable",
)


@pytest.fixture
def profile():
    return get("doblo263")


@pytest.fixture
def injector():
    bus = can.Bus(interface="socketcan", channel=VCAN)
    yield bus
    bus.shutdown()


def send_raw(injector, can_id, hexdata, extended=True):
    injector.send(
        can.Message(
            arbitration_id=can_id, is_extended_id=extended, data=bytes.fromhex(hexdata)
        )
    )


def _await(bus, can_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = bus.recv(0.2)
        if frame is not None and frame.can_id == can_id:
            return frame
    pytest.fail(f"no frame {can_id:08X} within {timeout}s")


def _await_raw(bus, can_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = bus.recv(0.2)
        if msg is not None and msg.arbitration_id == can_id:
            return msg
    pytest.fail(f"no frame {can_id:08X} within {timeout}s")


def test_listen_only_bus_cannot_transmit(profile):
    gate = TxGate(profile, CanMode.LISTEN_ONLY, Confidence.UNKNOWN)
    with CanBus(VCAN, profile, gate) as bus:
        with pytest.raises(TransmitRefused, match="listen-only"):
            bus.send("bm_status_response", bytes(2))
    assert gate.sent == 0


def test_frames_are_matched_against_the_profile(profile, injector):
    gate = TxGate(profile, CanMode.LISTEN_ONLY)
    with CanBus(VCAN, profile, gate) as bus:
        send_raw(injector, 0x06354000, "8000")
        frame = _await(bus, 0x06354000)
        assert frame.spec is not None
        assert frame.spec.name == "steering_wheel_buttons"
        assert frame.extended


def test_unknown_ids_are_reported_as_unknown(profile, injector):
    gate = TxGate(profile, CanMode.LISTEN_ONLY)
    with CanBus(VCAN, profile, gate) as bus:
        send_raw(injector, 0x01234567, "DEAD")
        frame = _await(bus, 0x01234567)
        assert frame.spec is None and frame.name == "UNKNOWN"


def test_button_press_reaches_an_event(profile, injector):
    events = []
    ev = EventBus()
    ev.on("button", events.append)
    gate = TxGate(profile, CanMode.LISTEN_ONLY)
    dispatcher = Dispatcher(profile)
    dispatcher.on("steering_wheel_buttons", SteeringWheel(profile, ev).on_frame)
    with CanBus(VCAN, profile, gate) as bus:
        send_raw(injector, 0x06354000, "0400")
        dispatcher.dispatch(_await(bus, 0x06354000))
    assert events == ["source"]


def test_gated_transmit_puts_exactly_one_frame_on_the_wire(profile):
    """The Phase 4 bring-up pattern: one frame allowed, everything else refused."""
    listener = can.Bus(interface="socketcan", channel=VCAN)
    try:
        gate = TxGate(
            profile, CanMode.GATED_TX, Confidence.OTHER_VEHICLE, ["bm_status_response"]
        )
        with CanBus(VCAN, profile, gate) as bus:
            bus.send("bm_status_response", bytes.fromhex("000E"))
            with pytest.raises(TransmitRefused):
                bus.send("bm_watchdog", bytes(8))
        assert _await_raw(listener, 0x0E094021).data == bytes.fromhex("000E")
        assert gate.sent == 1 and gate.refused == 1
    finally:
        listener.shutdown()


def test_presence_answers_a_wake_sequence_end_to_end(profile, injector):
    ev = EventBus()
    presence = Presence(profile, ev)
    dispatcher = Dispatcher(profile)
    dispatcher.on("body_network_control", presence.on_frame)
    gate = TxGate(
        profile, CanMode.GATED_TX, Confidence.OTHER_VEHICLE, ["bm_status_response"]
    )
    listener = can.Bus(interface="socketcan", channel=VCAN)
    try:
        with CanBus(VCAN, profile, gate) as bus:
            send_raw(injector, 0x0E094000, "001C00000001")
            dispatcher.dispatch(_await(bus, 0x0E094000))
            bus.send("bm_status_response", presence.status_payload())
        assert _await_raw(listener, 0x0E094021).data == bytes.fromhex("000C")
    finally:
        listener.shutdown()
