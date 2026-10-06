"""Answer and hang up calls through bluealsa's hands-free control link.

bluealsa runs the HFP control channel (RFCOMM) to the phone itself, and hands
a raw end of it to a client that calls ``org.bluealsa.RFCOMM1.Open()`` on
``/org/bluealsa/hci0/dev_<address>/rfcomm``. AT commands written there go to
the phone, and its answers come back. That is enough for the steering wheel:
``ATA`` answers a ringing call, and ``AT+CHUP`` ends or rejects one.
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import time

from .sco_uplink import hfp_sinks, read_pcms

COMMANDS = {
    "answer": "ATA",
    "hangup": "AT+CHUP",
    "calls": "AT+CLCC",   # list current calls
}


def rfcomm_path(address: str, adapter: str = "hci0") -> str:
    return f"/org/bluealsa/{adapter}/dev_{address.upper().replace(':', '_')}/rfcomm"


def final_result(lines: list[str]) -> "str | None":
    """The AT final result code among response lines, if one has arrived.

    bluealsa 4.3 relays a bare result code to the client with a stray colon in
    front (``:OK``); lines with a value come through as sent (``+CIND: ...``).
    """
    for line in lines:
        code = line.lstrip(":")
        if code in ("OK", "ERROR") or code.startswith("+CME ERROR"):
            return code
    return None


def open_rfcomm(address: str) -> int:
    import dbus  # imported here so the module loads on machines without dbus

    obj = dbus.SystemBus().get_object("org.bluealsa", rfcomm_path(address))
    fd = dbus.Interface(obj, "org.bluealsa.RFCOMM1").Open()
    return fd.take()


def send(fd: int, command: str, timeout: float = 3.0) -> list[str]:
    os.write(fd, (command + "\r").encode())
    buf, lines, end = b"", [], time.monotonic() + timeout
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], max(0.0, end - time.monotonic()))
        if not ready:
            break
        chunk = os.read(fd, 1024)
        if not chunk:
            break
        buf += chunk
        *done, rest = buf.replace(b"\r", b"\n").split(b"\n")
        buf = rest
        lines += [d.decode(errors="replace").strip() for d in done if d.strip()]
        if final_result(lines):
            break
    return lines


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Control the phone's call over hands-free.")
    ap.add_argument("action", choices=sorted(COMMANDS))
    ap.add_argument("--device", help="phone address (default: the connected hands-free phone)")
    args = ap.parse_args(argv)
    address = args.device
    if not address:
        phones = hfp_sinks(read_pcms())
        if not phones:
            print("no phone connected for hands-free", file=sys.stderr)
            return 1
        address = phones[0].address
    fd = open_rfcomm(address)
    try:
        lines = send(fd, COMMANDS[args.action])
    finally:
        os.close(fd)
    for line in lines:
        print(line)
    return 0 if final_result(lines) == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
