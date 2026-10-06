"""blueandme-capture -- timestamped, listen-only CAN recorder.

This tool has no transmit path at all. Not a gated one, not a disabled one:
there is no code in this file that can put a frame on the bus. It is the first
thing that touches the car and it is meant to be provably harmless.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from collections import Counter
from pathlib import Path

from .logfmt import CaptureMeta, LogFrame, utc_now

SCENARIOS = {
    "A": "Ignition OFF -> ON. Resolves ID width, wake-up frame, PROXI challenge, node inventory.",
    "B": "Ignition ON -> OFF. Resolves the shutdown/sleep handshake.",
    "C": "Each steering-wheel button pressed individually, 3x, with pauses.",
    "E": "Steady state, engine off, no interaction. Baseline with Blue&Me absent.",
    "F": "Radio source changes, volume up/down, mute.",
    "G": "USB / media operation, if the car still offers it.",
    "H": "After PROXI re-alignment WITH Blue&Me enabled, module still absent. "
         "Diff against E names the frames the Body Computer now expects.",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueandme-capture",
        description="Record B-CAN traffic to a candump -l log. Listen-only, always.",
        epilog="Scenarios:\n"
        + "\n".join(f"  {k}  {v}" for k, v in SCENARIOS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--iface", default="can0", help="SocketCAN interface (default: can0)")
    p.add_argument("--out", required=True, type=Path, help="output .log path")
    p.add_argument(
        "--scenario", choices=sorted(SCENARIOS),
        help="capture scenario letter from the plan; recorded in the sidecar",
    )
    p.add_argument("--label", default="", help="free-text label for the sidecar")
    p.add_argument(
        "--tap-point", default="",
        help="where the bus was tapped, e.g. 'B&M connector 30/14/15' or 'OBD2 3/11'",
    )
    p.add_argument("--duration", type=float, help="stop after N seconds")
    p.add_argument("--bitrate", type=int, default=50000, help="recorded in the sidecar")
    p.add_argument(
        "--filter-id", action="append", default=[],
        help="only record this ID (hex, repeatable). Omit to record everything.",
    )
    p.add_argument("--quiet", action="store_true", help="no live statistics")
    return p


def _parse_filters(values: list[str]) -> set[int]:
    out = set()
    for v in values:
        out.add(int(v, 16))
    return out


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import can
    except ImportError:
        print("python-can is not installed; run install.sh", file=sys.stderr)
        return 2

    wanted = _parse_filters(args.filter_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    meta = CaptureMeta(
        label=args.label,
        scenario=f"{args.scenario}: {SCENARIOS[args.scenario]}" if args.scenario else "",
        interface=args.iface,
        bitrate=args.bitrate,
        tap_point=args.tap_point,
        started_at=utc_now(),
    )

    ids: Counter[str] = Counter()
    widths: Counter[str] = Counter()
    errors = 0
    count = 0
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    started = time.time()
    last_report = started
    print(f"recording {args.iface} -> {args.out}  (Ctrl-C to stop)", file=sys.stderr)

    # receive_own_messages=False and no send() anywhere in this module.
    bus = can.Bus(interface="socketcan", channel=args.iface, receive_own_messages=False)
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            while not stop:
                if args.duration and time.time() - started >= args.duration:
                    break
                msg = bus.recv(0.5)
                if msg is None:
                    continue
                if getattr(msg, "is_error_frame", False):
                    errors += 1
                    continue
                if wanted and msg.arbitration_id not in wanted:
                    continue
                frame = LogFrame(
                    timestamp=float(msg.timestamp),
                    interface=args.iface,
                    can_id=int(msg.arbitration_id),
                    extended=bool(msg.is_extended_id),
                    data=bytes(msg.data),
                )
                fh.write(frame.format() + "\n")
                count += 1
                ids[frame.hex_id] += 1
                widths["29-bit" if frame.extended else "11-bit"] += 1
                now = time.time()
                if not args.quiet and now - last_report >= 1.0:
                    rate = count / max(now - started, 1e-6)
                    print(
                        f"\r{count:>8} frames  {rate:6.1f}/s  "
                        f"{len(ids):>3} unique IDs  {errors} errors",
                        end="", file=sys.stderr, flush=True,
                    )
                    last_report = now
    finally:
        bus.shutdown()

    meta.duration_s = round(time.time() - started, 3)
    meta.frame_count = count
    meta.unique_ids = len(ids)
    meta.id_widths = dict(widths)
    meta.error_frames = errors
    meta.save(args.out)

    print(file=sys.stderr)
    print(f"wrote {count} frames, {len(ids)} unique IDs to {args.out}", file=sys.stderr)
    print(f"sidecar: {CaptureMeta.path_for(args.out)}", file=sys.stderr)
    if widths:
        print(f"ID widths seen: {dict(widths)}   <- decision gate D1", file=sys.stderr)
    if errors:
        print(
            f"WARNING: {errors} error frames. Check bitrate, crystal and termination.",
            file=sys.stderr,
        )
    if count == 0:
        print(
            "No frames captured. Check: interface up, correct bitrate, "
            "CAN-H/CAN-L not swapped, MCP2515 oscillator matches the crystal.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
