"""FCA 6-bit display text codec.

Evidence level: LIKELY_SHARED. fmntf's Doblo 263 character map
(gist c4b2744bad3908ef10fc9a5d377f2823) and karolkrupa's Alfa 159 map
(alfa-blue-me/utils/TextEncoder.py) are value-for-value identical, and both
projects frame multi-frame text the same way. Two independent teams on two
platform generations arriving at the same table is the strongest non-Giulietta
evidence in this project -- but the *frame ID* that carries this payload is
still UNKNOWN for the 940 and lives in the profile, not here.

Wire format
-----------
Each CAN frame carries a 2-byte header followed by up to 6 payload bytes::

    byte 0:  (total_frames - 1) << 4 | frame_index
    byte 1:  destination << 4 | 0xA
    bytes 2..7: text bits, 6 bits per character, MSB first

The character stream is terminated by the end-of-string code (0b000000) and
zero-padded to fill the frame. Multi-field strings (artist / title) are joined
with the field separator code (0b111111).
"""

from __future__ import annotations

import re
import unicodedata
from enum import IntEnum

# fmntf gist / karolkrupa TextEncoder, transcribed verbatim. Index == 6-bit code.
_CODE_TO_CHAR: dict[int, str] = {
    0b000000: "\r",  # end of string
    0b000001: "0", 0b000010: "1", 0b000011: "2", 0b000100: "3",
    0b000101: "4", 0b000110: "5", 0b000111: "6", 0b001000: "7",
    0b001001: "8", 0b001010: "9", 0b001011: ".",
    0b001100: "A", 0b001101: "B", 0b001110: "C", 0b001111: "D",
    0b010000: "E", 0b010001: "F", 0b010010: "G", 0b010011: "H",
    0b010100: "I", 0b010101: "J", 0b010110: "K", 0b010111: "L",
    0b011000: "M", 0b011001: "N", 0b011010: "O", 0b011011: "P",
    0b011100: "Q", 0b011101: "R", 0b011110: "S", 0b011111: "T",
    0b100000: "U", 0b100001: "V", 0b100010: "W", 0b100011: "X",
    0b100100: "Y", 0b100101: "Z",
    0b100110: "ñ", 0b100111: "ç",   # n-tilde, c-cedilla
    0b101000: " ",
    0b101001: "Ğ", 0b101010: "i", 0b101011: "j", 0b101100: "§",
    0b101101: "À", 0b101110: "Ä", 0b101111: "ŭ", 0b110000: "Ü",
    0b110001: "9",   # duplicate of 0b001010 in the source map
    0b110010: "_", 0b110011: "_", 0b110100: "_",
    0b110101: "?", 0b110110: "°", 0b110111: "!",
    0b111000: "+", 0b111001: "-", 0b111010: ":", 0b111011: "/",
    0b111100: "#", 0b111101: "*",
    0b111110: "_",
    0b111111: "\n",  # field separator
}

END_OF_STRING = 0b000000
FIELD_SEPARATOR = 0b111111
FALLBACK = 0b110010  # "_", used for anything not in the map
#: Codes whose glyph nobody has seen; the reference maps wrote them as "_".
#: The car's radio shows them in step 14 (node4021_full.py --glyph-test). If
#: one turns out to be a bracket, map it in _CODE_TO_CHAR and drop it from
#: SPACED below.
UNSEEN_GLYPHS = (0b110010, 0b110011, 0b110100, 0b111110)
#: Glyph test on the car (2026-09-27): all four show nothing, so "_" is
#: invisible on the radio and no bracket glyph exists. Brackets become
#: BRACKET (owner: "*"; "-" is the artist - title separator): "Pneuma (Audio)"
#: reads PNEUMA *AUDIO*.
DASHED = "()[]{}"
BRACKET = "*"
#: Characters not in the map to show as a space instead. Put "()[]{}" here
#: (and take them out of DASHED) to space brackets.
SPACED = ""

# First (canonical) code wins, so the duplicate "9" and "_" entries round-trip
# back to the lowest code rather than the alias.
_CHAR_TO_CODE: dict[str, int] = {}
for _code in sorted(_CODE_TO_CHAR):
    _CHAR_TO_CODE.setdefault(_CODE_TO_CHAR[_code], _code)

BITS_PER_CHAR = 6
PAYLOAD_BYTES = 6
HEADER_BYTES = 2
HEADER_NIBBLE = 0xA


class Display(IntEnum):
    """Destination display, from the header's high nibble."""

    DASHBOARD = 1
    RADIO = 2


def normalise(text: str) -> str:
    """Fold text into the printable subset of the 6-bit map.

    Plain letters are upper-cased first: the map's odd lowercase "i" and "j"
    slots are special glyphs, and the Doblo's module wrote "SOCIALE" with a
    capital I. Other characters that exist in the map (n-tilde, c-cedilla, the
    accented capitals) are preserved; everything else is stripped of
    diacritics, upper-cased, and finally replaced with ``_``.
    """
    out: list[str] = []
    spaced = False
    for ch in text:
        if ch in DASHED and ch not in SPACED:
            out.append(BRACKET)
            continue
        if ch in SPACED and ch not in _CHAR_TO_CODE:
            out.append(" ")
            spaced = True
            continue
        upper = ch.upper()
        if ch.isascii() and upper in _CHAR_TO_CODE:
            out.append(upper)
            continue
        if ch in _CHAR_TO_CODE:
            out.append(ch)
            continue
        if upper in _CHAR_TO_CODE:
            out.append(upper)
            continue
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", upper)
            if not unicodedata.combining(c)
        )
        out.append(folded if folded and folded in _CHAR_TO_CODE else "_")
    result = "".join(out)
    if spaced:  # tidy only what the brackets left: no doubled or edge spaces
        result = re.sub(r" {2,}", " ", result)
        result = re.sub(r" *\n *", "\n", result).strip(" ")
    return result


def _pack(codes: list[int]) -> bytes:
    """Pack 6-bit codes MSB-first, zero-padded to a byte boundary."""
    bits = 0
    nbits = 0
    out = bytearray()
    for code in codes:
        bits = (bits << BITS_PER_CHAR) | (code & 0x3F)
        nbits += BITS_PER_CHAR
        while nbits >= 8:
            nbits -= 8
            out.append((bits >> nbits) & 0xFF)
    if nbits:
        out.append((bits << (8 - nbits)) & 0xFF)
    return bytes(out)


def _unpack(data: bytes) -> list[int]:
    """Inverse of _pack. Trailing bits that cannot form a full code are dropped."""
    codes: list[int] = []
    bits = 0
    nbits = 0
    for byte in data:
        bits = (bits << 8) | byte
        nbits += 8
        while nbits >= BITS_PER_CHAR:
            nbits -= BITS_PER_CHAR
            codes.append((bits >> nbits) & 0x3F)
    return codes


def encode_text(text: str, *, terminate: bool = True) -> bytes:
    """Encode a string to packed 6-bit bytes, without any frame header.

    ``\\n`` in the input is emitted as the field separator, which is how both
    reference projects delimit artist from title.
    """
    codes = [_CHAR_TO_CODE.get(ch, FALLBACK) for ch in normalise(text)]
    if terminate:
        codes.append(END_OF_STRING)
    return _pack(codes)


def decode_text(data: bytes, *, stop_at_terminator: bool = True) -> str:
    """Decode packed 6-bit bytes back to a string."""
    out: list[str] = []
    for code in _unpack(data):
        if code == END_OF_STRING and stop_at_terminator:
            break
        out.append(_CODE_TO_CHAR.get(code, "_"))
    return "".join(out)


def encode_frames(text: str, display: Display = Display.RADIO) -> list[bytes]:
    """Encode text into a list of complete 8-byte CAN payloads.

    The caller supplies the arbitration ID from the active profile; this
    function never knows one.
    """
    return _frames(encode_text(text), display)


def encode_code_frames(codes: "list[int]", display: Display = Display.RADIO) -> list[bytes]:
    """Frames for raw 6-bit codes, terminated: for showing unseen glyphs."""
    return _frames(_pack([c & 0x3F for c in codes] + [END_OF_STRING]), display)


def _frames(body: bytes, display: Display) -> list[bytes]:
    chunks = [
        body[i : i + PAYLOAD_BYTES] for i in range(0, max(len(body), 1), PAYLOAD_BYTES)
    ] or [b""]
    total = len(chunks)
    if total > 16:
        raise ValueError(f"text needs {total} frames; header allows at most 16")
    frames: list[bytes] = []
    for index, chunk in enumerate(chunks):
        header = bytes(
            [((total - 1) << 4) | index, (int(display) << 4) | HEADER_NIBBLE]
        )
        frames.append(header + chunk.ljust(PAYLOAD_BYTES, b"\x00"))
    return frames


def decode_frames(frames: list[bytes]) -> tuple[str, Display | int]:
    """Reassemble a text message from its CAN payloads.

    Frames may arrive in any order; the header index puts them back together.
    Returns the text and the destination display.
    """
    if not frames:
        return "", Display.RADIO
    ordered: dict[int, bytes] = {}
    total = 1
    display: Display | int = Display.RADIO
    for payload in frames:
        if len(payload) < HEADER_BYTES:
            raise ValueError(f"text frame too short: {payload.hex()}")
        total = (payload[0] >> 4) + 1
        ordered[payload[0] & 0x0F] = payload[HEADER_BYTES:]
        raw_display = payload[1] >> 4
        try:
            display = Display(raw_display)
        except ValueError:
            display = raw_display
    missing = [i for i in range(total) if i not in ordered]
    if missing:
        raise ValueError(f"incomplete text message, missing frame index {missing}")
    body = b"".join(ordered[i] for i in range(total))
    return decode_text(body), display


def split_fields(text: str) -> list[str]:
    """Split a decoded string on the field separator."""
    return text.split("\n")
