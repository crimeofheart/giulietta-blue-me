"""Summarise a fnb58_log.py CSV per state, from a markers CSV.

    python power_summary.py power.csv markers.csv [--settle 10]

markers.csv: unix_time,marker -- each marker starts a state that runs until
the next one. The first --settle seconds of each state are skipped, so a
transition's spike does not count towards the state.
"""

from __future__ import annotations

import argparse
import csv
import statistics


def load(path: str) -> list[list[str]]:
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    return rows[1:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("power")
    ap.add_argument("markers")
    ap.add_argument("--settle", type=float, default=10.0)
    args = ap.parse_args()

    samples = [(float(t), float(v), float(a), float(w))
               for t, v, a, w in load(args.power) if w]
    marks = [(float(row[0]), ",".join(row[1:])) for row in load(args.markers)]  # text may hold commas
    print(f"{'state':<58} {'s':>4} {'n':>5} {'mean A':>7} {'mean W':>7} "
          f"{'min W':>6} {'max W':>6}")
    for (start, name), nxt in zip(marks, marks[1:] + [(float("inf"), "")]):
        window = [s for s in samples if start + args.settle <= s[0] < nxt[0]]
        if not window:
            continue
        watts = [s[3] for s in window]
        amps = [s[2] for s in window]
        span = window[-1][0] - window[0][0]
        print(f"{name[:58]:<58} {span:4.0f} {len(window):5d} {statistics.mean(amps):7.4f} "
              f"{statistics.mean(watts):7.3f} {min(watts):6.3f} {max(watts):6.3f}")


if __name__ == "__main__":
    main()
