"""Clean shutdown.

Order matters: stop declaring an active audio channel before the radio loses the
node, stop answering status polls so the Body Computer can put the network to
sleep, then halt. Leaving the "working" status asserted after a shutdown request
is what keeps the whole B-CAN awake and flattens the battery.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess

log = logging.getLogger("blueandme.lifecycle")


class Lifecycle:
    def __init__(self, halt_command: "list[str] | None" = None, dry_run: bool = False):
        self.halt_command = halt_command or ["systemctl", "poweroff"]
        self.dry_run = dry_run
        self.stopping = False
        self._callbacks: list = []

    def on_stop(self, callback) -> None:
        self._callbacks.append(callback)

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.request_stop())

    def request_stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        log.info("stopping")
        for callback in reversed(self._callbacks):
            try:
                callback()
            except Exception:
                log.exception("shutdown callback %r failed", callback)

    def halt(self) -> None:
        """Power the machine off. A no-op under dry_run, and when not root."""
        self.request_stop()
        if self.dry_run:
            log.info("dry run: would run %s", " ".join(self.halt_command))
            return
        if os.geteuid() != 0:
            log.warning("not root; skipping %s", " ".join(self.halt_command))
            return
        log.info("powering off")
        subprocess.run(self.halt_command, check=False)
