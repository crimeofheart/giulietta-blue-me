"""Profiles, frame specs and the invariants that keep them honest."""

import pytest

from blueandme.protocol.confidence import Confidence
from blueandme.protocol.frame import Direction, Evidence, FrameSpec, Profile
from blueandme.protocol.registry import PROFILES, REFERENCE_ONLY, get


def test_every_giulietta_frame_was_measured_on_this_car_or_the_owner_said_so():
    """The whole safety model rests on this. If it ever fails, something has been
    copied in from a reference vehicle without a capture to back it, or without
    the owner's dated decision to send it anyway (and then its real evidence
    level is kept, not raised)."""
    giulietta = get("giulietta940")
    for spec in giulietta:
        if spec.owner_exception:
            assert spec.confidence < Confidence.CONFIRMED_940, spec
            assert spec.owner_exception.startswith("20"), spec   # dated
            continue
        assert spec.confidence is Confidence.CONFIRMED_940, spec
        assert spec.source.startswith("captures/"), spec


def test_the_only_owner_exception_is_the_text_format():
    """2026-09-26: assume the Doblo's text format; change it if the car disagrees."""
    giulietta = get("giulietta940")
    assert [s.name for s in giulietta if s.owner_exception] == ["bm_text_message"]


def test_the_giulietta_sends_only_what_the_car_accepted():
    """Node 4021's five frames: this car's module sent them (capture M), or the
    car accepted them from the Pi in the supervised run (capture ZB)."""
    giulietta = get("giulietta940")
    sent = {s.name for s in giulietta.transmittable(Confidence.UNKNOWN)
            if not s.owner_exception}
    assert sent == {"bm_status_response", "bm_proxi_response", "bm_watchdog",
                    "bm_track_time", "bm_audio_channel"}
    for name in sent:
        source = giulietta.require(name).source
        assert "captures/M-" in source or "captures/ZB-" in source, name


def test_giulietta_buttons_are_the_ones_captured():
    """Capture C, one button at a time: the masks match the Doblo's and the
    159's, named after the Giulietta Blue&Me manual."""
    giulietta = get("giulietta940")
    expected = {
        "8000": "volume_up", "4000": "volume_down", "2000": "mute",
        "1000": "next", "0800": "prev", "0400": "source",
        "0080": "phone", "0040": "voice",
    }
    for payload, name in expected.items():
        assert giulietta.decode_buttons(bytes.fromhex(payload)) == [name]
    assert giulietta.decode_buttons(bytes.fromhex("0000")) == []


def test_giulietta_id_width_is_confirmed_by_capture_a():
    """29-bit extended: every frame of capture A, and of every capture since.

    First reported by karolkrupa (alfa-blue-me issue #1 comment 768146196),
    which stays cited as corroboration.
    """
    giulietta = get("giulietta940")
    assert giulietta.extended_ids is True
    assert giulietta.id_width_confidence is Confidence.CONFIRMED_940
    assert "captures/A-first-contact.log" in giulietta.id_width_evidence.source
    assert "768146196" in giulietta.id_width_evidence.source


def test_reported_evidence_still_cannot_transmit():
    assert not Confidence.REPORTED_940.transmittable_by_default
    assert Confidence.OTHER_VEHICLE < Confidence.REPORTED_940 < Confidence.CONFIRMED_940


def test_every_profile_cites_evidence_for_its_id_width():
    for profile in PROFILES.values():
        assert profile.id_width_evidence is not None, profile.key
        assert profile.id_width_evidence.source.strip(), profile.key


def test_the_giulietta_is_expected_to_resemble_the_doblo_not_the_159():
    """Consequence of the reported width: the 940 is contemporary with the Doblo
    263 and shares its 29-bit addressing, while the 159 is an older generation.
    Recorded as a structural expectation, not as importable frame IDs."""
    assert get("giulietta940").extended_ids == get("doblo263").extended_ids
    assert get("giulietta940").extended_ids != get("alfa939").extended_ids


def test_no_reference_frame_claims_giulietta_evidence():
    for key in REFERENCE_ONLY:
        for spec in get(key):
            assert spec.confidence <= Confidence.OTHER_VEHICLE, spec


def test_every_frame_cites_a_source():
    for profile in PROFILES.values():
        for spec in profile:
            assert spec.source.strip(), spec


def test_the_two_references_disagree_about_transport():
    """Documents the finding that motivates the entire capture-first design."""
    doblo, alfa = get("doblo263"), get("alfa939")
    assert doblo.extended_ids is True
    assert alfa.extended_ids is False
    shared = {s.name for s in doblo} & {s.name for s in alfa}
    assert shared, "the profiles should implement overlapping functions"
    for name in shared:
        assert doblo.require(name).can_id != alfa.require(name).can_id, name


#: Buttons both reference platforms name identically and encode identically.
#: This agreement across two unrelated FCA generations is the only cross-platform
#: hint we have about the Giulietta's button payload.
AGREED_BUTTONS = ("volume_up", "volume_down", "mute", "source", "menu", "windows")


def test_the_two_references_agree_about_the_common_button_masks():
    doblo = {b.name: b.mask for b in get("doblo263").buttons}
    alfa = {b.name: b.mask for b in get("alfa939").buttons}
    for name in AGREED_BUTTONS:
            assert doblo[name] == alfa[name], name


def test_doblo_up_down_are_the_159s_next_prev():
    """Same masks, different names -- a naming difference, not a protocol one.

    The 159's steering wheel has an extra pair of buttons which it calls up/down
    (0x0002 / 0x0001), so it needed different names for 0x1000 / 0x0800. Both
    names differ, which is why the Giulietta profile names its own (manual p. 9).
    """
    doblo = {b.name: b.mask for b in get("doblo263").buttons}
    alfa = {b.name: b.mask for b in get("alfa939").buttons}
    assert doblo["up"] == alfa["next"] == 0x1000
    assert doblo["down"] == alfa["prev"] == 0x0800
    assert alfa["up"] == 0x0002 and alfa["down"] == 0x0001


def test_frame_names_are_validated():
    with pytest.raises(ValueError, match="snake_case"):
        FrameSpec("Bad Name", 1, False, Direction.RX, Confidence.UNKNOWN, "x")


def test_source_is_mandatory():
    with pytest.raises(ValueError, match="source citation"):
        FrameSpec("ok", 1, False, Direction.RX, Confidence.UNKNOWN, "")


def test_id_range_is_checked_against_width():
    with pytest.raises(ValueError, match="out of range"):
        FrameSpec("ok", 0x800, False, Direction.RX, Confidence.UNKNOWN, "x")
    FrameSpec("ok", 0x7FF, False, Direction.RX, Confidence.UNKNOWN, "x")


def test_duplicate_frame_names_rejected():
    spec = FrameSpec("dup", 1, False, Direction.RX, Confidence.UNKNOWN, "x")
    with pytest.raises(ValueError, match="duplicate"):
        Profile("t", "t", 50000, False, [spec, spec],
                id_width_evidence=Evidence(Confidence.UNKNOWN, "test"))


def test_setting_an_id_width_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="no evidence is cited"):
        Profile("t", "t", 50000, True, [])


def test_require_explains_the_unknown_case():
    with pytest.raises(KeyError, match="still UNKNOWN"):
        get("giulietta940").require("radio_frequency")


def test_lookup_distinguishes_11_and_29_bit():
    doblo = get("doblo263")
    spec = doblo.require("steering_wheel_buttons")
    assert doblo.by_id(spec.can_id, extended=True) is spec
    assert doblo.by_id(spec.can_id, extended=False) is None
