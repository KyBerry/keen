"""Tests designed to kill mutmut survivors in harness/colors.py.

Existing test_colors.py covers the parse_color happy paths and rough
WCAG-ratio sanity checks but does not exhaust the CSS-keyword early-return
set, the channel arithmetic constants, the alpha-only blend, or the
near-boundary contrast ratios that mutmut tends to target.
"""

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

# ---------------------------------------------------------------------------
# CSS-keyword early-return set (mutations 291–294)
# ---------------------------------------------------------------------------


def test_parse_color_transparent_keyword_returns_none() -> None:
    # mutmut: kill mutation 291 — was: "transparent" -> "XXtransparentXX".
    assert parse_color("transparent") is None


def test_parse_color_currentcolor_keyword_returns_none() -> None:
    # mutmut: kill mutation 292 — was: "currentcolor" -> "XXcurrentcolorXX".
    assert parse_color("currentcolor") is None


def test_parse_color_inherit_keyword_returns_none() -> None:
    # mutmut: kill mutation 293 — was: "inherit" -> "XXinheritXX".
    assert parse_color("inherit") is None


def test_parse_color_initial_keyword_returns_none() -> None:
    # mutmut: kill mutation 294 — was: "initial" -> "XXinitialXX".
    assert parse_color("initial") is None


def test_parse_color_unknown_keyword_returns_none() -> None:
    # Lock in the general contract — only the four listed keywords are
    # treated as "no color"; other random strings also fall through to
    # the unrecognized branch.
    assert parse_color("orange") is None
    assert parse_color("foo") is None


# ---------------------------------------------------------------------------
# parse_color arithmetic constants
# ---------------------------------------------------------------------------


def test_parse_color_three_hex_doubles_each_nibble() -> None:
    # Pin the c*2 expansion in the 3-digit branch. #abc -> a=0xaa, b=0xbb,
    # c=0xcc. Mutmut commonly mutates `c * 2` to `c * 3` or `c + 2`.
    r, g, b, a = parse_color("#abc")  # type: ignore[misc]
    assert math.isclose(r, 0xAA / 255)
    assert math.isclose(g, 0xBB / 255)
    assert math.isclose(b, 0xCC / 255)
    assert a == 1.0


def test_parse_color_eight_hex_alpha_uses_alpha_byte() -> None:
    # 80 = 128 -> a = 128/255. Pin the slice-pair (0,2,4,6) for the
    # 8-digit branch.
    r, g, b, a = parse_color("#abcdef80")  # type: ignore[misc]
    assert math.isclose(r, 0xAB / 255)
    assert math.isclose(g, 0xCD / 255)
    assert math.isclose(b, 0xEF / 255)
    assert math.isclose(a, 128 / 255)


def test_parse_color_eight_hex_full_alpha() -> None:
    # ff alpha must map to 1.0 exactly.
    _, _, _, a = parse_color("#000000ff")  # type: ignore[misc]
    assert a == 1.0


def test_parse_color_eight_hex_zero_alpha() -> None:
    # 00 alpha -> 0.0.
    _, _, _, a = parse_color("#000000")  # type: ignore[misc]
    assert a == 1.0
    _, _, _, a2 = parse_color("#00000000")  # type: ignore[misc]
    assert a2 == 0.0


def test_parse_color_rgb_alpha_default_when_missing() -> None:
    # When the alpha capture group is None, the default is 1.0. If a
    # mutmut mutation flipped the default to 0.0 the contrast checks
    # downstream would silently misclassify many pages.
    _, _, _, a = parse_color("rgb(10, 20, 30)")  # type: ignore[misc]
    assert a == 1.0


def test_parse_color_rgb_channel_value_division_by_255() -> None:
    # rgb(255, 0, 0) -> (1.0, 0.0, 0.0, 1.0). Pin the division by 255.
    r, g, b, _ = parse_color("rgb(255, 0, 0)")  # type: ignore[misc]
    assert r == 1.0 and g == 0.0 and b == 0.0
    # Pin a midpoint to detect any +/- off-by-one in the divisor.
    r2, _, _, _ = parse_color("rgb(127, 0, 0)")  # type: ignore[misc]
    assert math.isclose(r2, 127 / 255)


def test_parse_color_empty_returns_none() -> None:
    # Pin the empty short-circuit.
    assert parse_color("") is None


def test_parse_color_whitespace_only_returns_none() -> None:
    # `s.strip()` reduces whitespace to "" which becomes the empty-color
    # fall-through.
    assert parse_color("   ") is None


# ---------------------------------------------------------------------------
# rel_luminance — WCAG constants
# ---------------------------------------------------------------------------


def test_rel_luminance_white_is_one() -> None:
    assert math.isclose(rel_luminance((1.0, 1.0, 1.0, 1.0)), 1.0, rel_tol=1e-6)


def test_rel_luminance_black_is_zero() -> None:
    assert rel_luminance((0.0, 0.0, 0.0, 1.0)) == 0.0


def test_rel_luminance_red_uses_specified_coefficient() -> None:
    # Pure red. r channel transformed via WCAG formula, then weighted by
    # 0.2126. Pin the coefficient.
    r_only = rel_luminance((1.0, 0.0, 0.0, 1.0))
    assert math.isclose(r_only, 0.2126, rel_tol=1e-6)


def test_rel_luminance_green_uses_specified_coefficient() -> None:
    g_only = rel_luminance((0.0, 1.0, 0.0, 1.0))
    assert math.isclose(g_only, 0.7152, rel_tol=1e-6)


def test_rel_luminance_blue_uses_specified_coefficient() -> None:
    b_only = rel_luminance((0.0, 0.0, 1.0, 1.0))
    assert math.isclose(b_only, 0.0722, rel_tol=1e-6)


def test_rel_luminance_threshold_uses_0_03928() -> None:
    # The piecewise function changes at 0.03928. Pin a value just below
    # and just above.
    low = rel_luminance((0.03, 0.0, 0.0, 1.0))
    high = rel_luminance((0.05, 0.0, 0.0, 1.0))
    # Below threshold: linear scaling x/12.92.
    expected_low = 0.2126 * (0.03 / 12.92)
    # Above threshold: ((x + 0.055) / 1.055) ** 2.4
    expected_high = 0.2126 * (((0.05 + 0.055) / 1.055) ** 2.4)
    assert math.isclose(low, expected_low, rel_tol=1e-6)
    assert math.isclose(high, expected_high, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# blend_over — alpha compositing
# ---------------------------------------------------------------------------


def test_blend_over_zero_alpha_returns_bg() -> None:
    # Pure transparent fg: blend = bg color (channel 3 always 1.0).
    fg = (1.0, 0.0, 0.0, 0.0)
    bg = (0.0, 0.0, 1.0, 1.0)
    r, g, b, a = blend_over(fg, bg)
    assert math.isclose(r, 0.0)
    assert math.isclose(g, 0.0)
    assert math.isclose(b, 1.0)
    assert a == 1.0


def test_blend_over_full_alpha_returns_fg() -> None:
    fg = (0.5, 0.5, 0.5, 1.0)
    bg = (0.0, 0.0, 0.0, 1.0)
    r, g, b, a = blend_over(fg, bg)
    assert math.isclose(r, 0.5)
    assert math.isclose(g, 0.5)
    assert math.isclose(b, 0.5)
    assert a == 1.0


def test_blend_over_half_alpha_is_midpoint() -> None:
    # 0.5 alpha fg over opaque bg = midpoint of the two color tuples.
    fg = (1.0, 1.0, 1.0, 0.5)
    bg = (0.0, 0.0, 0.0, 1.0)
    r, g, b, a = blend_over(fg, bg)
    assert math.isclose(r, 0.5)
    assert math.isclose(g, 0.5)
    assert math.isclose(b, 0.5)
    assert a == 1.0


# ---------------------------------------------------------------------------
# contrast_ratio
# ---------------------------------------------------------------------------


def test_contrast_ratio_black_on_white_is_21() -> None:
    # The canonical maximum ratio.
    assert math.isclose(contrast_ratio("#000000", "#ffffff"), 21.0, rel_tol=1e-6)


def test_contrast_ratio_white_on_black_also_21() -> None:
    # Order independence — `light, dark = max(lf, lb), min(lf, lb)` ensures
    # symmetric output.
    assert math.isclose(contrast_ratio("#ffffff", "#000000"), 21.0, rel_tol=1e-6)


def test_contrast_ratio_identical_colors_is_1() -> None:
    assert math.isclose(contrast_ratio("#777777", "#777777"), 1.0, rel_tol=1e-6)


def test_contrast_ratio_returns_none_for_unparseable_fg() -> None:
    assert contrast_ratio("not-a-color", "#000000") is None


def test_contrast_ratio_returns_none_for_unparseable_bg() -> None:
    assert contrast_ratio("#ffffff", "not-a-color") is None


def test_contrast_ratio_blends_translucent_fg_against_bg() -> None:
    # A translucent white fg over opaque black bg should produce a
    # finite ratio less than 21 (because the effective fg luminance
    # is less than 1.0).
    r = contrast_ratio("#ffffff80", "#000000")
    assert r is not None
    assert 1.0 < r < 21.0
    # The same translucent fg over opaque white must approach 1.0 (the
    # blended fg is itself white).
    r2 = contrast_ratio("#ffffff80", "#ffffff")
    assert r2 is not None
    assert math.isclose(r2, 1.0, rel_tol=1e-6)


def test_contrast_ratio_constant_0_05_used_in_numerator_and_denominator() -> None:
    # WCAG adds 0.05 to both light and dark. Pin a midpoint case where
    # the answer hard-codes the 0.05 constant: gray on white.
    # rel_lum(gray) = ((0.5 + 0.055)/1.055)^2.4 * 1 (all channels equal)
    # gray = #808080 -> 0.5019607...
    g = 0.5019607843137255
    l_gray = ((g + 0.055) / 1.055) ** 2.4
    expected = (1.0 + 0.05) / (l_gray + 0.05)
    actual = contrast_ratio("#808080", "#ffffff")
    assert actual is not None
    assert math.isclose(actual, expected, rel_tol=1e-4)


# ---------------------------------------------------------------------------
# to_hex
# ---------------------------------------------------------------------------


def test_to_hex_drops_alpha() -> None:
    # Pin that to_hex returns 6-digit (no alpha) even for inputs that
    # carried alpha.
    out = to_hex("#abcdef80")
    assert out == "#abcdef"


def test_to_hex_rounds_to_nearest_byte() -> None:
    # Pin the round() — not floor or ceil. rgb(127.5, ...) would be
    # ambiguous so use exact integer channels.
    assert to_hex("rgb(255, 0, 0)") == "#ff0000"
    assert to_hex("rgb(128, 64, 32)") == "#804020"


def test_to_hex_three_digit_input_expanded() -> None:
    # `to_hex("#abc")` should round-trip through parse_color and re-emit
    # the 6-digit form #aabbcc.
    assert to_hex("#abc") == "#aabbcc"


def test_to_hex_unparseable_input_returns_none() -> None:
    assert to_hex("totally-not-a-color") is None


def test_to_hex_format_uses_lowercase_hex() -> None:
    # The format string is "#{r:02x}{g:02x}{b:02x}" — lowercase, 2-digit.
    out = to_hex("rgb(255, 254, 253)")
    assert out is not None
    assert out == out.lower()
    assert len(out) == 7  # `#` + six hex digits


# ---------------------------------------------------------------------------
# is_neutral / is_neutral_hex
# ---------------------------------------------------------------------------


def test_is_neutral_zero_difference_is_neutral() -> None:
    assert is_neutral((128, 128, 128)) is True


def test_is_neutral_within_tolerance() -> None:
    # max-min = 8 with default tolerance=8 -> True.
    assert is_neutral((120, 124, 128)) is True
    # Pin off-by-one: tolerance=8 includes equal to 8, not strictly less.
    # If mutmut changed `<= tolerance` to `< tolerance` this becomes False.
    assert is_neutral((100, 100, 108)) is True


def test_is_neutral_above_tolerance() -> None:
    # max-min = 9 > tolerance=8.
    assert is_neutral((100, 100, 109)) is False


def test_is_neutral_custom_tolerance() -> None:
    # Pin that tolerance is honored.
    assert is_neutral((100, 110, 120), tolerance=20) is True
    assert is_neutral((100, 110, 120), tolerance=10) is False


def test_is_neutral_hex_three_digit_expansion() -> None:
    # Pin that 3-digit hex is doubled to compare correctly.
    assert is_neutral_hex("#777") is True  # 0x77 == 0x77 == 0x77
    assert is_neutral_hex("#abc", tolerance=10) is False  # 0xaa..0xcc spread = 34


def test_is_neutral_hex_strips_leading_hash() -> None:
    # lstrip("#") path; same color without "#" must give same answer.
    assert is_neutral_hex("aaaaaa") == is_neutral_hex("#aaaaaa")


# ---------------------------------------------------------------------------
# is_neutral_hex base-16 parsing boundaries (mutations 163, 166)
# ---------------------------------------------------------------------------


def test_is_neutral_hex_g_channel_uses_base_16_not_base_17() -> None:
    # mutmut: kill mutation 163 — was: int(h[2:4], 16) -> int(h[2:4], 17).
    # Choose a hex where G under base-16 is just inside the default
    # tolerance, but under base-17 is just outside.
    # R = B = 0x77 = 119. G under base-16 = 0x7e = 126 (diff 7 — neutral).
    # G under base-17 = 7*17+14 = 133 (diff 14 — NOT neutral).
    assert is_neutral_hex("#777E77") is True


def test_is_neutral_hex_b_channel_uses_base_16_not_base_17() -> None:
    # mutmut: kill mutation 166 — was: int(h[4:6], 16) -> int(h[4:6], 17).
    # Same trick on the B channel. R = G = 0x77 = 119. B under base-16
    # = 0x7e = 126; under base-17 = 133.
    assert is_neutral_hex("#77777E") is True


# ---------------------------------------------------------------------------
# blend_over channel coupling (mutation 104)
# ---------------------------------------------------------------------------


def test_blend_over_g_channel_uses_g_components_not_b() -> None:
    # mutmut: kill mutation 104 — was: fg[1] * a + bg[1] * (1 - a) ->
    # fg[2] * a + bg[1] * (1 - a). With distinct R/G/B values on fg and
    # an opaque bg, the resulting G channel must equal the linear blend
    # of fg.g and bg.g, not fg.b.
    fg = (0.0, 0.5, 0.9, 0.5)  # alpha = 0.5
    bg = (0.0, 0.0, 0.0, 1.0)
    _r, g, _b, _a = blend_over(fg, bg)
    # Original: g = 0.5*0.5 + 0*0.5 = 0.25
    # Mutant:   g = 0.5*0.9 + 0*0.5 = 0.45
    assert math.isclose(g, 0.25)
    # Sanity: it must NOT equal the mutant value 0.45 (within floating
    # tolerance).
    assert not math.isclose(g, 0.45, abs_tol=0.01)
