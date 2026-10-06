# Blue&Me replacement for the Alfa Romeo Giulietta (940)

A Raspberry Pi Zero W that takes the place of the factory Blue&Me module
(Magneti Marelli 50521871) in an Alfa Romeo Giulietta. It plugs into the car's
own Blue&Me connector, answers the car as that module on the body CAN bus, and
plays the phone's Bluetooth audio through the factory radio. The radio stays.

- Bluetooth music on the radio's Blue&Me source, with artist, title and time on the radio's display
- Hands-free calls through the car's speakers and its own roof microphone (wideband, mSBC)
- Steering-wheel buttons: next, previous, answer, reject, hang up, microphone mute,
  the phone's voice assistant, move a call between car and phone
- The radio's arrow keys: skip a track, Bluetooth volume
- Parked: standby for 72 hours, then a halt; it wakes on bus traffic or on ignition

How it was built, with the wiring, the measurements and the mistakes:
**<https://duzgun.org/blog/giulietta-blue-me/>**

## Status

Installed in one car since 1 October 2026: a 2011 Giulietta 2.0 JTDm with the
standard radio. Everything in the list above works in that car. It has not been
tried on another car, model year or radio.

One difference from the copy that runs in that car: there the car's PROXI value
is built in. Here it is a setting, `can.proxi_value`, because every car has its
own. That setting is covered by the tests, not yet by a drive.

Still open: echo cancellation on the Pi, what the car does when the Pi answers
late after a parking longer than 72 hours, a read-only root filesystem, and the
glovebox USB socket, which the original module powered.

## Read this before you connect anything to a car

- This software transmits on the car's CAN bus. A fresh install does not: it
  only listens until you switch it with `tx.sh on`.
- One step of the build writes a configuration record in the Body Computer, so
  that the car expects the module (`experiments/node61/write_bc_record.py`).
  Read the record first, keep the copy, and understand the step before you run it.
- Every frame the application sends was first recorded from this one car. Your
  car can differ. Record your own car and compare before anything transmits.
- Never use the PROXI value of another car. Use your own (see below).
- 12 V from the car goes only to the step-down converter, behind a fuse.
- There is no warranty. You do this at your own risk.

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi Zero W | Raspberry Pi OS Lite, Bookworm, 32-bit |
| MCP2515 CAN module with TJA1050 | 8 MHz crystal, 120 Ω terminator removed, transceiver on 5 V and controller on 3.3 V |
| USB sound card, C-Media HS-100B (`0d8c:0014`) | line out to the radio, microphone in from the car |
| 12 V to 5 V step-down converter | has to hold 5 V while the engine starts |
| Perfboard | microphone supply and divider, ignition sense transistor, diode and 2200 µF for engine starts |

Pins used on the Blue&Me connector: 30 CAN high, 14 CAN low, 15 ground,
5 / 22 / 21 audio left / right / ground, 8 / 24 microphone + / −,
16 permanent 12 V, 32 ignition.

The bus is B-CAN at 50 kbit/s with 29-bit identifiers (`topic << 16 | node`).
The module is node `4021`.

## Install

Copy the repository to the Pi and run the installer there (the examples use
the user `admin` and the host name `blueandme`):

```
rsync -a --exclude .git ./ admin@blueandme.local:giulietta-blue-me/
ssh admin@blueandme.local
cd ~/giulietta-blue-me && sudo ./install.sh --oscillator 8000000
```

`--oscillator` is the crystal on your MCP2515 module, 8000000 or 16000000.
The installer sets up the CAN interface, the sound card, Bluetooth audio and the
services, and leaves everything listen-only. Afterwards:

| Command | What it does |
|---|---|
| `blueandme-status` | checks the CAN interface, the sound card and the audio services |
| `sudo ./preflight.sh` | tests the CAN chain on the bench, with no car attached |
| `blueandme-pair` | makes the Pi pairable for the phone |
| `blueandme-capture`, `blueandme-decode`, `blueandme-diff` | record the bus and read the recordings |
| `sudo ./tx.sh on`, `off`, `status` | switch between listening and answering as the module |

The configuration is `/etc/blueandme/config.yaml`; the commented defaults are in
`blueandme/config/default.yaml`.

## From listening to transmitting

1. **Listen.** Record your car with the Pi on the Blue&Me connector and compare
   it with what the post describes.
2. **Your PROXI value.** About a second after the bus wakes, the Body Computer
   sends `1E114000#` and the other modules answer with the same six bytes (in
   my car `1E11400A#…`, `1E114003#…` and `1E11401A#…`). Put those 12 hex digits,
   in quotes, in the configuration as `can.proxi_value`. While it is empty the
   Pi does not answer that question, and a car that expects the module blinks
   its odometer.
3. **Transmit.** `sudo ./tx.sh on` puts the CAN controller in normal mode and
   lets the application send the module's six frames. `tx.sh off` goes back.

Which frames may be sent is not a matter of trust. Each frame in
`blueandme/protocol/profiles/giulietta940.py` names the recording and the moment
it was seen in, and the transmit gate (`blueandme/can/txgate.py`) sends only
frames confirmed on this car, plus exceptions that are named and dated in the
profile. The recordings themselves are of my car and are not published.

## Repository

```
blueandme/          the application
  can/              bus access and the transmit gate
  protocol/         frames with their evidence: the Giulietta, and for comparison
                    only the Fiat Doblò 263 and the Alfa 159
  vehicle/          what the module says on the bus (node.py), wheel buttons, radio text
  media/            Bluetooth: phone link, calls, wheel actions, microphone relay
  power/            standby and ignition
  tools/            capture, decode, diff, status
systemd/  udev/     units and rules that install.sh installs
install.sh          installer for the Pi
preflight.sh        bench test of the CAN chain
tx.sh               listen-only <-> transmitting
experiments/        supervised scripts from the bring-up (read its README first)
hardware/housing/   OpenSCAD models for a carrier inside the original case
packages/           bluez-alsa 4.3.1 with mSBC, built for the Zero W
tests/
```

The housing is unfinished: `board_template` fits the bosses of the original
case, the sled has only been test-printed, and the printed pin holder was not
used in the end (a piece of perfboard carries the header pins).

## Tests

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
tests/fixtures/fetch.sh          # optional: the Doblò traces, see tests/fixtures/README.md
.venv/bin/python -m pytest
```

## Thanks

- **fmntf**, for [fiatcan](https://github.com/fmntf/fiatcan) and the
  [four-part write-up on Medium](https://medium.com/@fmntf): the Doblò protocol,
  the node addressing, the PROXI answer taken from the neighbours, the
  wheel-button bits, the text format and the insight about boot timing. This
  build grew out of that work.
- **karolkrupa**, for [alfa-blue-me](https://github.com/karolkrupa/alfa-blue-me):
  the Alfa 159 build, an independent confirmation of the character set, and the
  report that the Giulietta uses 29-bit identifiers.
- **terilenard**, for
  [mcp2515-tja1050-wiring](https://github.com/terilenard/mcp2515-tja1050-wiring):
  the split-rail modification of the CAN module.
- **Arkadiusz Bokowy**, for [bluez-alsa](https://github.com/arkq/bluez-alsa).

## Licence

MIT, see `LICENSE`. The package in `packages/` is a build of bluez-alsa, also
MIT, © Arkadiusz Bokowy (`packages/LICENSE.bluez-alsa`).

This is a private project. It is not affiliated with Alfa Romeo, Fiat,
Stellantis or Magneti Marelli. Blue&Me is a trademark of its owner.
