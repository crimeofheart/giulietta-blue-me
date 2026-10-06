"""Vehicle logic: buttons, presence, PROXI, radio, power.

Every test runs against the doblo263 reference profile, the one with the most
frames in it, and additionally asserts that the same code does nothing at all
against a profile that knows none of its frames. The Giulietta's own node
logic is vehicle/node.py (tests/test_node.py).
"""

import pytest

from blueandme.can.bus import ReceivedFrame
from blueandme.events import EventBus
from blueandme.power.ignition import IgnitionMonitor
from blueandme.power.lifecycle import Lifecycle
from blueandme.protocol.registry import get
from blueandme.vehicle.display import Display
from blueandme.vehicle.presence import NodeState, Presence
from blueandme.vehicle.proxi import ProxiResponder
from blueandme.vehicle.radio import AudioChannel, Radio
from blueandme.vehicle.steering_wheel import SteeringWheel


@pytest.fixture
def doblo():
    return get("doblo263")


@pytest.fixture
def giulietta():
    return get("giulietta940")


@pytest.fixture
def unknown():
    """A 29-bit profile that knows no frames: what every module must survive."""
    from blueandme.protocol.confidence import Confidence
    from blueandme.protocol.frame import Evidence, Profile

    return Profile("unknown", "no frames known", 50000, True, [],
                   id_width_evidence=Evidence(Confidence.CONFIRMED_940, "test"))


def frame(profile, can_id, hexdata, extended=True):
    data = bytes.fromhex(hexdata)
    return ReceivedFrame(0.0, can_id, extended, data,
                         profile.by_id(can_id, extended))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestSteeringWheel:
    def test_press_fires_once_while_held(self, doblo):
        events, bus = [], EventBus()
        bus.on("button", events.append)
        wheel = SteeringWheel(doblo, bus, clock=Clock())
        for _ in range(5):
            wheel.feed(bytes.fromhex("8000"))
        assert events == ["volume_up"]

    def test_release_then_press_fires_again(self, doblo):
        clock = Clock()
        events, bus = [], EventBus()
        bus.on("button", events.append)
        wheel = SteeringWheel(doblo, bus, clock=clock)
        for payload in ("8000", "0000", "8000"):
            clock.t += 0.2
            wheel.feed(bytes.fromhex(payload))
        assert events == ["volume_up", "volume_up"]

    def test_contact_bounce_is_suppressed(self, doblo):
        clock = Clock()
        events, bus = [], EventBus()
        bus.on("button", events.append)
        wheel = SteeringWheel(doblo, bus, bounce_s=0.05, clock=clock)
        wheel.feed(bytes.fromhex("8000"))
        clock.t += 0.01
        wheel.feed(bytes.fromhex("0000"))
        clock.t += 0.01
        wheel.feed(bytes.fromhex("8000"))
        assert events == ["volume_up"]

    def test_multiple_buttons_at_once(self, doblo):
        events, bus = [], EventBus()
        bus.on("button", events.append)
        SteeringWheel(doblo, bus, clock=Clock()).feed(bytes.fromhex("A000"))
        assert sorted(events) == ["mute", "volume_up"]

    def test_all_documented_masks_decode(self, doblo):
        expected = {
            "8000": "volume_up", "4000": "volume_down", "2000": "mute",
            "1000": "up", "0800": "down", "0400": "source",
            "0080": "menu", "0040": "windows",
        }
        for payload, name in expected.items():
            assert doblo.decode_buttons(bytes.fromhex(payload)) == [name]
        assert doblo.decode_buttons(bytes.fromhex("0000")) == []

    def test_giulietta_profile_decodes_its_captured_buttons(self, giulietta):
        wheel = SteeringWheel(giulietta, EventBus(), clock=Clock())
        assert wheel.feed(bytes.fromhex("0080")) == ["phone"]

    def test_release_reports_how_long_the_button_was_held(self, giulietta):
        clock = Clock()
        released, bus = [], EventBus()
        bus.on("button_released", lambda name, held: released.append((name, held)))
        wheel = SteeringWheel(giulietta, bus, clock=clock)
        wheel.feed(bytes.fromhex("0080"))      # press
        clock.t = 0.5
        wheel.feed(bytes.fromhex("0080"))      # the 2 Hz repeat while held
        clock.t = 1.6
        wheel.feed(bytes.fromhex("0000"))      # release
        assert released == [("phone", 1.6)]

    def test_a_bounce_is_not_a_second_press(self, giulietta):
        clock = Clock()
        released, bus = [], EventBus()
        bus.on("button_released", lambda name, held: released.append(name))
        wheel = SteeringWheel(giulietta, bus, clock=clock)
        wheel.feed(bytes.fromhex("0040"))      # press
        clock.t = 0.01
        wheel.feed(bytes.fromhex("0000"))      # contact bounce: released...
        clock.t = 0.02
        wheel.feed(bytes.fromhex("0040"))      # ...and made again within 50 ms
        clock.t = 0.5
        wheel.feed(bytes.fromhex("0000"))      # the real release
        assert released == ["voice"]           # one press, one release


class TestPresence:
    def test_reproduces_the_documented_sequence(self, doblo):
        """fmntf part 4: 001C wake, 001E status query, 001A shut down; the node
        answers 000C booting, 000E working, 000A sleeping."""
        bus = EventBus()
        presence = Presence(doblo, bus)
        steps = [
            ("001C00000001", NodeState.BOOTING, "000C"),
            ("001E00000001", NodeState.WORKING, "000E"),
            ("001E00000001", NodeState.WORKING, "000E"),
            ("001A0400106B", NodeState.SLEEPING, "000A"),
        ]
        for payload, state, reply in steps:
            presence.on_frame(frame(doblo, 0x0E094000, payload))
            assert presence.state is state
            assert presence.status_payload().hex().upper() == reply

    def test_never_reports_working_after_a_shutdown_request(self, doblo):
        """The bug fmntf describes: keep answering 'working' and the Body
        Computer loops forever asking the network to sleep."""
        presence = Presence(doblo, EventBus())
        presence.on_frame(frame(doblo, 0x0E094000, "001C00000001"))
        presence.on_frame(frame(doblo, 0x0E094000, "001A0400106B"))
        presence.mark_ready()
        assert presence.status_payload().hex().upper() == "000A"

    def test_emits_ignition_events(self, doblo):
        seen, bus = [], EventBus()
        bus.on("ignition", seen.append)
        bus.on("sleep_requested", lambda: seen.append("sleep"))
        presence = Presence(doblo, bus)
        presence.on_frame(frame(doblo, 0x0E094000, "001C00000001"))
        presence.on_frame(frame(doblo, 0x0E094000, "001A0400106B"))
        assert seen == [True, False, "sleep"]

    def test_empty_profile_produces_no_payload(self, unknown):
        assert Presence(unknown, EventBus()).status_payload() is None


class TestProxi:
    def test_echoes_a_peer_value(self, doblo):
        responder = ProxiResponder(doblo, EventBus())
        assert responder.response_payload() is None
        responder.observe(frame(doblo, 0x1E114003, "362630045880"))
        assert responder.response_payload().hex().upper() == "362630045880"
        assert "instrument_panel_proxi" in responder.source

    def test_counts_challenges(self, doblo):
        responder = ProxiResponder(doblo, EventBus())
        for _ in range(3):
            responder.observe(frame(doblo, 0x1E114000, ""))
        assert responder.challenges_seen == 3

    def test_static_value_overrides_peer_echo(self, doblo):
        responder = ProxiResponder(doblo, EventBus(),
                                   static_value=bytes.fromhex("AABBCCDDEEFF"))
        responder.observe(frame(doblo, 0x1E114003, "362630045880"))
        assert responder.response_payload().hex().upper() == "AABBCCDDEEFF"

    def test_wrong_length_value_is_not_sent(self, doblo):
        responder = ProxiResponder(doblo, EventBus(), static_value=b"\x01\x02")
        assert responder.response_payload() is None

    def test_empty_profile_never_answers(self, unknown):
        responder = ProxiResponder(unknown, EventBus(),
                                   static_value=bytes.fromhex("362630045880"))
        assert responder.response_payload() is None


class TestRadio:
    def test_audio_channel_selectors_match_the_documented_values(self, doblo):
        radio = Radio(doblo, EventBus())
        expected = {
            AudioChannel.MUTED: "0000000000000080",
            AudioChannel.PHONE: "0000000000000081",
            AudioChannel.VOICE: "0000000000000082",
            AudioChannel.NAVIGATION: "0000000000000083",
            AudioChannel.MEDIA: "0000000000000084",
        }
        for channel, payload in expected.items():
            radio.set_channel(channel)
            assert radio.audio_channel_payload().hex().upper() == payload

    def test_track_time_matches_fiatcans_output(self, doblo):
        radio = Radio(doblo, EventBus())
        radio.track_position_s = 4 * 60 + 38
        assert radio.track_time_payload().hex().upper() == "0438487800000000"

    def test_follows_the_head_units_source_selection(self, doblo):
        seen, bus = [], EventBus()
        bus.on("audio_channel", seen.append)
        radio = Radio(doblo, bus)
        radio.on_frame(frame(doblo, 0x0A114005, "E30000000200"))
        radio.on_frame(frame(doblo, 0x0A114005, "420415000000"))
        assert seen == ["bm", "fm"]

    def test_empty_profile_produces_no_payloads(self, unknown):
        radio = Radio(unknown, EventBus())
        radio.set_channel(AudioChannel.MEDIA)
        assert radio.audio_channel_payload() is None
        assert radio.track_time_payload() is None


class TestDisplay:
    def test_track_uses_the_field_separator(self, doblo):
        from blueandme.protocol import text as T

        frames = Display(doblo).track("DAFT PUNK", "ONE MORE TIME")
        assert T.decode_frames(frames)[0] == "DAFT PUNK\nONE MORE TIME"

    def test_empty_profile_has_no_display(self, unknown):
        display = Display(unknown)
        assert not display.available
        assert display.track("A", "B") == []


class TestPower:
    def test_shutdown_waits_for_the_delay(self):
        clock = Clock()
        bus = EventBus()
        monitor = IgnitionMonitor(bus, stay_awake_s=0, shutdown_delay_s=30,
                                  clock=clock)
        bus.emit("ignition", True)
        assert not monitor.should_shut_down()
        bus.emit("ignition", False)
        clock.t = 29.9
        assert not monitor.should_shut_down()
        clock.t = 30.0
        assert monitor.should_shut_down()

    def test_key_back_on_cancels_shutdown(self):
        clock = Clock()
        bus = EventBus()
        monitor = IgnitionMonitor(bus, shutdown_delay_s=30, clock=clock)
        bus.emit("ignition", False)
        clock.t = 20.0
        bus.emit("ignition", True)
        clock.t = 100.0
        assert not monitor.should_shut_down()

    def test_stay_awake_extends_the_delay(self):
        clock = Clock()
        bus = EventBus()
        monitor = IgnitionMonitor(bus, stay_awake_s=600, shutdown_delay_s=30,
                                  clock=clock)
        bus.emit("ignition", False)
        clock.t = 600.0
        assert not monitor.should_shut_down()
        clock.t = 630.0
        assert monitor.should_shut_down()

    def test_shutdown_callbacks_run_in_reverse(self):
        order = []
        lifecycle = Lifecycle(dry_run=True)
        lifecycle.on_stop(lambda: order.append("audio"))
        lifecycle.on_stop(lambda: order.append("can"))
        lifecycle.halt()
        assert order == ["can", "audio"]

    def test_a_failing_callback_does_not_block_the_rest(self):
        order = []
        lifecycle = Lifecycle(dry_run=True)
        lifecycle.on_stop(lambda: order.append("first"))
        lifecycle.on_stop(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        lifecycle.halt()
        assert order == ["first"]
