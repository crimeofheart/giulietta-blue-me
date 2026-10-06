#!/usr/bin/env python3
"""Supervised one-off: write record 2023 to the Body Computer with a chosen flag byte.

Route 2. MultiEcuScan sources the telematic-present flag from the Body Computer's
own copy of record 2023 and propagates it outward, so seeding the telematic node
cannot change it (captures U and W). This writes the BC's copy directly, using the
same UDS sequence MultiEcuScan uses:

    10 03                 extended session   -> 50 03 ...
    2E 2023 <93 bytes>    write the record   -> 6E 20 23
    22 2023               read it back       -> 62 20 23 <93 bytes>

This writes configuration into the Body Computer. It refuses to run without
--transmit, prints every frame, and stops at the first negative or missing reply.
Outside TxGate by the owner's decision, 2026-09-21.
"""
import argparse
import sys
import time

import can

TX_ID = 0x18DA40F1   # tester -> Body Computer
RX_ID = 0x18DAF140   # Body Computer -> tester
EXT_MASK = 0x1FFFFFFF


def _send(bus, data):
    bus.send(can.Message(arbitration_id=TX_ID, is_extended_id=True, data=data))


def _recv(bus, timeout):
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        msg = bus.recv(timeout=left)
        if msg is not None and msg.arbitration_id == RX_ID and msg.is_extended_id:
            return bytes(msg.data)


def recv_message(bus, log, timeout=2.0):
    """Receive one ISO-TP message, sending flow control for a multi-frame reply."""
    d = _recv(bus, timeout)
    if d is None:
        return None
    pci = d[0] >> 4
    if pci == 0:
        return d[1:1 + (d[0] & 0x0F)]
    if pci != 1:
        log(f"   unexpected frame {d.hex(' ')}")
        return None
    total = ((d[0] & 0x0F) << 8) | d[1]
    buf = bytearray(d[2:])
    _send(bus, bytes([0x30, 0x00, 0x00]))
    while len(buf) < total:
        d = _recv(bus, timeout)
        if d is None:
            return None
        if d[0] >> 4 != 2:
            continue
        buf += d[1:]
    return bytes(buf[:total])


def send_message(bus, log, payload, fc_timeout=2.0):
    n = len(payload)
    if n <= 7:
        _send(bus, bytes([n]) + payload)
        return True
    _send(bus, bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6])
    fc = _recv(bus, fc_timeout)
    if fc is None or fc[0] >> 4 != 3:
        log(f"   no flow control (got {fc.hex(' ') if fc else 'nothing'})")
        return False
    stmin = fc[2] if len(fc) > 2 else 0
    pos, seq = 6, 1
    while pos < n:
        _send(bus, bytes([0x20 | seq]) + payload[pos:pos + 7])
        pos += 7
        seq = (seq + 1) & 0x0F
        time.sleep(stmin / 1000 if 0 < stmin <= 0x7F else 0.001)
    return True


def request(bus, log, payload, what):
    log(f"-> BC  {payload[:8].hex(' ')}{' ...' if len(payload) > 8 else ''}  ({what})")
    if not send_message(bus, log, payload):
        return None
    while True:
        reply = recv_message(bus, log)
        if reply is None:
            log("   no reply")
            return None
        if reply[0] == 0x7F and len(reply) > 2 and reply[2] == 0x78:
            log("<- BC  pending, waiting")
            continue
        ascii_ = "".join(chr(c) if 32 <= c < 127 else "." for c in reply)
        shown = reply.hex(" ") if len(reply) <= 12 else f"{reply[:6].hex(' ')} ... ({len(reply)} bytes)"
        shown += f"   ascii: {ascii_}" if len(reply) > 3 else ""
        log(f"<- BC  {shown}")
        if reply[0] == 0x7F:
            log(f"   NEGATIVE: service {reply[1]:02X}, code {reply[2]:02X}")
            return None
        return reply


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", default="can0")
    p.add_argument("--interface", default="socketcan")
    p.add_argument("--record", help="hex file holding the 93-byte record to write")
    p.add_argument("--target", default="40",
                   help="diagnostic address: 40 Body Computer, 60/98/C0 the other aligned nodes, 61 telematic")
    p.add_argument("--did", default="2023", help="data identifier to read or write, hex")
    p.add_argument("--read-only", action="store_true", help="read record 2023 and print it, write nothing")
    p.add_argument("--log", default="write-bc.log")
    p.add_argument("--transmit", action="store_true", help="required: this talks to a vehicle ECU")
    a = p.parse_args()
    if not a.transmit:
        sys.exit("refusing to run without --transmit: this talks to a vehicle ECU")
    if not a.read_only and not a.record:
        sys.exit("--record is required unless --read-only is given")

    global TX_ID, RX_ID
    target = int(a.target, 16)
    TX_ID = 0x18DA00F1 | (target << 8)
    RX_ID = 0x18DAF100 | target

    record = bytes.fromhex(open(a.record).read().strip()) if a.record else b""
    did = bytes.fromhex(a.did)
    t0 = time.monotonic()
    logf = open(a.log, "a")

    def log(line):
        s = f"{time.monotonic() - t0:7.3f}  {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    flags = [record[i:i + 5].hex().upper() for i in range(len(record) - 4)
             if record[i:i + 4].hex().upper() == "6F040005"]
    log(f"target {target:02X}: tx {TX_ID:08X}, rx {RX_ID:08X}")
    if a.read_only:
        log("read-only: nothing will be written")
    else:
        log(f"record {len(record)} bytes from {a.record}; flag groups: {flags or '-'}")

    bus = can.Bus(interface=a.interface, channel=a.channel,
                  can_filters=[{"can_id": RX_ID, "can_mask": EXT_MASK, "extended": True}])
    try:
        if request(bus, log, bytes([0x10, 0x03]), "extended session") is None:
            return 1
        if not a.read_only:
            if request(bus, log, bytes([0x2E]) + did + record, "write record 2023") is None:
                return 1
        back = request(bus, log, bytes([0x22]) + did,
                       "read record 2023" if a.read_only else "read it back")
        if back is None:
            return 1
        got = [back[i:i + 5].hex().upper() for i in range(len(back) - 4)
               if back[i:i + 4].hex().upper() == "6F040005"]
        log(f"flag groups: {got or '-'}")
        if not a.read_only:
            log("WROTE AND VERIFIED" if got == flags else "WRITTEN BUT READ BACK DIFFERS")
        return 0
    finally:
        bus.shutdown()
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
