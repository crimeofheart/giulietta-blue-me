"""Log a FNIRSI FNB58 USB power meter over Bluetooth LE, from the dev PC.

Measuring the Pi from the PC keeps the meter off the Pi's own Bluetooth,
which is part of what is being measured (and is switched off in standby).

    python fnb58_log.py --out power.csv           # scans for the meter
    python fnb58_log.py --mac AA:BB:.. --raw      # also print every frame

Protocol (reverse-engineered by others: github.com/yeckel/OpenFNB58,
gist.github.com/parkerlreed/0ce45e907ce536a0541afb90b5b49350): commands go to
characteristic ffe9, AA 81 00 F4 then, ~2 s later, AA 82 00 A7; the meter then
streams notifications on ffe4, each one or more frames AA <type> <len>
<data> <sum>. Type 04 is volts, amps, watts as little-endian int32 / 10000
(what the gist reads at byte 21); type 07 is VBUS, IBUS as uint16 / 1000
(what OpenFNB58 reads).

Needs bleak (pip install bleak); nothing else outside the standard library.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

WRITE = "0000ffe9-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ffe4-0000-1000-8000-00805f9b34fb"
WAKE = bytes([0xAA, 0x81, 0x00, 0xF4])
START = bytes([0xAA, 0x82, 0x00, 0xA7])


def frames(data: bytes):
    """(type, payload) for each AA <type> <len> <payload> <sum> frame."""
    i = 0
    while i + 3 <= len(data):
        n = data[i + 2]
        if data[i] == 0xAA and i + 3 + n + 1 <= len(data):
            yield data[i + 1], data[i + 3:i + 3 + n]
            i += 3 + n + 1
        else:
            i += 1


def decode(data: bytes) -> "tuple[float, float, float | None] | None":
    """Volts, amps, watts. Frame 04 (int32 LE / 10000 each, 0.1 mA) when the
    notification has one, else frame 07 (uint16 LE / 1000, 1 mA, no watts).
    Checked 2026-09-25: 04 read 5.0665 V 0.2055 A 1.0416 W beside 07's
    5.066 V 0.205 A."""
    fallback = None
    for kind, payload in frames(data):
        if kind == 0x04 and len(payload) >= 12:
            v, a, w = (x / 10000 for x in struct.unpack_from("<iii", payload))
            return v, a, w
        if kind == 0x07 and len(payload) >= 4 and fallback is None:
            v, a = struct.unpack_from("<HH", payload)
            fallback = (v / 1000, a / 1000, None)
    return fallback


async def find(timeout: float) -> str:
    print(f"scanning {timeout:.0f} s for the FNB58 (turn its Bluetooth on)...", file=sys.stderr)
    for dev in await BleakScanner.discover(timeout=timeout):
        if dev.name and "FNB" in dev.name.upper():
            print(f"found {dev.name} {dev.address}", file=sys.stderr)
            return dev.address
    raise SystemExit("no FNB58 found")


async def run(args: argparse.Namespace) -> None:
    mac = args.mac or await find(args.scan)
    out = open(args.out, "a", newline="") if args.out else None
    writer = csv.writer(out) if out else None
    if writer and out.tell() == 0:
        writer.writerow(["unix_time", "volts", "amps", "watts"])

    def on_data(_sender, data: bytearray) -> None:
        data = bytes(data)
        if args.raw:
            print(data.hex(" "), file=sys.stderr)
        got = decode(data)
        if got is None:
            return
        v, a, w = got
        if w is None and not args.all:
            return  # frame 07 only: coarser, and frame 04 follows within ~0.2 s
        row = [f"{time.time():.3f}", f"{v:.4f}", f"{a:.4f}", "" if w is None else f"{w:.4f}"]
        if writer:
            writer.writerow(row)
            out.flush()
        if not args.quiet:
            print(f"{row[0]}  {v:6.3f} V  {a:7.4f} A  " + ("" if w is None else f"{w:6.3f} W"),
                  flush=True)

    async with BleakClient(mac) as client:
        print(f"connected to {mac}", file=sys.stderr)
        await client.start_notify(NOTIFY, on_data)
        await client.write_gatt_char(WRITE, WAKE)
        await asyncio.sleep(2)
        await client.write_gatt_char(WRITE, START)
        end = time.monotonic() + args.seconds if args.seconds else None
        while client.is_connected and (end is None or time.monotonic() < end):
            await asyncio.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mac", help="meter address (default: scan)")
    ap.add_argument("--scan", type=float, default=15.0, help="scan seconds")
    ap.add_argument("--out", help="append samples to this CSV")
    ap.add_argument("--seconds", type=float, default=0, help="stop after (0 = never)")
    ap.add_argument("--raw", action="store_true", help="print every notification in hex")
    ap.add_argument("--all", action="store_true", help="also log frame 07 samples (1 mA)")
    ap.add_argument("--quiet", action="store_true", help="no per-sample output")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
