"""blueandme-giulietta -- the service.

Wires the CAN side, the vehicle logic and Bluetooth together and runs the
cyclic transmit loop. On a stock install this process starts, opens the bus in
listen-only mode, decodes what it can, transmits nothing, and says so clearly in
the journal. That is the intended behaviour until the Giulietta profile has been
populated from real captures.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from .can.bus import CanBus, ReceivedFrame
from .can.dispatcher import Dispatcher
from .can.txgate import CanMode, TransmitRefused, TxGate
from .config import Config, load
from .events import EventBus
from .power.ignition import IgnitionMonitor
from .power.lifecycle import Lifecycle
from .protocol.registry import get
from .vehicle import presence as presence_mod
from .vehicle import proxi as proxi_mod
from .vehicle import node as node_mod
from .vehicle.display import Display
from .vehicle.presence import Presence
from .vehicle.proxi import ProxiResponder
from .vehicle.radio import AudioChannel, Radio
from .vehicle.steering_wheel import SteeringWheel

log = logging.getLogger("blueandme.app")


def has_wheel(profile) -> bool:
    """Whether the profile knows the wheel's button frame and its masks."""
    return "steering_wheel_buttons" in profile and bool(profile.buttons)


def caller_text(calls) -> "str | None":
    """Who to show on the cluster: the ringing or waiting call first."""
    from .media.wheel_control import INCOMING, WAITING

    if not calls:
        return None
    ringing = [c for c in calls if c.status in (INCOMING, WAITING)]
    call = (ringing or calls)[0]
    return call.name or call.number or "CALL"


def has_node(profile) -> bool:
    """Whether the profile has every frame node 4021 speaks (the Giulietta's)."""
    return all(name in profile for name in (*node_mod.RX_FRAMES, *node_mod.TX_FRAMES))

CYCLE_S = 1.0
#: How often the phone's calls and track are read while the car is awake.
PHONE_POLL_S = 2.0


class Application:
    def __init__(self, cfg: Config, phone: bool = True) -> None:
        self.cfg = cfg
        self.profile = get(cfg.can.profile)
        self.events = EventBus()
        self.gate = TxGate(
            self.profile,
            cfg.can.mode,
            cfg.can.min_tx_confidence,
            cfg.can.allowed_tx_frames,
        )
        self.bus = CanBus(cfg.can.interface, self.profile, self.gate, cfg.can.bitrate,
                          filter_to_profile=has_node(self.profile))
        self.dispatcher = Dispatcher(self.profile)
        self.presence = Presence(self.profile, self.events)
        self.proxi = ProxiResponder(self.profile, self.events)
        self.radio = Radio(self.profile, self.events)
        self.display = Display(self.profile)
        self.wheel = SteeringWheel(self.profile, self.events)
        self.ignition = IgnitionMonitor(
            self.events, cfg.power.stay_awake_after_key_off_s,
            cfg.power.shutdown_delay_s,
        )
        self.lifecycle = Lifecycle()
        self.media = None
        self.phone = None
        self.wheel_control = None
        if phone and has_wheel(self.profile):
            from .media.wheel_control import BluetoothPhone, WheelControl

            # The wheel drives the phone over Bluetooth only, so it runs in
            # every CAN mode, listen-only included.
            self.phone = BluetoothPhone()
            self.wheel_control = WheelControl(self.events, self.phone)
        # Node 4021 as this car accepted it (vehicle/node.py). The Doblo-rule
        # presence, PROXI and radio modules above serve the reference profiles.
        self.node = node_mod.BlueAndMeNode(self.events, self._node_send,
                                           proxi_value=cfg.can.proxi_bytes()) \
            if has_node(self.profile) else None
        self._not_sent: set[str] = set()
        self._halt_deferred = False
        self._running = False
        self._subscribe()

    # -- wiring ------------------------------------------------------------

    def _subscribe(self) -> None:
        p = self.profile
        if "steering_wheel_buttons" in p:
            self.dispatcher.on("steering_wheel_buttons", self.wheel.on_frame)
        if self.node is not None:
            for name in (*node_mod.RX_FRAMES, "steering_wheel_buttons"):
                self.dispatcher.on(name, self.node.on_frame)
        elif presence_mod.RX_NETWORK_CONTROL in p:
            self.dispatcher.on(presence_mod.RX_NETWORK_CONTROL, self.presence.on_frame)
        for name in (proxi_mod.RX_REQUEST, *proxi_mod.PEER_FRAMES):
            if name in p:
                self.dispatcher.on(name, self.proxi.observe)
        if "radio_frequency" in p:
            self.dispatcher.on("radio_frequency", self.radio.on_frame)
        self.dispatcher.on_unmatched(self._unknown)

        self.events.on("media_playing", self._on_playing)
        self.events.on("media_position", self._on_position)
        self.events.on("media_skipped", self._on_skipped)
        self.events.on("audio_channel", self._on_radio_source)
        self.events.on("sleep_requested", self._on_sleep)

    def _unknown(self, frame: ReceivedFrame) -> None:
        log.debug("unrecognised %s#%s", frame.hex_id, frame.data.hex().upper())

    # -- bluetooth ---------------------------------------------------------

    def start_bluetooth(self) -> None:
        from .media.bluez import BluetoothMedia
        from .media import pairing

        try:
            pairing.register(self.cfg.bluetooth.name, self.cfg.bluetooth.pairable)
        except Exception:
            log.exception("pairing agent registration failed; continuing")
        self.media = BluetoothMedia(
            self.events,
            self.cfg.bluetooth.reconnect_interval_s,
            self.cfg.bluetooth.auto_reconnect,
        )
        self.media.start()

    # -- event handlers ----------------------------------------------------

    def _on_playing(self, playing: bool) -> None:
        self.radio.set_channel(AudioChannel.MEDIA if playing else AudioChannel.MUTED)

    def _on_skipped(self, action: str) -> None:
        if action == "previous" and self.node is not None:
            self.node.track_restarted()

    def _on_position(self, seconds: int) -> None:
        # fiatcan guards against phones that report absurd positions.
        if 0 <= seconds < 60 * 60:
            self.radio.track_position_s = seconds

    def _on_radio_source(self, source: str) -> None:
        """The driver changed source on the head unit; follow it."""
        if self.phone is not None:
            # Off the receive thread: a D-Bus round trip on the Zero W must not
            # hold up the next answer to the car.
            action = "play" if source == "bm" else "pause"
            threading.Thread(target=self.phone.media, args=(action,),
                             name="radio-source", daemon=True).start()
            return
        if self.media is None:
            return
        if source == "bm":
            self.media.play()
        else:
            self.media.pause()

    def _on_sleep(self) -> None:
        log.info("sleep requested by the vehicle")

    # -- cyclic transmit ---------------------------------------------------

    def _cycle(self) -> None:
        """One pass of the 1 Hz loop. Every send goes through TxGate."""
        if self.node is not None:
            self.node.tick()
            return
        self._try_send(presence_mod.TX_STATUS, self.presence.status_payload())
        payload = self.proxi.response_payload()
        if payload is not None and self.proxi.challenges_seen:
            if self._try_send(proxi_mod.TX_RESPONSE, payload):
                self.proxi.answered()
        self._try_send("bm_audio_channel", self.radio.audio_channel_payload())
        self._try_send("bm_track_time", self.radio.track_time_payload())

    def _node_send(self, frame_name: str, payload: bytes) -> bool:
        """Send for the node, quietly: in listen-only, or before a frame is on
        the gated-tx allow-list, it says so once rather than every second."""
        spec = self.profile.require(frame_name)
        why = self.gate.refusal(spec, payload)
        if why is not None:
            if frame_name not in self._not_sent:
                self._not_sent.add(frame_name)
                log.info("not sending %s: %s", frame_name, why)
            return False
        return self._try_send(frame_name, payload)

    def _try_send(self, frame_name: str, payload: "bytes | None") -> bool:
        if payload is None or frame_name not in self.profile:
            return False
        try:
            self.bus.send(frame_name, payload)
            return True
        except TransmitRefused:
            return False  # already logged by the gate

    # -- run ---------------------------------------------------------------

    def run(self) -> int:
        problems = self.cfg.validate()
        for problem in problems:
            log.error("config: %s", problem)
        if problems and self.cfg.can.mode is not CanMode.LISTEN_ONLY:
            log.error("refusing to start in a transmitting mode with config problems")
            return 2

        log.info("profile %r, %s", self.profile.key, self.gate.describe())
        if (self.node is not None and self.node.proxi_value is None
                and self.cfg.can.mode is not CanMode.LISTEN_ONLY):
            log.warning(
                "can.proxi_value is not set: the Body Computer's PROXI challenge "
                "will not be answered, and a car that expects this node blinks "
                "its odometer"
            )
        if self.profile.extended_ids is None:
            log.warning(
                "profile %s has not resolved 11-bit vs 29-bit addressing "
                "(decision gate D1). Running as a listener only.",
                self.profile.key,
            )

        self.lifecycle.install_signal_handlers()
        self.lifecycle.on_stop(self._stop)
        self._running = True

        with self.bus:
            if self.node is not None:
                # Pairing and reconnecting belong to blueandme-pair and
                # blueandme-bt-reconnect; the wheel and the radio reach the
                # phone through self.phone.
                pass
            elif self.cfg.can.mode is not CanMode.LISTEN_ONLY:
                self.start_bluetooth()
            else:
                log.info("listen-only: Bluetooth stack not started")
            threading.Thread(target=self._rx_loop, name="can-rx", daemon=True).start()
            if self.node is not None and self.phone is not None:
                threading.Thread(target=self._phone_loop, name="phone-poll",
                                 daemon=True).start()
                threading.Thread(target=self._scroll_loop, name="text-scroll",
                                 daemon=True).start()
            self._tx_loop()
        return 0

    def _rx_loop(self) -> None:
        failing = False
        while self._running:
            try:
                frame = self.bus.recv(0.5)
            except Exception as exc:
                # can0 taken down and up (preflight, car_tx.sh, a bus-off): the
                # socket keeps its interface, so wait and read on rather than
                # let the thread die and the wheel go quiet.
                if not failing:
                    log.warning("CAN receive failed (%s); retrying", exc)
                    failing = True
                time.sleep(1.0)
                continue
            if failing:
                log.info("CAN receive working again")
                failing = False
            if frame is not None:
                self.dispatcher.dispatch(frame)

    def _scroll_loop(self) -> None:
        """The radio's running title, a step every TEXT_SCROLL_S."""
        from .protocol.profiles import giulietta940 as P

        next_at = time.monotonic()
        while self._running:
            next_at = max(next_at + P.TEXT_SCROLL_S, time.monotonic())
            time.sleep(max(0.0, next_at - time.monotonic()))   # the clock moved on
            try:
                self.node.scroll()
            except Exception:
                log.exception("text scroll failed")

    def _phone_loop(self) -> None:
        """Calls, track and position into the node, while the car is awake."""
        while self._running:
            time.sleep(PHONE_POLL_S)
            if not self.node.periodic:
                continue   # parked or on the bench: nothing to show anyone
            try:
                calls = self.phone.calls(quiet=True)
                track, position, playing = self.phone.player()
                assistant = self.wheel_control is not None and self.wheel_control.assistant
                self.node.track = track
                self.node.caller = caller_text(calls)
                self.node.set_phone(bool(calls), assistant, position, playing)
            except Exception:
                log.exception("phone poll failed")

    def _tx_loop(self) -> None:
        next_tick = time.monotonic()
        while self._running:
            self._cycle()
            if self.ignition.should_shut_down():
                if self.cfg.power.standby:
                    # blueandme-standby owns power while parked (2026-09-25):
                    # halting here would end the hold the car's PROXI needs.
                    if not self._halt_deferred:
                        log.info("ignition off; standby keeps the Pi up, not halting")
                        self._halt_deferred = True
                else:
                    log.info("ignition off long enough; halting")
                    self.lifecycle.halt()
                    break
            else:
                self._halt_deferred = False
            next_tick += CYCLE_S
            time.sleep(max(0.0, next_tick - time.monotonic()))

    def _stop(self) -> None:
        self._running = False
        if self.media is not None:
            self.media.stop()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueandme-giulietta",
        description="Blue&Me replacement service for the Alfa Romeo Giulietta 940.",
    )
    p.add_argument("--config", type=Path)
    p.add_argument("--log-level", default=None)
    p.add_argument(
        "--check", action="store_true",
        help="validate the configuration and exit without touching the bus",
    )
    p.add_argument(
        "--no-phone", action="store_true",
        help="leave the phone alone: no wheel actions, no play/pause on the "
             "radio's source (for bench replays of captures)",
    )
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load(args.config)
    logging.basicConfig(
        level=getattr(logging, (args.log_level or cfg.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
    )
    if args.check:
        problems = cfg.validate()
        for problem in problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
        print("configuration is valid" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    try:
        return Application(cfg, phone=not args.no_phone).run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
