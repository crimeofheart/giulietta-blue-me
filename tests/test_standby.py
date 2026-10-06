"""Standby while parked: the policy, and bus activity from the receive counter."""

import shlex
import subprocess

from blueandme.power import standby as sb

QUIET, HOLD = 60, 72 * 3600


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def policy():
    clock = Clock()
    return sb.StandbyPolicy(QUIET, HOLD, clock=clock), clock


class TestPolicy:
    def test_a_bench_pi_never_goes_lean(self):
        # No car seen: the bus is always quiet on the bench, and standby would
        # switch off the Wi-Fi the bench is reached by.
        p, clock = policy()
        clock.t += 10 * QUIET
        assert p.tick() is None
        assert p.state is sb.State.AWAKE

    def test_quiet_bus_after_a_car_was_seen_means_standby(self):
        p, clock = policy()
        p.on_frame()
        clock.t += QUIET - 1
        assert p.tick() is None
        clock.t += 1
        assert p.tick() is sb.Action.ENTER
        assert p.state is sb.State.STANDBY

    def test_traffic_keeps_it_awake(self):
        p, clock = policy()
        for _ in range(5):
            clock.t += QUIET / 2
            p.on_frame()
            assert p.tick() is None

    def test_a_frame_wakes_it(self):
        p, clock = policy()
        p.on_frame()
        clock.t += QUIET
        p.tick()
        assert p.on_frame() is sb.Action.WAKE
        assert p.state is sb.State.AWAKE

    def test_the_ignition_wakes_it_and_arms_it(self):
        p, clock = policy()
        p.on_ignition(True)
        assert p.armed
        p.on_ignition(False)
        clock.t += QUIET
        assert p.tick() is sb.Action.ENTER
        assert p.on_ignition(True) is sb.Action.WAKE

    def test_ignition_on_blocks_standby_on_a_quiet_bus(self):
        p, clock = policy()
        p.on_frame()
        p.on_ignition(True)
        clock.t += 10 * QUIET
        assert p.tick() is None

    def test_the_hold_ends_in_power_off(self):
        p, clock = policy()
        p.on_frame()
        clock.t += QUIET
        p.tick()
        clock.t += HOLD - 1
        assert p.tick() is None
        clock.t += 1
        assert p.tick() is sb.Action.POWER_OFF
        assert p.state is sb.State.HOLD_OVER

    def test_after_the_hold_a_frame_still_wakes_it(self):
        # A bench run does not halt at the end of the hold: still wakeable.
        p, clock = policy()
        p.on_frame()
        clock.t += QUIET
        p.tick()
        clock.t += HOLD
        p.tick()
        assert p.on_frame() is sb.Action.WAKE

    def test_repeated_ignition_levels_are_not_events(self):
        p, _ = policy()
        assert p.on_ignition(False) is None
        p.on_ignition(True)
        assert p.on_ignition(True) is None


class TestCanActivity:
    @staticmethod
    def counter(tmp_path, value):
        d = tmp_path / "can0" / "statistics"
        d.mkdir(parents=True, exist_ok=True)
        (d / "rx_packets").write_text(f"{value}\n")

    def test_a_moving_receive_counter_is_activity(self, tmp_path, monkeypatch):
        self.counter(tmp_path, 100)
        can = sb.CanActivity("can0", sysfs=tmp_path)
        monkeypatch.setattr(can, "in_loopback", lambda: False)
        assert not can.changed()
        self.counter(tmp_path, 103)
        assert can.changed()
        assert not can.changed()

    def test_loopback_frames_do_not_arm_or_wake(self, tmp_path, monkeypatch):
        # preflight.sh loops frames back through the controller on the bench.
        self.counter(tmp_path, 0)
        can = sb.CanActivity("can0", sysfs=tmp_path)
        monkeypatch.setattr(can, "in_loopback", lambda: True)
        self.counter(tmp_path, 6)
        assert not can.changed(check_loopback=True)
        self.counter(tmp_path, 12)
        assert can.changed(check_loopback=False)  # awake and armed: just activity

    def test_simulations_may_count_loopback(self, tmp_path, monkeypatch):
        self.counter(tmp_path, 0)
        can = sb.CanActivity("can0", accept_loopback=True, sysfs=tmp_path)
        monkeypatch.setattr(can, "in_loopback", lambda: True)
        self.counter(tmp_path, 1)
        assert can.changed()

    def test_no_interface_is_no_activity(self, tmp_path):
        can = sb.CanActivity("can0", sysfs=tmp_path)
        assert not can.changed()
        self.counter(tmp_path, 5)
        assert not can.changed()  # first reading is a baseline, not a change
        self.counter(tmp_path, 6)
        assert can.changed(check_loopback=False)


class TestLed:
    """Lean switches the LED off; the wake gives it back the mode it had."""

    @staticmethod
    def system(tmp_path, monkeypatch, trigger):
        (tmp_path / "trigger").write_text(trigger)
        monkeypatch.setattr(sb, "ACT_LED", tmp_path)
        monkeypatch.setattr(sb, "GOVERNOR", tmp_path / "governor")
        monkeypatch.setattr(sb, "_run", lambda *cmd, **kw: None)
        monkeypatch.setattr(sb, "page_phone", lambda: None)
        return sb.System()

    def test_the_wake_puts_back_the_mode_the_pi_booted_with(self, tmp_path, monkeypatch):
        # 2026-10-02: the wake wrote "mmc0" where the Zero W boots with
        # "actpwr", and the LED went from lit to dark with short pulses.
        system = self.system(tmp_path, monkeypatch, "none timer [actpwr] mmc0\n")
        system.enter()
        assert (tmp_path / "trigger").read_text() == "none"
        assert (tmp_path / "brightness").read_text() == "0"
        system.wake()
        assert (tmp_path / "trigger").read_text() == "actpwr"

    def test_started_already_lean_it_does_not_keep_the_led_off(self, tmp_path, monkeypatch):
        system = self.system(tmp_path, monkeypatch, "[none] timer actpwr mmc0\n")
        system.enter()
        system.wake()
        assert (tmp_path / "trigger").read_text() == "mmc0"


class TestWifiAfterAPowerCut:
    """Standby's Wi-Fi off is saved by NetworkManager and by systemd-rfkill, and
    a power cut in standby leaves both: blueandme-wifi-on forgets them at boot."""

    UNIT = "systemd/blueandme-wifi-on.service"

    def line(self, key):
        with open(self.UNIT) as f:
            return next(l.strip() for l in f if l.startswith(key + "="))[len(key) + 1:]

    def test_it_runs_before_the_two_that_read_the_saved_state(self):
        # On the Pi the radio's block is restored 60 s into the boot, when the
        # chip appears: anything later than this unit has to win a race.
        assert {"systemd-rfkill.socket", "systemd-rfkill.service",
                "NetworkManager.service"} <= set(self.line("Before").split())

    def test_it_resets_both_saved_switches_and_only_the_wifi(self, tmp_path):
        rfkill = tmp_path / "systemd" / "rfkill"
        rfkill.mkdir(parents=True)
        (rfkill / "platform-20300000.mmcnr:wlan").write_text("1\n")
        (rfkill / "platform-soc:bluetooth").write_text("1\n")
        state = tmp_path / "NetworkManager" / "NetworkManager.state"
        state.parent.mkdir()
        state.write_text("[main]\nNetworkingEnabled=true\nWirelessEnabled=false\n")

        sh, flag, script = shlex.split(self.line("ExecStart"))
        assert (sh, flag) == ("/bin/sh", "-c")
        # systemd turns $$ into $; the test points /var/lib at a scratch folder.
        subprocess.run(["sh", "-c", script.replace("$$", "$").replace("/var/lib", str(tmp_path))],
                       check=True)

        assert (rfkill / "platform-20300000.mmcnr:wlan").read_text() == "0\n"
        assert (rfkill / "platform-soc:bluetooth").read_text() == "1\n"
        assert state.read_text() == "[main]\nNetworkingEnabled=true\nWirelessEnabled=true\n"

    def test_a_pi_without_saved_state_is_not_a_failure(self, tmp_path):
        script = shlex.split(self.line("ExecStart"))[2]
        subprocess.run(["sh", "-c", script.replace("$$", "$").replace("/var/lib", str(tmp_path))],
                       check=True, stderr=subprocess.DEVNULL)

    def test_install_copies_and_enables_it(self):
        with open("install.sh") as f:
            assert f.read().count("blueandme-wifi-on.service") == 2
