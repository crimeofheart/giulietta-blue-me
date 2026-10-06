"""Replay node 60's alignment dialogue from capture P against the node-61 responder."""
import os
import sys
import threading

import can

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import node61_responder as r

TO_61 = 0x18DA61F1
FROM_61 = 0x18DAF161

# A made-up 93-byte record, no car's, cut into the frames a tester sends for
# 2E 2023: a first frame, then consecutive frames 21..2D.
RECORD = b"00000000000EXAMPLE-ONLY" + bytes.fromhex("6F04000500") + bytes(range(65))
_WRITE = bytes([0x2E, 0x20, 0x23]) + RECORD
WRITE_FRAMES = [(bytes([0x10 | (len(_WRITE) >> 8), len(_WRITE) & 0xFF]) + _WRITE[:6]).hex().upper()]
WRITE_FRAMES += [(bytes([0x20 | ((i + 1) & 0x0F)]) + _WRITE[6 + 7 * i:13 + 7 * i]).hex().upper()
                 for i in range((len(_WRITE) - 6 + 6) // 7)]

SINGLE = [
    ("021003", "065003003201F4"),
    ("03222023", "037F2231"),
    ("03222024", "037F2231"),
    ("023E00", "027E00"),
    ("03222023", "037F2231"),
    ("032240A1", "037F2231"),
    ("032240A2", "037F2231"),
]

failures = []


def expect(tester, want_hex, what, skip_hex=()):
    while True:
        m = tester.recv(timeout=2)
        if m is None:
            failures.append(f"{what}: no reply, wanted {want_hex}")
            return
        got = bytes(m.data).hex().upper()
        if m.arbitration_id != FROM_61:
            continue
        if got in skip_hex:
            continue
        if got != want_hex:
            failures.append(f"{what}: got {got}, wanted {want_hex}")
        else:
            print(f"ok  {what}: {got}")
        return


def send(tester, hx):
    tester.send(can.Message(arbitration_id=TO_61, is_extended_id=True, data=bytes.fromhex(hx)))


responder_bus = can.Bus(interface="virtual", channel="node61test")
tester = can.Bus(interface="virtual", channel="node61test")
store, stop, lines = {}, threading.Event(), []
t = threading.Thread(target=r.serve, args=(responder_bus, lines.append, store, stop))
t.start()

try:
    for req, want in SINGLE:
        send(tester, req)
        expect(tester, want, f"req {req}")

    send(tester, WRITE_FRAMES[0])
    expect(tester, "300000", "flow control for 2E write")
    for cf in WRITE_FRAMES[1:]:
        send(tester, cf)
    expect(tester, "036E2023", "2E 2023 accepted", skip_hex=("037F2E78",))

    send(tester, "0322102A")
    expect(tester, "100C62102A000000", "102A first frame")
    send(tester, "300000")
    expect(tester, "21000000000000", "102A consecutive frame")

    send(tester, "032240AA")
    expect(tester, "037F2231", "req 032240AA")

    payload = bytes.fromhex("".join(f[4:] if i == 0 else f[2:] for i, f in enumerate(WRITE_FRAMES)))
    record = payload[3:]
    if store.get(0x2023) != record:
        failures.append(f"stored record mismatch: {len(store.get(0x2023, b''))} bytes stored, {len(record)} expected")
    else:
        print(f"ok  stored record 2023 = {len(record)} bytes, starts {record[:11].decode('ascii')}")

    send(tester, "021101")
    expect(tester, "037F1111", "unknown service gets NRC 11")
finally:
    stop.set()
    t.join()
    responder_bus.shutdown()
    tester.shutdown()

print("\n".join(lines))
if failures:
    print("FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS: responder reproduces node 60's alignment replies byte for byte")
