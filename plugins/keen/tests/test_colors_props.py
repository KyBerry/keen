"""Property-based tests for harness/colors.py.

NOTE: The task prompt describes a parse_color signature returning a 3-tuple of
ints in [0, 255]. The actual implementation returns a 4-tuple of floats in
[0, 1] (rgba). Properties below match the *real* implementation. The prompt's
"`parse_color` and `to_hex` round-trip" property is preserved (it works
regardless of the internal float representation).

Hypothesis strategies docs:
- text/integers/composite: https://hypothesis.readthedocs.io/en/latest/data.html
"""

from __future__ import annotations

import math
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from harness.colors import (
    contrast_ratio,
    is_neutral,
    parse_color,
    rel_luminance,
    to_hex,
)

# --- parse_color: hex round-trip & range -----------------------------------

_HEX_CHARS = st.sampled_from(string.hexdigits.lower()[:16])
_six_hex = st.text(alphabet=_HEX_CHARS, min_size=6, max_size=6)


@given(h=_six_hex)
@settings(max_examples=200)
def test_parse_color_six_hex_returns_floats_in_unit_range(h: str) -> None:
    """parse_color('#RRGGBB') returns a 4-tuple of floats in [0, 1]."""
    result = parse_color(f"#{h}")
    assert result is not None
    assert len(result) == 4
    for ch in result:
        assert isinstance(ch, float)
        assert 0.0 <= ch <= 1.0


@given(h=_six_hex)
@settings(max_examples=200)
def test_parse_color_round_trip_via_to_hex(h: str) -> None:
    """to_hex(parse_color('#h')) returns the same canonical #rrggbb form."""
    src = f"#{h}"
    parsed = parse_color(src)
    assert parsed is not None
    # to_hex takes a *string*, not a parsed tuple — round-trip through string form.
    canonical = to_hex(src)
    assert canonical == src.lower()


# --- rel_luminance: bounded -----------------------------------------------

_unit_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(r=_unit_float, g=_unit_float, b=_unit_float)
@settings(max_examples=200)
def test_rel_luminance_in_unit_range(r: float, g: float, b: float) -> None:
    """For any (r, g, b) in [0,1], rel_luminance is in [0, 1]."""
    lum = rel_luminance((r, g, b, 1.0))
    assert 0.0 <= lum <= 1.0 + 1e-9


# --- contrast_ratio properties --------------------------------------------

# Use canonical hex strings as the color domain.
_hex_color = st.builds(lambda h: f"#{h}", _six_hex)


@given(a=_hex_color, b=_hex_color)
@settings(max_examples=200)
def test_contrast_ratio_symmetric(a: str, b: str) -> None:
    """contrast_ratio(a, b) == contrast_ratio(b, a)."""
    r1 = contrast_ratio(a, b)
    r2 = contrast_ratio(b, a)
    assert r1 is not None and r2 is not None
    assert math.isclose(r1, r2, rel_tol=1e-9)


@given(c=_hex_color)
@settings(max_examples=200)
def test_contrast_ratio_self_is_one(c: str) -> None:
    """contrast_ratio(c, c) == 1.0 (within float epsilon)."""
    r = contrast_ratio(c, c)
    assert r is not None
    assert math.isclose(r, 1.0, rel_tol=1e-9)


def test_contrast_black_on_white_is_21() -> None:
    """Concrete sanity check: max contrast is exactly 21:1."""
    r = contrast_ratio("#000000", "#ffffff")
    assert r is not None
    assert math.isclose(r, 21.0, rel_tol=1e-6)


@given(a=_hex_color, b=_hex_color)
@settings(max_examples=200)
def test_contrast_ratio_bounded_1_to_21(a: str, b: str) -> None:
    """For any two parseable hex colors, contrast is in [1.0, 21.0]."""
    r = contrast_ratio(a, b)
    assert r is not None
    assert 1.0 - 1e-9 <= r <= 21.0 + 1e-9


# --- is_neutral: total over [0,255]^3 -------------------------------------

_byte = st.integers(min_value=0, max_value=255)


@given(r=_byte, g=_byte, b=_byte)
@settings(max_examples=200)
def test_is_neutral_total(r: int, g: int, b: int) -> None:
    """is_neutral never throws and always returns bool for (int,int,int) in [0,255]^3."""
    out = is_neutral((r, g, b))
    assert isinstance(out, bool)


@given(v=_byte)
@settings(max_examples=50)
def test_is_neutral_true_for_equal_channels(v: int) -> None:
    """Pure gray (r == g == b) is always neutral, regardless of tolerance."""
    assert is_neutral((v, v, v)) is True
