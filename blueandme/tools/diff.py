"""blueandme-diff -- differential analysis of two captures.

This is the tool that substitutes for having a working reference module. The
original Blue&Me is dead and removed, so the frames it used to send cannot be
recorded. What *can* be recorded is the car's behaviour with and without the
Body Computer expecting a Blue&Me node, and the difference between those two
captures names the frames the replacement has to produce.

Three views, because a frame can differ in three ways:

  --by id       which arbitration IDs appear in one capture and not the other
  --by payload  which shared IDs carry different payload sets
  --by bits     which *bit positions* within a shared ID ever change

The bit view is the cansniffer trick. fiatcan needed a patched cansniffer for it
because upstream can-utils cannot handle 29-bit IDs; doing it here sidesteps
that, and works whichever width the Giulietta turns out to use.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ..protocol.annotate import annotate
from ..protocol.registry import PROFILES, get
from .logfmt import CaptureMeta, read


class CaptureStats:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.meta = CaptureMeta.load(path)
        self.counts: Counter[str] = Counter()
        self.payloads: dict[str, set[bytes]] = defaultdict(set)
        self.extended: dict[str, bool] = {}
        self.first_seen: dict[str, float] = {}
        self.span = 0.0
        self.total = 0

        t0 = None
        last = None
        for frame in read(path):
            self.total += 1
            key = frame.hex_id
            if t0 is None:
                t0 = frame.timestamp
            last = frame.timestamp
            self.counts[key] += 1
            self.payloads[key].add(frame.data)
            self.extended[key] = frame.extended
            self.first_seen.setdefault(key, frame.timestamp - t0)
        if t0 is not None and last is not None:
            self.span = last - t0

    @property
    def ids(self) -> set[str]:
        return set(self.counts)

    def rate(self, hid: str) -> float:
        return self.counts[hid] / self.span if self.span else 0.0

    def changing_bits(self, hid: str) -> int:
        """Bitmask of positions that are not constant across this ID's payloads."""
        payloads = self.payloads.get(hid) or set()
        if len(payloads) < 2:
            return 0
        width = max(len(p) for p in payloads)
        ints = [int.from_bytes(p.ljust(width, b"\x00"), "big") for p in payloads]
        ones = 0
        zeros = 0
        for value in ints:
            ones |= value
            zeros |= ~value & ((1 << (width * 8)) - 1)
        return ones & zeros


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueandme-diff",
        description="Compare two captures and rank the differences.",
        epilog="The key comparison for this project is "
               "'blueandme-diff captures/E-baseline.log captures/H-proxi-with-bm.log', "
               "which names the frames the Body Computer expects from Blue&Me.",
    )
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.add_argument("--by", choices=["id", "payload", "bits", "all"], default="all")
    p.add_argument(
        "--min-count", type=int, default=1,
        help="ignore IDs seen fewer than N times, to suppress one-off noise",
    )
    p.add_argument(
        "--profile", default="giulietta940",
        help=f"annotate known frames. Available: {', '.join(sorted(PROFILES))}",
    )
    p.add_argument("--show-payloads", type=int, default=4,
                   help="max distinct payloads to print per ID (default 4)")
    return p


def _header(stats: CaptureStats, role: str) -> str:
    label = stats.meta.label if stats.meta and stats.meta.label else stats.path.name
    widths = stats.meta.id_widths if stats.meta else {}
    extra = f"  widths={widths}" if widths else ""
    return (
        f"{role:<10} {stats.path}\n"
        f"           {stats.total} frames / {len(stats.ids)} IDs / "
        f"{stats.span:.1f}s  [{label}]{extra}"
    )


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    for path in (args.baseline, args.candidate):
        if not path.exists():
            print(f"no such capture: {path}", file=sys.stderr)
            return 2
    try:
        profile = get(args.profile)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2

    base = CaptureStats(args.baseline)
    cand = CaptureStats(args.candidate)

    print(_header(base, "baseline"))
    print(_header(cand, "candidate"))

    def keep(stats: CaptureStats, hid: str) -> bool:
        return stats.counts[hid] >= args.min_count

    def describe(hid: str, extended: bool) -> str:
        spec = profile.by_id(int(hid, 16), extended)
        if spec is None:
            return ""
        return f"  <- {spec.name} [{spec.confidence.name}]"

    def sample(stats: CaptureStats, hid: str) -> str:
        payloads = sorted(stats.payloads.get(hid, ()))[: args.show_payloads]
        out = []
        for p in payloads:
            spec = profile.by_id(int(hid, 16), stats.extended.get(hid, False))
            note = annotate(spec, p, profile) if spec else ""
            out.append(f"      {hid}#{p.hex().upper()}" + (f"   {note}" if note else ""))
        extra = len(stats.payloads.get(hid, ())) - len(payloads)
        if extra > 0:
            out.append(f"      ... and {extra} more distinct payloads")
        return "\n".join(out)

    only_cand = sorted(h for h in cand.ids - base.ids if keep(cand, h))
    only_base = sorted(h for h in base.ids - cand.ids if keep(base, h))
    shared = sorted(base.ids & cand.ids)

    if args.by in ("id", "all"):
        print(f"\n=== IDs only in {args.candidate.name}  ({len(only_cand)}) "
              f"===  strongest leads")
        for hid in only_cand:
            print(f"  {hid}  x{cand.counts[hid]:<6} "
                  f"{cand.rate(hid):5.1f}/s  first@{cand.first_seen[hid]:.2f}s"
                  f"{describe(hid, cand.extended[hid])}")
            print(sample(cand, hid))
        if not only_cand:
            print("  (none)")

        print(f"\n=== IDs only in {args.baseline.name}  ({len(only_base)}) ===")
        for hid in only_base:
            print(f"  {hid}  x{base.counts[hid]:<6} "
                  f"{base.rate(hid):5.1f}/s{describe(hid, base.extended[hid])}")
        if not only_base:
            print("  (none)")

    if args.by in ("payload", "all"):
        changed = [
            h for h in shared
            if base.payloads[h] != cand.payloads[h] and keep(cand, h)
        ]
        print(f"\n=== shared IDs whose payload set changed  ({len(changed)}) ===")
        for hid in changed:
            new = cand.payloads[hid] - base.payloads[hid]
            gone = base.payloads[hid] - cand.payloads[hid]
            print(f"  {hid}  +{len(new)} new / -{len(gone)} gone"
                  f"{describe(hid, cand.extended[hid])}")
            spec = profile.by_id(int(hid, 16), cand.extended[hid])
            for p in sorted(new)[: args.show_payloads]:
                note = annotate(spec, p, profile) if spec else ""
                print(f"      + {hid}#{p.hex().upper()}" + (f"   {note}" if note else ""))
        if not changed:
            print("  (none)")

    if args.by in ("bits", "all"):
        print("\n=== changing bit positions per shared ID ===")
        rows = []
        for hid in shared:
            if not keep(cand, hid):
                continue
            mask = cand.changing_bits(hid)
            if mask:
                width = max(len(p) for p in cand.payloads[hid])
                rows.append((bin(mask).count("1"), hid, mask, width))
        for bits, hid, mask, width in sorted(rows, reverse=True):
            print(f"  {hid}  {bits:>3} volatile bits  "
                  f"mask={mask:0{width * 2}X}{describe(hid, cand.extended[hid])}")
        if not rows:
            print("  (none)")

    print(
        f"\n{len(only_cand)} new IDs, {len(only_base)} disappeared, "
        f"{len(shared)} shared."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
