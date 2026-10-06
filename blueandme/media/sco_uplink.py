"""Mic to phone for calls: the uplink half of hands-free.

``bluealsa-aplay --profile-sco`` plays the phone's side of a call to the sound
card. Nothing in bluealsa 4.0 sends the other direction, so this does. It
records the card's mic and writes it to bluealsa's SCO playback PCM for the
phone.

**The PCM is held open for as long as the phone's hands-free link exists, not
just during a call.** bluealsa 4.0 accepts an incoming SCO (call audio) link
from the phone and then releases it within milliseconds unless some client
already has one of that phone's SCO PCMs open. The phone then falls back to its
earpiece. On the bench (2026-09-24) the phone retried every few seconds and
never kept the call on Bluetooth, until the PCM was held open beforehand.
Holding it open does not make the Pi open a SCO link itself: outside a call
there is none.

What exists comes from bluealsa's own PCM list (``GetPCMs`` over D-Bus, read
with ``busctl``). An ``HFP``/``HSP`` PCM in ``sink`` mode is the one to write
to; its ``Sampling`` gives the rate. That is 8 kHz CVSD, the only hands-free
codec in Bookworm's bluealsa 4.0.

**Mic audio is relayed, not piped.** Outside a call, bluealsa takes nothing
from the PCM. A plain ``arecord | aplay`` then fills the pipe with up to 64 KiB
of stale mic audio (4 s at 8 kHz), and the next call runs that far behind; the
owner heard the delay on 2026-09-24. So this process sits between the two and
drops mic audio whenever more than ``MAX_BACKLOG_S`` is already waiting. The
delay therefore cannot build up, before or during a call. ``alsaloop`` was tried
instead, and quits with "Poll FD initialization failed" on bluealsa's PCM.

**Clock drift is absorbed by trimming samples.** The card's clock and the
phone's Bluetooth clock differ by a fraction of a percent. On 2026-09-24 the
card delivered about 9.97 s of audio for every 10 s the phone took, so aplay
ran dry about every 18 s, which was one short glitch each time. The mic is
therefore recorded ``OVERSAMPLE`` fast, which guarantees a slight surplus.
Whenever more than ``TARGET_BACKLOG_S`` is waiting, the relay removes a couple
of single samples from the chunk it is about to send. Removing one sample in
about 80 is inaudible in speech, where a buffer running dry is not.
"""

from __future__ import annotations

import argparse
import array
import fcntl
import json
import logging
import os
import re
import signal
import struct
import subprocess
import termios
import threading
import time
import warnings
from typing import Callable, NamedTuple, Optional

with warnings.catch_warnings():
    # audioop is C and fast on the Zero W; deprecated in 3.11, gone in 3.13,
    # where apply_gain falls back to a slower pure-Python loop.
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop as _audioop
    except ImportError:  # pragma: no cover - Python 3.13+
        _audioop = None

log = logging.getLogger("blueandme.sco_uplink")

#: After the pipeline dies with the phone still there, wait this long to restart.
RETRY_S = 2.0
#: Mic audio is moved in chunks of this length...
CHUNK_S = 0.02
#: ...and dropped whole once this much is already waiting for aplay. That should
#: only happen outside calls, when bluealsa takes nothing. At 80 ms, mSBC's
#: bursty 16 kHz consumption still hit it: 20-40 ms dropped per 10 s during a
#: call (2026-09-24). The drift trim keeps the steady level near the target, so
#: a higher ceiling adds no steady delay.
MAX_BACKLOG_S = 0.12
#: During a call, keep about this much waiting: enough that aplay never runs
#: dry, little enough to add no noticeable delay.
TARGET_BACKLOG_S = 0.03
#: Record this much faster than the SCO rate, so there is always a surplus to
#: trim rather than a shortfall to hear.
OVERSAMPLE = 0.005
#: Samples trimmed from a 20 ms chunk when above the target, and when far above.
TRIM_SAMPLES = 2
TRIM_SAMPLES_FAST = 4
FAR_ABOVE_S = 0.02
#: The ALSA buffers either side: arecord on the card, aplay on the SCO PCM.
#: 40 ms and 60 ms (2026-09-24) gave no delay but dropouts: arecord overran on
#: the busy single core, and aplay underran every 6-10 s. A capture buffer does
#: not add delay (audio is read out a period at a time); the playback one does.
REC_BUFFER_US = 100000
PLAY_BUFFER_US = 120000
#: During a call, log how much mic audio was sent and dropped this often.
STATS_S = 10.0
#: While this file exists the mic is muted: silence is sent instead (the wheel's
#: mute button during a call, media/wheel_control.py). Checked this often.
MUTE_FLAG = "/run/blueandme/mic-mute"
MUTE_CHECK_S = 0.25
#: The smallest pipe Linux allows (one page), so little can wait there anyway.
PIPE_BYTES = 4096
_F_SETPIPE_SZ = 1031  # fcntl.F_SETPIPE_SZ, missing from older Pythons


def backlog(fd: int) -> int:
    """Bytes written to a pipe and not yet read (FIONREAD works on either end)."""
    return struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, b"\0\0\0\0"))[0]


def apply_gain(data: bytes, gain_db: float) -> bytes:
    """Scale S16 samples by ``gain_db``, saturating rather than wrapping."""
    if not gain_db:
        return data
    factor = 10 ** (gain_db / 20)
    if _audioop is not None:
        return _audioop.mul(data, 2, factor)
    samples = array.array("h", data)
    for i, v in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(v * factor)))
    return samples.tobytes()


def trim_count(waiting: int, target: int, far: int) -> int:
    """Samples to trim from the next chunk, given the bytes already waiting."""
    if waiting > target + far:
        return TRIM_SAMPLES_FAST
    if waiting > target:
        return TRIM_SAMPLES
    return 0


def trim(data: bytes, n: int) -> bytes:
    """Remove ``n`` single S16 samples from ``data``, spread evenly across it."""
    samples = len(data) // 2
    if n <= 0 or samples <= n:
        return data
    step = samples // (n + 1)
    out, start = [], 0
    for k in range(1, n + 1):
        cut = k * step
        out.append(data[start * 2:cut * 2])
        start = cut + 1
    out.append(data[start * 2:])
    return b"".join(out)


def relay_chunk(data: bytes, fd: int, max_backlog: int) -> bool:
    """Write ``data`` to the non-blocking pipe ``fd`` unless that would leave
    more than ``max_backlog`` bytes waiting. Returns False when it is dropped."""
    if backlog(fd) + len(data) > max_backlog:
        return False
    try:
        os.write(fd, data)
    except BlockingIOError:
        return False
    return True

#: The bluealsa ALSA plugin in Bookworm is built with debug output, and aplay
#: dumps hw params on errors. Neither is worth a journal line.
_NOISE = re.compile(
    r"^(D: |(ACCESS|FORMAT|SUBFORMAT|SAMPLE_BITS|FRAME_BITS|CHANNELS|RATE"
    r"|PERIOD_TIME|PERIOD_SIZE|PERIOD_BYTES|PERIODS|BUFFER_TIME|BUFFER_SIZE"
    r"|BUFFER_BYTES|TICK_TIME):\s)"
)

#: bluealsa 4.0 (Debian Bookworm) lists PCMs with Manager1.GetPCMs. 4.1 and
#: later dropped that for the standard ObjectManager.GetManagedObjects, which
#: nests the properties one level deeper, under the interface name.
GET_OBJECTS = ["busctl", "--json=short", "call", "org.bluealsa", "/org/bluealsa",
               "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"]
GET_PCMS = ["busctl", "--json=short", "call", "org.bluealsa", "/org/bluealsa",
            "org.bluealsa.Manager1", "GetPCMs"]
PCM_IFACE = "org.bluealsa.PCM1"


class Target(NamedTuple):
    address: str   # the phone, AA:BB:CC:DD:EE:FF
    rate: int      # sampling rate of its SCO PCM


def _address(device_path: str) -> str:
    """/org/bluez/hci0/dev_11_22_33_44_55_66 -> 11:22:33:44:55:66"""
    return device_path.rsplit("/dev_", 1)[-1].replace("_", ":").upper()


def _pcm_props(entry: dict) -> "dict | None":
    """PCM properties from a GetPCMs entry, or from a GetManagedObjects entry
    (where they sit under the interface name). None for non-PCM objects."""
    if PCM_IFACE in entry:
        return entry[PCM_IFACE]
    if any(key.startswith("org.") for key in entry):
        return None  # a GetManagedObjects entry without a PCM, e.g. rfcomm
    return entry


def hfp_sinks(pcms_json: str) -> list[Target]:
    """Phones with a hands-free PCM we can write the mic to, from GetPCMs or
    GetManagedObjects."""
    try:
        objects = json.loads(pcms_json)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    found = []
    for entry in objects.values():
        props = _pcm_props(entry)
        if props is None:
            continue
        value = {k: v.get("data") for k, v in props.items()}
        transport = str(value.get("Transport", ""))
        if value.get("Mode") == "sink" and transport.startswith(("HFP", "HSP")):
            found.append(Target(_address(str(value.get("Device", ""))),
                                int(value.get("Sampling") or value.get("Rate") or 8000)))
    return sorted(found)


def phone_profiles(pcms_json: str) -> dict[str, set[str]]:
    """Which audio profiles bluealsa has a PCM for, per phone: ``"A2DP"`` for
    music, ``"HFP"`` for calls."""
    try:
        objects = json.loads(pcms_json)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}
    found: dict[str, set[str]] = {}
    for entry in objects.values():
        props = _pcm_props(entry)
        if props is None:
            continue
        value = {k: v.get("data") for k, v in props.items()}
        transport = str(value.get("Transport", ""))
        kind = ("A2DP" if transport.startswith("A2DP")
                else "HFP" if transport.startswith(("HFP", "HSP")) else None)
        if kind:
            found.setdefault(_address(str(value.get("Device", ""))), set()).add(kind)
    return found


def read_pcms() -> str:
    for cmd in (GET_OBJECTS, GET_PCMS):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out.strip():
            return out
    return ""


class Pipeline:
    """``arecord`` from the card's mic, relayed into ``aplay`` on the phone's SCO PCM."""

    def __init__(self, target: Target, capture_pcm: str, gain_db: float = 0.0) -> None:
        self.target = target
        self.gain_db = gain_db
        fmt = ["-q", "-f", "S16_LE", "-c", "1", "-t", "raw"]
        capture_rate = round(target.rate * (1 + OVERSAMPLE))
        self.rec = subprocess.Popen(
            ["arecord", "-D", capture_pcm, "-B", str(REC_BUFFER_US),
             "-r", str(capture_rate), *fmt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.play = subprocess.Popen(
            ["aplay", "-D", f"bluealsa:DEV={target.address},PROFILE=sco",
             "-B", str(PLAY_BUFFER_US), "-r", str(target.rate), *fmt],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert self.rec.stdout is not None and self.play.stdin is not None
        self._dst = self.play.stdin.fileno()
        try:
            fcntl.fcntl(self._dst, _F_SETPIPE_SZ, PIPE_BYTES)
        except OSError:
            pass
        os.set_blocking(self._dst, False)
        frame = 2  # S16_LE mono
        self._chunk = int(capture_rate * CHUNK_S) * frame
        self._max_backlog = int(target.rate * MAX_BACKLOG_S) * frame
        self._target = int(target.rate * TARGET_BACKLOG_S) * frame
        self._far = int(target.rate * FAR_ABOVE_S) * frame
        self._bytes_per_s = target.rate * frame
        self.dropped = 0
        self.xruns = 0
        self._threads = [
            threading.Thread(target=self._relay, daemon=True),
            threading.Thread(target=self._watch, args=("arecord", self.rec.stderr),
                             daemon=True),
            threading.Thread(target=self._watch, args=("aplay", self.play.stderr),
                             daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _watch(self, name: str, stream) -> None:
        """Count xruns, and log what arecord and aplay say, minus the bluealsa
        plugin's debug lines and hw-params dumps."""
        for raw in iter(stream.readline, b""):
            line = raw.decode(errors="replace").rstrip()
            if "underrun" in line or "overrun" in line:
                self.xruns += 1
            elif line and not _NOISE.match(line):
                log.warning("%s: %s", name, line)

    def _relay(self) -> None:
        assert self.rec.stdout is not None
        sent = dropped = trimmed = 0
        xruns_seen = 0
        window = time.monotonic()
        muted, mute_checked = False, 0.0
        while True:
            data = self.rec.stdout.read(self._chunk)
            if not data:
                return  # arecord ended
            now = time.monotonic()
            if now - mute_checked >= MUTE_CHECK_S:
                was, muted, mute_checked = muted, os.path.exists(MUTE_FLAG), now
                if muted != was:
                    log.info("mic %s", "muted" if muted else "on")
            data = bytes(len(data)) if muted else apply_gain(data, self.gain_db)
            try:
                waiting = backlog(self._dst)
                n = trim_count(waiting, self._target, self._far)
                if n:
                    data = trim(data, n)
                    trimmed += n
                if relay_chunk(data, self._dst, self._max_backlog):
                    sent += len(data)
                else:
                    dropped += len(data)
                    self.dropped += len(data)
            except OSError:
                return  # aplay ended
            now = time.monotonic()
            if now - window >= STATS_S:
                # Outside a call almost everything is dropped; log only calls.
                if sent > self._bytes_per_s * STATS_S / 2:
                    log.info("uplink: %.2f s of mic sent, %.2f s dropped, "
                             "%d samples trimmed, %d xruns in %.0f s",
                             sent / self._bytes_per_s, dropped / self._bytes_per_s,
                             trimmed, self.xruns - xruns_seen, now - window)
                sent = dropped = trimmed = 0
                xruns_seen = self.xruns
                window = now

    def alive(self) -> bool:
        return (self.rec.poll() is None and self.play.poll() is None
                and self._threads[0].is_alive())

    def stop(self) -> None:
        for proc in (self.play, self.rec):
            if proc.poll() is None:
                proc.terminate()
        for proc in (self.play, self.rec):
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        for t in self._threads:
            t.join(timeout=2)


class Uplink:
    """Keeps one pipeline open for as long as a phone's hands-free PCM exists."""

    def __init__(
        self,
        capture_pcm: str = "default",
        pcms: Callable[[], str] = read_pcms,
        start: Callable[..., Pipeline] = Pipeline,
        clock: Callable[[], float] = time.monotonic,
        gain_db: float = 0.0,
    ) -> None:
        self.capture_pcm = capture_pcm
        self.gain_db = gain_db
        self.pcms = pcms
        self.start = start
        self.clock = clock
        self.active: Optional[Pipeline] = None
        self._retry_at = 0.0

    def step(self) -> None:
        targets = hfp_sinks(self.pcms())
        want = targets[0] if targets else None
        cur = self.active
        if cur is not None and cur.target != want:
            log.info("hands-free link to %s gone; uplink stopped", cur.target.address)
            cur.stop()
            self.active = None
        elif cur is not None and not cur.alive():
            log.warning("uplink to %s exited; retrying", cur.target.address)
            cur.stop()
            self.active = None
            self._retry_at = self.clock() + RETRY_S
        if want is not None and self.active is None and self.clock() >= self._retry_at:
            log.info("hands-free link to %s; holding its call PCM open (%d Hz, %+.0f dB)",
                     want.address, want.rate, self.gain_db)
            self.active = self.start(want, self.capture_pcm, self.gain_db)

    def close(self) -> None:
        if self.active is not None:
            self.active.stop()
            self.active = None


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="Send the sound card's mic to the phone for hands-free calls."
    )
    ap.add_argument("--capture-pcm", default="default",
                    help="ALSA capture device (default: %(default)s)")
    # Each check starts busctl, which on the Zero W's single core took long
    # enough to starve arecord and aplay once a second (2026-09-24). The PCM is
    # held from connection, not from the call, so a slow check costs nothing.
    # Hardware gain tops out at +23 dB. Headphones used as a bench mic needed
    # about +18 dB more (2026-09-24); the car's amplified mic is ~30 dB hotter
    # and will want 0 dB or less.
    ap.add_argument("--gain-db", type=float, default=0.0,
                    help="digital gain applied to the mic, dB (default: %(default)s)")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="seconds between checks of bluealsa's PCMs (default: %(default)s)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    uplink = Uplink(capture_pcm=args.capture_pcm, gain_db=args.gain_db)
    running = True

    def _stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("watching bluealsa for hands-free links")
    try:
        while running:
            uplink.step()
            time.sleep(args.interval)
    finally:
        uplink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
