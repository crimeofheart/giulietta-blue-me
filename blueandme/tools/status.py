"""blueandme-status -- one screen that answers "why isn't it working?".

First command to run in the car, and first command to run when anything
misbehaves. Everything it prints is read directly from the system; nothing is
inferred from configuration alone, because the whole class of bug this catches
is config and reality disagreeing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..can.txgate import CanMode
from ..config import Config, load
from ..protocol.registry import REFERENCE_ONLY, get

OK, WARN, BAD, INFO = "ok", "warn", "BAD", "--"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.problems = 0

    def add(self, status: str, label: str, detail: str = "") -> None:
        if status == BAD:
            self.problems += 1
        self.rows.append((status, label, detail))

    def section(self, title: str) -> None:
        self.rows.append(("", f"__{title}__", ""))

    def render(self) -> str:
        out = []
        for status, label, detail in self.rows:
            if label.startswith("__"):
                out.append(f"\n{label.strip('_')}")
                out.append("-" * len(label.strip("_")))
                continue
            out.append(f"  [{status:^4}] {label:<34} {detail}")
        return "\n".join(out)


def _run(cmd: list[str]) -> str:
    if shutil.which(cmd[0]) is None:
        return ""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=5
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def can_controller_state(ip_details: str) -> "str | None":
    """The controller's state from the ``can ...`` line of ``ip -details``.

    Not the first ``state`` in the output: that is the link's (``state UP``).
    """
    m = re.search(r"^\s*can (?:<[^>]*> )?state (\S+)", ip_details, re.M)
    return m.group(1) if m else None


def can_error_counters(ip_details: str) -> dict[str, int]:
    """The CAN error counters, which ``ip -details -statistics`` prints as a
    header row (``re-started bus-errors arbit-lost ...``) over a row of values."""
    m = re.search(r"^\s*(re-started\s+bus-errors[^\n]*)\n\s*([\d \t]+)", ip_details, re.M)
    if not m:
        return {}
    return dict(zip(m.group(1).split(), (int(v) for v in m.group(2).split())))


def check_can(rep: Report, cfg: Config) -> None:
    rep.section(f"CAN interface: {cfg.can.interface}")
    out = _run(["ip", "-details", "-statistics", "link", "show", cfg.can.interface])
    if not out:
        rep.add(BAD, "interface present", f"{cfg.can.interface} not found. "
                                          "Check dtoverlay=mcp2515-can0 and dmesg.")
        return

    state = "UP" if re.search(r"<[^>]*\bUP\b", out) else "DOWN"
    rep.add(OK if state == "UP" else BAD, "link state", state)

    m = re.search(r"bitrate (\d+)", out)
    if m:
        actual = int(m.group(1))
        match = actual == cfg.can.bitrate
        rep.add(
            OK if match else BAD, "bitrate",
            f"{actual} (config says {cfg.can.bitrate})"
            + ("" if match else "  <- check the MCP2515 crystal vs oscillator="),
        )
    else:
        rep.add(WARN, "bitrate", "not reported by the driver")

    ctrl = re.search(r"(LISTEN-ONLY|listen-only)", out)
    rep.add(
        OK if ctrl or cfg.can.mode is not CanMode.LISTEN_ONLY else WARN,
        "controller listen-only",
        "yes" if ctrl else "no (software gate still blocks transmission)",
    )

    counters = can_error_counters(out)
    errors = counters.get("bus-errors", 0)
    rep.add(
        OK if errors == 0 else BAD, "bus errors", str(errors)
        + ("" if errors == 0 else "  <- bitrate, crystal, termination or CAN-H/L swapped"),
    )
    if counters.get("re-started"):
        rep.add(WARN, "controller restarts", str(counters["re-started"]))

    st = can_controller_state(out)
    if st:
        rep.add(OK if st in ("ERROR-ACTIVE", "STOPPED") else BAD, "controller state", st)

    rx = re.search(r"RX:\s+bytes\s+packets.*?\n\s*(\d+)\s+(\d+)", out, re.S)
    if rx:
        rep.add(
            OK if int(rx.group(2)) else WARN, "frames received", rx.group(2)
            + ("" if int(rx.group(2)) else "  <- nothing on the bus; is the car awake?"),
        )


def check_spi(rep: Report) -> None:
    rep.section("SPI / MCP2515")
    devs = sorted(Path("/dev").glob("spidev*"))
    rep.add(
        OK if devs else BAD, "spidev present",
        ", ".join(d.name for d in devs) or "none. Set dtparam=spi=on in config.txt.",
    )
    cfgtxt = next(
        (p for p in (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
         if p.exists()), None,
    )
    if cfgtxt:
        text = cfgtxt.read_text(errors="replace")
        osc = re.search(r"mcp2515[^\n]*oscillator=(\d+)", text)
        rep.add(
            OK if osc else WARN, "mcp2515 overlay",
            f"oscillator={osc.group(1)} in {cfgtxt}" if osc
            else f"no mcp2515 overlay found in {cfgtxt}",
        )
        if osc:
            rep.add(
                INFO, "crystal cross-check",
                f"overlay says {int(osc.group(1)) // 1000000} MHz -- confirm the "
                "marking on the module's crystal matches",
            )


def check_profile(rep: Report, cfg: Config) -> None:
    rep.section("Protocol profile and transmit gate")
    try:
        profile = get(cfg.can.profile)
    except KeyError as exc:
        rep.add(BAD, "profile", str(exc))
        return
    rep.add(OK, "profile", f"{profile.key}  {len(profile)} frames")
    from ..protocol.confidence import Confidence

    rep.add(
        OK if profile.id_width_confidence >= Confidence.CONFIRMED_940 else WARN,
        "identifier width", profile.describe_id_width(),
    )
    if cfg.can.profile in REFERENCE_ONLY:
        rep.add(
            WARN if cfg.can.mode is CanMode.LISTEN_ONLY else BAD,
            "reference profile loaded",
            f"{cfg.can.profile} describes another vehicle; it may never transmit",
        )
    rep.add(
        OK if cfg.can.mode is CanMode.LISTEN_ONLY else WARN,
        "CAN mode", cfg.can.mode.value,
    )
    rep.add(INFO, "min tx confidence", cfg.can.min_tx_confidence.name)
    allowed = ", ".join(cfg.can.allowed_tx_frames) or "(none)"
    rep.add(INFO, "allowed tx frames", allowed)

    sendable = profile.transmittable(cfg.can.min_tx_confidence)
    if cfg.can.mode is CanMode.GATED_TX:
        sendable = [s for s in sendable if s.name in set(cfg.can.allowed_tx_frames)]
    if cfg.can.mode is CanMode.LISTEN_ONLY:
        sendable = []
    rep.add(
        OK, "frames that could be sent now",
        ", ".join(s.name for s in sendable) or "none",
    )
    for problem in cfg.validate():
        rep.add(BAD, "config", problem)


#: The build's sound card: a Ugreen USB adapter on a C-Media HS-100B. ALSA names
#: it "Device", from its USB product string "USB Audio Device" (measured
#: 2026-09-24). install.sh makes it the default ALSA card by this name.
SOUND_CARD_ID = "Device"
SOUND_CARD_USBID = "0d8c:0014"


def check_audio(rep: Report, cfg: Config, proc: Path = Path("/proc/asound")) -> None:
    rep.section("Audio (USB sound card)")
    cards = proc / "cards"
    if not cards.exists():
        rep.add(BAD, "ALSA", f"{cards} missing; no sound subsystem")
        return
    text = cards.read_text(errors="replace").strip()
    rep.add(
        OK if text and "no soundcards" not in text.lower() else BAD,
        "sound cards", text.splitlines()[0].strip() if text else "none",
    )
    # /proc/asound/<id> is a symlink to cardN, and only USB cards have usbid.
    usbid = proc / SOUND_CARD_ID / "usbid"
    if not usbid.exists():
        rep.add(WARN, "USB sound card",
                f"no ALSA card named {SOUND_CARD_ID!r}; check the OTG adapter and aplay -l")
        return
    got = usbid.read_text(errors="replace").strip()
    if got == SOUND_CARD_USBID:
        rep.add(OK, "USB sound card", f"{SOUND_CARD_ID} ({got})")
    else:
        rep.add(WARN, "USB sound card",
                f"{SOUND_CARD_ID} is {got}, expected {SOUND_CARD_USBID}")

    # Both switches must be off, and udev/95-blueandme-soundcard.rules sets them
    # off whenever the card appears. Mic Playback loops the mic straight to the
    # output, which in the car would send the cabin mic into the radio. Auto Gain
    # Control moves the mic gain on its own, which defeats echo cancellation.
    for control, label in (("Mic Playback Switch", "mic loopback to output"),
                           ("Auto Gain Control", "mic auto gain")):
        out = _run(["amixer", "-c", SOUND_CARD_ID, "cget", f"name={control}"])
        m = re.search(r":\s*values=(on|off)", out)
        if m and m.group(1) == "off":
            rep.add(OK, label, "off")
        elif m:
            rep.add(WARN, label,
                    f"on; amixer -c {SOUND_CARD_ID} cset name='{control}' off")


def check_bluetooth(rep: Report, cfg: Config) -> None:
    rep.section("Bluetooth")
    out = _run(["bluetoothctl", "show"])
    if not out:
        rep.add(BAD, "bluez", "bluetoothctl unavailable or no adapter")
        return
    powered = "Powered: yes" in out
    rep.add(OK if powered else BAD, "adapter powered", "yes" if powered else "no")
    name = re.search(r"Name:\s*(.+)", out)
    if name:
        want = cfg.bluetooth.name
        got = name.group(1).strip()
        rep.add(OK if got == want else WARN, "adapter name",
                f"{got}" + ("" if got == want else f" (config wants {want!r})"))
    devs = _run(["bluetoothctl", "devices", "Connected"]).strip()
    rep.add(OK if devs else INFO, "connected devices", devs.replace("\n", "; ") or "none")

    # BlueALSA is the actual audio path: the daemon registers the A2DP sink with
    # BlueZ, bluealsa-aplay pumps the decoded stream into ALSA. A phone can pair
    # and connect happily while both are dead, and the car stays silent.
    for unit in ("bluealsa.service", "bluealsa-aplay.service"):
        state = _run(["systemctl", "is-active", unit]).strip()
        if not state:
            rep.add(WARN, unit, "systemd not available")
        else:
            rep.add(OK if state == "active" else BAD, unit, state)

    # An active player can still be deaf: started before the sound driver
    # loaded, its DeviceAllow=char-alsa matched nothing, and every stream fails
    # to open the card until the player restarts (2026-09-25).
    for unit in PLAYER_UNITS:
        log = _run(["journalctl", "-b", "-u", unit, "-n", "50", "-o", "cat", "--no-pager"])
        if not log:
            continue  # no journal access, or the unit never ran
        label = f"{unit.split('.')[0]} opens card"
        if player_cannot_open_card(log):
            rep.add(BAD, label, f"no: sudo systemctl restart {unit}")
        else:
            rep.add(OK, label, "no failures since it started")


#: The two bluealsa-aplay instances: music, and the caller's voice.
PLAYER_UNITS = ("bluealsa-aplay.service", "blueandme-sco-aplay.service")


def player_cannot_open_card(journal: str) -> bool:
    """Whether the player has failed to open the card since it last started."""
    since_start = journal.split("Started ")[-1]
    return "Couldn't open ALSA playback PCM" in since_start


def check_service(rep: Report) -> None:
    rep.section("Service")
    out = _run(["systemctl", "show", "blueandme-giulietta.service",
                "--property=ActiveState,SubState,NRestarts,ExecMainStartTimestamp"])
    if not out:
        rep.add(INFO, "systemd unit", "not installed (fine during development)")
        return
    props = dict(
        line.split("=", 1) for line in out.strip().splitlines() if "=" in line
    )
    active = props.get("ActiveState", "?")
    rep.add(OK if active == "active" else WARN, "unit state",
            f"{active}/{props.get('SubState','?')}")
    restarts = props.get("NRestarts", "0")
    rep.add(OK if restarts in ("0", "") else WARN, "restarts", restarts)
    if props.get("ExecMainStartTimestamp"):
        rep.add(INFO, "started", props["ExecMainStartTimestamp"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueandme-status",
        description="Health check for the Blue&Me replacement.",
    )
    p.add_argument("--config", type=Path, help="config file (default: search order)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load(args.config)
    rep = Report()
    check_can(rep, cfg)
    check_spi(rep)
    check_profile(rep, cfg)
    check_audio(rep, cfg)
    check_bluetooth(rep, cfg)
    check_service(rep)

    if args.json:
        print(json.dumps(
            {"problems": rep.problems,
             "rows": [{"status": s, "label": l, "detail": d}
                      for s, l, d in rep.rows if not l.startswith("__")]},
            indent=2,
        ))
    else:
        print(rep.render())
        print(f"\n{rep.problems} problem(s).")
    return 1 if rep.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
