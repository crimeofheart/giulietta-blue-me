"""Steering-wheel buttons to the phone, the way the Giulietta's Blue&Me did it.

What each button does comes from the Giulietta Blue&Me manual (604_38_194,
p. 9). The action happens on release, and a press held for more than a second
is a long press:

    phone  short: answer a ringing call; swap to the waiting or held call;
                  close the assistant
           long:  reject a ringing call; end the call in progress
    mute   short: mute / unmute the mic in a call; close the assistant.
                  Outside calls nothing here: on the car the radio itself
                  leaves Blue&Me for the previous source ~0.5 s after mute
                  is pressed, short or held (2026-09-27), and leaving pauses
                  the phone (see the app); SRC back to Blue&Me resumes it.
                  A pause from here would only fight that.
    voice  short: open the phone's own assistant (owner, 2026-09-26)
    next / prev:  next / previous track, outside calls
    source short: in a call, move it between the car and the phone. Outside
                  calls the radio uses the same button to change source.
                  To the phone: the Pi drops the call's (e)SCO audio link at
                  the radio (HCI Disconnect), and the phone takes the call to
                  its earpiece. Back: AT+BCC asks the phone to set the audio
                  link up again. Both verified with a call, 2026-09-26.
                  (Stopping the players that hold the link is not enough on
                  bluealsa 4.3.1: the link stays up and the call goes silent.)
    volume_up / volume_down: nothing here; the radio sets its own volume.

The call state is asked of the phone at each press (``AT+CLCC``) rather than
tracked, so nothing can drift out of step with it. Everything here talks to the
phone over Bluetooth; nothing is sent on CAN.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Protocol

from ..events import EventBus

log = logging.getLogger("blueandme.wheel")

#: Longer than this is a long press (manual: "more than one second").
LONG_PRESS_S = 1.0

#: An assistant session the phone ended on its own is forgotten after this.
#: Android ends it itself once it has answered (bench, 2026-09-26: AT+BVRA=0
#: then gets ERROR), leaving only its page on the phone's screen.
ASSISTANT_S = 30.0

#: The phone answers AT+BVRA=1 only once the call audio is up, 3-5 s later,
#: and its answer means nothing: the OnePlus opened the assistant each time
#: on the bench (2026-09-26) while answering nothing, OK or ERROR.
ASSISTANT_OPEN_S = 8.0

#: How often calls are checked while the mic is muted or a call sits on the phone.
WATCH_S = 2.0

#: The radio's arrow pad on Blue&Me (owner, 2026-09-27): left / right =
#: previous / next, held longer than this = rewind / fast forward until let go;
#: up / down = the music's volume, a step per press; held, it repeats after
#: VOLUME_REPEAT_S and quickens to VOLUME_FASTEST_S (owner, 2026-09-27: twice
#: the first try's 0.35 / 0.08 s).
SEEK_AFTER_S = 0.8
VOLUME_STEP = 8           # of bluealsa's 0..127
VOLUME_REPEAT_S = 0.175
VOLUME_FASTEST_S = 0.04

#: The mic relay (sco_uplink) sends silence while this file exists.
MIC_MUTE_FLAG = Path("/run/blueandme/mic-mute")

#: A line of ``hcitool con`` for a call audio link: kind, address, handle.
_SCO_LINK = re.compile(r"\b(e?SCO)\s+([0-9A-F:]{17})\s+handle\s+(\d+)")
#: HCI Disconnect (OGF 0x01, OCF 0x0006), reason 0x13: remote user terminated.
HCI_DISCONNECT = ("0x01", "0x0006")

# +CLCC call states (3GPP TS 27.007).
ACTIVE, HELD, DIALING, ALERTING, INCOMING, WAITING = range(6)


class Call(NamedTuple):
    index: int
    status: int
    number: str = ""
    name: str = ""


_CLCC = re.compile(r'\+CLCC:\s*(\d+),\s*(\d+),\s*(\d+),\s*\d+,\s*\d+'
                   r'(?:,\s*"([^"]*)",\s*\d+(?:,\s*"([^"]*)")?)?')


def parse_clcc(lines: list[str]) -> list[Call]:
    """Calls from the phone's answer to ``AT+CLCC``."""
    calls = []
    for line in lines:
        m = _CLCC.search(line)
        if m:
            calls.append(Call(int(m.group(1)), int(m.group(3)),
                              m.group(4) or "", m.group(5) or ""))
    return calls


def decide(button: str, long: bool, calls: list[Call], assistant: bool) -> Optional[str]:
    """The action for one button release. No side effects."""
    states = {c.status for c in calls}
    in_call = bool(states & {ACTIVE, HELD, DIALING, ALERTING})
    if button == "phone":
        if long:
            return "hangup" if calls else None
        if assistant:
            return "assistant_off"
        if WAITING in states or (HELD in states and ACTIVE in states):
            return "swap"
        if INCOMING in states:
            return "answer"
        return None
    if button == "mute":
        if assistant:
            return "assistant_off"
        if ACTIVE in states:
            return "mic_toggle"
        return None
    if button == "voice":
        return None if calls else "assistant_on"
    if button in ("next", "prev"):
        return None if calls else ("next" if button == "next" else "previous")
    if button == "source":
        return "audio_toggle" if in_call else None
    return None


AT_COMMANDS = {
    "answer": "ATA",
    "hangup": "AT+CHUP",
    "swap": "AT+CHLD=2",
    "assistant_on": "AT+BVRA=1",
    "assistant_off": "AT+BVRA=0",
}


class Phone(Protocol):
    """What the wheel needs from the phone. `BluetoothPhone` is the real one."""

    def calls(self) -> list[Call]: ...
    def at(self, command: str, timeout: float = 3.0) -> Optional[str]:
        """The phone's final result code ("OK", "ERROR"), None if none came."""
    def media(self, action: str) -> bool: ...
    def mic_muted(self) -> bool: ...
    def set_mic_muted(self, muted: bool) -> None: ...
    def audio_to_phone(self) -> bool: ...
    def audio_to_car(self) -> bool: ...
    def call_audio_up(self) -> bool: ...
    def volume_step(self, delta: int) -> None: ...


class WheelControl:
    """Acts on button releases, one at a time, off the CAN receive thread."""

    def __init__(self, events: EventBus, phone: Phone,
                 clock: Callable[[], float] = time.monotonic,
                 watch_s: float = WATCH_S) -> None:
        self.events = events
        self.phone = phone
        self.clock = clock
        self.watch_s = watch_s
        self._assistant_since: Optional[float] = None
        self._audio_on_phone = False
        self._jobs: "queue.Queue[tuple[str, float]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._watcher: Optional[threading.Thread] = None
        events.on("button_released", self.on_release)
        events.on("radio_key", self.on_radio_key)
        events.on("button", self.on_press)
        self._key_down: dict[str, int] = {}   # key -> press number, while held
        self._presses = 0
        self._seeking: Optional[str] = None

    @property
    def assistant(self) -> bool:
        """Open: the first ASSISTANT_OPEN_S while its audio link comes up, then
        for as long as the link stays up (at most ASSISTANT_S). The phone ends
        the session itself once it has answered; it drops the link then, and
        the radio leaves its "voice" screen (car, 2026-09-27)."""
        since = self._assistant_since
        if since is None:
            return False
        elapsed = self.clock() - since
        if elapsed < ASSISTANT_OPEN_S:
            return True
        if elapsed < ASSISTANT_S and self.phone.call_audio_up():
            return True
        self._assistant_since = None
        log.info("assistant closed (audio link down)")
        return False

    #: The wheel's arrows work like the radio's left / right: a tap skips,
    #: held they rewind / fast forward (owner, 2026-09-27).
    WHEEL_AS_RADIO_KEY = {"prev": "left", "next": "right"}

    def on_press(self, name: str) -> None:
        if name in self.WHEEL_AS_RADIO_KEY:
            self.on_radio_key(self.WHEEL_AS_RADIO_KEY[name], True)

    def on_release(self, name: str, held_s: float) -> None:
        if name in ("volume_up", "volume_down"):
            return  # the radio's
        if name in self.WHEEL_AS_RADIO_KEY:
            self.on_radio_key(self.WHEEL_AS_RADIO_KEY[name], False)
            return
        self._jobs.put((name, held_s))
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="wheel", daemon=True)
            self._worker.start()

    def on_radio_key(self, key: str, pressed: bool) -> None:
        """The radio's arrow pad; runs off the CAN receive thread."""
        if pressed:
            self._presses += 1
            self._key_down[key] = self._presses
            threading.Thread(target=self._hold, args=(key, self._presses),
                             name="radio-key", daemon=True).start()
            return
        self._key_down.pop(key, None)
        if key in ("left", "right"):
            if self._seeking == key:
                self._seeking = None
                self._radio_media("play")            # ends the seek
            else:
                self._radio_media("previous" if key == "left" else "next")

    def _hold(self, key: str, press: int) -> None:
        if key in ("up", "down"):
            wait = VOLUME_REPEAT_S
            while self._key_down.get(key) == press:
                self.phone.volume_step(VOLUME_STEP if key == "up" else -VOLUME_STEP)
                time.sleep(wait)
                wait = max(VOLUME_FASTEST_S, wait * 0.6)
            return
        time.sleep(SEEK_AFTER_S)
        if self._key_down.get(key) == press:
            self._seeking = key
            self._radio_media("rewind" if key == "left" else "fast_forward")

    def _radio_media(self, action: str) -> None:
        log.info("radio key: %s", action)
        self.phone.media(action)
        if action in ("next", "previous"):
            self.events.emit("media_skipped", action)

    def _work(self) -> None:
        while True:
            name, held_s = self._jobs.get()
            try:
                self.handle(name, held_s)
            except Exception:
                log.exception("button %s failed", name)

    def handle(self, name: str, held_s: float) -> Optional[str]:
        """Decide and act on one release. Returns the action taken, if any."""
        long = held_s > LONG_PRESS_S
        calls = self.phone.calls()
        action = decide(name, long, calls, self.assistant)
        log.info("%s %s press, %d call(s)%s: %s", name, "long" if long else "short",
                 len(calls), ", assistant open" if self.assistant else "",
                 action or "nothing")
        if action is None:
            return None
        if action in AT_COMMANDS:
            timeout = ASSISTANT_OPEN_S if action == "assistant_on" else 3.0
            result = self.phone.at(AT_COMMANDS[action], timeout)
            if action == "assistant_on":
                self._assistant_since = self.clock()  # whatever it answered
            elif action == "assistant_off":
                self._assistant_since = None
                if result != "OK":
                    # The phone had closed it already: the button does its
                    # usual job instead (mute pauses the music).
                    instead = decide(name, long, calls, assistant=False)
                    if instead is not None and instead not in AT_COMMANDS:
                        log.info("assistant already closed: %s instead", instead)
                        self._act(instead)
                        return instead
            elif action == "hangup" and len(calls) <= 1:
                self._restore(call_over=True)
            return action
        self._act(action)
        return action

    def _act(self, action: str) -> None:
        """The actions that are not a single AT command."""
        if action == "mic_toggle":
            self.phone.set_mic_muted(not self.phone.mic_muted())
            self._watch()
        elif action == "audio_toggle":
            if self._audio_on_phone:
                self._audio_on_phone = not self.phone.audio_to_car()
            else:
                self._audio_on_phone = self.phone.audio_to_phone()
                self._watch()
        else:  # next, previous, play_pause
            self.phone.media(action)
            self.events.emit("media_skipped", action)

    def _restore(self, call_over: bool) -> None:
        """After a call: mic on again, and forget that the call was on the
        phone (the next call comes to the car by itself)."""
        if self.phone.mic_muted():
            self.phone.set_mic_muted(False)
        if call_over:
            self._audio_on_phone = False

    def _watch(self) -> None:
        """While the mic is muted or a call sits on the phone, check for the end
        of the call, so the next call starts with neither."""
        if self._watcher is not None and self._watcher.is_alive():
            return

        def watch() -> None:
            while self.phone.mic_muted() or self._audio_on_phone:
                time.sleep(self.watch_s)
                if not self.phone.calls():
                    log.info("call over: mic on, next call to the car")
                    self._restore(call_over=True)
                    return

        self._watcher = threading.Thread(target=watch, name="wheel-watch", daemon=True)
        self._watcher.start()


class BluetoothPhone:
    """The phone that is connected for hands-free, through bluealsa and BlueZ."""

    #: The hands-free phone's address is looked up (a busctl start, which on
    #: the Zero W once starved the call audio) at most this often.
    ADDRESS_S = 30.0

    def __init__(self, mute_flag: Path = MIC_MUTE_FLAG,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.mute_flag = mute_flag
        self.clock = clock
        self._address_cache: Optional[str] = None
        self._address_at = 0.0
        # One user of bluealsa's RFCOMM link at a time: the wheel and the poll.
        self._link = threading.Lock()

    # -- hands-free: AT commands over bluealsa's RFCOMM link ---------------

    def _address(self) -> Optional[str]:
        from .sco_uplink import hfp_sinks, read_pcms

        now = self.clock()
        if self._address_cache is None or now - self._address_at >= self.ADDRESS_S:
            phones = hfp_sinks(read_pcms())
            self._address_cache = phones[0].address if phones else None
            self._address_at = now
        return self._address_cache

    def _send(self, command: str, timeout: float = 3.0) -> list[str]:
        from .call_control import open_rfcomm, send

        with self._link:
            address = self._address()
            if address is None:
                return []
            try:
                fd = open_rfcomm(address)
            except Exception:
                self._address_cache = None   # gone, or another phone: look again
                raise
            try:
                return send(fd, command, timeout)
            finally:
                os.close(fd)

    def calls(self, quiet: bool = False) -> list[Call]:
        try:
            return parse_clcc(self._send("AT+CLCC"))
        except Exception:
            if not quiet:
                log.warning("could not list calls", exc_info=True)
            return []

    def at(self, command: str, timeout: float = 3.0) -> Optional[str]:
        from .call_control import final_result

        try:
            lines = self._send(command, timeout)
        except Exception:
            log.warning("%s failed", command, exc_info=True)
            return None
        result = final_result(lines)
        log.info("%s -> %s", command, result or "no answer")
        return result

    # -- music: BlueZ MediaPlayer1 ------------------------------------------

    def player(self) -> "tuple[Optional[tuple[str, str, str]], Optional[float], bool]":
        """(artist, album, title), position in seconds, playing: in one call."""
        import dbus

        try:
            manager = dbus.Interface(dbus.SystemBus().get_object("org.bluez", "/"),
                                     "org.freedesktop.DBus.ObjectManager")
            for ifaces in manager.GetManagedObjects().values():
                player = ifaces.get("org.bluez.MediaPlayer1")
                if player is None:
                    continue
                t = player.get("Track", {})
                title = str(t.get("Title", "") or "")
                track = ((str(t.get("Artist", "") or ""), str(t.get("Album", "") or ""), title)
                         if title and title != "Not Provided" else None)
                position = player.get("Position")
                return (track, int(position) / 1000 if position is not None else None,
                        str(player.get("Status", "")) == "playing")
        except Exception:
            log.debug("no player", exc_info=True)
        return None, None, False

    def media(self, action: str) -> bool:
        import dbus

        bus = dbus.SystemBus()
        manager = dbus.Interface(bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager")
        for path, ifaces in manager.GetManagedObjects().items():
            player = ifaces.get("org.bluez.MediaPlayer1")
            if player is None:
                continue
            if action == "play_pause":
                action = "pause" if str(player.get("Status")) == "playing" else "play"
            method = {"next": "Next", "previous": "Previous",
                      "play": "Play", "pause": "Pause",
                      "fast_forward": "FastForward", "rewind": "Rewind"}[action]
            try:
                dbus.Interface(bus.get_object("org.bluez", path),
                               "org.bluez.MediaPlayer1").get_dbus_method(method)()
                return True
            except Exception:
                log.warning("%s on %s failed", method, path, exc_info=True)
                return False
        log.info("no media player on the phone; %s ignored", action)
        return False

    # -- mic mute: a flag file the mic relay reads -------------------------

    def mic_muted(self) -> bool:
        return self.mute_flag.exists()

    def set_mic_muted(self, muted: bool) -> None:
        if muted:
            self.mute_flag.parent.mkdir(parents=True, exist_ok=True)
            self.mute_flag.touch()
        else:
            self.mute_flag.unlink(missing_ok=True)
        log.info("mic %s", "muted" if muted else "on")

    # -- call audio: the (e)SCO link at the radio ---------------------------

    def _sco_handles(self) -> list[int]:
        out = subprocess.run(["hcitool", "con"], capture_output=True, text=True,
                             timeout=5).stdout
        return [int(m.group(3)) for m in _SCO_LINK.finditer(out)]

    def volume_step(self, delta: int) -> None:
        """The music's volume, both channels, through bluealsa's A2DP PCM
        (0..127 each, in the high and low byte)."""
        import dbus

        try:
            bus = dbus.SystemBus()
            manager = dbus.Interface(bus.get_object("org.bluealsa", "/org/bluealsa"),
                                     "org.freedesktop.DBus.ObjectManager")
            for path, ifaces in manager.GetManagedObjects().items():
                pcm = ifaces.get("org.bluealsa.PCM1")
                if pcm is None or "a2dpsnk" not in str(path):
                    continue
                volume = int(pcm.get("Volume", 0x7F7F))
                left = max(0, min(127, (volume >> 8 & 0x7F) + delta))
                right = max(0, min(127, (volume & 0x7F) + delta))
                dbus.Interface(bus.get_object("org.bluealsa", path),
                               "org.freedesktop.DBus.Properties").Set(
                    "org.bluealsa.PCM1", "Volume", dbus.UInt16(left << 8 | right))
                log.info("music volume %d/127", left)
                return
            log.info("no music PCM; volume step ignored")
        except Exception:
            log.warning("volume step failed", exc_info=True)

    def call_audio_up(self) -> bool:
        """An (e)SCO link to the phone is up: a call or the assistant."""
        try:
            return bool(self._sco_handles())
        except Exception:
            log.debug("hcitool con failed", exc_info=True)
            return True   # unknown: leave it to ASSISTANT_S

    def audio_to_phone(self) -> bool:
        """Drop the call's audio link; the phone moves the call to its earpiece."""
        handles = self._sco_handles()
        for handle in handles:
            subprocess.run(["hcitool", "cmd", *HCI_DISCONNECT, f"0x{handle & 0xFF:02x}",
                            f"0x{handle >> 8:02x}", "0x13"], capture_output=True, timeout=5)
        log.info("call audio to the phone (link %s dropped)",
                 ", ".join(map(str, handles)) or "none: nothing")
        return bool(handles)

    def audio_to_car(self) -> bool:
        """Ask the phone to set the call's audio link up again (AT+BCC)."""
        ok = self.at("AT+BCC") == "OK"
        log.info("call audio back to the car%s", "" if ok else ": the phone refused")
        return ok
