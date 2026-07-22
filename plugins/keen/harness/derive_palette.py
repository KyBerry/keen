"""Palette-derivation strategies.

Each strategy takes a seed hex + an integer step count and produces a
`Palette` containing a primary ramp (lightness-uniform OKLCH walk through
the seed's hue) plus a neutral ramp (the same lightness walk at a desaturated
chroma) plus, for multi-hue strategies, additional ramps at fixed hue
offsets.

All math goes through harness._oklch — no LLM, no I/O.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from harness._oklch import hex_to_oklch, oklch_to_hex
from harness.colors import contrast_ratio

logger = logging.getLogger("keen")

# Lightness extents for ramps. We stay slightly off the endpoints because
# pure 0 / pure 1 are visually ugly anchor points.
_L_MIN = 0.05
_L_MAX = 0.97

# Chroma floor for the neutral ramp. Designers tend to want a hint of the
# brand hue in their "gray" palette, hence non-zero.
_NEUTRAL_C = 0.012


@dataclass
class Palette:
    """A palette: one or more ramps (each a list of hex strings)."""

    seed: str
    strategy: str
    steps: int
    primary: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)
    # Multi-hue strategies populate `accents` with one ramp per offset.
    accents: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "strategy": self.strategy,
            "steps": self.steps,
            "primary": self.primary,
            "neutral": self.neutral,
            "accents": self.accents,
        }


def _ramp(L_seed: float, C_seed: float, h_deg: float, steps: int) -> list[str]:
    """Walk L uniformly across [_L_MIN, _L_MAX] at fixed (C, h).

    L_seed is accepted for signature compatibility but the ramp walks the
    full lightness range; the seed's L is preserved implicitly by the
    interpolation passing through it.

    Special cases:
      steps <= 0: empty list (caller asked for nothing).
      steps == 1: single midpoint color.
    """
    del L_seed  # ramp walks full L range; seed L is not the anchor
    if steps <= 0:
        return []
    if steps == 1:
        midpoint = (_L_MIN + _L_MAX) / 2.0
        return [oklch_to_hex(midpoint, C_seed, h_deg)]
    out: list[str] = []
    span = _L_MAX - _L_MIN
    for i in range(steps):
        L = _L_MIN + span * (i / (steps - 1))
        out.append(oklch_to_hex(L, C_seed, h_deg))
    return out


def derive_monochromatic(seed: str, steps: int = 11) -> Palette:
    """Single-hue ramp. Lightness walks uniformly; chroma + hue from seed."""
    L_seed, C_seed, h = hex_to_oklch(seed)
    primary = _ramp(L_seed, C_seed, h, steps)
    neutral = _ramp(L_seed, _NEUTRAL_C, h, steps - 2)  # smaller ramp by convention
    return Palette(
        seed=seed, strategy="monochromatic", steps=steps, primary=primary, neutral=neutral
    )


def _shift(h: float, deg: float) -> float:
    return (h + deg) % 360.0


def derive_complementary(seed: str, steps: int = 11) -> Palette:
    L, C, h = hex_to_oklch(seed)
    primary = _ramp(L, C, h, steps)
    neutral = _ramp(L, _NEUTRAL_C, h, steps - 2)
    secondary = _ramp(L, C, _shift(h, 180.0), steps)
    return Palette(
        seed=seed,
        strategy="complementary",
        steps=steps,
        primary=primary,
        neutral=neutral,
        accents={"secondary": secondary},
    )


def derive_triadic(seed: str, steps: int = 11) -> Palette:
    L, C, h = hex_to_oklch(seed)
    primary = _ramp(L, C, h, steps)
    neutral = _ramp(L, _NEUTRAL_C, h, steps - 2)
    return Palette(
        seed=seed,
        strategy="triadic",
        steps=steps,
        primary=primary,
        neutral=neutral,
        accents={
            "accent_b": _ramp(L, C, _shift(h, 120.0), steps),
            "accent_c": _ramp(L, C, _shift(h, 240.0), steps),
        },
    )


def derive_split_complementary(seed: str, steps: int = 11) -> Palette:
    L, C, h = hex_to_oklch(seed)
    primary = _ramp(L, C, h, steps)
    neutral = _ramp(L, _NEUTRAL_C, h, steps - 2)
    return Palette(
        seed=seed,
        strategy="split-complementary",
        steps=steps,
        primary=primary,
        neutral=neutral,
        accents={
            "accent_b": _ramp(L, C, _shift(h, 150.0), steps),
            "accent_c": _ramp(L, C, _shift(h, 210.0), steps),
        },
    )


def derive_analogous(seed: str, steps: int = 11) -> Palette:
    L, C, h = hex_to_oklch(seed)
    primary = _ramp(L, C, h, steps)
    neutral = _ramp(L, _NEUTRAL_C, h, steps - 2)
    return Palette(
        seed=seed,
        strategy="analogous",
        steps=steps,
        primary=primary,
        neutral=neutral,
        accents={
            "accent_b": _ramp(L, C, _shift(h, 30.0), steps),
            "accent_c": _ramp(L, C, _shift(h, -30.0), steps),
        },
    )


def derive_tetradic(seed: str, steps: int = 11) -> Palette:
    L, C, h = hex_to_oklch(seed)
    primary = _ramp(L, C, h, steps)
    neutral = _ramp(L, _NEUTRAL_C, h, steps - 2)
    return Palette(
        seed=seed,
        strategy="tetradic",
        steps=steps,
        primary=primary,
        neutral=neutral,
        accents={
            "accent_b": _ramp(L, C, _shift(h, 90.0), steps),
            "accent_c": _ramp(L, C, _shift(h, 180.0), steps),
            "accent_d": _ramp(L, C, _shift(h, 270.0), steps),
        },
    )


STRATEGIES: dict[str, Callable[[str, int], Palette]] = {
    "monochromatic": derive_monochromatic,
    "complementary": derive_complementary,
    "triadic": derive_triadic,
    "split-complementary": derive_split_complementary,
    "analogous": derive_analogous,
    "tetradic": derive_tetradic,
}


def derive(seed: str, strategy: str = "monochromatic", steps: int = 11) -> Palette:
    """Look up a strategy by name and run it."""
    fn = STRATEGIES.get(strategy)
    if fn is None:
        raise ValueError(
            f"unknown strategy: {strategy!r}; expected one of: {', '.join(sorted(STRATEGIES))}"
        )
    return fn(seed, steps)


def palette_with_contrast(p: Palette) -> dict:
    """Augment palette with contrast vs white and black for every step."""

    def _row(hex_str: str) -> dict:
        return {
            "hex": hex_str,
            "contrast_on_white": contrast_ratio(hex_str, "#FFFFFF"),
            "contrast_on_black": contrast_ratio(hex_str, "#000000"),
        }

    out = {
        "seed": p.seed,
        "strategy": p.strategy,
        "steps": p.steps,
        "primary": [_row(h) for h in p.primary],
        "neutral": [_row(h) for h in p.neutral],
        "accents": {k: [_row(h) for h in v] for k, v in p.accents.items()},
    }
    return out
