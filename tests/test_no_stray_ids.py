"""Architectural invariant: CAN identifiers live only in protocol profiles.

Brief section 7: "Do not scatter raw CAN IDs throughout the Python source. All
vehicle-specific CAN IDs and frame formats should be centralized."

This test enforces it mechanically. If it fails, a magic number has leaked out of
a profile and into logic, which is how the reference projects ended up with IDs
in six files each.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "blueandme"
PROFILE_DIR = PACKAGE / "protocol" / "profiles"

#: Files permitted to contain CAN identifiers, and why.
ALLOWED = {
    PROFILE_DIR / "doblo263.py",
    PROFILE_DIR / "alfa939.py",
    PROFILE_DIR / "giulietta940.py",
}

#: Anything below this is too small to be an 11-bit identifier worth hiding.
MIN_SUSPICIOUS = 0x100

#: Structural bounds, not identifiers: the maximum 11-bit and 29-bit values.
#: frame.py needs them to range-check the IDs the profiles declare.
WIDTH_BOUNDS = {0x7FF, 0x1FFFFFFF}


def source_files():
    return sorted(p for p in PACKAGE.rglob("*.py") if p not in ALLOWED)


def looks_like_a_can_id(node: ast.Constant, source: str) -> bool:
    value = node.value
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    if value < MIN_SUSPICIOUS or value in WIDTH_BOUNDS:
        return False
    if not (value <= 0x7FF or 0x10000 <= value <= 0x1FFFFFFF):
        return False
    # A CAN identifier is written in hex. A decimal literal in the same numeric
    # range -- 1000 for milliseconds, 1000000 for a crystal frequency -- is not.
    segment = ast.get_source_segment(source, node) or ""
    return segment.lower().startswith(("0x", "0b"))


@pytest.mark.parametrize("path", source_files(), ids=lambda p: str(p.name))
def test_no_can_ids_outside_profiles(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    offenders = [
        (node.lineno, hex(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and looks_like_a_can_id(node, source)
    ]
    assert not offenders, (
        f"{path} contains what look like CAN identifiers at "
        f"{offenders}. Move them into a protocol profile."
    )


def test_the_guard_itself_catches_a_planted_id(tmp_path):
    """A test that only ever passes is not a test."""
    planted = tmp_path / "bad.py"
    planted.write_text("CANID_BM_STATUS = 0x0E094021\n")
    source = planted.read_text()
    tree = ast.parse(source)
    found = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and looks_like_a_can_id(n, source)
    ]
    assert found == [0x0E094021]


def test_the_guard_ignores_decimal_literals_in_the_same_range(tmp_path):
    planted = tmp_path / "fine.py"
    planted.write_text("CRYSTAL_HZ = 16000000\nPERIOD_MS = 1000\n")
    source = planted.read_text()
    tree = ast.parse(source)
    assert not [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and looks_like_a_can_id(n, source)
    ]
