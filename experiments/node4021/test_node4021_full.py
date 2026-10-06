"""Check the full node-4021 impersonation: replies, periodic streams, and the idle rule."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import can

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import node4021_full as r

VALUE = "0A1B2C3D4E5F"   # made up

PERIOD = 0.25
IDLE = 0.6
failures = []
seen = []


def collect(bus, seconds, poke=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if poke is not None and time.monotonic() >= poke[0]:
            bus.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=r.STEADY_POLL))
            poke[0] += 0.1
        m = bus.recv(timeout=0.05)
        if m is not None:
            seen.append((time.monotonic(), m.arbitration_id, bytes(m.data).hex().upper()))


def ids_of(kind, since=0.0):
    return [(t, d) for t, a, d in seen if a == kind and t >= since]


responder_bus = can.Bus(interface="virtual", channel="node4021fulltest")
car = can.Bus(interface="virtual", channel="node4021fulltest")
stop, lines = threading.Event(), []
t = threading.Thread(target=r.serve, args=(responder_bus, lines.append, bytes.fromhex(VALUE), stop),
                     kwargs={"period": PERIOD, "idle_timeout": IDLE, "boot_seconds": 0.0})
t.start()

try:
    car.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=r.STEADY_POLL))
    car.send(can.Message(arbitration_id=r.CHALLENGE_ID, is_extended_id=True, data=b""))
    car.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=bytes.fromhex("001E00000029")))
    start = time.monotonic()
    collect(car, 1.2, poke=[time.monotonic() + 0.1])

    status = [d for _, d in ids_of(r.STATUS_TX_ID)]
    if "001E" not in status or "000A" not in status:
        failures.append(f"status replies wrong: {status[:6]}")
    proxi = [d for _, d in ids_of(r.PROXI_TX_ID)]
    if proxi != [VALUE]:
        failures.append(f"PROXI answer wrong: {proxi}")

    wd = [(t_, d) for t_, d in ids_of(r.WATCHDOG_ID)]
    pairs = list(zip(wd[::2], wd[1::2]))
    if len(pairs) < 3:
        failures.append(f"expected at least 3 watchdog pairs in 1.2 s at {PERIOD}s, got {len(pairs)}")
    for (t1, d1), (t2, d2) in pairs:
        if (d1, d2) != ("4000000000000002", "5000000000000002"):
            failures.append(f"watchdog pair wrong: {d1} {d2}")
        elif not 0.010 <= t2 - t1 <= 0.060:
            failures.append(f"watchdog gap {t2 - t1:.3f}s outside 10-60 ms")
    # idle, with no radio request: muted, and the two alternating idle track times
    for name, arb, want in (("track time", r.TRACK_TIME_ID,
                             {"0000400000000000", "0000800000000000"}),
                            ("audio channel", r.AUDIO_CHANNEL_ID,
                             {"0000000000000080"})):
        vals = [d for _, d in ids_of(arb)]
        if len(vals) < 3:
            failures.append(f"{name}: expected at least 3 frames, got {len(vals)}")
        if not set(vals) <= want:
            failures.append(f"{name}: unexpected payload {set(vals) - want}")
    idle_track = [d for _, d in ids_of(r.TRACK_TIME_ID)]
    if len(set(idle_track)) < 2:
        failures.append(f"idle track time should alternate, got {set(idle_track)}")

    quiet_from = time.monotonic()
    collect(car, IDLE + 0.6)
    late = [x for x in seen if x[0] > quiet_from + IDLE + 0.2]
    if late:
        failures.append(f"kept transmitting {len(late)} frames after the bus went quiet")
finally:
    stop.set()
    t.join()
    responder_bus.shutdown()
    car.shutdown()

# the source handshake: the radio asks, the module answers -- never the other way round
seen.clear()
hs_bus = can.Bus(interface="virtual", channel="node4021hstest")
hs_car = can.Bus(interface="virtual", channel="node4021hstest")
hs_stop = threading.Event()
ht = threading.Thread(target=r.serve, args=(hs_bus, [].append, bytes.fromhex(VALUE), hs_stop),
                      kwargs={"period": PERIOD, "idle_timeout": IDLE, "boot_seconds": 0.0})
ht.start()


def radio(payload):
    hs_car.send(can.Message(arbitration_id=r.RADIO_CHANNEL_ID, is_extended_id=True,
                            data=bytes.fromhex(payload)))


try:
    hs_car.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=r.STEADY_POLL))
    collect(hs_car, 0.4, poke=[time.monotonic() + 0.1])

    asked = time.monotonic()
    radio("0C00000000000000")                      # "open media player"
    collect(hs_car, 0.8, poke=[time.monotonic() + 0.1])
    answers = [(t_, d) for t_, d in ids_of(r.AUDIO_CHANNEL_ID, since=asked)]
    first = next((x for x in answers if x[1] == "0000000000000084"), None)
    if first is None:
        failures.append(f"no 84 after the radio asked: {[d for _, d in answers]}")
    elif first[0] - asked > 0.2:
        failures.append(f"answered 84 after {first[0] - asked:.3f}s, the module takes ~0.1s")
    playing_track = [d for t_, d in ids_of(r.TRACK_TIME_ID, since=asked) if t_ > first[0]] if first else []
    if not playing_track or any(not d.endswith("407800000000") for d in playing_track):
        failures.append(f"track time while playing should carry the 4078 trailer: {playing_track}")

    left = time.monotonic()
    radio("2400000000000000")                      # radio takes the audio back
    collect(hs_car, 0.8, poke=[time.monotonic() + 0.1])
    back = [(t_, d) for t_, d in ids_of(r.AUDIO_CHANNEL_ID, since=left) if d == "0000000000000080"]
    if not back:
        failures.append("did not go back to 80 when the radio left the media player")
    elif back[0][0] - left > 0.2:
        failures.append(f"went back to 80 after {back[0][0] - left:.3f}s")
    after = [d for t_, d in ids_of(r.TRACK_TIME_ID, since=back[0][0])] if back else []
    if any(d.endswith("407800000000") for d in after):
        failures.append(f"track time should be idle again after leaving: {after}")
finally:
    hs_stop.set()
    ht.join()
    hs_bus.shutdown()
    hs_car.shutdown()

# the real module reports 000C while booting, then 001E
boot_bus = can.Bus(interface="virtual", channel="node4021boottest")
boot_car = can.Bus(interface="virtual", channel="node4021boottest")
boot_stop = threading.Event()
bt = threading.Thread(target=r.serve, args=(boot_bus, [].append, bytes.fromhex(VALUE), boot_stop),
                      kwargs={"period": 9, "idle_timeout": 5, "boot_seconds": 30})
bt.start()
try:
    boot_car.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=r.STEADY_POLL))
    m = boot_car.recv(timeout=2)
    got = bytes(m.data).hex().upper() if m else "nothing"
    if got != "000C":
        failures.append(f"boot phase: answered {got}, expected 000C")
finally:
    boot_stop.set()
    bt.join()
    boot_bus.shutdown()
    boot_car.shutdown()

# text: idle 0028 until the media player plays, then the track once, and the
# dashboard text; the frames must decode back to what was meant
if r.textcodec is not None:
    tx_bus = can.Bus(interface="virtual", channel="node4021texttest")
    tx_car = can.Bus(interface="virtual", channel="node4021texttest")
    tx_stop = threading.Event()
    tt = threading.Thread(target=r.serve, args=(tx_bus, [].append, bytes.fromhex(VALUE), tx_stop),
                          kwargs={"period": PERIOD, "idle_timeout": IDLE, "boot_seconds": 0.0,
                                  "track": lambda: r.TEXT_TEST, "dash": "HELLO GIULIETTA"})
    tt.start()
    texts, every_text = [], []

    def grab(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            tx_car.send(can.Message(arbitration_id=r.POLL_ID, is_extended_id=True, data=r.STEADY_POLL))
            m = tx_car.recv(timeout=0.05)
            while m is not None:
                if m.arbitration_id == r.TEXT_ID:
                    texts.append(bytes(m.data))
                    every_text.append(bytes(m.data))
                m = tx_car.recv(timeout=0.0)
            time.sleep(0.05)

    try:
        grab(0.7)
        idle = [d for d in texts if d == r.TEXT_IDLE]
        if not idle:
            failures.append("no 0028 idle text before the media player played")
        texts.clear()
        tx_car.send(can.Message(arbitration_id=r.RADIO_CHANNEL_ID, is_extended_id=True,
                                data=bytes.fromhex("0C00000000000000")))
        grab(1.0)
        radio_frames = [d for d in texts if d[1] >> 4 == 2 and d != r.TEXT_IDLE]
        dash_frames = [d for d in every_text if d[1] >> 4 == 1]  # sent at the start
        want = r.radio_track_text(*r.TEXT_TEST)
        got = r.textcodec.decode_frames(radio_frames[:(radio_frames[0][0] >> 4) + 1])[0] if radio_frames else ""
        if got != want.rstrip("\n") and got != want:
            failures.append(f"radio text {got!r}, expected {want!r}")
        if len(radio_frames) != (radio_frames[0][0] >> 4) + 1 if radio_frames else True:
            failures.append(f"radio text should go once per track, got {len(radio_frames)} frames")
        dash = r.textcodec.decode_frames(dash_frames)[0] if dash_frames else ""
        if dash != "HELLO GIULIETTA":
            failures.append(f"dashboard text {dash!r}")
    finally:
        tx_stop.set()
        tt.join()
        tx_bus.shutdown()
        tx_car.shutdown()

guard = subprocess.run([sys.executable, str(HERE / "node4021_full.py")], capture_output=True, text=True)
if guard.returncode == 0 or "--transmit" not in guard.stderr:
    failures.append(f"guard: exit {guard.returncode}, stderr {guard.stderr!r}")

if failures:
    print("FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print(f"PASS: replies correct, {len(seen)} frames seen, periodic streams at {PERIOD}s, "
      f"stopped within {IDLE}s of silence, refuses to run without --transmit"
      + (", radio and dashboard text" if r.textcodec is not None else ", text NOT tested (no blueandme)"))
