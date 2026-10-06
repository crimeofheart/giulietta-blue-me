#!/usr/bin/env python3
"""Supervised one-off: impersonate Blue&Me node 4021 as a *working* module.

The frame set is measured on this car in captures/M-module-present-obd.log, with
the original module plugged in and the Pi tapped at OBD pins 1/9:

  reactive   1E114000#            -> 1E114021#<PROXI value>  (the module never did this)
             0E094000#001E0500046F -> 0E094021#000E
             0E094000#001E......   -> 0E094021#000A
  periodic   0A014021#4000000000000002 then #5000000000000002, 20 ms apart, 1 Hz
             08094021#0000000000000000                                        1 Hz
             06314021#0000000000000080                                        1 Hz

but that module is dead in the ways that matter: it never answers PROXI, it
reports zero track time, and it never gets offered as a radio source. The
payloads below therefore come from a *working* Blue&Me, in fmntf/fiatcan's own
trace Traces/03-radio-play-mute-menu-esc.log (confidence OTHER_VEHICLE; this
script is outside blueandme/ and so outside TxGate by the owner's decision):

  0E094021#001E              working status, not this car's module's 000E
  08094021#0000400000000000  idle track time, alternating with ...800000000000
  08094021#<MM><SS>40780...  while playing, BCD position, counting up
  06314021#...80             idle, muted -- NOT ...84

The source handshake, measured in that trace at 35.2-35.8 s and again at 73 s
and 133 s, and confirmed by karolkrupa/alfa-blue-me on the 159 (radio 0x405
payload 0C 00 .. = "open media player"):

  radio  06314005#0C00...    "open media player"   -> module answers ...84 in ~100 ms
  radio  06314005#2400... / 2000...  leaving       -> module answers ...80 in ~100 ms
  radio  06314005#0400000010000000  confirms the media player is playing

So the radio initiates and the module answers. Announcing ...84 unprompted, as
this script did before 2026-09-22, is not what a module does, and this car's
radio ignored it across two key cycles in captures/Z-align-after-bc-write.log.

Periodic frames stop when the bus goes quiet, so the Pi can never hold the car
awake. Run only with the original module UNPLUGGED: two nodes claiming 4021 would
contend. Outside TxGate by the owner's decision, 2026-09-21.
"""
import argparse
import re
import sys
import time

import can

try:  # run with /opt/blueandme/.venv/bin/python, as car_tx.sh does
    from blueandme.protocol import text as textcodec
except ImportError:  # pragma: no cover
    textcodec = None

EXT_MASK = 0x1FFFFFFF
CHALLENGE_ID = 0x1E114000
POLL_ID = 0x0E094000
RADIO_CHANNEL_ID = 0x06314005
PROXI_TX_ID = 0x1E114021
STATUS_TX_ID = 0x0E094021
WATCHDOG_ID = 0x0A014021
TRACK_TIME_ID = 0x08094021
AUDIO_CHANNEL_ID = 0x06314021
TEXT_ID = 0x0A394021

STEADY_POLL = bytes.fromhex("001E0500046F")
WORKING = bytes.fromhex("001E")
WORKING_DEAD_MODULE = bytes.fromhex("000E")
BOOTING = bytes.fromhex("000C")
SLEEPING = bytes.fromhex("000A")
WATCHDOG_A = bytes.fromhex("4000000000000002")
WATCHDOG_B = bytes.fromhex("5000000000000002")
TRACK_IDLE_A = bytes.fromhex("0000400000000000")
TRACK_IDLE_B = bytes.fromhex("0000800000000000")
TRACK_TRAILER = bytes.fromhex("407800000000")
AUDIO_MUTED = bytes.fromhex("0000000000000080")
AUDIO_MEDIA_PLAYER = bytes.fromhex("0000000000000084")
WATCHDOG_GAP_S = 0.020
#: Text, as the Doblo's working module sent it (tests/fixtures/doblo-02-driving.log):
#: 0028... once a second while the media player is not playing; on each new
#: track, the three fields below in 7 frames ~19 ms apart, header 2A (radio).
TEXT_IDLE = bytes.fromhex("0028000000000000")
TEXT_GAP_S = 0.019
TEXT_TEST = ("TEST ARTIST", "TEST ALBUM", "TEST TITLE")
#: The four codes whose glyph nobody has seen, between the digits 0-4 (codes
#: 1-5): the radio shows which, if any, is a bracket.
GLYPH_TEST = ("glyphs",)
GLYPH_CODES = [1, 0b110010, 2, 0b110011, 3, 0b110100, 4, 0b111110, 5]
DASH_EVERY_S = 10.0

#: Byte 0 of the radio's own 06314005. 0x0C hands the audio to the media player;
#: 0x20 and 0x24 take it back; 0x00 and 0x02 are the radio off or shutting down.
RADIO_OPEN_MEDIA_PLAYER = 0x0C
RADIO_LEAVES_MEDIA_PLAYER = (0x00, 0x02, 0x0F, 0x20, 0x24)

#: Let the kernel drop everything this node does not answer. Without this the
#: Zero W reads all ~116 frames/s of body traffic and answers the radio from
#: behind that queue: measured on vcan0 with capture E replaying underneath,
#: 78 ms median and 156 ms worst case, against a ~100 ms window. The buttons
#: frame is in the list only as a steady heartbeat for the idle check, since
#: the status poll alone is about 1.4 Hz.
HEARTBEAT_ID = 0x06354000
CAN_FILTERS = [{"can_id": i, "can_mask": EXT_MASK, "extended": True}
               for i in (CHALLENGE_ID, POLL_ID, RADIO_CHANNEL_ID, HEARTBEAT_ID)]


def track_time_frame(playing, position, toggle):
    """The module's 08094021 payload: a real position while playing, else idle."""
    if not playing:
        return TRACK_IDLE_A if toggle else TRACK_IDLE_B
    minutes, seconds = divmod(int(position) % 3600, 60)
    return bytes.fromhex(f"{minutes:02d}{seconds:02d}") + TRACK_TRAILER


def radio_track_text(artist, album, title):
    """The Doblo module's layout: "+" and 13 of the artist, 14 of the second
    field, 18 of the title, each ended by the field separator."""
    return f"+{artist}"[:14] + "\n" + (album or artist)[:14] + "\n" + title[:18] + "\n"


class PhoneTrack:
    """What the phone is playing, read from BlueZ every couple of seconds."""

    def __init__(self, every=2.0):
        import threading

        self.current = None
        self.every = every
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        import dbus

        bus = dbus.SystemBus()
        while True:
            try:
                manager = dbus.Interface(bus.get_object("org.bluez", "/"),
                                         "org.freedesktop.DBus.ObjectManager")
                track = None
                for ifaces in manager.GetManagedObjects().values():
                    player = ifaces.get("org.bluez.MediaPlayer1")
                    if player is not None and "Track" in player:
                        t = player["Track"]
                        track = (str(t.get("Artist", "")), str(t.get("Album", "")),
                                 str(t.get("Title", "")))
                        break
                self.current = track if track and track[2] else None
            except Exception:
                self.current = None
            time.sleep(self.every)


def send_text(bus, frames):
    for i, data in enumerate(frames):
        if i:
            time.sleep(TEXT_GAP_S)
        _send(bus, TEXT_ID, data)


def reply_for(msg, value, working=WORKING):
    if not msg.is_extended_id:
        return None
    data = bytes(msg.data)
    if msg.arbitration_id == CHALLENGE_ID:
        return PROXI_TX_ID, value
    if msg.arbitration_id == POLL_ID and data[:2] == b"\x00\x1e":
        return STATUS_TX_ID, working if data == STEADY_POLL else SLEEPING
    return None


def _send(bus, arb, data):
    bus.send(can.Message(arbitration_id=arb, is_extended_id=True, data=data))


def send_periodic(bus, log, audio, track):
    _send(bus, WATCHDOG_ID, WATCHDOG_A)
    time.sleep(WATCHDOG_GAP_S)
    _send(bus, WATCHDOG_ID, WATCHDOG_B)
    _send(bus, TRACK_TIME_ID, track)
    _send(bus, AUDIO_CHANNEL_ID, audio)
    log(f"periodic: watchdog pair, track time {track.hex().upper()}, "
        f"audio channel {audio.hex().upper()}")


def serve(bus, log, value, stop=None, until=None, period=1.0, idle_timeout=3.0,
          force_audio=None, boot_seconds=10.0, working=WORKING, track=None, dash=None):
    """``track``: a callable giving (artist, album, title) or None, shown on the
    radio while the media player plays. ``dash``: a fixed text for the
    instrument cluster, repeated every DASH_EVERY_S."""
    shown = None
    dash_at = 0.0
    last_rx = None
    next_tick = None
    wake_at = None
    last_poll_steady = None
    playing = False
    position = 0
    toggle = True
    while not (stop is not None and stop.is_set()):
        now = time.monotonic()
        if until is not None and now >= until:
            return
        msg = bus.recv(timeout=0.05)
        now = time.monotonic()
        if msg is not None:
            last_rx = now
            if msg.arbitration_id == POLL_ID and bytes(msg.data)[:2] == STEADY_POLL[:2]:
                steady = bytes(msg.data) == STEADY_POLL
                # The car's wake signature: the poll returns to steady after a
                # wind-down sequence. In capture H the real nodes answer 000C here.
                if steady and last_poll_steady is False:
                    wake_at = now
                    log("poll returned to steady: announcing 000C as a booting module")
                last_poll_steady = steady
            if msg.arbitration_id == RADIO_CHANNEL_ID and force_audio is None and msg.data:
                head = bytes(msg.data)[0]
                if head == RADIO_OPEN_MEDIA_PLAYER and not playing:
                    playing, position = True, 0
                    _send(bus, AUDIO_CHANNEL_ID, AUDIO_MEDIA_PLAYER)
                    log(f"radio asked for the media player ({head:02X}): answered "
                        f"{AUDIO_MEDIA_PLAYER.hex().upper()}")
                elif head in RADIO_LEAVES_MEDIA_PLAYER and playing:
                    playing = False
                    _send(bus, AUDIO_CHANNEL_ID, AUDIO_MUTED)
                    log(f"radio left the media player ({head:02X}): answered "
                        f"{AUDIO_MUTED.hex().upper()}")
            booting = wake_at is None or (now - wake_at) < boot_seconds
            reply = reply_for(msg, value, BOOTING if booting else working)
            if reply is not None:
                arb, data = reply
                _send(bus, arb, data)
                log(f"{msg.arbitration_id:08X}#{bytes(msg.data).hex().upper()} -> {arb:08X}#{data.hex().upper()}")
        awake = last_rx is not None and (now - last_rx) <= idle_timeout
        if not awake:
            if next_tick is not None:
                log("bus quiet: periodic frames stopped")
            next_tick = None
            wake_at = None
            playing = False
            continue
        if next_tick is None:
            next_tick = now + period
            wake_at = now
            log(f"bus awake: booting for {boot_seconds}s, then working {working.hex().upper()}")
        elif now >= next_tick:
            audio = force_audio if force_audio is not None else (
                AUDIO_MEDIA_PLAYER if playing else AUDIO_MUTED)
            send_periodic(bus, log, audio, track_time_frame(playing, position, toggle))
            if track is not None:
                now_playing = track() if playing else None
                if not playing:
                    _send(bus, TEXT_ID, TEXT_IDLE)
                    shown = None
                elif now_playing and now_playing != shown:
                    if now_playing == GLYPH_TEST:
                        message = "0 32 1 33 2 34 3 3E 4 (unseen glyphs between the digits)"
                        frames = textcodec.encode_code_frames(GLYPH_CODES, textcodec.Display.RADIO)
                    else:
                        message = radio_track_text(*now_playing)
                        frames = textcodec.encode_frames(message, textcodec.Display.RADIO)
                    send_text(bus, frames)
                    shown = now_playing
                    log(f"radio text: {message!r}")
            if dash and time.monotonic() >= dash_at:
                send_text(bus, textcodec.encode_frames(dash, textcodec.Display.DASHBOARD))
                dash_at = time.monotonic() + DASH_EVERY_S
                log(f"dashboard text: {dash!r}")
            toggle = not toggle
            if playing:
                position += period
            next_tick += period
            if next_tick < time.monotonic():
                next_tick = time.monotonic() + period


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", default="can0")
    p.add_argument("--interface", default="socketcan")
    p.add_argument("--log", default="node4021-full.log")
    p.add_argument("--value", help="required: the car's own PROXI value, 12 hex digits")
    p.add_argument("--period", type=float, default=1.0, help="seconds between periodic sets")
    p.add_argument("--idle-timeout", type=float, default=3.0, help="stop periodics after this much silence")
    p.add_argument("--boot-seconds", type=float, default=10.0,
                   help="report 000C booting for this long after the bus wakes, as the real module does")
    p.add_argument("--status-working", default="001E",
                   help="status answer once booted: 001E as a working Blue&Me, "
                        "000E as this car's own dead module")
    p.add_argument("--force-audio", default=None,
                   help="pin the audio channel low byte instead of following the radio: "
                        "80 muted, 81 phone, 82 voice, 83 nav, 84 media player")
    p.add_argument("--no-filters", action="store_true",
                   help="read every frame instead of only the four this node answers; "
                        "slower to react on a Zero W under real bus load")
    p.add_argument("--text", action="store_true",
                   help="show the phone's track on the radio while the media player plays")
    p.add_argument("--text-test", action="store_true",
                   help="as --text, but a fixed TEST ARTIST / TEST ALBUM / TEST TITLE")
    p.add_argument("--glyph-test", action="store_true",
                   help="radio text 0?1?2?3?4 with the four unseen glyph codes between "
                        "the digits: which of them, if any, is a bracket")
    p.add_argument("--dash-text", default=None,
                   help="a fixed text for the instrument cluster, every 10 s (untested format)")
    p.add_argument("--duration", type=float, default=0, help="seconds; 0 = until Ctrl-C")
    p.add_argument("--transmit", action="store_true", help="required: this sends frames")
    a = p.parse_args()
    if not a.transmit:
        sys.exit("refusing to run without --transmit: this script puts frames on the bus")
    if not a.value or not re.fullmatch(r"[0-9A-Fa-f]{12}", a.value):
        sys.exit("--value is required: the car's own PROXI value, 12 hex digits "
                 "(what its other nodes answer to 1E114000#)")
    if (a.text or a.text_test or a.glyph_test or a.dash_text) and textcodec is None:
        sys.exit("text needs the blueandme package: run with /opt/blueandme/.venv/bin/python")
    track = None
    if a.glyph_test:
        track = lambda: GLYPH_TEST  # noqa: E731
    elif a.text_test:
        track = lambda: TEXT_TEST  # noqa: E731
    elif a.text:
        phone = PhoneTrack()
        track = lambda: phone.current  # noqa: E731
    value = bytes.fromhex(a.value)
    working = bytes.fromhex(a.status_working)
    force_audio = None if a.force_audio is None else bytes(7) + bytes.fromhex(a.force_audio)
    t0 = time.monotonic()
    logf = open(a.log, "a")

    def log(line):
        s = f"{time.monotonic() - t0:8.3f}  {line}"
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    bus = can.Bus(interface=a.interface, channel=a.channel,
                  can_filters=None if a.no_filters else CAN_FILTERS)
    log(f"impersonating node 4021 on {a.channel}: PROXI value {value.hex().upper()}, "
        f"status {working.hex().upper()}, period {a.period}s, "
        + ("audio follows the radio" if force_audio is None
           else f"audio pinned to {force_audio.hex().upper()}"))
    try:
        serve(bus, log, value, until=(t0 + a.duration) if a.duration else None,
              period=a.period, idle_timeout=a.idle_timeout, force_audio=force_audio,
              boot_seconds=a.boot_seconds, working=working, track=track,
              dash=a.dash_text)
    except KeyboardInterrupt:
        pass
    finally:
        log("stopped")
        bus.shutdown()
        logf.close()


if __name__ == "__main__":
    main()
