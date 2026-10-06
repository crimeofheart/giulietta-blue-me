"""Supervised: how does the radio take track text sent again, or scrolled by the Pi?

Runs beside the app (which keeps answering the radio) and sends extra
0A394021 radio-text messages with cansend. Owner watches the radio display.

    sudo /opt/blueandme/.venv/bin/python scroll_test.py resend  ARTIST ALBUM TITLE
    sudo /opt/blueandme/.venv/bin/python scroll_test.py scroll  ARTIST ALBUM TITLE [--period 0.5] [--step 1] [--seconds 20]
    sudo /opt/blueandme/.venv/bin/python scroll_test.py first   ARTIST ALBUM TITLE [...]   # marquee in field 1

A resend restarts the radio's cycle at field 1 ("+artist"), seen 2026-09-27,
so "first" runs the marquee ("TITLE - ARTIST") in field 1 itself.
"""
import argparse
import socket
import struct
import time

from blueandme.protocol import text as textcodec
from blueandme.vehicle.node import radio_track_text

CAN_ID = "0A394021"


_sock = None


def send(message: str) -> None:
    """Raw SocketCAN (cansend per frame cost ~0.2 s a message)."""
    global _sock
    if _sock is None:
        _sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        _sock.bind(("can0",))
    can_id = (int(CAN_ID, 16) | socket.CAN_EFF_FLAG) & 0xFFFFFFFF   # negative on armhf
    for i, payload in enumerate(textcodec.encode_frames(message, textcodec.Display.RADIO)):
        if i:
            time.sleep(0.019)
        _sock.send(struct.pack("=IB3x8s", can_id, len(payload), payload))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("resend", "scroll", "first"))
    ap.add_argument("artist")
    ap.add_argument("album")
    ap.add_argument("title")
    ap.add_argument("--period", type=float, default=0.5)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--width", type=int, default=18, help="title window, chars")
    a = ap.parse_args()
    if a.mode == "resend":
        send(radio_track_text(a.artist, a.album, a.title))
        print("sent once")
        return
    loop = (f"{a.title} - {a.artist}" if a.mode == "first" else a.title) + "   "
    if a.mode == "first":
        a.width = min(a.width, 13)   # field 1 is 14 with the "+"
    end = time.monotonic() + a.seconds
    i = 0
    next_at = time.monotonic()
    while time.monotonic() < end:
        window = (loop[i:] + loop[:i])[: a.width]
        if a.mode == "first":
            send(radio_track_text(window, a.album or a.artist, a.title))
        else:
            send(radio_track_text(a.artist, a.album, window))
        print(f"{time.monotonic():.1f} {window!r}", flush=True)
        i = (i + a.step) % len(loop)
        next_at += a.period
        time.sleep(max(0.0, next_at - time.monotonic()))


if __name__ == "__main__":
    main()
