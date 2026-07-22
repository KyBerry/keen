"""Tests for color utilities in harness/colors.py."""

from __future__ import annotations

import math

from harness.colors import (
    blend_over,
    contrast_ratio,
    is_neutral,
    is_neutral_hex,
    parse_color,
    rel_luminance,
    to_hex,
)

# --- parse_color ----------------------------------------------------------


def test_parse_color_six_digit_hex_white() -> None:
    assert parse_color("#ffffff") == (1.0, 1.0, 1.0, 1.0)


def test_parse_color_six_digit_hex_black() -> None:
    assert parse_color("#000000") == (0.0, 0.0, 0.0, 1.0)


def test_parse_color_three_digit_hex_expands() -> None:
    # #fff -> #ffffff
    assert parse_color("#fff") == (1.0, 1.0, 1.0, 1.0)


def test_parse_color_three_digit_hex_mid() -> None:
    # #abc -> #aabbcc -> (170/255, 187/255, 204/255, 1.0)
    r, g, b, a = parse_color("#abc")  # type: ignore[misc]
    assert math.isclose(r, 170 / 255)
    assert math.isclose(g, 187 / 255)
    assert math.isclose(b, 204 / 255)
    assert a == 1.0


def test_parse_color_eight_digit_hex_with_alpha() -> None:
    # 80 = 128/255 alpha
    r, g, b, a = parse_color("#ff000080")  # type: ignore[misc]
    assert r == 1.0
    assert g == 0.0
    assert b == 0.0
    assert math.isclose(a, 128 / 255)


def test_parse_color_hex_case_insensitive() -> None:
    assert parse_color("#FFFFFF") == parse_color("#ffffff")
    assert parse_color("#AbCdEf") == parse_color("#abcdef")


def test_parse_color_rgb_function() -> None:
    assert parse_color("rgb(255, 0, 0)") == (1.0, 0.0, 0.0, 1.0)


def test_parse_color_rgb_no_spaces() -> None:
    assert parse_color("rgb(0,128,255)") is not None


def test_parse_color_rgba_with_alpha() -> None:
    r, g, b, a = parse_color("rgba(255, 255, 255, 0.5)")  # type: ignore[misc]
    assert r == 1.0 and g == 1.0 and b == 1.0
    assert a == 0.5


def test_parse_color_rgb_space_separated_slash_alpha() -> None:
    # The regex supports `rgb(R G B / A)` notation (slash before alpha).
    result = parse_color("rgb(255 0 0 / 0.5)")
    assert result is not None
    r, g, b, a = result
    assert r == 1.0 and g == 0.0 and b == 0.0
    assert a == 0.5


def test_parse_color_transparent_returns_none() -> None:
    assert parse_color("transparent") is None


def test_parse_color_currentcolor_returns_none() -> None:
    assert parse_color("currentcolor") is None


def test_parse_color_empty_returns_none() -> None:
    assert parse_color("") is None


def test_parse_color_unknown_named_color_returns_none() -> None:
    # Named CSS colors like "red" are not supported by the parser
    assert parse_color("red") is None


def test_parse_color_garbage_returns_none() -> None:
    assert parse_color("not-a-color") is None


def test_parse_color_strips_whitespace() -> None:
    assert parse_color("  #ffffff  ") == (1.0, 1.0, 1.0, 1.0)


# --- rel_luminance --------------------------------------------------------


def test_rel_luminance_white_is_one() -> None:
    assert math.isclose(rel_luminance((1.0, 1.0, 1.0, 1.0)), 1.0)


def test_rel_luminance_black_is_zero() -> None:
    assert rel_luminance((0.0, 0.0, 0.0, 1.0)) == 0.0


def test_rel_luminance_mid_gray_under_threshold_uses_linear_branch() -> None:
    # Very small linear values use x/12.92 branch (x <= 0.03928)
    lum = rel_luminance((0.02, 0.02, 0.02, 1.0))
    # Expected: 0.02 / 12.92 * (0.2126 + 0.7152 + 0.0722) = 0.02 / 12.92
    assert math.isclose(lum, 0.02 / 12.92, rel_tol=1e-9)


# --- contrast_ratio -------------------------------------------------------


def test_contrast_black_on_white_is_21() -> None:
    assert math.isclose(contrast_ratio("#000000", "#ffffff"), 21.0, rel_tol=1e-6)


def test_contrast_white_on_white_is_1() -> None:
    assert math.isclose(contrast_ratio("#ffffff", "#ffffff"), 1.0, rel_tol=1e-6)


def test_contrast_white_on_black_is_21() -> None:
    # Symmetric: order doesn't matter.
    assert math.isclose(contrast_ratio("#ffffff", "#000000"), 21.0, rel_tol=1e-6)


def test_contrast_returns_none_when_fg_unparseable() -> None:
    assert contrast_ratio("garbage", "#ffffff") is None


def test_contrast_returns_none_when_bg_unparseable() -> None:
    assert contrast_ratio("#000000", "garbage") is None


def test_contrast_returns_none_when_both_unparseable() -> None:
    assert contrast_ratio("transparent", "transparent") is None


def test_contrast_with_translucent_fg_blends_over_bg() -> None:
    # rgba(0,0,0,0.5) over #ffffff should compose to mid-gray-ish, not pure black.
    ratio = contrast_ratio("rgba(0, 0, 0, 0.5)", "#ffffff")
    assert ratio is not None
    assert 1.0 < ratio < 21.0


def test_contrast_ratio_bounded() -> None:
    # WCAG ratio must be in [1.0, 21.0].
    for fg, bg in [
        ("#ff0000", "#00ff00"),
        ("#888888", "#ffffff"),
        ("#0000ff", "#ffff00"),
    ]:
        r = contrast_ratio(fg, bg)
        assert r is not None
        assert 1.0 <= r <= 21.0


# --- blend_over -----------------------------------------------------------


def test_blend_over_fully_opaque_fg_returns_fg() -> None:
    fg = (1.0, 0.0, 0.0, 1.0)
    bg = (0.0, 0.0, 1.0, 1.0)
    assert blend_over(fg, bg) == (1.0, 0.0, 0.0, 1.0)


def test_blend_over_fully_transparent_fg_returns_bg_color() -> None:
    fg = (1.0, 0.0, 0.0, 0.0)
    bg = (0.0, 1.0, 0.0, 1.0)
    r, g, b, a = blend_over(fg, bg)
    assert math.isclose(r, 0.0)
    assert math.isclose(g, 1.0)
    assert math.isclose(b, 0.0)
    assert a == 1.0


def test_blend_over_half_alpha_is_midpoint() -> None:
    fg = (1.0, 0.0, 0.0, 0.5)
    bg = (0.0, 0.0, 1.0, 1.0)
    r, g, b, a = blend_over(fg, bg)
    assert math.isclose(r, 0.5)
    assert math.isclose(g, 0.0)
    assert math.isclose(b, 0.5)
    assert a == 1.0


# --- to_hex ---------------------------------------------------------------


def test_to_hex_normalizes_hex() -> None:
    assert to_hex("#ffffff") == "#ffffff"


def test_to_hex_short_form_expands() -> None:
    assert to_hex("#fff") == "#ffffff"


def test_to_hex_from_rgb() -> None:
    assert to_hex("rgb(255, 0, 0)") == "#ff0000"


def test_to_hex_drops_alpha() -> None:
    # Alpha is dropped per docstring ("Drops alpha")
    assert to_hex("#ff000080") == "#ff0000"


def test_to_hex_invalid_returns_none() -> None:
    assert to_hex("garbage") is None


def test_to_hex_empty_returns_none() -> None:
    assert to_hex("") is None


def test_to_hex_uppercase_normalizes_to_lowercase() -> None:
    assert to_hex("#ABCDEF") == "#abcdef"


# --- is_neutral / is_neutral_hex -----------------------------------------


def test_is_neutral_pure_gray() -> None:
    assert is_neutral((128, 128, 128)) is True


def test_is_neutral_black_white() -> None:
    assert is_neutral((0, 0, 0)) is True
    assert is_neutral((255, 255, 255)) is True


def test_is_neutral_slight_tint_within_tolerance() -> None:
    # max - min = 5 <= 8
    assert is_neutral((100, 105, 102)) is True


def test_is_neutral_strong_tint_rejected() -> None:
    # max - min = 100, way above default tolerance of 8
    assert is_neutral((200, 100, 100)) is False


def test_is_neutral_custom_tolerance_relaxed() -> None:
    # max - min = 50, accept with tolerance=100
    assert is_neutral((100, 150, 120), tolerance=100) is True


def test_is_neutral_hex_six_digit() -> None:
    assert is_neutral_hex("#808080") is True


def test_is_neutral_hex_short_form() -> None:
    assert is_neutral_hex("#888") is True


def test_is_neutral_hex_no_hash() -> None:
    # lstrip("#") tolerates no-hash form
    assert is_neutral_hex("808080") is True


def test_is_neutral_hex_strong_tint_rejected() -> None:
    assert is_neutral_hex("#ff0000") is False
