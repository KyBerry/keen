"""sRGB ↔ OKLCH conversion + gamut clipping.

Based on Björn Ottosson's OKLab/OKLCH spec:
  https://bottosson.github.io/posts/oklab/
  https://bottosson.github.io/posts/colorpicker/

This module is pure math. No I/O. Used by harness.derive_palette.
"""

from __future__ import annotations

import math

# -- sRGB transfer function ----------------------------------------------

# Threshold at which the sRGB transfer function switches between the linear
# segment near black and the gamma-2.4 segment.
_SRGB_THRESHOLD = 0.04045
_SRGB_GAMMA = 2.4


def srgb_to_linear_channel(c: float) -> float:
    """Convert one sRGB channel (0..1) to linear light (0..1)."""
    if c <= _SRGB_THRESHOLD:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** _SRGB_GAMMA


def linear_to_srgb_channel(c: float) -> float:
    """Convert one linear channel (0..1) back to sRGB-encoded (0..1)."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / _SRGB_GAMMA)) - 0.055


# -- linear sRGB → OKLab -------------------------------------------------
#
# Constants from Björn Ottosson's reference implementation. The forward
# matrix maps linear sRGB → an intermediate "LMS" cone space, then a fixed
# nonlinearity (cube root) maps LMS → OKLab.

_M1 = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)

_M2 = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)


def _cbrt(x: float) -> float:
    """Real cube root that handles negative inputs."""
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def linear_rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert linear-light sRGB (0..1) to OKLab (L, a, b)."""
    # Names l/m/s mirror the LMS cone-space convention in Björn Ottosson's
    # OKLab spec; renaming would obscure the reference implementation.
    l = _M1[0][0] * r + _M1[0][1] * g + _M1[0][2] * b  # noqa: E741
    m = _M1[1][0] * r + _M1[1][1] * g + _M1[1][2] * b
    s = _M1[2][0] * r + _M1[2][1] * g + _M1[2][2] * b
    l_, m_, s_ = _cbrt(l), _cbrt(m), _cbrt(s)
    L = _M2[0][0] * l_ + _M2[0][1] * m_ + _M2[0][2] * s_
    A = _M2[1][0] * l_ + _M2[1][1] * m_ + _M2[1][2] * s_
    B = _M2[2][0] * l_ + _M2[2][1] * m_ + _M2[2][2] * s_
    return (L, A, B)


# Inverse matrices for OKLab → linear sRGB.
_INV_M2 = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_INV_M1 = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)


def oklab_to_linear_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab back to linear-light sRGB (0..1)."""
    l_ = _INV_M2[0][0] * L + _INV_M2[0][1] * a + _INV_M2[0][2] * b
    m_ = _INV_M2[1][0] * L + _INV_M2[1][1] * a + _INV_M2[1][2] * b
    s_ = _INV_M2[2][0] * L + _INV_M2[2][1] * a + _INV_M2[2][2] * b
    # LMS cone-space names retained from the reference OKLab spec.
    l = l_**3  # noqa: E741
    m = m_**3
    s = s_**3
    r = _INV_M1[0][0] * l + _INV_M1[0][1] * m + _INV_M1[0][2] * s
    g = _INV_M1[1][0] * l + _INV_M1[1][1] * m + _INV_M1[1][2] * s
    b_out = _INV_M1[2][0] * l + _INV_M1[2][1] * m + _INV_M1[2][2] * s
    return (r, g, b_out)


# -- OKLab ↔ OKLCH (polar) -----------------------------------------------


def oklab_to_oklch(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab cartesian to OKLCH (L, C, h_degrees)."""
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a))
    if h < 0:
        h += 360.0
    return (L, C, h)


def oklch_to_oklab(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    """Convert OKLCH polar to OKLab cartesian."""
    h_rad = math.radians(h_deg)
    return (L, C * math.cos(h_rad), C * math.sin(h_rad))


# -- Hex parsing & emission ----------------------------------------------


def _parse_hex(s: str) -> tuple[float, float, float]:
    s = s.lstrip("#").strip()
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {s!r}")
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r, g, b)


def _to_hex(r: float, g: float, b: float) -> str:
    """Quantize 0..1 channels to 0..255 ints and emit #RRGGBB (uppercase)."""

    def _q(c: float) -> int:
        return max(0, min(255, round(c * 255)))

    return f"#{_q(r):02X}{_q(g):02X}{_q(b):02X}"


def hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    """Parse #RRGGBB to OKLCH (L, C, h_degrees)."""
    sr, sg, sb = _parse_hex(hex_str)
    lr = srgb_to_linear_channel(sr)
    lg = srgb_to_linear_channel(sg)
    lb = srgb_to_linear_channel(sb)
    L, a, b = linear_rgb_to_oklab(lr, lg, lb)
    return oklab_to_oklch(L, a, b)


def oklch_to_hex(L: float, C: float, h_deg: float) -> str:
    """Convert OKLCH back to #RRGGBB. Performs gamut clipping (see task 4)."""
    return oklch_to_hex_clipped(L, C, h_deg)


def _in_gamut(r: float, g: float, b: float) -> bool:
    """Are all three sRGB channels in [0, 1]?"""
    return 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0


def oklch_to_hex_clipped(L: float, C: float, h_deg: float, *, eps: float = 1e-4) -> str:
    """Convert OKLCH to #RRGGBB, reducing chroma until the result fits sRGB.

    Bisection in chroma keeps L and h fixed; only C shrinks. This preserves
    perceptual lightness and hue while sacrificing saturation — the right
    trade for design system ramps where the L step is the load-bearing axis.
    """
    L = max(0.0, min(1.0, L))
    C = max(0.0, C)

    def to_lin(L: float, C: float) -> tuple[float, float, float]:
        L_, a, b = oklch_to_oklab(L, C, h_deg)
        return oklab_to_linear_rgb(L_, a, b)

    # First check the requested chroma. If it's already in gamut, we're done.
    r, g, b = to_lin(L, C)
    if _in_gamut(r, g, b):
        return _to_hex(
            linear_to_srgb_channel(r),
            linear_to_srgb_channel(g),
            linear_to_srgb_channel(b),
        )

    # Bisect chroma to find the largest in-gamut C ≤ requested.
    lo, hi = 0.0, C
    while hi - lo > eps:
        mid = (lo + hi) * 0.5
        r, g, b = to_lin(L, mid)
        if _in_gamut(r, g, b):
            lo = mid
        else:
            hi = mid
    r, g, b = to_lin(L, lo)
    return _to_hex(
        linear_to_srgb_channel(r),
        linear_to_srgb_channel(g),
        linear_to_srgb_channel(b),
    )
