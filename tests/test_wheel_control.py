"""Wheel buttons to phone actions, against the Giulietta Blue&Me manual's table."""

import time

import pytest

from blueandme.events import EventBus
from blueandme.media import wheel_control as wc
from blueandme.media.wheel_control import (
    ACTIVE, HELD, INCOMING, WAITING, Call, WheelControl, decide, parse_clcc,
)

RINGING = [Call(1, INCOMING, "+34000000001")]
IN_CALL = [Call(1, ACTIVE, "+34000000001")]
WAITING_CALL = [Call(1, ACTIVE, "+34000000001"), Call(2, WAITING, "+34000000002")]
ON_HOLD = [Call(1, HELD, "+34000000001"), Call(2, ACTIVE, "+34000000002")]


class TestClcc:
    def test_parses_calls_with_number_and_name(self):
        lines = ['+CLCC: 1,1,4,0,0,"+34000000001",145,"Maria"', ":OK"]
        assert parse_clcc(lines) == [Call(1, INCOMING, "+34000000001", "Maria")]

    def test_parses_two_calls_and_one_without_a_number(self):
        lines = ['+CLCC: 1,0,0,0,0,"+34000000001",145', "+CLCC: 2,1,5,0,0", ":OK"]
        assert parse_clcc(lines) == [Call(1, ACTIVE, "+34000000001"),
                                     Call(2, WAITING)]

    def test_no_calls(self):
        assert parse_clcc([":OK"]) == []


class TestDecide:
    """Manual 604_38_194, p. 9, button by button."""

    @pytest.mark.parametrize("calls, long, expected", [
        (RINGING, False, "answer"),
        (RINGING, True, "hangup"),          # refuse an incoming call
        (IN_CALL, True, "hangup"),          # end the call in progress
        (IN_CALL, False, None),
        (WAITING_CALL, False, "swap"),      # take the second call
        (ON_HOLD, False, "swap"),           # change from one call to the other
        ([], False, None),                  # the Blue&Me menu: there is none
        ([], True, None),
    ])
    def test_phone(self, calls, long, expected):
        assert decide("phone", long, calls, assistant=False) == expected

    def test_phone_closes_the_assistant(self):
        assert decide("phone", False, [], assistant=True) == "assistant_off"

    @pytest.mark.parametrize("calls, long, expected", [
        ([], False, None),                  # the radio leaves Blue&Me itself,
        ([], True, None),                   # which pauses the phone
        (IN_CALL, False, "mic_toggle"),
        (RINGING, False, None),             # the phone's ringtone is its own
    ])
    def test_mute(self, calls, long, expected):
        assert decide("mute", long, calls, assistant=False) == expected

    def test_voice_opens_the_phones_assistant(self):
        assert decide("voice", False, [], assistant=False) == "assistant_on"
        assert decide("voice", False, [], assistant=True) == "assistant_on"
        assert decide("voice", False, IN_CALL, assistant=False) is None

    def test_mute_closes_the_assistant(self):
        assert decide("mute", False, [], assistant=True) == "assistant_off"

    def test_arrows_skip_tracks_outside_calls(self):
        assert decide("next", False, [], False) == "next"
        assert decide("prev", False, [], False) == "previous"
        assert decide("next", False, IN_CALL, False) is None

    def test_source_moves_a_call_and_is_otherwise_the_radios(self):
        assert decide("source", False, IN_CALL, False) == "audio_toggle"
        assert decide("source", False, [], False) is None
        assert decide("source", False, RINGING, False) is None

    def test_volume_is_the_radios(self):
        assert decide("volume_up", False, IN_CALL, False) is None
        assert decide("volume_down", False, [], False) is None


class FakePhone:
    def __init__(self, calls=()):
        self.call_list = list(calls)
        self.sent: list[str] = []
        self.media_actions: list[str] = []
        self.muted = False
        self.in_car = True
        self.audio_up = False
        self.volume_steps: list[int] = []
        self.results: dict[str, "str | None"] = {}   # command -> result, else OK
        self.timeouts: list[float] = []

    def calls(self):
        return list(self.call_list)

    def at(self, command, timeout=3.0):
        self.sent.append(command)
        self.timeouts.append(timeout)
        return self.results.get(command, "OK")

    def media(self, action):
        self.media_actions.append(action)
        return True

    def mic_muted(self):
        return self.muted

    def set_mic_muted(self, muted):
        self.muted = muted

    def audio_to_phone(self):
        self.in_car = False
        return True

    def audio_to_car(self):
        self.in_car = True
        return True

    def call_audio_up(self):
        return self.audio_up

    def volume_step(self, delta):
        self.volume_steps.append(delta)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def control(phone, clock=None):
    return WheelControl(EventBus(), phone, clock=clock or Clock(), watch_s=0.01)


class TestWheelControl:
    def test_a_short_press_answers_and_a_long_one_rejects(self):
        phone = FakePhone(RINGING)
        w = control(phone)
        assert w.handle("phone", 0.3) == "answer"
        assert w.handle("phone", 1.5) == "hangup"
        assert phone.sent == ["ATA", "AT+CHUP"]

    def test_one_second_exactly_is_still_short(self):
        phone = FakePhone(RINGING)
        assert control(phone).handle("phone", 1.0) == "answer"

    def test_mute_in_a_call_mutes_the_mic_then_the_end_of_the_call_restores_it(self):
        phone = FakePhone(IN_CALL)
        w = control(phone)
        w.handle("mute", 0.2)
        assert phone.muted
        phone.call_list = []                   # the other side hangs up
        w._watcher.join(timeout=2)
        assert not phone.muted

    def test_hanging_up_unmutes_the_mic(self):
        phone = FakePhone(IN_CALL)
        phone.muted = True
        control(phone).handle("phone", 2.0)
        assert phone.sent == ["AT+CHUP"]
        assert not phone.muted

    def test_source_moves_the_call_to_the_phone_and_back(self):
        phone = FakePhone(IN_CALL)
        w = control(phone)
        w.handle("source", 0.2)
        assert not phone.in_car
        w.handle("source", 0.2)
        assert phone.in_car

    def test_after_a_call_left_on_the_phone_the_next_goes_to_the_car(self):
        """Nothing to send: a new call comes to the car by itself. The next
        SRC in a call must drop the link again, not ask for it back."""
        phone = FakePhone(IN_CALL)
        w = control(phone)
        w.handle("source", 0.2)
        phone.call_list = []
        w._watcher.join(timeout=2)
        assert phone.sent == []
        phone.call_list, phone.in_car = list(IN_CALL), True   # the next call
        w.handle("source", 0.2)
        assert not phone.in_car

    def test_source_with_no_audio_link_keeps_the_call_in_the_car(self):
        phone = FakePhone(IN_CALL)
        phone.audio_to_phone = lambda: False     # hcitool found no SCO link
        w = control(phone)
        w.handle("source", 0.2)
        assert not w._audio_on_phone

    def test_voice_opens_the_assistant_and_waits_for_the_audio(self):
        phone = FakePhone()
        w = control(phone)
        w.handle("voice", 0.2)
        assert phone.sent == ["AT+BVRA=1"]
        assert phone.timeouts == [wc.ASSISTANT_OPEN_S]
        assert w.assistant

    @pytest.mark.parametrize("answer", ["OK", "ERROR", None])
    def test_the_assistant_counts_as_open_whatever_the_phone_answers(self, answer):
        """Bench, 2026-09-26: the phone opened it on no answer and on ERROR."""
        phone = FakePhone()
        phone.results["AT+BVRA=1"] = answer
        w = control(phone)
        w.handle("voice", 0.2)
        assert w.assistant

    def test_the_assistant_closes_when_the_phone_drops_its_audio(self):
        clock = Clock()
        phone = FakePhone()
        w = control(phone, clock)
        w.handle("voice", 0.2)
        phone.audio_up = True
        clock.t = wc.ASSISTANT_OPEN_S + 5
        assert w.assistant                                # talking
        phone.audio_up = False
        assert not w.assistant                            # it answered and ended
        assert not w.assistant

    def test_no_audio_after_opening_means_no_assistant(self):
        clock = Clock()
        w = control(FakePhone(), clock)
        w.handle("voice", 0.2)
        assert w.assistant
        clock.t = wc.ASSISTANT_OPEN_S + 0.1
        assert not w.assistant

    def test_phone_or_mute_close_the_assistant(self):
        phone = FakePhone()
        w = control(phone)
        w.handle("voice", 0.2)
        w.handle("phone", 0.2)
        assert phone.sent == ["AT+BVRA=1", "AT+BVRA=0"]
        assert not w.assistant

    def test_mute_does_nothing_more_if_the_phone_had_closed_the_assistant_itself(self):
        """Android ends the session once it has answered; closing it then gets
        ERROR. The radio handles mute outside calls, so nothing follows."""
        phone = FakePhone()
        phone.results["AT+BVRA=0"] = "ERROR"
        w = control(phone)
        w.handle("voice", 0.2)
        w.handle("mute", 0.2)
        assert phone.media_actions == []

    def test_the_assistant_is_forgotten_after_a_while(self):
        clock = Clock()
        phone = FakePhone()
        w = control(phone, clock)
        w.handle("voice", 0.2)
        clock.t = wc.ASSISTANT_S + 1
        w.handle("mute", 0.2)
        assert phone.sent == ["AT+BVRA=1"]

    def test_music_buttons(self):
        phone = FakePhone()
        w = control(phone)
        for name, held in (("next", 0.2), ("prev", 0.2), ("mute", 0.2), ("mute", 1.5)):
            w.handle(name, held)
        assert phone.media_actions == ["next", "previous"]
        assert phone.sent == []

    def test_volume_never_reaches_the_phone(self):
        phone = FakePhone(IN_CALL)
        bus = EventBus()
        WheelControl(bus, phone)
        bus.emit("button_released", "volume_up", 0.2)
        assert phone.sent == [] and phone.media_actions == []

    def test_releases_are_acted_on_off_the_receive_thread(self):
        phone = FakePhone(RINGING)
        bus = EventBus()
        w = WheelControl(bus, phone)
        bus.emit("button_released", "phone", 0.2)
        for _ in range(200):
            if phone.sent:
                break
            time.sleep(0.01)
        assert phone.sent == ["ATA"]
        assert w._worker is not None and w._worker.name == "wheel"


class TestMuteFlag:
    def test_the_relay_and_the_wheel_agree_on_the_flag(self):
        from blueandme.media import sco_uplink

        assert str(wc.MIC_MUTE_FLAG) == sco_uplink.MUTE_FLAG

    def test_bluetooth_phone_sets_and_clears_it(self, tmp_path):
        phone = wc.BluetoothPhone(mute_flag=tmp_path / "run" / "mic-mute")
        assert not phone.mic_muted()
        phone.set_mic_muted(True)
        assert phone.mic_muted()
        phone.set_mic_muted(False)
        assert not phone.mic_muted()


class TestScoLinks:
    def test_finds_the_call_audio_handle_in_hcitool_con(self, monkeypatch):
        out = ("Connections:\n"
               "\t< ACL 11:22:33:44:55:66 handle 12 state 1 lm PERIPHERAL AUTH ENCRYPT \n"
               "\t> eSCO 11:22:33:44:55:66 handle 6 state 1 lm PERIPHERAL \n")
        runs = []

        def run(cmd, **kw):
            runs.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, out if cmd[1] == "con" else "", "")

        monkeypatch.setattr(wc.subprocess, "run", run)
        phone = wc.BluetoothPhone()
        assert phone._sco_handles() == [6]
        assert phone.audio_to_phone()
        assert runs[-1] == ["hcitool", "cmd", "0x01", "0x0006", "0x06", "0x00", "0x13"]

    def test_a_handle_above_255_is_sent_low_byte_first(self, monkeypatch):
        runs = []

        def run(cmd, **kw):
            runs.append(cmd)
            import subprocess
            text = "\t> SCO AA:BB:CC:DD:EE:FF handle 300 state 1\n" if cmd[1] == "con" else ""
            return subprocess.CompletedProcess(cmd, 0, text, "")

        monkeypatch.setattr(wc.subprocess, "run", run)
        wc.BluetoothPhone().audio_to_phone()
        assert runs[-1][4:6] == ["0x2c", "0x01"]


class TestRadioKeys:
    """The radio's arrow pad on Blue&Me."""

    def keys(self, monkeypatch):
        monkeypatch.setattr(wc, "SEEK_AFTER_S", 0.05)
        monkeypatch.setattr(wc, "VOLUME_REPEAT_S", 0.05)
        monkeypatch.setattr(wc, "VOLUME_FASTEST_S", 0.01)
        phone = FakePhone()
        bus = EventBus()
        WheelControl(bus, phone)
        return bus, phone

    def test_a_held_volume_key_speeds_up(self, monkeypatch):
        bus, phone = self.keys(monkeypatch)
        bus.emit("radio_key", "down", True)
        time.sleep(0.2)
        bus.emit("radio_key", "down", False)
        assert len(phone.volume_steps) >= 6               # 0.05 s alone gives 4

    def test_the_wheel_arrows_work_the_same(self, monkeypatch):
        bus, phone = self.keys(monkeypatch)
        bus.emit("button", "next")
        bus.emit("button_released", "next", 0.1)
        bus.emit("button", "prev")
        time.sleep(0.15)
        bus.emit("button_released", "prev", 0.15)
        assert phone.media_actions == ["next", "rewind", "play"]

    def test_left_and_right_skip(self, monkeypatch):
        bus, phone = self.keys(monkeypatch)
        for key in ("left", "right"):
            bus.emit("radio_key", key, True)
            bus.emit("radio_key", key, False)
        assert phone.media_actions == ["previous", "next"]

    def test_held_right_fast_forwards_until_let_go(self, monkeypatch):
        bus, phone = self.keys(monkeypatch)
        bus.emit("radio_key", "right", True)
        time.sleep(0.15)
        bus.emit("radio_key", "right", False)
        assert phone.media_actions == ["fast_forward", "play"]

    def test_up_and_down_step_the_volume_and_repeat_while_held(self, monkeypatch):
        bus, phone = self.keys(monkeypatch)
        bus.emit("radio_key", "up", True)
        time.sleep(0.12)
        bus.emit("radio_key", "up", False)
        bus.emit("radio_key", "down", True)
        bus.emit("radio_key", "down", False)
        time.sleep(0.1)
        assert phone.volume_steps[0] == wc.VOLUME_STEP
        assert phone.volume_steps.count(wc.VOLUME_STEP) >= 2
        assert phone.volume_steps[-1] == -wc.VOLUME_STEP
