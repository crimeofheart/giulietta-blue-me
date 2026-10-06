"""The 6-bit display codec, checked against real captured frames.

The strongest test here is test_decodes_real_captured_menu_frames: those two
payloads were recorded from a genuine Blue&Me module by fmntf and published in
his part-3 article. They decode to an Italian menu string, which is not
something a wrong codec produces by accident.
"""

import pytest

from blueandme.protocol import text as T


def test_char_map_is_complete_and_six_bits():
    assert len(T._CODE_TO_CHAR) == 64
    assert set(T._CODE_TO_CHAR) == set(range(64))


def test_canonical_code_wins_for_duplicated_characters():
    # The published map lists "9" twice and "_" four times. Encoding must pick
    # the lowest code so that round-tripping is stable.
    assert T._CHAR_TO_CODE["9"] == 0b001010
    assert T._CHAR_TO_CODE["_"] == T.FALLBACK


@pytest.mark.parametrize(
    "value",
    ["HELLO", "PRONTO?", "0123456789", "A B C", "", "*#+-:/",
     "DAFT PUNK\nAROUND THE WORLD"],
)
def test_round_trip(value):
    assert T.decode_frames(T.encode_frames(value))[0] == value


def test_lowercase_and_accents_are_folded():
    assert T.decode_frames(T.encode_frames("Mötley"))[0] == "MOTLEY"
    assert T.decode_frames(T.encode_frames("café"))[0] == "CAFE"


def test_lowercase_i_and_j_are_capitals_not_the_special_slots():
    assert T.decode_frames(T.encode_frames("Maria jazz"))[0] == "MARIA JAZZ"


def test_unmappable_characters_become_underscore():
    assert T.decode_frames(T.encode_frames("A中B"))[0] == "A_B"


def test_header_encodes_total_index_and_display():
    frames = T.encode_frames("A" * 20, T.Display.RADIO)
    assert len(frames) == 3
    for index, frame in enumerate(frames):
        assert frame[0] >> 4 == len(frames) - 1
        assert frame[0] & 0x0F == index
        assert frame[1] == (int(T.Display.RADIO) << 4) | T.HEADER_NIBBLE
        assert len(frame) == 8


def test_header_matches_values_published_by_fmntf():
    # "0x202A means first message (3 total) on radio unit display"
    assert T.encode_frames("A" * 20, T.Display.RADIO)[0][:2] == bytes.fromhex("202A")
    # 0x101A: first of two, dashboard.
    assert T.encode_frames("A" * 10, T.Display.DASHBOARD)[0][:2] == bytes.fromhex("101A")


def test_decodes_real_captured_menu_frames():
    """Frames recorded from a genuine Blue&Me ECU on a Fiat Doblo 263.

    Source: medium.com/@fmntf part 3, where they are shown being emitted after a
    menu button press.
    """
    frames = [bytes.fromhex("101A8177D4610A0E"), bytes.fromhex("111A4D43182E8000")]
    body, display = T.decode_frames(frames)
    assert body == "ULTIME CHIAM. "
    assert display is T.Display.DASHBOARD


def test_frames_reassemble_out_of_order():
    frames = T.encode_frames("REASSEMBLE ME PLEASE", T.Display.RADIO)
    assert T.decode_frames(list(reversed(frames)))[0] == "REASSEMBLE ME PLEASE"


def test_incomplete_message_is_rejected():
    frames = T.encode_frames("A" * 20)
    with pytest.raises(ValueError, match="missing frame index"):
        T.decode_frames(frames[:1])


def test_field_separator_splits_fields():
    body, _ = T.decode_frames(T.encode_frames("ARTIST\nTITLE"))
    assert T.split_fields(body) == ["ARTIST", "TITLE"]


def test_encoder_reproduces_the_doblo_modules_own_track_text():
    """A working Blue&Me on the Doblo sent this on a track change
    (tests/fixtures/doblo-02-driving.log, 1517155333.36): our encoder makes
    the same seven frames, bit for bit."""
    from blueandme.protocol import text

    sent = ["602AE176A879F31F", "612A6A879A394FD7", "622A6A879F31F6A8",
            "632A79A39433F720", "642A4175DAA0E4D0", "652AA174283DA659",
            "662AFC0000000000"]
    message = "+LO STATO SOCI\nLO STATO SOCIA\nQUELLO CHE LE DONN\n"
    assert [f.hex().upper() for f in text.encode_frames(message, text.Display.RADIO)] == sent


def test_brackets_become_dashes_the_radio_has_no_bracket_nor_visible_underscore():
    assert T.normalise("Pneuma (Audio)") == "PNEUMA *AUDIO*"
    assert T.normalise("Song [Live]") == "SONG *LIVE*"


def test_spacing_brackets_when_switched_on(monkeypatch):
    monkeypatch.setattr(T, "SPACED", "()[]{}")
    assert T.normalise("Pneuma (Audio)") == "PNEUMA AUDIO"
    assert T.normalise("Song(Remix) [Live]") == "SONG REMIX LIVE"
    assert T.normalise("A  B") == "A  B"                 # only bracket spaces tidied


def test_raw_codes_for_the_glyph_test():
    frames = T.encode_code_frames([1, 0b110010, 2], T.Display.RADIO)
    assert T.decode_frames(frames) == ("0_1", T.Display.RADIO)
