"""The transmit gate. These are the tests that keep the car safe.

Section 23 of the brief says: never blindly transmit CAN frames copied from
another vehicle, default to listen-only, do not transmit unknown frames. Each of
those is a test below rather than a policy in a document.
"""

import pytest

from blueandme.can.txgate import CanMode, TransmitRefused, TxGate
from blueandme.config import Config, load
from blueandme.protocol.confidence import Confidence
from blueandme.protocol.registry import get


@pytest.fixture
def doblo():
    return get("doblo263")


def test_listen_only_refuses_everything(doblo):
    gate = TxGate(doblo, CanMode.LISTEN_ONLY, Confidence.UNKNOWN)
    for spec in doblo:
        assert not gate.permits(spec)


def test_default_threshold_refuses_every_reference_frame(doblo):
    """A Doblo frame must never reach a Giulietta bus, whatever the mode."""
    from blueandme.protocol.frame import Direction

    gate = TxGate(doblo, CanMode.TX, Confidence.CONFIRMED_940)
    for spec in doblo:
        # RX frames are refused for direction before confidence is even consulted;
        # what matters is that nothing at all gets through.
        expected = ("receive-only" if spec.direction is Direction.RX
                    else "below the required CONFIRMED_940")
        with pytest.raises(TransmitRefused, match=expected):
            gate.check(spec, bytes(spec.dlc or 0))


def test_receive_only_frames_are_never_transmitted(doblo):
    gate = TxGate(doblo, CanMode.TX, Confidence.OTHER_VEHICLE)
    with pytest.raises(TransmitRefused, match="receive-only"):
        gate.check(doblo.require("steering_wheel_buttons"), bytes(2))


def test_gated_mode_allows_only_the_named_frame(doblo):
    gate = TxGate(
        doblo, CanMode.GATED_TX, Confidence.OTHER_VEHICLE, ["bm_status_response"]
    )
    gate.check(doblo.require("bm_status_response"), bytes(2))
    with pytest.raises(TransmitRefused, match="not in allowed_tx_frames"):
        gate.check(doblo.require("bm_watchdog"), bytes(8))


def test_frame_from_another_profile_is_refused():
    """Holding a FrameSpec from the wrong profile must not get it onto the bus."""
    gate = TxGate(get("alfa939"), CanMode.TX, Confidence.OTHER_VEHICLE)
    with pytest.raises(TransmitRefused, match="does not belong to the active"):
        gate.check(get("doblo263").require("bm_watchdog"), bytes(8))


def test_payload_length_is_validated(doblo):
    gate = TxGate(doblo, CanMode.TX, Confidence.OTHER_VEHICLE)
    with pytest.raises(TransmitRefused, match="spec says 2"):
        gate.check(doblo.require("bm_status_response"), bytes(3))


def test_allow_list_typos_fail_at_construction(doblo):
    with pytest.raises(ValueError, match="absent from profile"):
        TxGate(doblo, CanMode.GATED_TX, Confidence.OTHER_VEHICLE, ["bm_stauts"])


def test_refusals_are_counted(doblo):
    gate = TxGate(doblo, CanMode.LISTEN_ONLY)
    for _ in range(3):
        with pytest.raises(TransmitRefused):
            gate.check(doblo.require("bm_watchdog"), bytes(8))
    assert gate.refused == 3 and gate.sent == 0


class TestShippedConfig:
    def test_defaults_are_safe(self):
        cfg = load("blueandme/config/default.yaml")
        assert cfg.can.mode is CanMode.LISTEN_ONLY
        assert cfg.can.profile == "giulietta940"
        assert cfg.can.min_tx_confidence is Confidence.CONFIRMED_940
        assert cfg.can.allowed_tx_frames == []
        assert cfg.validate() == []

    def test_a_fresh_install_can_transmit_nothing(self):
        cfg = load("blueandme/config/default.yaml")
        profile = get(cfg.can.profile)
        gate = TxGate(profile, cfg.can.mode, cfg.can.min_tx_confidence,
                      cfg.can.allowed_tx_frames)
        # The Giulietta profile has frames to send; the shipped listen-only
        # mode is what keeps every one of them off the bus.
        assert cfg.can.mode is CanMode.LISTEN_ONLY
        assert profile.transmittable(cfg.can.min_tx_confidence) != []
        assert not any(gate.permits(spec) for spec in profile)

    def test_transmitting_a_reference_profile_is_a_config_error(self):
        cfg = Config()
        cfg.can.profile = "doblo263"
        cfg.can.mode = CanMode.GATED_TX
        assert any("may never be transmitted" in p for p in cfg.validate())

    def test_transmitting_before_gate_d1_is_a_config_error(self):
        """A reported identifier width does not unlock transmission.

        The width is now known to be 29-bit on the strength of the alfa-blue-me
        author's statement, but that is testimony. Get the width wrong and every
        frame is malformed on a live vehicle bus, so this stays gated until a
        capture from this car confirms it.
        """
        from blueandme.protocol.confidence import Confidence
        from blueandme.protocol.frame import Evidence
        from blueandme.protocol.registry import get

        cfg = Config()
        cfg.can.mode = CanMode.GATED_TX
        assert not any("Decision gate D1" in p for p in cfg.validate())
        # Were the width only reported, as it was before capture A:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(get("giulietta940"), "id_width_evidence",
                       Evidence(Confidence.REPORTED_940, "testimony only"))
            problems = cfg.validate()
        assert any("Decision gate D1" in p for p in problems)
        assert any("REPORTED_940" in p for p in problems)


class TestControllerListenOnly:
    """Layer 1: the CAN controller itself is put in listen-only mode.

    This layer lives in a systemd unit rather than in Python, so nothing else in
    the suite exercises it and vcan has no controller mode to test against. It
    failed on the first real boot: systemd substitutes "${VAR}" as a single
    argument, so ip received "listen-only on" as one token, rejected it, and
    can0 was never configured. Only a bare "$VAR" standing as its own word is
    split at whitespace -- and only that form also expands to zero arguments
    when CTRLMODE is emptied for normal mode at step 12.
    """

    UNIT = "systemd/can0.service"
    ENV = "systemd/can0.env"

    def _setup_line(self):
        with open(self.UNIT) as f:
            lines = [l.strip() for l in f if "type can" in l]
        assert len(lines) == 1, "expected exactly one 'ip link ... type can' line"
        return lines[0]

    def test_ctrlmode_is_word_split_by_systemd(self):
        tokens = self._setup_line().split()
        assert "$CTRLMODE" in tokens, "CTRLMODE must be a bare $VAR on its own"
        assert "${CTRLMODE}" not in self._setup_line()

    def test_shipped_ctrlmode_is_listen_only(self):
        with open(self.ENV) as f:
            values = [l.split("=", 1)[1].strip() for l in f
                      if l.startswith("CTRLMODE=")]
        assert values == ["listen-only on"]

    def test_a_bus_off_does_not_leave_can0_dead(self):
        # restart-ms 0: the driver sleeps the chip at bus-off until can0 goes
        # down and up. A literal: the installed can0.env predates any variable.
        tokens = self._setup_line().split()
        value = tokens[tokens.index("restart-ms") + 1]
        assert value.isdigit() and int(value) > 0

    def test_app_does_not_run_on_an_unconfigured_interface(self):
        """If can0.service fails, the app must not start regardless."""
        with open("systemd/blueandme-giulietta.service") as f:
            unit = f.read()
        assert "Requires=can0.service" in unit
        assert "can0.service" in next(l for l in unit.splitlines()
                                      if l.startswith("After="))


class TestOwnerException:
    """A frame below the evidence threshold goes out only on the owner's dated
    decision, and even then only by name in gated-tx."""

    @staticmethod
    def profile():
        from blueandme.protocol.confidence import Confidence as C
        from blueandme.protocol.frame import Direction as D, Evidence, FrameSpec, Profile

        return Profile("t", "test", 50000, True, [
            FrameSpec("excepted", 0x0A394021, True, D.TX, C.OTHER_VEHICLE, "doblo",
                      dlc=8, owner_exception="2026-09-26, owner: send it"),
            FrameSpec("plain", 0x0A194021, True, D.TX, C.OTHER_VEHICLE, "doblo", dlc=8),
        ], id_width_evidence=Evidence(C.CONFIRMED_940, "test"))

    def gate(self, mode, allowed=()):
        return TxGate(self.profile(), mode, allowed=list(allowed))

    def test_passes_in_gated_tx_when_allowed(self):
        g = self.gate(CanMode.GATED_TX, ["excepted"])
        assert g.refusal(g.profile.require("excepted"), bytes(8)) is None

    @pytest.mark.parametrize("mode, allowed", [
        (CanMode.LISTEN_ONLY, ["excepted"]),
        (CanMode.GATED_TX, []),
        (CanMode.TX, []),
    ])
    def test_refused_otherwise(self, mode, allowed):
        g = self.gate(mode, allowed)
        assert g.refusal(g.profile.require("excepted"), bytes(8)) is not None

    def test_no_exception_no_passage(self):
        g = self.gate(CanMode.GATED_TX, ["plain"])
        assert "capture it from this car first" in g.refusal(g.profile.require("plain"), bytes(8))
