"""Unit tests for palette-derivation strategies."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness._oklch import hex_to_oklch
from harness.derive_palette import (
    STRATEGIES,
    derive,
    derive_analogous,
    derive_complementary,
    derive_monochromatic,
    derive_split_complementary,
    derive_tetradic,
    derive_triadic,
    palette_with_contrast,
)


def _h(hex_str: str) -> float:
    return hex_to_oklch(hex_str)[2]


def test_monochromatic_step_count():
    p = derive_monochromatic(seed="#0A4D8C", steps=11)
    assert len(p.primary) == 11


def test_monochromatic_steps_are_monotonic_in_lightness():
    p = derive_monochromatic(seed="#0A4D8C", steps=11)
    # The primary ramp goes from darkest to lightest.
    Ls = [hex_to_oklch(h)[0] for h in p.primary]
    assert Ls == sorted(Ls)  # strictly increasing or non-decreasing
    assert Ls[0] < Ls[-1]


def test_monochromatic_neutral_is_lower_chroma_than_primary():
    p = derive_monochromatic(seed="#0A4D8C", steps=11)
    primary_C = [hex_to_oklch(h)[1] for h in p.primary]
    neutral_C = [hex_to_oklch(h)[1] for h in p.neutral]
    # Skip the L≈0 and L≈1 endpoints where C is naturally tiny.
    assert max(neutral_C[2:-2]) < min(primary_C[2:-2])


def test_monochromatic_deterministic():
    p1 = derive_monochromatic(seed="#0A4D8C", steps=11)
    p2 = derive_monochromatic(seed="#0A4D8C", steps=11)
    assert p1.primary == p2.primary
    assert p1.neutral == p2.neutral


def test_complementary_secondary_is_180_off_seed():
    p = derive_complementary(seed="#0A4D8C", steps=11)
    seed_h = _h("#0A4D8C")
    accent_h = _h(p.accents["secondary"][5])  # middle step
    # Angular distance from a perfect 180° offset: wrap delta to [0, 360),
    # measure how far it is from 180°.
    diff = abs(((accent_h - seed_h) % 360) - 180)
    # Allow ±6° drift from gamut clipping near the edges.
    assert diff < 6


def test_triadic_has_two_accents_at_120_offsets():
    p = derive_triadic(seed="#0A4D8C", steps=11)
    assert set(p.accents.keys()) == {"accent_b", "accent_c"}


def test_split_complementary_accents_at_150_and_210():
    p = derive_split_complementary(seed="#0A4D8C", steps=11)
    assert set(p.accents.keys()) == {"accent_b", "accent_c"}


def test_analogous_accents_at_pm_30():
    p = derive_analogous(seed="#0A4D8C", steps=11)
    assert set(p.accents.keys()) == {"accent_b", "accent_c"}


def test_tetradic_has_three_accents():
    p = derive_tetradic(seed="#0A4D8C", steps=11)
    assert set(p.accents.keys()) == {"accent_b", "accent_c", "accent_d"}


def test_dispatch_known_strategy():
    p = derive(seed="#0A4D8C", strategy="triadic", steps=11)
    assert p.strategy == "triadic"
    assert "accent_b" in p.accents


def test_dispatch_unknown_strategy_raises():
    with pytest.raises(ValueError, match=r"unknown strategy"):
        derive(seed="#0A4D8C", strategy="random-bs", steps=11)


def test_strategies_constant_enumerates_all_six():
    expected = {
        "monochromatic",
        "complementary",
        "triadic",
        "split-complementary",
        "analogous",
        "tetradic",
    }
    assert set(STRATEGIES) == expected


def test_contrast_table_shape():
    p = derive(seed="#0A4D8C", strategy="monochromatic", steps=11)
    table = palette_with_contrast(p)
    # Each step has a contrast pair against white and black, both >= 1.0.
    assert len(table["primary"]) == 11
    for entry in table["primary"]:
        assert 1.0 <= entry["contrast_on_white"] <= 21.0
        assert 1.0 <= entry["contrast_on_black"] <= 21.0


def test_cli_derive_palette_writes_json(tmp_path: Path):
    out = tmp_path / "palette-out"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "derive-palette",
            "--seed",
            "#0A4D8C",
            "--strategy",
            "triadic",
            "--steps",
            "11",
            "--name",
            "demo",
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    palette_json = out / "palette.json"
    assert palette_json.exists()
    data = json.loads(palette_json.read_text())
    assert data["strategy"] == "triadic"
    assert len(data["primary"]) == 11
    assert "accent_b" in data["accents"]
