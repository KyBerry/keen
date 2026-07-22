"""Hypothesis property tests for harness.derive_palette."""

import itertools
import re

from hypothesis import given, settings
from hypothesis import strategies as st

from harness._oklch import hex_to_oklch
from harness.derive_palette import (
    STRATEGIES,
    derive,
    derive_monochromatic,
    palette_with_contrast,
)

_HEX = st.from_regex(r"^#[0-9A-Fa-f]{6}$", fullmatch=True)


@given(
    seed=_HEX,
    strategy=st.sampled_from(sorted(STRATEGIES)),
    steps=st.integers(min_value=3, max_value=15),
)
@settings(max_examples=200, deadline=2000)
def test_all_strategies_emit_valid_hex(seed: str, strategy: str, steps: int) -> None:
    """For any valid sRGB seed and any strategy, every emitted color is a
    syntactically-valid hex string in #RRGGBB form (uppercase)."""
    p = derive(seed, strategy, steps)
    hex_re = re.compile(r"^#[0-9A-F]{6}$")
    accent_colors = list(itertools.chain.from_iterable(p.accents.values()))
    for color in p.primary + p.neutral + accent_colors:
        assert hex_re.match(color), f"bad hex: {color!r}"


@given(seed=_HEX, steps=st.integers(min_value=3, max_value=15))
@settings(max_examples=100, deadline=2000)
def test_monochromatic_lightness_is_monotonic(seed: str, steps: int) -> None:
    p = derive_monochromatic(seed, steps)
    Ls = [hex_to_oklch(h)[0] for h in p.primary]
    # Each step's L must be >= the previous (modulo gamut-clip noise).
    for prev, curr in itertools.pairwise(Ls):
        assert curr + 1e-3 >= prev


@given(seed=_HEX, strategy=st.sampled_from(sorted(STRATEGIES)))
@settings(max_examples=50, deadline=2000)
def test_contrast_table_in_range(seed: str, strategy: str) -> None:
    p = derive(seed, strategy, steps=11)
    table = palette_with_contrast(p)
    for entry in table["primary"]:
        assert 1.0 <= entry["contrast_on_white"] <= 21.0
        assert 1.0 <= entry["contrast_on_black"] <= 21.0
