"""Node 4021 on the Giulietta: what the Blue&Me module says on B-CAN.

Ported from experiments/node4021/node4021_full.py, the supervised script whose
frames the car accepted in capture ZB (2026-09-23): no blink, and the radio
offered and selected Blue&Me. The frames and payloads live in the profile
(protocol/profiles/giulietta940.py), each citing its capture.

    reactive   PROXI challenge            -> the car's PROXI value (the setting
                                             can.proxi_value), at once
               status poll, steady        -> 000C for BOOT_S after the bus
                                             wakes, then 001E working
               status poll, winding down  -> 000A sleeping
               radio asks for the media player (0C) -> audio channel ...84 at
                                             once; the radio leaving -> ...80
    periodic   once a second while the bus is awake: the watchdog pair, track
               time, audio channel. Nothing after IDLE_S of silence, so the Pi
               can never hold the car awake.
    phone      ...0481 on the audio channel for as long as a call is up, and
               ...0482 while the phone's assistant is open, so the radio
               plays them even from another source (the Doblo's values, owner's
               decision 2026-09-26); the change goes out at once.
    text       the phone's track on the radio, on each new track while the
               media player plays, else the idle frame once a second; the
               caller on the instrument cluster while a call rings or runs.
               The Doblo module's format (tests/fixtures/doblo-02-driving.log),
               sent on the owner's decision (2026-09-26) until the car shows
               otherwise.

It answers the radio only when asked: ...84 announced unprompted was ignored
(capture Z). Every frame goes out through the caller's ``send``, which is the
transmit gate: in listen-only mode this class decides and nothing leaves.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..can.bus import ReceivedFrame
from ..events import EventBus
from ..protocol import text as textcodec
from ..protocol.profiles import giulietta940 as P

log = logging.getLogger("blueandme.node")

#: Frames this node needs from the profile.
RX_FRAMES = ("bc_status_poll", "bc_proxi_challenge", "radio_media_request")
TX_FRAMES = ("bm_status_response", "bm_proxi_response", "bm_watchdog",
             "bm_track_time", "bm_audio_channel", "bm_text_message")

#: The cluster's caller text is sent again this often, in case the cluster
#: showed something else in between.
DASH_REPEAT_S = 10.0

Send = Callable[[str, bytes], bool]


def radio_track_text(artist: str, album: str, title: str) -> str:
    """The Doblo module's three fields: "+" and the artist, the album (or the
    artist again), the title, cut to 14, 14 and 18, each ended by the separator."""
    a, b, c = P.RADIO_TEXT_FIELDS
    return f"+{artist}"[:a] + "\n" + (album or artist)[:b] + "\n" + title[:c] + "\n"


def track_time(playing: bool, position_s: float, toggle: bool) -> bytes:
    """08094021: BCD minutes and seconds while playing, else the idle pair."""
    if not playing:
        return P.TRACK_IDLE[0] if toggle else P.TRACK_IDLE[1]
    minutes, seconds = divmod(int(position_s) % 3600, 60)
    return bytes.fromhex(f"{minutes:02d}{seconds:02d}") + P.TRACK_TRAILER


class BlueAndMeNode:
    def __init__(self, events: EventBus, send: Send,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 boot_s: float = P.BOOT_S, idle_s: float = P.IDLE_S,
                 proxi_value: Optional[bytes] = None) -> None:
        self.events = events
        #: This car's PROXI value; None: the challenge is not answered.
        self.proxi_value = proxi_value
        self._proxi_warned = False
        self.send = send
        self.clock = clock
        self.sleep = sleep
        self.boot_s = boot_s
        self.idle_s = idle_s
        self._lock = threading.Lock()
        #: One text message at a time: the tick's and the scroll's frames
        #: must not interleave.
        self._text_lock = threading.Lock()
        self._scroll_i = 0
        self._scrolled: Optional[str] = None
        self.last_rx: Optional[float] = None
        self.wake_at: Optional[float] = None
        self.periodic = False
        self.last_poll_steady: Optional[bool] = None
        self.playing = False
        self.position_s = 0.0
        self._toggle = True
        self._last_tick: Optional[float] = None
        #: (artist, album, title) the phone is playing, and the caller's name or
        #: number while a call rings or runs; the app keeps both up to date.
        self.track: Optional[tuple[str, str, str]] = None
        self.caller: Optional[str] = None
        self._shown_track: Optional[tuple[str, str, str]] = None
        self._shown_caller: Optional[str] = None
        self._caller_sent_at = 0.0
        self.call = False
        self.assistant = False
        self.phone_playing = True
        #: The radio muted Blue&Me (0x24 with Blue&Me still selected).
        self.radio_muted = False
        #: The radio's arrow keys held now.
        self._radio_keys: set[str] = set()
        #: The phone's last position, and how far it runs ahead of the track:
        #: after "previous" restarts the song, the OnePlus keeps reporting the
        #: old position (2026-09-27), so it is counted from the restart.
        self._phone_pos: Optional[float] = None
        self._pos_offset = 0.0
        self._offset_track: Optional[tuple[str, str, str]] = None

    # -- receive: answers go out at once -------------------------------------

    def on_frame(self, frame: ReceivedFrame) -> None:
        """Any frame the app receives; the wheel's 2 Hz frame keeps it awake."""
        now = self.clock()
        reply: list[tuple[str, bytes]] = []
        channel = None
        keys: list[tuple[str, bool]] = []
        with self._lock:
            if not self._awake(now):
                self.wake_at = now
            self.last_rx = now
            name = frame.spec.name if frame.spec is not None else None
            data = frame.data
            if name == "bc_status_poll" and data[:2] == P.POLL_ANSWERED:
                steady = data == P.POLL_STEADY
                if steady and self.last_poll_steady is False:
                    # The wake after a wind-down: nodes report booting again.
                    self.wake_at = now
                    log.info("poll back to steady: booting")
                self.last_poll_steady = steady
                if not steady:
                    status = P.STATUS_SLEEPING
                elif now - self.wake_at < self.boot_s:
                    status = P.STATUS_BOOTING
                else:
                    status = P.STATUS_WORKING
                reply.append(("bm_status_response", status))
            elif name == "bc_proxi_challenge":
                if self.proxi_value is not None:
                    reply.append(("bm_proxi_response", self.proxi_value))
                elif not self._proxi_warned:
                    # A guessed value is worse than none: the Body Computer
                    # takes a wrong one for a wrongly configured node.
                    self._proxi_warned = True
                    log.warning("PROXI challenge not answered: "
                                "can.proxi_value is not set")
            elif name == "radio_media_request" and data:
                head = data[0] & ~P.RADIO_KEYS["up"][1]   # "up" rides on byte 0
                selected = len(data) > 4 and bool(data[4] & P.RADIO_BM_SELECTED)
                if self.playing and selected and head == P.RADIO_PLAYING:
                    held = {k for k, (i, m) in P.RADIO_KEYS.items()
                            if len(data) > i and data[i] & m}
                    keys = [(k, k in held) for k in sorted(held ^ self._radio_keys)]
                    self._radio_keys = held
                if not self.playing and (head == P.RADIO_OPEN_MEDIA_PLAYER or (
                        head == P.RADIO_PLAYING and selected)):
                    # 04…10 without a 0C first: the radio skipped the request
                    # and times out to CD unless answered (sniff2, 2026-09-27).
                    self.playing, self.position_s, self.radio_muted = True, 0.0, False
                    self._scrolled = None
                    reply.append(("bm_audio_channel", P.AUDIO_MEDIA_PLAYER))
                    channel = "bm"
                elif head == P.RADIO_MUTES and selected and self.playing:
                    # Mute (wheel or radio) on Blue&Me: the phone pauses; the
                    # next 04…10 is the unmute and it plays on (owner, 2026-09-27).
                    if not self.radio_muted:
                        self.radio_muted = True
                        channel = "muted"
                elif head == P.RADIO_PLAYING and selected and self.playing:
                    if self.radio_muted:
                        self.radio_muted = False
                        channel = "unmuted"
                elif self.playing and (head in P.RADIO_LEAVES_MEDIA_PLAYER or (
                        head in (P.RADIO_PLAYING, P.RADIO_MUTES) and not selected)):
                    self.playing, self.radio_muted = False, False
                    reply.append(("bm_audio_channel", P.AUDIO_MUTED))
                    channel = "radio"
        for frame_name, payload in reply:
            self.send(frame_name, payload)
        for key, pressed in keys:
            log.info("radio key %s %s", key, "pressed" if pressed else "released")
            self.events.emit("radio_key", key, pressed)
        if channel in ("muted", "unmuted"):
            log.info("radio %s Blue&Me", channel)
            self.events.emit("audio_channel", "radio" if channel == "muted" else "bm")
        elif channel is not None:
            log.info("radio %s the media player", "opened" if channel == "bm" else "left")
            self.events.emit("audio_channel", channel)

    # -- the phone's side, kept up to date by the app ---------------------------

    def set_phone(self, call: bool, assistant: bool,
                  position_s: "float | None" = None,
                  phone_playing: "bool | None" = None) -> None:
        """A call up or not, the assistant open or not, the phone's own
        position in the track and whether it plays (paused on the phone: the
        time stands still). A new audio channel goes out at once."""
        with self._lock:
            before = self._audio()
            self.call, self.assistant = call, assistant
            if phone_playing is not None:
                self.phone_playing = phone_playing
            if position_s is not None:
                self._phone_pos = position_s
                if self._pos_offset and (self.track != self._offset_track
                                         or position_s < self._pos_offset):
                    self._pos_offset = 0.0   # a new song, or the phone caught up
                if self.playing:
                    self.position_s = position_s - self._pos_offset
            after = self._audio()
            awake = self._awake(self.clock()) and self.periodic
        if after != before and awake:
            log.info("audio channel %s", after.hex().upper()[-4:])
            self.send("bm_audio_channel", after)

    def track_restarted(self) -> None:
        """'Previous' was sent: if the song stays, it started again at 0:00."""
        with self._lock:
            self._pos_offset = self._phone_pos or 0.0
            self._offset_track = self.track
            self.position_s = 0.0

    def _audio(self) -> bytes:
        if self.call:
            return P.AUDIO_PHONE
        if self.assistant:
            return P.AUDIO_VOICE
        return P.AUDIO_MEDIA_PLAYER if self.playing else P.AUDIO_MUTED

    # -- periodic: called about once a second --------------------------------

    def tick(self) -> None:
        now = self.clock()
        with self._lock:
            if not self._awake(now):
                if self.periodic:
                    log.info("bus quiet: periodic frames stopped")
                self.periodic = False
                self.wake_at = None
                self.playing = False
                self._last_tick = None
                return
            if not self.periodic:
                # First tick after the wake: start next second, as the module does.
                self.periodic = True
                self._last_tick = now
                log.info("bus awake: booting for %.0f s, then working", self.boot_s)
                return
            if self.playing and self.phone_playing and self._last_tick is not None:
                self.position_s += now - self._last_tick
            self._last_tick = now
            track = track_time(self.playing, self.position_s, self._toggle)
            self._toggle = not self._toggle
            audio = self._audio()
            texts = self._texts(now)
        self.send("bm_watchdog", P.WATCHDOG[0])
        self.sleep(P.WATCHDOG_GAP_S)
        self.send("bm_watchdog", P.WATCHDOG[1])
        self.send("bm_track_time", track)
        self.send("bm_audio_channel", audio)
        self._send_texts(texts)

    def _send_texts(self, payloads: list[bytes]) -> None:
        with self._text_lock:
            for i, payload in enumerate(payloads):
                if i:
                    self.sleep(P.TEXT_GAP_S)
                self.send("bm_text_message", payload)

    def scroll(self) -> None:
        """One step of the radio's two lines, called every TEXT_SCROLL_S once
        the track's own text is out: "artist - title" running through the top
        (field 3), the playing time at the bottom (field 1). Sent only when
        either changed."""
        with self._lock:
            track = self.track
            if not (self.periodic and self.playing and track is not None
                    and track == self._shown_track):
                return
            artist, album, title = track
            loop = f"{artist} - {title}" if artist else title
            width = P.RADIO_SCROLL_WIDTH
            if len(loop) > width:
                loop += "   "
                i = self._scroll_i % len(loop)
                self._scroll_i = i + 1
                top = (loop[i:] + loop[:i])[:width]
            else:
                top = loop
            minutes, seconds = divmod(int(self.position_s) % 3600, 60)
            message = f"+{minutes}:{seconds:02d}\n{album or artist}\n{top}\n"
            if message == self._scrolled:
                return
            self._scrolled = message
            payloads = textcodec.encode_frames(message, textcodec.Display.RADIO)
        self._send_texts(payloads)

    def _texts(self, now: float) -> list[bytes]:
        """This second's text frames: the radio's, then the cluster's."""
        frames: list[bytes] = []
        if not self.playing:
            frames.append(P.TEXT_IDLE_RADIO)
            self._shown_track = None
        elif self.track is not None and self.track != self._shown_track:
            message = radio_track_text(*self.track)
            frames += textcodec.encode_frames(message, textcodec.Display.RADIO)
            self._shown_track = self.track
            self._scroll_i = 0
            log.info("radio text: %r", message)
        caller = self.caller
        if caller != self._shown_caller or (
                caller and now - self._caller_sent_at >= DASH_REPEAT_S):
            if caller:
                frames += textcodec.encode_frames(caller, textcodec.Display.DASHBOARD)
                if caller != self._shown_caller:
                    log.info("cluster text: %r", caller)
            else:
                frames.append(P.TEXT_CLEAR_DASHBOARD)
            self._shown_caller, self._caller_sent_at = caller, now
        return frames

    def _awake(self, now: float) -> bool:
        return self.last_rx is not None and now - self.last_rx <= self.idle_s
