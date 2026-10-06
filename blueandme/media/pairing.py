"""BlueZ pairing agent.

NoInputNoOutput capability: the car has no keypad and no display we can put a
passkey on during pairing, so it accepts Just Works pairing, which is what every
factory hands-free unit does.
"""

from __future__ import annotations

import logging

log = logging.getLogger("blueandme.pairing")

AGENT_PATH = "/org/blueandme/agent"
CAPABILITY = "NoInputNoOutput"


def make_agent_class():
    """Build the Agent class lazily, so importing this module needs no dbus."""
    import dbus.service

    class Agent(dbus.service.Object):
        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            log.info("pairing agent released")

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            log.info("authorising %s", device)

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            log.info("authorising service %s on %s", uuid, device)

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            log.info("confirming passkey %06d for %s", passkey, device)

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            log.info("pairing cancelled")

    return Agent


def register(adapter_name: str = "Blue&Me", pairable: bool = True) -> None:
    """Register the agent and make the adapter discoverable under `adapter_name`."""
    import dbus

    bus = dbus.SystemBus()
    agent = make_agent_class()(bus, AGENT_PATH)  # noqa: F841 -- kept alive by dbus
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(AGENT_PATH, CAPABILITY)
    manager.RequestDefaultAgent(AGENT_PATH)
    log.info("pairing agent registered (%s)", CAPABILITY)

    manager_obj = dbus.Interface(bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in manager_obj.GetManagedObjects().items():
        if "org.bluez.Adapter1" not in ifaces:
            continue
        props = dbus.Interface(bus.get_object("org.bluez", path),
                               "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Adapter1", "Alias", dbus.String(adapter_name))
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(pairable))
        props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(pairable))
        log.info("adapter %s configured as %r", path, adapter_name)
        return
    log.error("no Bluetooth adapter found")
