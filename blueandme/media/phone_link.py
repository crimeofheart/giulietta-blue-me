"""Keeping the phone connected, and pairing a new one.

**Reconnect.** A phone reconnects to a car kit by itself when *its own*
Bluetooth comes on (owner, 2026-09-24). It does not reconnect when the kit
comes up after it, which is exactly the car's case: the phone's Bluetooth is
already on when the Pi boots. On the bench, after a reboot, nothing connected
until the owner tapped "blueandme" on the phone. So the Pi pages every paired,
trusted phone that is not connected, backing off while none answers.

**Pair.** While the app is listen-only, nothing on the Pi answers a pairing
request. ``blueandme-pair`` opens pairing for a set time with a Just Works agent
(``bt-agent -c NoInputNoOutput``, from bluez-tools), trusts each phone that
pairs, and closes pairing again. The phone must pair after the hands-free
profile is registered, or Android will not offer "Phone calls" for the Pi.
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import subprocess
import time
from typing import Callable

from .sco_uplink import phone_profiles, read_pcms

log = logging.getLogger("blueandme.phone_link")

_DEVICE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\b", re.MULTILINE)
_FLAG = re.compile(r"^\s*(Paired|Trusted|Connected):\s*(yes|no)\s*$", re.MULTILINE)

#: Seconds between attempts while no phone answers, doubling up to the maximum.
#: The maximum is how long a phone can sit in the car unconnected, since it
#: never reconnects by itself; an attempt only costs the radio while no phone
#: is connected, so there is no call audio to disturb.
BACKOFF_MIN_S = 5.0
BACKOFF_MAX_S = 20.0
#: Seconds between checks while a phone is connected.
CONNECTED_CHECK_S = 15.0
#: Seconds between checks while waiting: for bluealsa, or for a phone's profiles.
SHORT_CHECK_S = 1.0

#: What the Pi offers, listed by the adapter once bluealsa has registered it:
#: the music sink and the hands-free unit. Paged at boot before bluealsa had
#: registered them, the phone came up with neither, Android did not make the Pi
#: its audio device, and calls only worked after a tap on the phone, 88 s
#: later (2026-09-25).
LOCAL_UUIDS = ("0000110b-0000-1000-8000-00805f9b34fb",   # Audio Sink
               "0000111e-0000-1000-8000-00805f9b34fb")   # Handsfree
#: The phone's end of each profile, to connect one on its own.
PHONE_UUIDS = {"A2DP": "0000110a-0000-1000-8000-00805f9b34fb",   # Audio Source
               "HFP": "0000111f-0000-1000-8000-00805f9b34fb"}    # Handsfree Audio Gateway
#: Seconds a connected phone may lack a profile before the Pi connects it.
PROFILE_GRACE_S = 2.0


def parse_devices(bluetoothctl_devices: str) -> list[str]:
    """Addresses from ``bluetoothctl devices [Paired|Connected]``."""
    return [a.upper() for a in _DEVICE.findall(bluetoothctl_devices)]


def parse_info(bluetoothctl_info: str) -> dict[str, bool]:
    """The Paired / Trusted / Connected flags from ``bluetoothctl info``."""
    return {k: v == "yes" for k, v in _FLAG.findall(bluetoothctl_info)}


def btctl(*args: str, timeout: float = 20) -> str:
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True,
                              text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def adapter_ready(bluetoothctl_show: str) -> bool:
    """Whether bluealsa has registered both of its profiles with BlueZ."""
    text = bluetoothctl_show.lower()
    return all(uuid in text for uuid in LOCAL_UUIDS)


def connect_profile(address: str, uuid: str) -> bool:
    path = "/org/bluez/hci0/dev_" + address.replace(":", "_")
    try:
        return subprocess.run(
            ["busctl", "call", "--timeout=20", "org.bluez", path,
             "org.bluez.Device1", "ConnectProfile", "s", uuid],
            capture_output=True, timeout=25).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Reconnector:
    """Pages paired, trusted phones until one is connected, with music and calls."""

    def __init__(self, ctl: Callable[..., str] = btctl,
                 pcms: Callable[[], str] = read_pcms,
                 profile: Callable[[str, str], bool] = connect_profile,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ctl, self.pcms, self.profile, self.clock = ctl, pcms, profile, clock
        self.backoff = BACKOFF_MIN_S
        self.waiting_logged = False
        self.incomplete_since: dict[str, float] = {}
        self.tried: set[tuple[str, str]] = set()
        self.complete: set[str] = set()

    def step(self) -> float:
        """One round. Returns the seconds to wait before the next."""
        paired = parse_devices(self.ctl("devices", "Paired"))
        if not paired:
            return BACKOFF_MAX_S
        info = {addr: parse_info(self.ctl("info", addr)) for addr in paired}
        connected = [addr for addr, i in info.items() if i.get("Connected")]
        if connected:
            self.backoff = BACKOFF_MIN_S
            return self._check_profiles(connected)
        self.incomplete_since.clear()
        self.tried.clear()
        self.complete.clear()
        if not adapter_ready(self.ctl("show")):
            if not self.waiting_logged:
                log.info("waiting for bluealsa to register music and calls before paging")
                self.waiting_logged = True
            return SHORT_CHECK_S
        for addr, i in info.items():
            if not i.get("Trusted"):
                continue
            log.info("paging %s", addr)
            # By profile, music then calls: a plain connect (Device1.Connect)
            # opened by the Pi brought up the link with neither, for 40 s and
            # more; by name both were up in 2.1 s (2026-09-25).
            music = self.profile(addr, PHONE_UUIDS["A2DP"])
            if not music and not parse_info(self.ctl("info", addr)).get("Connected"):
                continue  # not in range: no second page timeout for calls
            calls = self.profile(addr, PHONE_UUIDS["HFP"])
            if music or calls:
                log.info("connected to %s (music %s, calls %s)", addr,
                         "yes" if music else "no", "yes" if calls else "no")
                self.backoff = BACKOFF_MIN_S
                return SHORT_CHECK_S  # and check both are up
        wait = self.backoff
        self.backoff = min(self.backoff * 2, BACKOFF_MAX_S)
        return wait

    def _check_profiles(self, connected: list[str]) -> float:
        """A connected phone should have both music and calls. One missing for
        longer than the grace period is connected by hand, once per link."""
        have = phone_profiles(self.pcms())
        now = self.clock()
        wait = CONNECTED_CHECK_S
        for addr in connected:
            missing = set(PHONE_UUIDS) - have.get(addr, set())
            if not missing:
                self.incomplete_since.pop(addr, None)
                if addr not in self.complete:
                    log.info("%s: music and calls connected", addr)
                    self.complete.add(addr)
                continue
            self.complete.discard(addr)
            since = self.incomplete_since.setdefault(addr, now)
            if now - since < PROFILE_GRACE_S:
                wait = min(wait, SHORT_CHECK_S)
                continue
            for kind in sorted(missing):
                if (addr, kind) in self.tried:
                    continue
                self.tried.add((addr, kind))
                log.info("%s is connected without %s; connecting it", addr, kind)
                if not self.profile(addr, PHONE_UUIDS[kind]):
                    log.warning("%s refused %s (is it switched off for this Pi on "
                                "the phone?)", addr, kind)
                wait = min(wait, SHORT_CHECK_S)
        return wait


def _run_forever(step: Callable[[], float]) -> int:
    running = True

    def _stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while running:
        wait = step()
        end = time.monotonic() + wait
        while running and time.monotonic() < end:
            time.sleep(0.5)
    return 0


def reconnect_main(argv: "list[str] | None" = None) -> int:
    argparse.ArgumentParser(
        description="Page paired, trusted phones until one is connected."
    ).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    log.info("keeping a paired phone connected")
    return _run_forever(Reconnector().step)


def new_pairings(before: set[str], now: list[str]) -> list[str]:
    return [a for a in now if a not in before]


def pair_main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open pairing for a while, trust each phone that pairs, then close it."
    )
    ap.add_argument("--seconds", type=int, default=120,
                    help="how long pairing stays open (default: %(default)s)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    agent = subprocess.Popen(["bt-agent", "-c", "NoInputNoOutput"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    before = set(parse_devices(btctl("devices", "Paired")))
    btctl("discoverable-timeout", str(args.seconds))
    btctl("pairable", "on")
    btctl("discoverable", "on")
    name = re.search(r"Alias:\s*(.+)", btctl("show"))
    log.info("pairing open for %d s: pair the phone with %r", args.seconds,
             name.group(1).strip() if name else "this Pi")

    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    end = time.monotonic() + args.seconds
    paired: list[str] = []
    try:
        while not stop and time.monotonic() < end:
            for addr in new_pairings(before, parse_devices(btctl("devices", "Paired"))):
                btctl("trust", addr)
                before.add(addr)
                paired.append(addr)
                log.info("paired and trusted %s", addr)
            time.sleep(2)
    finally:
        btctl("discoverable", "off")
        btctl("pairable", "off")
        agent.terminate()
        try:
            agent.wait(timeout=5)
        except subprocess.TimeoutExpired:
            agent.kill()
        log.info("pairing closed; %d new phone(s)", len(paired))
    return 0
