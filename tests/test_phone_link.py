"""Reconnect and pairing helpers, and call control's pure parts."""

from blueandme.media import call_control as cc
from blueandme.media import phone_link as pl

from tests.test_sco_uplink import HF_SINK, HF_SOURCE, MUSIC, get_pcms

PHONE = "11:22:33:44:55:66"
#: Real bluetoothctl output from the Pi (2026-09-24).
PAIRED = f"Device {PHONE} OnePlus 12\n"
INFO = "\tName: OnePlus 12\n\tPaired: yes\n\tTrusted: yes\n\tConnected: {c}\n"
#: bluetoothctl show on the Pi with bluealsa up (2026-09-25), UUID lines only.
SHOW_READY = (
    "\tUUID: A/V Remote Control        (0000110e-0000-1000-8000-00805f9b34fb)\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tUUID: Handsfree                 (0000111e-0000-1000-8000-00805f9b34fb)\n"
)
SHOW_EARLY = SHOW_READY.split("\tUUID: Handsfree")[0]
BOTH = get_pcms(MUSIC, HF_SINK, HF_SOURCE)
MUSIC_ONLY = get_pcms(MUSIC)


class FakeCtl:
    def __init__(self, connected=False, paired=PAIRED, trusted=True, show=SHOW_READY):
        self.connected = connected
        self.paired, self.trusted, self.show = paired, trusted, show
        self.calls = []

    def __call__(self, *args, timeout=20):
        self.calls.append(args)
        if args == ("devices", "Paired"):
            return self.paired
        if args == ("show",):
            return self.show
        if args[0] == "info":
            text = INFO.format(c="yes" if self.connected else "no")
            return text if self.trusted else text.replace("Trusted: yes", "Trusted: no")
        return ""


class TestParsing:
    def test_devices_lists_addresses(self):
        assert pl.parse_devices(PAIRED) == [PHONE]
        assert pl.parse_devices("") == []

    def test_info_reads_the_flags(self):
        assert pl.parse_info(INFO.format(c="no")) == {
            "Paired": True, "Trusted": True, "Connected": False}

    def test_new_pairings_are_the_difference(self):
        assert pl.new_pairings({PHONE}, [PHONE, "AA:BB:CC:DD:EE:FF"]) == ["AA:BB:CC:DD:EE:FF"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def reconnector(ctl, pcms=BOTH, profile_ok=True):
    state = {"pcms": pcms, "profiles": []}

    def profile(addr, uuid):
        state["profiles"].append((addr, uuid))
        return profile_ok

    clock = Clock()
    r = pl.Reconnector(ctl, pcms=lambda: state["pcms"], profile=profile, clock=clock)
    return r, state, clock


class TestReconnector:
    def test_a_connected_phone_with_music_and_calls_is_left_alone(self):
        ctl = FakeCtl(connected=True)
        r, state, _ = reconnector(ctl)
        assert r.step() == pl.CONNECTED_CHECK_S
        assert not any("connect" in c for c in ctl.calls)
        assert state["profiles"] == []

    def test_a_trusted_phone_is_paged_by_profile_music_first(self):
        ctl = FakeCtl()
        r, state, _ = reconnector(ctl)
        r.backoff = 40
        assert r.step() == pl.SHORT_CHECK_S  # and check both are up
        assert state["profiles"] == [(PHONE, pl.PHONE_UUIDS["A2DP"]),
                                     (PHONE, pl.PHONE_UUIDS["HFP"])]
        assert r.backoff == pl.BACKOFF_MIN_S

    def test_paging_never_uses_a_plain_connect(self):
        # bluetoothctl connect (Device1.Connect) opened by the Pi brought up
        # the link without music or calls, for 40 s and more (2026-09-25).
        ctl = FakeCtl()
        reconnector(ctl)[0].step()
        assert not any("connect" in c for c in ctl.calls)

    def test_a_phone_out_of_range_costs_one_page_not_two(self):
        ctl = FakeCtl()
        r, state, _ = reconnector(ctl, profile_ok=False)
        assert r.step() == pl.BACKOFF_MIN_S
        assert state["profiles"] == [(PHONE, pl.PHONE_UUIDS["A2DP"])]

    def test_no_page_before_bluealsa_has_registered_both_profiles(self):
        ctl = FakeCtl(show=SHOW_EARLY)
        r, state, _ = reconnector(ctl)
        assert r.step() == pl.SHORT_CHECK_S
        assert state["profiles"] == []
        ctl.show = SHOW_READY
        r.step()
        assert state["profiles"]

    def test_a_missing_profile_gets_a_grace_period_then_is_connected_once(self):
        ctl = FakeCtl(connected=True)
        r, state, clock = reconnector(ctl, pcms=MUSIC_ONLY)
        assert r.step() == pl.SHORT_CHECK_S
        assert state["profiles"] == []
        clock.t = pl.PROFILE_GRACE_S
        r.step()
        assert state["profiles"] == [(PHONE, pl.PHONE_UUIDS["HFP"])]
        clock.t += 10
        r.step()
        assert len(state["profiles"]) == 1  # once per link, not a loop

    def test_a_new_link_may_try_again(self):
        ctl = FakeCtl(connected=True)
        r, state, clock = reconnector(ctl, pcms=MUSIC_ONLY)
        r.step()
        clock.t = pl.PROFILE_GRACE_S
        r.step()
        ctl.connected, ctl.show = False, SHOW_EARLY  # link gone; no page this round
        r.step()
        ctl.connected = True
        r.step()
        clock.t += pl.PROFILE_GRACE_S
        r.step()
        assert len(state["profiles"]) == 2

    def test_nothing_is_connected_by_hand_once_both_are_up(self):
        ctl = FakeCtl(connected=True)
        r, state, clock = reconnector(ctl, pcms=MUSIC_ONLY)
        r.step()
        state["pcms"] = BOTH
        clock.t = pl.PROFILE_GRACE_S
        assert r.step() == pl.CONNECTED_CHECK_S
        assert state["profiles"] == []

    def test_backoff_doubles_to_the_maximum_while_nothing_answers(self):
        r, _, _ = reconnector(FakeCtl(), profile_ok=False)
        waits = [r.step() for _ in range(5)]
        assert waits == [5.0, 10.0, 20.0, 20.0, 20.0]

    def test_an_untrusted_phone_is_not_paged(self):
        r, state, _ = reconnector(FakeCtl(trusted=False))
        r.step()
        assert state["profiles"] == []

    def test_nothing_paired_means_a_long_wait(self):
        assert pl.Reconnector(FakeCtl(paired="")).step() == pl.BACKOFF_MAX_S


class TestCallControl:
    def test_rfcomm_path_matches_bluealsa(self):
        assert cc.rfcomm_path(PHONE) == "/org/bluealsa/hci0/dev_11_22_33_44_55_66/rfcomm"

    def test_final_result_codes(self):
        assert cc.final_result(["+CLCC: 1,1,0,0,0", "OK"]) == "OK"
        assert cc.final_result(["ERROR"]) == "ERROR"
        assert cc.final_result(["+CME ERROR: 3"]) == "+CME ERROR: 3"
        assert cc.final_result(["+CIEV: 1,1"]) is None

    def test_bluealsa_43_relays_a_bare_result_code_with_a_colon(self):
        # Seen on the Pi, 2026-09-24: AT+CIND? -> "+CIND: 0,0,1,5,0,3,0", ":OK"
        assert cc.final_result(["+CIND: 0,0,1,5,0,3,0", ":OK"]) == "OK"
        assert cc.final_result([":ERROR"]) == "ERROR"

    def test_the_wheel_actions_map_to_standard_at_commands(self):
        assert cc.COMMANDS["answer"] == "ATA"
        assert cc.COMMANDS["hangup"] == "AT+CHUP"

    def test_send_collects_lines_until_the_final_result(self):
        import socket
        ours, phone = socket.socketpair()
        phone.sendall(b"\r\n+CLCC: 1,1,0,0,0\r\n\r\nOK\r\n")
        lines = cc.send(ours.fileno(), "AT+CLCC", timeout=1)
        assert phone.recv(64) == b"AT+CLCC\r"
        assert lines == ["+CLCC: 1,1,0,0,0", "OK"]
        ours.close()
        phone.close()
