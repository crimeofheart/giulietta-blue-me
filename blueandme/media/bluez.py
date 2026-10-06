"""BlueZ D-Bus glue: device connection, MediaPlayer1 control, track metadata.

Structure follows karolkrupa's ``bluetooth/objects/Player.py``, which is the
cleaner of the two reference wrappers, with fiatcan's reconnect loop and
play-status debounce ported in -- the debounce matters because some phones emit
a transient "paused" between tracks, and without the settle delay the car
display flickers and the audio channel frame thrashes.

D-Bus imports are lazy so the rest of the package, the tools and the test suite
all work on a machine with no BlueZ.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from ..events import EventBus

log = logging.getLogger("blueandme.bluez")

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
PLAYER_IFACE = "org.bluez.MediaPlayer1"
CONTROL_IFACE = "org.bluez.MediaControl1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

DEVICE_PATH = re.compile(r"^/org/bluez/hci\d+/dev_[A-Z0-9_]+$")

#: A phone that pauses for less than this between tracks is still "playing".
PAUSE_SETTLE_S = 0.5


class BluetoothMedia:
    def __init__(
        self,
        events: EventBus,
        reconnect_interval_s: int = 10,
        auto_reconnect: bool = True,
    ) -> None:
        self.events = events
        self.reconnect_interval_s = reconnect_interval_s
        self.auto_reconnect = auto_reconnect
        self.connected = False
        self.player_path: str | None = None
        self.playing = False
        self.artist = ""
        self.title = ""
        self.position_s = 0
        self._bus = None
        self._player_iface = None
        self._running = False
        self._pause_pending = False
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        import dbus
        import dbus.mainloop.glib

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._bus.add_signal_receiver(
            self._properties_changed,
            bus_name=BLUEZ,
            dbus_interface=PROPS_IFACE,
            signal_name="PropertiesChanged",
            path_keyword="path",
        )
        self._running = True
        if self.auto_reconnect:
            threading.Thread(
                target=self._reconnect_loop, name="bt-reconnect", daemon=True
            ).start()
        log.info("bluez media started")

    def stop(self) -> None:
        self._running = False

    # -- reconnect ---------------------------------------------------------

    def _reconnect_loop(self) -> None:
        while self._running:
            if not self.connected:
                try:
                    self._connect_known_device()
                except Exception:
                    log.debug("reconnect attempt failed", exc_info=True)
            time.sleep(self.reconnect_interval_s)

    def _connect_known_device(self) -> None:
        """Try every paired device in turn. The phone that is in the car wins."""
        import dbus

        manager = dbus.Interface(self._bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        for path, ifaces in manager.GetManagedObjects().items():
            if not DEVICE_PATH.match(str(path)) or DEVICE_IFACE not in ifaces:
                continue
            if not ifaces[DEVICE_IFACE].get("Paired", False):
                continue
            if ifaces[DEVICE_IFACE].get("Connected", False):
                self.connected = True
                return
            log.info("connecting to %s", path)
            try:
                dbus.Interface(
                    self._bus.get_object(BLUEZ, path), DEVICE_IFACE
                ).Connect()
                return
            except Exception:
                log.debug("connect to %s failed", path, exc_info=True)

    # -- signals -----------------------------------------------------------

    def _properties_changed(self, interface, changed, invalidated, path) -> None:
        try:
            if interface == DEVICE_IFACE and "Connected" in changed:
                self.connected = bool(changed["Connected"])
                log.info("device %s connected=%s", path, self.connected)
                self.events.emit("bt_connected", self.connected, str(path))
                if not self.connected:
                    self._set_playing(False, immediate=True)
            elif interface == CONTROL_IFACE:
                if "Player" in changed:
                    self._attach_player(str(changed["Player"]))
                if "Connected" in changed and not changed["Connected"]:
                    self._set_playing(False, immediate=True)
            elif interface == PLAYER_IFACE:
                self._player_props(changed)
        except Exception:
            log.exception("failed handling PropertiesChanged on %s", path)

    def _player_props(self, changed) -> None:
        if "Track" in changed:
            track = changed["Track"]
            title = str(track.get("Title", "") or "")
            artist = str(track.get("Artist", "") or "")
            # Some phones announce a placeholder before real metadata arrives.
            if title and title != "Not Provided":
                self.title, self.artist = title, artist
                log.info("track: %s - %s", artist, title)
                self.events.emit("media_track", artist, title)
        if "Status" in changed:
            self._set_playing(str(changed["Status"]) == "playing")
        if "Position" in changed:
            self.position_s = int(changed["Position"]) // 1000
            self.events.emit("media_position", self.position_s)

    def _attach_player(self, path: str) -> None:
        import dbus

        self.player_path = path
        self._player_iface = dbus.Interface(
            self._bus.get_object(BLUEZ, path), PLAYER_IFACE
        )
        log.info("media player attached: %s", path)

    def _set_playing(self, playing: bool, immediate: bool = False) -> None:
        """Debounced play-state change (fiatcan's possible_pause logic)."""
        if playing:
            with self._lock:
                self._pause_pending = False
            if not self.playing:
                self.playing = True
                self.events.emit("media_playing", True)
            return
        if immediate:
            if self.playing:
                self.playing = False
                self.events.emit("media_playing", False)
            return
        with self._lock:
            self._pause_pending = True

        def settle() -> None:
            time.sleep(PAUSE_SETTLE_S)
            with self._lock:
                if not self._pause_pending:
                    return
                self._pause_pending = False
            if self.playing:
                self.playing = False
                self.events.emit("media_playing", False)

        threading.Thread(target=settle, name="bt-pause-settle", daemon=True).start()

    # -- control -----------------------------------------------------------

    def _call(self, method: str) -> bool:
        if self._player_iface is None:
            log.info("no media player attached; ignoring %s", method)
            return False
        try:
            getattr(self._player_iface, method)()
            return True
        except Exception:
            log.exception("AVRCP %s failed", method)
            return False

    def play(self) -> bool:
        return self._call("Play")

    def pause(self) -> bool:
        return self._call("Pause")

    def next(self) -> bool:
        return self._call("Next")

    def previous(self) -> bool:
        return self._call("Previous")

    def toggle(self) -> bool:
        return self.pause() if self.playing else self.play()
