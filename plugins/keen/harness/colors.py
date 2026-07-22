"""Color utilities shared across the harness.

Centralized so analyze, tokens, systemize, and report all parse colors the same
way. Pure stdlib — no Pillow/Playwright deps live here.
"""

from __future__ import annotations

import re

_HEX = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$", re.I)
_RGB = re.compile(r"^rgba?\(\s*(\d+)\s*,?\s*(\d+)\s*,?\s*(\d+)(?:\s*[,/]\s*([\d.]+))?\s*\)$")


def parse_color(s: str) -> tuple[float, float, float, float] | None:
    """Parse a CSS color string to (r, g, b, a) in [0, 1]. Returns None if unrecognized."""
    if not s:
        return None
    s = s.strip()
    if s in ("transparent", "currentcolor", "inherit", "initial"):
        return None
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            ri, gi, bi = (int(c * 2, 16) for c in h)
            return ri / 255, gi / 255, bi / 255, 1.0
        if len(h) == 6:
            ri, gi, bi = (int(h[i : i + 2], 16) for i in (0, 2, 4))
            return ri / 255, gi / 255, bi / 255, 1.0
        if len(h) == 8:
            ri, gi, bi, ai = (int(h[i : i + 2], 16) for i in (0, 2, 4, 6))
            return ri / 255, gi / 255, bi / 255, ai / 255
    m = _RGB.match(s)
    if m:
        rf, gf, bf = (int(m.group(i)) / 255 for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return rf, gf, bf, a
    return None


def rel_luminance(c: tuple[float, float, float, float]) -> float:
    """WCAG 2.1 relative luminance."""

    def chan(x: float) -> float:
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    return 0.2126 * chan(c[0]) + 0.7152 * chan(c[1]) + 0.0722 * chan(c[2])


def blend_over(
    fg: tuple[float, float, float, float], bg: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Composite fg over opaque bg using fg.a."""
    a = fg[3]
    return (
        fg[0] * a + bg[0] * (1 - a),
        fg[1] * a + bg[1] * (1 - a),
        fg[2] * a + bg[2] * (1 - a),
        1.0,
    )


def contrast_ratio(fg: str, bg: str) -> float | None:
    """WCAG contrast ratio; returns None if either color can't be parsed."""
    f = parse_color(fg)
    b = parse_color(bg)
    if not f or not b:
        return None
    if f[3] < 1.0:
        f = blend_over(f, b)
    lf = rel_luminance(f)
    lb = rel_luminance(b)
    light, dark = max(lf, lb), min(lf, lb)
    return (light + 0.05) / (dark + 0.05)


def to_hex(s: str) -> str | None:
    """Normalize any CSS color string to #rrggbb. Drops alpha."""
    p = parse_color(s)
    if not p:
        return None
    r, g, b = (round(p[i] * 255) for i in (0, 1, 2))
    return f"#{r:02x}{g:02x}{b:02x}"


def is_neutral(rgb_tuple: tuple[int, int, int], tolerance: int = 8) -> bool:
    r, g, b = rgb_tuple
    return max(r, g, b) - min(r, g, b) <= tolerance


def is_neutral_hex(hex_color: str, tolerance: int = 8) -> bool:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb: tuple[int, int, int] = (
        int(h[0:2], 16),
        int(h[2:4], 16),
        int(h[4:6], 16),
    )
    return is_neutral(rgb, tolerance)
