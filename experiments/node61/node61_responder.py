#!/usr/bin/env python3
"""Supervised one-off: answer PROXI-alignment diagnostics as telematic node 61.

Replies mirror node 60 in captures/P-proxi-align-no-bm.log (227.7-236.2 s).
Sends nothing unsolicited: every frame is a reply to one addressed to 61.
Deliberately outside TxGate (INFERRED_940) by the owner's decision, 2026-09-13.
"""
import argparse
import sys
import time

import can

RX_ID = 0x18DA61F1
TX_ID = 0x18DAF161
EXT_MASK = 0x1FFFFFFF


def build_reply(req, store):
    sid = req[0]
    if sid == 0x10 and len(req) >= 2:
        return bytes([0x50, req[1], 0x00, 0x32, 0x01, 0xF4])
    if sid == 0x3E and len(req) >= 2:
        if req[1] & 0x80:
            return None
        return bytes([0x7E, req[1]])
    if sid == 0x22 and len(req) >= 3:
        did = (req[1] << 8) | req[2]
        if did == 0x102A:
            return bytes([0x62, 0x10, 0x2A]) + bytes(9)
        if did in store:
            return bytes([0x62, req[1], req[2]]) + store[did]
        return bytes([0x7F, 0x22, 0x31])
    if sid == 0x19 and len(req) >= 2:
        # Read DTC information. The Body Computer answers 19 02 0D with 59 02 0F
        # in captures/U-post-battery-reset.log; mirror that, reporting no DTCs.
        return bytes([0x59, req[1], 0x0F])
    if sid == 0x2E and len(req) >= 3:
        did = (req[1] << 8) | req[2]
        if did == 0x2023:
            store[did] = bytes(req[3:])
            return bytes([0x6E, req[1], req[2]])
        return bytes([0x7F, 0x2E, 0x31])
    return bytes([0x7F, sid, 0x11])


def _send(bus, data):
    bus.send(can.Message(arbitration_id=TX_ID, is_extended_id=True, data=data))


def _wait_fc(bus, timeout):
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        msg = bus.recv(timeout=left)
        if msg is None or msg.arbitration_id != RX_ID or not msg.is_extended_id:
            continue
        d = bytes(msg.data) + b"\x00\x00\x00"
        if d[0] >> 4 == 3:
            return d[0] & 0x0F, d[1], d[2]


def send_isotp(bus, payload, fc_timeout=1.0):
    n = len(payload)
    if n <= 7:
        _send(bus, bytes([n]) + payload)
        return True
    _send(bus, bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6])
    pos, seq = 6, 1
    while pos < n:
        while True:
            fc = _wait_fc(bus, fc_timeout)
            if fc is None:
                return False
            status, bs, stmin = fc
            if status == 1:
                continue
            if status != 0:
                return False
            break
        sent = 0
        while pos < n and (bs == 0 or sent < bs):
            _send(bus, bytes([0x20 | seq]) + payload[pos:pos + 7])
            pos += 7
            seq = (seq + 1) & 0x0F
            sent += 1
            if pos < n and stmin:
                time.sleep(stmin / 1000 if stmin <= 0x7F else 0.0005)
    return True


def serve(bus, log, store, stop=None, until=None, record_path=None):
    rx = None
    while not (stop is not None and stop.is_set()):
        if until is not None and time.monotonic() >= until:
            return
        msg = bus.recv(timeout=0.2)
        if msg is None or msg.arbitration_id != RX_ID or not msg.is_extended_id:
            continue
        d = bytes(msg.data)
        if not d:
            continue
        pci = d[0] >> 4
        if pci == 0:
            req = d[1:1 + (d[0] & 0x0F)]
        elif pci == 1:
            rx = {"total": ((d[0] & 0x0F) << 8) | d[1], "data": bytearray(d[2:]), "seq": 1}
            _send(bus, bytes([0x30, 0x00, 0x00]))
            continue
        elif pci == 2:
            if rx is None or (d[0] & 0x0F) != rx["seq"]:
                log(f"!! unexpected consecutive frame {d.hex(' ')}")
                rx = None
                continue
            rx["data"] += d[1:]
            rx["seq"] = (rx["seq"] + 1) & 0x0F
            if len(rx["data"]) < rx["total"]:
                continue
            req = bytes(rx["data"][:rx["total"]])
            rx = None
        else:
            continue
        if not req:
            continue
        shown = req.hex(" ") if len(req) <= 12 else f"{req[:6].hex(' ')} ... ({len(req)} bytes)"
        log(f"<- MES  {shown}")
        reply = build_reply(req, store)
        if reply is None:
            log("   (no reply: suppress-positive-response)")
            continue
        ok = send_isotp(bus, reply)
        log(f"-> MES  {reply.hex(' ')}{'' if ok else '   !! flow control timed out'}")
        if req[0] == 0x2E and record_path and 0x2023 in store:
            with open(record_path, "w") as f:
                f.write(store[0x2023].hex() + "\n")
            log(f"   record 2023 ({len(store[0x2023])} bytes) saved to {record_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", default="can0")
    p.add_argument("--interface", default="socketcan")
    p.add_argument("--log", default="node61.log")
    p.add_argument("--record", default="node61-record-2023.hex")
    p.add_argument("--duration", type=float, default=0, help="seconds; 0 = until Ctrl-C")
    p.add_argument("--seed-record", help="hex file whose record 2023 is returned to 22 2023 reads")
    p.add_argument("--transmit", action="store_true", help="required: this sends frames")
    a = p.parse_args()
    if not a.transmit:
        sys.exit("refusing to run without --transmit: this script puts frames on the bus")
    t0 = time.monotonic()
    logf = open(a.log, "a")

    def log(line):
        s = f"{time.monotonic() - t0:8.3f}  {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    bus = can.Bus(interface=a.interface, channel=a.channel,
                  can_filters=[{"can_id": RX_ID, "can_mask": EXT_MASK, "extended": True}])
    log(f"answering as node 61 on {a.channel}: rx {RX_ID:08X}, tx {TX_ID:08X}")
    store = {}
    if a.seed_record:
        store[0x2023] = bytes.fromhex(open(a.seed_record).read().strip())
        log(f"seeded record 2023 ({len(store[0x2023])} bytes) from {a.seed_record}; 22 2023 will return it")
    try:
        serve(bus, log, store, until=(t0 + a.duration) if a.duration else None, record_path=a.record)
    except KeyboardInterrupt:
        pass
    finally:
        log("stopped")
        bus.shutdown()
        logf.close()


if __name__ == "__main__":
    main()
