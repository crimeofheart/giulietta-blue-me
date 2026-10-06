"""Node 4021 on the Giulietta: the answers and periodic frames the car accepted.

Each case mirrors what experiments/node4021/node4021_full.py did in capture ZB
(2026-09-23), now through vehicle/node.py and the transmit gate.
"""

import logging

import pytest

from blueandme.can.bus import ReceivedFrame
from blueandme.events import EventBus
from blueandme.protocol.profiles import giulietta940 as P
from blueandme.protocol.registry import get
from blueandme.protocol import text
from blueandme.vehicle.node import BlueAndMeNode, radio_track_text, track_time

G = get("giulietta940")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def rx(name, hexdata=""):
    spec = G.require(name)
    return ReceivedFrame(0.0, spec.can_id, True, bytes.fromhex(hexdata), spec)


STEADY = P.POLL_STEADY.hex()
WIND_DOWN = "001E00000029"
PROXI = "0A1B2C3D4E5F"   # made up; a car's own value is the setting can.proxi_value


@pytest.fixture
def node():
    clock, sent, channels, events = Clock(), [], [], EventBus()
    events.on("audio_channel", channels.append)
    n = BlueAndMeNode(events, lambda name, data: sent.append((name, data.hex().upper())) or True,
                      clock=clock, sleep=lambda s: None,
                      proxi_value=bytes.fromhex(PROXI))
    n.test_clock, n.test_sent, n.test_channels = clock, sent, channels
    return n


class TestAnswers:
    def test_status_is_booting_then_working(self, node):
        for _ in range(int(P.BOOT_S) + 2):     # the poll comes about once a second
            node.on_frame(rx("bc_status_poll", STEADY))
            node.test_clock.t += 1
        answers = [d for _, d in node.test_sent]
        assert answers[:int(P.BOOT_S)] == ["000C"] * int(P.BOOT_S)
        assert answers[-1] == "001E"

    def test_a_quiet_bus_then_a_poll_is_a_new_wake(self, node):
        node.on_frame(rx("bc_status_poll", STEADY))
        node.test_clock.t += P.BOOT_S + 0.1    # nothing for longer than IDLE_S
        node.on_frame(rx("bc_status_poll", STEADY))
        assert [d for _, d in node.test_sent] == ["000C", "000C"]

    def test_the_wind_down_is_answered_sleeping(self, node):
        node.on_frame(rx("bc_status_poll", WIND_DOWN))
        assert node.test_sent == [("bm_status_response", "000A")]

    @pytest.mark.parametrize("poll", ["001A00000029", "001800000001", "001B00000001"])
    def test_polls_that_are_not_001E_get_no_answer(self, node, poll):
        node.on_frame(rx("bc_status_poll", poll))
        assert node.test_sent == []

    def test_back_to_steady_after_a_wind_down_is_booting_again(self, node):
        node.on_frame(rx("bc_status_poll", STEADY))
        node.test_clock.t += P.BOOT_S + 1
        node.on_frame(rx("bc_status_poll", WIND_DOWN))
        node.test_clock.t += 1
        node.on_frame(rx("bc_status_poll", STEADY))
        assert [d for _, d in node.test_sent] == ["000C", "000A", "000C"]

    def test_proxi_is_answered_with_the_configured_value(self, node):
        node.on_frame(rx("bc_proxi_challenge"))
        assert node.test_sent == [("bm_proxi_response", PROXI)]

    def test_proxi_is_not_answered_without_a_value(self, node, caplog):
        caplog.set_level(logging.WARNING, logger="blueandme.node")
        node.proxi_value = None
        node.on_frame(rx("bc_proxi_challenge"))
        node.on_frame(rx("bc_proxi_challenge"))
        assert node.test_sent == []
        said = [r for r in caplog.records if "can.proxi_value is not set" in r.message]
        assert len(said) == 1


class TestRadio:
    def test_answers_the_radio_when_asked(self, node):
        node.on_frame(rx("radio_media_request", "0400000000000000"))   # another source
        assert node.test_sent == []
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        assert node.test_sent == [("bm_audio_channel", "0000000000000084")]
        assert node.test_channels == ["bm"]

    def test_a_second_open_while_playing_is_not_answered_again(self, node):
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        assert len(node.test_sent) == 1

    def test_leaving_goes_back_to_muted(self, node):
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.on_frame(rx("radio_media_request", "0400000010000000"))   # playing
        node.on_frame(rx("radio_media_request", "0400000000000000"))   # Blue&Me deselected
        assert node.test_sent[-1] == ("bm_audio_channel", "0000000000000080")
        assert node.test_channels == ["bm", "radio"]

    def test_mute_without_blue_and_me_selected_is_leaving(self, node):
        """SRC to CD: the radio says 2400000000000000 at once, 04…00 only 1.5 s
        later, and answering "…80" then spoiled the next selection (sniff2)."""
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.on_frame(rx("radio_media_request", "2400000000000000"))
        assert node.test_sent[-1] == ("bm_audio_channel", "0000000000000080")

    def test_selected_without_a_request_is_answered(self, node):
        """The radio sometimes skips 0C and just selects Blue&Me (sniff2)."""
        node.on_frame(rx("radio_media_request", "0400000000000000"))
        assert node.test_sent == []
        node.on_frame(rx("radio_media_request", "0400000010000000"))
        assert node.test_sent == [("bm_audio_channel", "0000000000000084")]
        assert node.test_channels == ["bm"]

    def test_the_arrow_pad_on_blue_and_me(self, node):
        keys = []
        node.events.on("radio_key", lambda k, p: keys.append((k, p)))
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        for frame in ("0400000010000000", "0440000010000000", "0400000010000000",
                      "0500000010000000", "0400000010000000", "0480000010000000"):
            node.on_frame(rx("radio_media_request", frame))
        assert keys == [("left", True), ("left", False), ("up", True), ("up", False),
                        ("down", True)]
        assert node.test_sent == [("bm_audio_channel", "0000000000000084")]

    def test_mute_is_not_leaving_it_pauses_and_the_unmute_plays(self, node):
        """Answering 0x24 with "…80" made the radio fall back to CD (2026-09-27)."""
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.on_frame(rx("radio_media_request", "0400000010000000"))   # playing
        node.on_frame(rx("radio_media_request", "2400000010000000"))   # mute
        node.on_frame(rx("radio_media_request", "2400000010000000"))   # repeated
        node.on_frame(rx("radio_media_request", "0400000010000000"))   # unmute
        assert node.test_sent == [("bm_audio_channel", "0000000000000084")]
        assert node.test_channels == ["bm", "radio", "bm"]


class TestPeriodic:
    def wake(self, node):
        node.on_frame(rx("steering_wheel_buttons", "0000"))

    def test_starts_a_second_after_the_wake(self, node):
        self.wake(node)
        node.tick()
        assert node.test_sent == []
        node.test_clock.t += 1
        self.wake(node)
        node.tick()
        assert node.test_sent == [
            ("bm_watchdog", "4000000000000002"), ("bm_watchdog", "5000000000000002"),
            ("bm_track_time", "0000400000000000"), ("bm_audio_channel", "0000000000000080"),
            ("bm_text_message", "0028000000000000"),
        ]

    def test_idle_track_time_alternates(self, node):
        self.wake(node)
        node.tick()
        tracks = []
        for _ in range(3):
            node.test_clock.t += 1
            self.wake(node)
            node.tick()
            tracks.append([d for n, d in node.test_sent if n == "bm_track_time"][-1])
        assert tracks == ["0000400000000000", "0000800000000000", "0000400000000000"]

    def test_while_playing_the_position_counts_up(self, node):
        self.wake(node)
        node.tick()
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        for _ in range(3):
            node.test_clock.t += 1
            self.wake(node)
            node.tick()
        tracks = [d for n, d in node.test_sent if n == "bm_track_time"]
        audio = [d for n, d in node.test_sent if n == "bm_audio_channel"]
        assert tracks[-1] == "0003407800000000"
        assert audio[-1] == "0000000000000084"

    def test_stops_after_the_bus_goes_quiet(self, node):
        self.wake(node)
        node.tick()
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.test_clock.t += P.IDLE_S + 0.5
        node.test_sent.clear()
        node.tick()
        node.test_clock.t += 1
        node.tick()
        assert node.test_sent == []
        assert not node.playing

    def test_track_time_format(self):
        assert track_time(True, 125, True).hex().upper() == "0205407800000000"
        assert track_time(True, 3600 + 61, True).hex().upper() == "0101407800000000"


class TestText:
    """The Doblo module's text, sent on the owner's decision (2026-09-26)."""

    def running(self, node):
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()

    def second(self, node):
        node.test_clock.t += 1
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.test_sent.clear()
        node.tick()
        return [bytes.fromhex(d) for n, d in node.test_sent if n == "bm_text_message"]

    def test_the_track_goes_to_the_radio_once_per_track(self, node):
        node.track = ("Lo Stato Sociale", "", "Quello che le donne non dicono")
        self.running(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        frames = self.second(node)
        assert text.decode_frames(frames) == (
            "+LO STATO SOCI\nLO STATO SOCIA\nQUELLO CHE LE DONN\n", text.Display.RADIO)
        assert self.second(node) == []                    # same track: nothing new
        node.track = ("Lo Stato Sociale", "", "Amore ai tempi dell'Ivana")
        assert len(self.second(node)) == 7

    def test_title_runs_at_the_top_and_the_time_sits_at_the_bottom(self, node):
        node.track = ("Trivium", "Trivium", "Until the World Goes Cold")
        self.running(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        self.second(node)                                 # the track's own text first
        shown = []
        for _ in range(3):
            node.test_sent.clear()
            node.scroll()
            frames = [bytes.fromhex(d) for n, d in node.test_sent if n == "bm_text_message"]
            shown.append(text.decode_frames(frames)[0].split(chr(10)))
        assert [s[2] for s in shown] == ["TRIVIUM - UN", "RIVIUM - UNT", "IVIUM - UNTI"]
        assert shown[0][1] == "TRIVIUM" and shown[0][0].startswith("+0:0")

    def test_a_short_title_is_sent_again_only_when_the_time_changes(self, node):
        node.track = ("AB", "", "Song")
        self.running(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.scroll()                                     # not shown yet
        assert [n for n, _ in node.test_sent if n == "bm_text_message"] == []
        self.second(node)
        node.test_sent.clear()
        node.scroll()
        once = len(node.test_sent)
        node.scroll()
        assert once and len(node.test_sent) == once       # nothing new
        node.position_s += 1
        node.scroll()
        assert len(node.test_sent) == 2 * once

    def test_the_doblo_modules_own_frames(self):
        """The same seven frames the Doblo's module sent for this track."""
        message = radio_track_text("Lo Stato Sociale", "", "Quello che le donne non dicono")
        frames = [f.hex().upper() for f in text.encode_frames(message, text.Display.RADIO)]
        assert frames[0] == "602AE176A879F31F" and frames[-1] == "662AFC0000000000"

    def test_idle_while_the_radio_is_elsewhere(self, node):
        node.track = ("A", "B", "C")
        self.running(node)
        assert self.second(node) == [P.TEXT_IDLE_RADIO]

    def test_the_caller_goes_to_the_cluster_and_is_cleared_after(self, node):
        self.running(node)
        node.caller = "Maria"
        frames = [f for f in self.second(node) if f != P.TEXT_IDLE_RADIO]
        assert text.decode_frames(frames) == ("MARIA", text.Display.DASHBOARD)
        assert [f for f in self.second(node) if f != P.TEXT_IDLE_RADIO] == []
        node.caller = None
        assert P.TEXT_CLEAR_DASHBOARD in self.second(node)

    def test_the_caller_is_repeated_every_ten_seconds(self, node):
        self.running(node)
        node.caller = "+34000000001"
        sent = []
        for _ in range(21):
            sent.append(any(f[1] >> 4 == 1 for f in self.second(node) if f != P.TEXT_CLEAR_DASHBOARD))
        assert sent.count(True) == 3


class TestPhoneChannel:
    """The Doblo's audio channel values for a call and (inferred) the assistant."""

    def awake(self, node):
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()                     # periodic frames start
        node.test_sent.clear()

    def test_a_call_switches_the_radio_at_once_and_back_after(self, node):
        self.awake(node)
        node.set_phone(call=True, assistant=False)
        assert node.test_sent == [("bm_audio_channel", "0000000000000481")]
        node.set_phone(call=True, assistant=False)          # no change, nothing sent
        assert len(node.test_sent) == 1
        node.set_phone(call=False, assistant=False)
        assert node.test_sent[-1] == ("bm_audio_channel", "0000000000000080")

    def test_after_a_call_music_comes_back(self, node):
        self.awake(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.set_phone(call=True, assistant=False)
        node.set_phone(call=False, assistant=False)
        assert [d for _, d in node.test_sent][-2:] == ["0000000000000481", "0000000000000084"]

    def test_the_assistant_uses_the_voice_value_and_a_call_beats_it(self, node):
        self.awake(node)
        node.set_phone(call=False, assistant=True)
        node.set_phone(call=True, assistant=True)
        assert [d for _, d in node.test_sent] == ["0000000000000482", "0000000000000481"]

    def test_the_periodic_frame_carries_it_too(self, node):
        self.awake(node)
        node.set_phone(call=True, assistant=False)
        node.test_clock.t += 1
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()
        assert ("bm_audio_channel", "0000000000000481") in node.test_sent[1:]

    def test_nothing_while_the_bus_sleeps(self, node):
        node.set_phone(call=True, assistant=False)
        assert node.test_sent == []

    def test_the_phones_position_is_the_track_time(self, node):
        self.awake(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.set_phone(call=False, assistant=False, position_s=125.4)
        node.test_clock.t += 1
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()
        track = [d for n, d in node.test_sent if n == "bm_track_time"][-1]
        assert track == "0206407800000000"                # 2:06, a second on

    def test_previous_restarts_the_time_though_the_phone_keeps_counting(self, node):
        self.awake(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.track = ("A", "B", "Song")
        node.set_phone(call=False, assistant=False, position_s=257.0)
        node.track_restarted()
        node.set_phone(call=False, assistant=False, position_s=262.0)   # stale, +5 s
        node.test_clock.t += 1
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()
        track = [d for n, d in node.test_sent if n == "bm_track_time"][-1]
        assert track == "0006407800000000"                # 0:05 + a second
        node.track = ("A", "B", "Next song")
        node.set_phone(call=False, assistant=False, position_s=3.0)
        assert node.position_s == 3.0

    def test_paused_on_the_phone_the_time_stands_still(self, node):
        self.awake(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.set_phone(call=False, assistant=False, position_s=65.0, phone_playing=False)
        for _ in range(3):
            node.test_clock.t += 1
            node.on_frame(rx("steering_wheel_buttons", "0000"))
            node.tick()
        track = [d for n, d in node.test_sent if n == "bm_track_time"][-1]
        assert track == "0105407800000000"                # 1:05, not 1:08
        node.set_phone(call=False, assistant=False, position_s=65.0, phone_playing=True)
        node.test_clock.t += 1
        node.on_frame(rx("steering_wheel_buttons", "0000"))
        node.tick()
        assert node.position_s == 66.0

    def test_previous_that_changed_the_song_follows_the_phone(self, node):
        self.awake(node)
        node.on_frame(rx("radio_media_request", "0C00000000000000"))
        node.track = ("A", "B", "Song")
        node.set_phone(call=False, assistant=False, position_s=2.0)
        node.track_restarted()
        node.track = ("A", "B", "Song before")
        node.set_phone(call=False, assistant=False, position_s=1.0)
        assert node.position_s == 1.0


class TestCallerText:
    def test_the_ringing_call_first_by_name_else_number(self):
        from blueandme.app import caller_text
        from blueandme.media.wheel_control import ACTIVE, INCOMING, WAITING, Call

        assert caller_text([]) is None
        assert caller_text([Call(1, INCOMING, "+34000000001", "Maria")]) == "Maria"
        assert caller_text([Call(1, ACTIVE, "+34000000001"),
                            Call(2, WAITING, "+34000000002")]) == "+34000000002"
        assert caller_text([Call(1, ACTIVE)]) == "CALL"


class TestInTheApp:
    """The app runs the node for the Giulietta, through the transmit gate."""

    @pytest.fixture
    def app(self):
        from blueandme.app import Application
        from blueandme.config import load

        return Application(load("blueandme/config/default.yaml"))

    def test_the_giulietta_gets_the_node_and_kernel_filters(self, app):
        assert app.node is not None
        assert app.bus.filter_to_profile

    def test_listen_only_sends_nothing_and_says_so_once(self, app, caplog):
        caplog.set_level(logging.INFO, logger="blueandme.app")
        assert app._node_send("bm_proxi_response", bytes.fromhex(PROXI)) is False
        assert app._node_send("bm_proxi_response", bytes.fromhex(PROXI)) is False
        said = [r for r in caplog.records if "not sending bm_proxi_response" in r.message]
        assert len(said) == 1
        assert app.gate.refused == 0          # decided quietly, not a refusal

    def test_the_text_frame_goes_out_on_the_owners_exception(self, app):
        from blueandme.can.txgate import CanMode, TxGate

        app.gate = TxGate(app.profile, CanMode.GATED_TX, allowed=["bm_text_message"])
        sent = []
        app._try_send = lambda name, data: sent.append(name) or True
        assert app._node_send("bm_text_message", P.TEXT_IDLE_RADIO)
        assert sent == ["bm_text_message"]

    def test_gated_tx_sends_only_the_allowed_frame(self, app):
        from blueandme.can.txgate import CanMode, TxGate

        app.gate = TxGate(app.profile, CanMode.GATED_TX, allowed=["bm_proxi_response"])
        sent = []
        app._try_send = lambda name, data: sent.append(name) or True
        assert app._node_send("bm_proxi_response", bytes.fromhex(PROXI))
        assert not app._node_send("bm_status_response", P.STATUS_WORKING)
        assert sent == ["bm_proxi_response"]


class TestProxiSetting:
    """The PROXI value is one car's: a setting, empty in a fresh install."""

    @pytest.fixture
    def cfg(self):
        from blueandme.config import load

        return load("blueandme/config/default.yaml")

    def test_a_fresh_install_has_no_value(self, cfg):
        assert cfg.can.proxi_value == ""
        assert cfg.can.proxi_bytes() is None
        assert cfg.validate() == []

    def test_the_configured_value_reaches_the_node(self, cfg):
        from blueandme.app import Application

        cfg.can.proxi_value = PROXI
        assert cfg.validate() == []
        assert Application(cfg).node.proxi_value == bytes.fromhex(PROXI)

    @pytest.mark.parametrize("value", ["0A1B2C", "0A1B2C3D4E5G", "0A 1B 2C 3D 4E 5F"])
    def test_a_malformed_value_is_a_config_error(self, cfg, value):
        cfg.can.proxi_value = value
        assert any("12 hex digits" in p for p in cfg.validate())
        assert cfg.can.proxi_bytes() is None

    def test_an_unquoted_value_is_a_config_error(self, tmp_path):
        from blueandme.config import load

        path = tmp_path / "config.yaml"
        path.write_text("can:\n  proxi_value: 123456789012\n", encoding="utf-8")
        cfg = load(path)
        assert any("in quotes" in p for p in cfg.validate())
        assert cfg.can.proxi_bytes() is None
