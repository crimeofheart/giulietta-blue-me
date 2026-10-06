"""blueandme-decode -- annotate a capture through one or more profiles.

Output deliberately mirrors fiatcan's ``*.decoded.log`` format so that decodes of
the Doblo fixtures can be compared line-for-line against fmntf's published
annotations, which is the regression test that the decoder is not inventing
meaning.

Every annotation carries the evidence level of the frame that produced it, so a
Doblo guess can never be mistaken for a Giulietta fact when reading output.
"""

from __future__ import annotations

import argparse
import html
import sys
from collections import Counter
from pathlib import Path

from ..protocol.annotate import annotate
from ..protocol.frame import Profile
from ..protocol.registry import PROFILES, get
from .logfmt import CaptureMeta, LogFrame, read


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueandme-decode",
        description="Annotate a candump log using a vehicle protocol profile.",
    )
    p.add_argument("log", type=Path, help="candump -l file to decode")
    p.add_argument(
        "--profile", action="append", default=[],
        help=f"profile to match against, repeatable. Available: "
             f"{', '.join(sorted(PROFILES))}. Default: giulietta940",
    )
    p.add_argument(
        "--unknown-only", action="store_true",
        help="print only frames no profile recognises -- the research queue",
    )
    p.add_argument(
        "--summary", action="store_true",
        help="per-ID counts and coverage instead of a line-by-line decode",
    )
    p.add_argument("--html", action="store_true", help="emit an HTML table")
    p.add_argument("--relative-time", action="store_true",
                   help="show seconds since the first frame, like fiatcan's decoded logs")
    p.add_argument("--limit", type=int, help="stop after N frames")
    return p


def _resolve(frame: LogFrame, profiles: list[Profile]):
    for profile in profiles:
        spec = profile.by_id(frame.can_id, frame.extended)
        if spec is not None:
            return profile, spec
    return None, None


def _decode_line(frame: LogFrame, profiles: list[Profile], multi: bool) -> str:
    profile, spec = _resolve(frame, profiles)
    if spec is None:
        return ""
    note = annotate(spec, frame.data, profile)
    tag = f"[{spec.confidence.name}"
    if multi:
        tag += f" {profile.key}"
    tag += "]"
    return f"{note}  {tag}" if note else tag


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    keys = args.profile or ["giulietta940"]
    try:
        profiles = [get(k) for k in keys]
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2
    multi = len(profiles) > 1

    if not args.log.exists():
        print(f"no such capture: {args.log}", file=sys.stderr)
        return 2

    meta = CaptureMeta.load(args.log)
    if meta and not args.html:
        print(f"# capture: {meta.label or args.log.name}", file=sys.stderr)
        if meta.scenario:
            print(f"# scenario: {meta.scenario}", file=sys.stderr)
        if meta.id_widths:
            print(f"# ID widths: {meta.id_widths}", file=sys.stderr)

    total = 0
    matched = 0
    unknown: Counter[str] = Counter()
    known: Counter[str] = Counter()
    t0: float | None = None
    rows: list[tuple[str, str, str, str]] = []

    for frame in read(args.log):
        if args.limit and total >= args.limit:
            break
        total += 1
        if t0 is None:
            t0 = frame.timestamp
        note = _decode_line(frame, profiles, multi)
        if note:
            matched += 1
            _, spec = _resolve(frame, profiles)
            known[spec.name] += 1
            if args.unknown_only:
                continue
        else:
            unknown[frame.hex_id] += 1

        if args.summary:
            continue
        stamp = (
            f"({frame.timestamp - t0:12.6f})"
            if args.relative_time
            else f"({frame.timestamp:.6f})"
        )
        body = f"{frame.hex_id}#{frame.data.hex().upper()}"
        if args.html:
            rows.append((stamp, frame.interface, body, note))
        else:
            print(f"{stamp} {frame.interface} {body:<24}\t{note}")

    if args.html:
        _emit_html(args.log, rows)

    if args.summary or not args.html:
        pct = 100.0 * matched / total if total else 0.0
        print(
            f"\n# {total} frames, {matched} recognised ({pct:.1f}%), "
            f"{len(unknown)} unrecognised IDs, profiles: {', '.join(keys)}",
            file=sys.stderr,
        )
    if args.summary:
        if known:
            print("\nrecognised:", file=sys.stderr)
            for name, n in known.most_common():
                print(f"  {n:>7}  {name}", file=sys.stderr)
        if unknown:
            print("\nunrecognised IDs (research queue):", file=sys.stderr)
            for hid, n in unknown.most_common():
                print(f"  {n:>7}  {hid}", file=sys.stderr)
    return 0


def _emit_html(log: Path, rows: list[tuple[str, str, str, str]]) -> None:
    print("<!doctype html><meta charset=utf-8>")
    print(f"<title>{html.escape(log.name)}</title>")
    print("<style>body{font:13px ui-monospace,monospace}td{padding:1px 8px}"
          "tr:nth-child(even){background:#f4f4f4}.n{color:#555}</style><table>")
    for stamp, iface, body, note in rows:
        print(
            f"<tr><td>{html.escape(stamp)}</td><td>{html.escape(iface)}</td>"
            f"<td>{html.escape(body)}</td><td class=n>{html.escape(note)}</td></tr>"
        )
    print("</table>")


if __name__ == "__main__":
    raise SystemExit(main())
