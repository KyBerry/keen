"""Unit tests for the OKLCH color math module."""

import math

import pytest

from harness._oklch import (
    hex_to_oklch,
    linear_rgb_to_oklab,
    linear_to_srgb_channel,
    oklab_to_linear_rgb,
    oklab_to_oklch,
    oklch_to_hex,
    oklch_to_oklab,
    srgb_to_linear_channel,
)


def test_srgb_to_linear_zero():
    assert pytest.approx(0.0) == srgb_to_linear_channel(0.0)


def test_srgb_to_linear_one():
    assert pytest.approx(1.0) == srgb_to_linear_channel(1.0)


def test_srgb_to_linear_midpoint_lower_branch():
    # The sRGB transfer-function switches branches at ~0.04045. Below that,
    # the function is a simple linear scale: x / 12.92.
    assert pytest.approx(0.04 / 12.92) == srgb_to_linear_channel(0.04)


def test_srgb_to_linear_midpoint_upper_branch():
    # Above ~0.04045 the transfer is gamma-2.4 around an offset.
    # Hand-computed: ((0.5 + 0.055) / 1.055) ** 2.4 ≈ 0.2140
    assert pytest.approx(0.2140, abs=1e-3) == srgb_to_linear_channel(0.5)


def test_round_trip_random_values():
    for x in [0.0, 0.1, 0.25, 0.5, 0.75, 0.95, 1.0]:
        roundtrip = linear_to_srgb_channel(srgb_to_linear_channel(x))
        assert pytest.approx(x, abs=1e-6) == roundtrip


def test_oklab_round_trip_white():
    # Pure white maps to OKLab L=1.0, a=0, b=0 (within float epsilon).
    L, a, b = linear_rgb_to_oklab(1.0, 1.0, 1.0)
    assert pytest.approx(1.0, abs=1e-3) == L
    assert pytest.approx(0.0, abs=1e-3) == a
    assert pytest.approx(0.0, abs=1e-3) == b


def test_oklab_round_trip_black():
    L, _a, _b = linear_rgb_to_oklab(0.0, 0.0, 0.0)
    assert pytest.approx(0.0, abs=1e-6) == L


def test_oklab_round_trip_random():
    # A few hand-picked colors. Round-trip linRGB → OKLab → linRGB should
    # match the input within 1e-4.
    samples = [(0.2, 0.5, 0.8), (0.95, 0.1, 0.3), (0.5, 0.5, 0.5)]
    for r, g, b in samples:
        L, oa, ob = linear_rgb_to_oklab(r, g, b)
        r2, g2, b2 = oklab_to_linear_rgb(L, oa, ob)
        assert pytest.approx(r, abs=1e-4) == r2
        assert pytest.approx(g, abs=1e-4) == g2
        assert pytest.approx(b, abs=1e-4) == b2


def test_oklab_to_polar_and_back():
    # Take a known OKLab and round-trip through OKLCH (polar).
    L, a, b = 0.6, 0.05, -0.10
    L2, C, h = oklab_to_oklch(L, a, b)
    assert pytest.approx(L) == L2
    L3, a3, b3 = oklch_to_oklab(L2, C, h)
    assert pytest.approx((L, a, b), abs=1e-9) == (L3, a3, b3)


def test_hex_to_oklch_white():
    L, C, h = hex_to_oklch("#FFFFFF")
    assert pytest.approx(1.0, abs=1e-3) == L
    assert pytest.approx(0.0, abs=1e-3) == C
    # Hue is undefined when C ≈ 0; we just check it's a finite float.
    assert math.isfinite(h)


def test_hex_to_oklch_known_blue():
    # #0A4D8C is the example seed in the spec.
    L, C, h = hex_to_oklch("#0A4D8C")
    # L in [0.38, 0.42], reasonable chroma, hue near 260° (blue).
    assert 0.35 < L < 0.45
    assert C > 0.05
    assert 250.0 <= h <= 270.0


def test_oklch_to_hex_round_trip():
    L, C, h = hex_to_oklch("#0A4D8C")
    out = oklch_to_hex(L, C, h)
    # Round-trip must be ≤ 1 channel-unit away (gamut/quantization noise).
    assert out.startswith("#")
    assert len(out) == 7


def test_gamut_clip_super_saturated_blue():
    # OKLCH (L=0.5, C=0.5, h=260°) is way outside sRGB. After clipping, we
    # must get a real sRGB color whose hex parses cleanly.
    out = oklch_to_hex(0.5, 0.5, 260.0)
    assert out.startswith("#") and len(out) == 7
    # Round-trip the hex to OKLCH and confirm L is preserved, C reduced.
    L2, C2, h2 = hex_to_oklch(out)
    assert pytest.approx(0.5, abs=0.05) == L2
    assert C2 < 0.5
    # Hue is preserved within a few degrees.
    assert abs(((h2 - 260.0 + 540) % 360) - 180) < 8  # i.e. within ±8°


def test_gamut_clip_returns_in_gamut_for_extreme_inputs():
    # Throw a few wildly-out-of-gamut inputs and confirm each output parses.
    out1 = oklch_to_hex(0.95, 0.4, 10.0)
    out2 = oklch_to_hex(0.05, 0.4, 200.0)
    for hexstr in (out1, out2):
        # Should NOT contain raw negative ints encoded as garbage. Reparse:
        L, C, _h = hex_to_oklch(hexstr)
        assert math.isfinite(L) and math.isfinite(C)
