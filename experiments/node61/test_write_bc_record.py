"""Check the Body Computer record writer against a fake BC on a virtual bus."""
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import can

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import write_bc_record as w

# A made-up record of the right size (93 bytes) with the flag group in it; no car's.
RECORD = b"00000000000EXAMPLE-ONLY" + bytes.fromhex("6F04000502") + bytes(range(65))
failures = []
received = {}


def fake_bc(bus, stop):
    """Answer like the Body Computer: session, flow control, 6E, then the record back."""
    stored = None
    while not stop.is_set():
        m = bus.recv(timeout=0.1)
        if m is None or m.arbitration_id != w.TX_ID:
            continue
        d = bytes(m.data)
        pci = d[0] >> 4
        if pci == 1:                                    # first frame of the write
            total = ((d[0] & 0x0F) << 8) | d[1]
            buf = bytearray(d[2:])
            bus.send(can.Message(arbitration_id=w.RX_ID, is_extended_id=True,
                                 data=bytes([0x30, 0x00, 0x00])))
            while len(buf) < total and not stop.is_set():
                m2 = bus.recv(timeout=1)
                if m2 is None or m2.arbitration_id != w.TX_ID:
                    continue
                d2 = bytes(m2.data)
                if d2[0] >> 4 == 2:
                    buf += d2[1:]
            req = bytes(buf[:total])
            received["write"] = req
            stored = req[3:]
            bus.send(can.Message(arbitration_id=w.RX_ID, is_extended_id=True,
                                 data=bytes([0x03, 0x6E, 0x20, 0x23])))
            continue
        if pci != 0:
            continue
        req = d[1:1 + (d[0] & 0x0F)]
        if req[:2] == bytes([0x10, 0x03]):
            bus.send(can.Message(arbitration_id=w.RX_ID, is_extended_id=True,
                                 data=bytes.fromhex("065003003201F4")))
        elif req[:3] == bytes([0x22, 0x20, 0x23]) and stored is not None:
            payload = bytes([0x62, 0x20, 0x23]) + stored
            n = len(payload)
            bus.send(can.Message(arbitration_id=w.RX_ID, is_extended_id=True,
                                 data=bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]))
            pos, seq = 6, 1
            while pos < n:
                m2 = bus.recv(timeout=1)           # wait for our flow control
                if m2 is not None and bytes(m2.data)[0] >> 4 == 3:
                    while pos < n:
                        bus.send(can.Message(arbitration_id=w.RX_ID, is_extended_id=True,
                                             data=bytes([0x20 | seq]) + payload[pos:pos + 7]))
                        pos += 7
                        seq = (seq + 1) & 0x0F
                        time.sleep(0.001)


bc_bus = can.Bus(interface="virtual", channel="writebctest")
tool_bus = can.Bus(interface="virtual", channel="writebctest")
stop = threading.Event()
t = threading.Thread(target=fake_bc, args=(bc_bus, stop))
t.start()

lines = []
try:
    with tempfile.NamedTemporaryFile("w", suffix=".hex", delete=False) as f:
        f.write(RECORD.hex())
        path = f.name
    ok = w.request(tool_bus, lines.append, bytes([0x10, 0x03]), "session") is not None
    if not ok:
        failures.append("session refused")
    if w.request(tool_bus, lines.append, bytes([0x2E, 0x20, 0x23]) + RECORD, "write") is None:
        failures.append("write got no positive reply")
    back = w.request(tool_bus, lines.append, bytes([0x22, 0x20, 0x23]), "read back")
    if back is None or back[3:] != RECORD:
        failures.append("read back did not match what was written")
finally:
    stop.set()
    t.join()
    bc_bus.shutdown()
    tool_bus.shutdown()

sent = received.get("write", b"")
if sent[:3] != bytes([0x2E, 0x20, 0x23]) or sent[3:] != RECORD:
    failures.append(f"BC received {len(sent)} bytes, not the intended 96")
if "6F04000502" not in RECORD.hex().upper():
    failures.append("test record does not carry the 02 flag")

guard = subprocess.run([sys.executable, str(HERE / "write_bc_record.py"), "--record", path],
                       capture_output=True, text=True)
if guard.returncode == 0 or "--transmit" not in guard.stderr:
    failures.append(f"guard: exit {guard.returncode}, stderr {guard.stderr!r}")

print("\n".join(lines))
if failures:
    print("FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS: session, 96-byte write, positive reply, read-back matches, guard refuses without --transmit")
