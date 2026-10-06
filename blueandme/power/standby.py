"""Standby while parked: the Pi stays up, lean, and wakes with the car.

Decided 2026-09-25 (owner): parked, the Pi stays on in standby for up to 72
hours, then halts; it wakes on CAN traffic and on the ignition, both sensed so
that either can be relied on later. The power latch is shelved. A running Pi answers the Body
Computer's PROXI check in milliseconds, which a cold boot cannot (gate D3), and
gets the phone back far sooner.

Measured on the bench (experiments/standby/, FNB58 at the Pi's 5 V input):

    working, phone connected       1.01 W
    parked, as before standby      1.00 W   (paging the phone every 20 s)
    standby: Bluetooth, Wi-Fi off,
      CPU powersave, LED off       0.63 W   about 65 mA from the car at 12.4 V
    halted                         0.22 W   and only a cold boot gets it back
    wake: CAN frame -> music 8.4 s, calls 9.1 s  (cold power-on: 33.5 / 34.2 s)

72 h of standby is about 4.7 Ah, some 7 % of a 70 Ah battery. At the end of
the hold this service powers off, which on a Zero W is a firmware halt at
about 25 mA from the car (0.6 Ah a day). A halted Zero W boots again when GPIO3
(header pin 5) is pulled low, so the ignition sense goes on GPIO3: key on is a
cold boot. If a power latch is ever fitted (``poweroff_gpio``,
dtoverlay=gpio-poweroff), the kernel raises that line once halted and the latch
cuts the rest. ``--bench`` runs only log the halt.

The policy is armed only once a car has been seen (a frame from a bus, or the
ignition on), so a Pi on the bench, where the bus is always quiet, never goes
lean and never switches its own Wi-Fi off. Frames the Pi sent itself, and
frames looped back by the controller (preflight.sh), do not arm or wake it.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Callable

log = logging.getLogger("blueandme.standby")


class State(Enum):
    AWAKE = "awake"
    STANDBY = "standby"
    HOLD_OVER = "hold over"


class Action(Enum):
    ENTER = "enter standby"
    WAKE = "wake"
    POWER_OFF = "power off"


class StandbyPolicy:
    """When to go lean, when to wake, when the hold is over. No side effects."""

    def __init__(self, quiet_s: float, hold_s: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.quiet_s, self.hold_s, self.clock = quiet_s, hold_s, clock
        self.state = State.AWAKE
        self.armed = False
        self.ignition_on = False
        self.last_activity = clock()
        self.standby_since: float | None = None

    def on_frame(self) -> "Action | None":
        """A frame from the car's bus."""
        self.last_activity = self.clock()
        if not self.armed:
            self.armed = True
            log.info("car seen on CAN; standby armed")
        return self._wake("CAN traffic")

    def on_ignition(self, on: bool) -> "Action | None":
        if on == self.ignition_on:
            return None
        self.ignition_on = on
        self.last_activity = self.clock()
        log.info("ignition %s", "on" if on else "off")
        if on:
            self.armed = True
            return self._wake("ignition")
        return None

    def tick(self) -> "Action | None":
        now = self.clock()
        if self.state is State.AWAKE:
            if (self.armed and not self.ignition_on
                    and now - self.last_activity >= self.quiet_s):
                self.state, self.standby_since = State.STANDBY, now
                return Action.ENTER
        elif self.state is State.STANDBY:
            if now - self.standby_since >= self.hold_s:
                self.state = State.HOLD_OVER
                return Action.POWER_OFF
        return None

    def _wake(self, why: str) -> "Action | None":
        if self.state is State.AWAKE:
            return None
        log.info("waking: %s", why)
        self.state, self.standby_since = State.AWAKE, None
        return Action.WAKE


# -- the system: what going lean and waking up actually switch --------------

GOVERNOR = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
ACT_LED = Path("/sys/class/leds/ACT")


def _run(*cmd: str, timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s: %s", cmd[0], exc)
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _write(path: Path, value: str) -> None:
    try:
        path.write_text(value)
    except OSError as exc:
        log.warning("%s: %s", path, exc)


def _led_trigger() -> "str | None":
    """The LED's current mode: the name sysfs shows in brackets."""
    try:
        m = re.search(r"\[([^\]]+)\]", (ACT_LED / "trigger").read_text())
    except OSError:
        return None
    return m.group(1) if m else None


class System:
    def __init__(self) -> None:
        # What wake() gives the LED back: the mode enter() found. The Zero W
        # boots with "actpwr" (lit, dark on card access); "mmc0" (dark, lit on
        # card access) only stands in when this service starts already lean.
        self.led_trigger = "mmc0"

    def enter(self) -> None:
        log.info("standby: reconnect stopped, Bluetooth off, Wi-Fi off, CPU powersave, LED off")
        _run("systemctl", "stop", "blueandme-bt-reconnect.service")
        _run("bluetoothctl", "power", "off")
        _run("nmcli", "radio", "wifi", "off")
        _write(GOVERNOR, "powersave")
        found = _led_trigger()
        if found and found != "none":
            self.led_trigger = found
        _write(ACT_LED / "trigger", "none")
        _write(ACT_LED / "brightness", "0")

    def wake(self) -> None:
        start = time.monotonic()
        _write(GOVERNOR, "ondemand")
        _write(ACT_LED / "trigger", self.led_trigger)
        _run("bluetoothctl", "power", "on")
        page_phone()
        log.info("awake: phone paged %.1f s after the wake", time.monotonic() - start)
        _run("systemctl", "start", "blueandme-bt-reconnect.service")
        _run("nmcli", "radio", "wifi", "on")  # last, so it does not compete

    def power_off(self) -> None:
        log.info("hold over: halting; the ignition on GPIO3 or a power cycle starts it again")
        _run("systemctl", "poweroff")


def page_phone(tries: int = 5) -> None:
    """Page the paired, trusted phone by profile, music then calls, as
    blueandme-bt-firstpage does (phone_link.py says why), without its wait
    for bluealsa: bluealsa stayed registered while Bluetooth was off."""
    from ..media.phone_link import PHONE_UUIDS, btctl, parse_devices, parse_info

    for addr in parse_devices(btctl("devices", "Paired")):
        if not parse_info(btctl("info", addr)).get("Trusted"):
            continue
        path = "/org/bluez/hci0/dev_" + addr.replace(":", "_")
        for kind in ("A2DP", "HFP"):
            for _ in range(tries):
                r = _run("busctl", "call", "--timeout=20", "org.bluez", path,
                         "org.bluez.Device1", "ConnectProfile", "s", PHONE_UUIDS[kind])
                if r.returncode == 0:
                    log.info("%s: %s connected", addr, kind)
                    break
                # Just powered on (not ready) or busy with the other profile.
                if not any(w in r.stderr for w in ("busy", "not ready", "NotReady")):
                    log.info("%s: %s: %s", addr, kind, r.stderr.strip())
                    break
                time.sleep(1)
        return


# -- inputs -----------------------------------------------------------------

class CanActivity:
    """Frames from the car's bus, by the interface's receive counter.

    Read twice a second, the counter costs nothing, where a socket would wake
    Python for every one of the bus's frames. Frames this Pi sends are counted
    as sent, not received, so once the app answers the Body Computer its own
    replies do not keep the Pi awake. Frames the controller loops back
    (preflight.sh) are counted as received, so a change seen while can0 is in
    loopback does not count unless ``accept_loopback`` (simulations).
    """

    def __init__(self, interface: str, accept_loopback: bool = False,
                 sysfs: Path = Path("/sys/class/net")) -> None:
        self.interface, self.accept_loopback = interface, accept_loopback
        self.counter = sysfs / interface / "statistics" / "rx_packets"
        self.last = self._read()

    def _read(self) -> "int | None":
        try:
            return int(self.counter.read_text())
        except (OSError, ValueError):
            return None  # can0 not there (yet)

    def changed(self, check_loopback: bool = True) -> bool:
        """Whether frames arrived since the last call."""
        now = self._read()
        moved = now is not None and self.last is not None and now != self.last
        if now is not None:
            self.last = now
        if not moved:
            return False
        return self.accept_loopback or not check_loopback or not self.in_loopback()

    def in_loopback(self) -> bool:
        out = _run("ip", "-details", "link", "show", self.interface, timeout=5).stdout
        return re.search(r"can <[^>]*LOOPBACK", out) is not None


class Gpio:
    """One BCM input line, through libgpiod (python3-libgpiod, v1 API)."""

    def __init__(self, number: int) -> None:
        import gpiod

        self.line = gpiod.Chip("gpiochip0").get_line(number)
        self.line.request(consumer="blueandme-standby", type=gpiod.LINE_REQ_DIR_IN)

    def get(self) -> int:
        return self.line.get_value()


# -- the service --------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    from ..config import load

    ap = argparse.ArgumentParser(description="Standby while parked, wake with the car.")
    ap.add_argument("--config", help="config file (default: /etc/blueandme/config.yaml)")
    ap.add_argument("--bench", action="store_true",
                    help="count frames the controller loops back, and never halt (simulations)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    cfg = load(args.config)
    p = cfg.power
    if not p.standby:
        log.info("standby disabled in the config")
        return 0

    policy = StandbyPolicy(p.standby_after_quiet_s, p.standby_hold_h * 3600)
    system = System()
    can = CanActivity(cfg.can.interface, accept_loopback=args.bench)
    ignition = Gpio(p.ignition_gpio) if p.ignition_gpio is not None else None
    log.info("standby after %d s quiet, hold %g h then halt; ignition %s; power latch %s",
             p.standby_after_quiet_s, p.standby_hold_h,
             f"GPIO{p.ignition_gpio}" if ignition else "not wired",
             f"on GPIO{p.poweroff_gpio}" if p.poweroff_gpio is not None else "not wired")
    if p.ignition_gpio != 3 and p.poweroff_gpio is None:
        log.warning("ignition not on GPIO3: after the hold only a power cycle starts the Pi")

    running = True

    def _stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while running:
        actions = []
        # Loopback matters only for arming and for waking; awake and armed, a
        # looped-back frame just counts as activity, which does no harm.
        if can.changed(check_loopback=not policy.armed or policy.state is not State.AWAKE):
            actions.append(policy.on_frame())
        if ignition is not None:
            level = ignition.get()
            actions.append(policy.on_ignition(bool(level) != p.ignition_active_low))
        actions.append(policy.tick())
        for action in filter(None, actions):
            if action is Action.ENTER:
                system.enter()
            elif action is Action.WAKE:
                system.wake()
            elif action is Action.POWER_OFF:
                if args.bench:
                    log.warning("hold of %g h over; bench run: not halting, staying in standby",
                                p.standby_hold_h)
                else:
                    system.power_off()
        time.sleep(0.5)
    if policy.state is not State.AWAKE and os.geteuid() == 0:
        system.wake()  # stopped in standby: leave the Pi usable
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
