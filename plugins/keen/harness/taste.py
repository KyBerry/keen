"""Taste DNA: a measurable fingerprint of a UI's design language.

If `analyze.py` is "what's wrong" and `slop.py` is "what's generic," then
`taste.py` is "what is this design's *signature*." It produces a fixed-
shape vector — color triads, type contrast, shape character, depth
grammar, density, temperature, chromatic spread — that you can compare,
remix, and reproduce.

Lifecycle workflows use this as an optional instrument:

  1. Refine or Establish can extract the vector for a captured run and write
     a human-readable characterization card plus machine-readable JSON.
  2. Explore or Establish can use the extracted vector as
     an *inspiration anchor*. The creation flow uses the vector to bias
     palette strategy, type ratio, radius character, and spacing density,
     while the anti-imitation predicate ensures the output stays distinct.

The vector is deterministic given the same DOM dumps + token data.

Pure Python — no model calls.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness._oklch import hex_to_oklch
from harness.colors import is_neutral_hex, to_hex

logger = logging.getLogger("keen.taste")


# -- Data model -----------------------------------------------------------


@dataclass
class TasteColor:
    """One characterizing color, with its OKLCH coordinates."""

    hex: str
    L: float
    C: float
    h_deg: float
    role_hint: str  # "primary" | "accent" | "neutral" | "surface"
    count: int


@dataclass
class TasteVector:
    """The full DNA fingerprint."""

    # --- color ---
    hue_anchor_deg: float | None  # dominant chromatic hue (OKLCH h)
    hue_spread_deg: float | None  # max arc between chromatic hues
    chromatic_intensity: float  # mean OKLCH C among non-neutral colors
    color_temperature: str  # "warm" | "neutral" | "cool"
    chromatic_count: int  # how many distinct hues > C-threshold
    palette: list[TasteColor]  # top characterizing colors
    # --- type ---
    primary_family: str | None
    secondary_family: str | None
    body_size_px: int | None
    display_ratio: float | None  # h1_size / body_size — typographic contrast
    weight_spread: int | None  # max_weight - min_weight
    family_count: int
    # --- shape ---
    radius_character: str  # "sharp" | "mixed" | "soft"
    radius_max_px: int
    distinct_radii_count: int
    # --- depth ---
    shadow_tier_count: int  # distinct shadow values
    has_layered_shadows: bool  # any element with >1 shadow stacked
    shadow_grammar: str  # "none" | "single" | "tiered" | "layered"
    # --- spacing / density ---
    spacing_grid_px: int  # 4 or 8 (or 0 if unclear)
    spacing_max_px: int
    density: str  # "compact" | "comfortable" | "airy"
    # --- composite ---
    archetype_hint: str  # nearest of the 5 archetypes
    # --- raw scores so the agent / downstream can reason ---
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["palette"] = [asdict(p) for p in self.palette]
        d["interpretation"] = (
            "Measured visual-language signals; composite values are heuristics, not quality ratings."
        )
        return d


# -- Helpers --------------------------------------------------------------


def _all_components(captures_dir: Path) -> list[dict]:
    out: list[dict] = []
    cdir = captures_dir / "components"
    if not cdir.exists():
        return out
    for path in sorted(cdir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            out.extend(data)
    return out


def _tokens(captures_dir: Path) -> dict[str, Any]:
    path = captures_dir / "tokens" / "extracted.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _temperature_for(hue_deg: float | None) -> str:
    """Map a dominant OKLCH hue to a temperature label.

    The bands match common designer convention: warm = red/orange/yellow
    (and the magenta-pink end which leans warm), cool = green-cyan-blue-
    violet-purple (anything with notable blue cast), neutral = the green
    spectrum where temperature is ambiguous.
    """
    if hue_deg is None:
        return "neutral"
    h = hue_deg % 360.0
    # Warm: red/orange/yellow + pink-magenta near red.
    if 0.0 <= h <= 80.0 or 325.0 <= h <= 360.0:
        return "warm"
    # Cool: cyan / blue / indigo / violet / purple — anything with blue cast.
    if 180.0 <= h <= 325.0:
        return "cool"
    # 80–180: greens and teals — temperature is genuinely ambiguous.
    return "neutral"


def _color_role_hint(hex_str: str, L: float, C: float) -> str:
    """Rough role assignment from OKLCH coords."""
    if is_neutral_hex(hex_str, tolerance=10) or C < 0.04:
        if L > 0.85:
            return "surface"
        return "neutral"
    return "accent" if C < 0.12 else "primary"


def _density_for(spacing_values: list[dict]) -> str:
    """Classify density from the spacing usage distribution."""
    if not spacing_values:
        return "comfortable"
    # Weighted mean of spacing values: count is the weight.
    total = sum(s.get("count", 0) for s in spacing_values)
    if total == 0:
        return "comfortable"
    weighted = sum(s.get("value", 0) * s.get("count", 0) for s in spacing_values) / total
    if weighted < 12:
        return "compact"
    if weighted < 28:
        return "comfortable"
    return "airy"


def _radius_character(distinct_radii: list[int]) -> str:
    if not distinct_radii:
        return "sharp"
    max_r = max(distinct_radii)
    if max_r <= 4:
        return "sharp"
    if max_r >= 16:
        return "soft"
    return "mixed"


def _shadow_grammar(shadow_values: Counter, layered: bool) -> str:
    if not shadow_values:
        return "none"
    if layered:
        return "layered"
    if len(shadow_values) == 1:
        return "single"
    return "tiered"


def _font_first_token(raw: str) -> str:
    first = raw.split(",", 1)[0].strip().strip('"').strip("'")
    return first


def _archetype_hint(
    density: str,
    radius_character: str,
    shadow_grammar: str,
    chromatic_intensity: float,
    display_ratio: float | None,
) -> str:
    """Pick the closest of the 5 archetypes from the vector.

    The mapping mirrors the priors in references/system-archetypes.md so a
    system-creation flow seeded from a taste vector lands on the same archetype
    that the human would pick.
    """
    # Brand-forward: high chromatic intensity (saturated color does the talking)
    if chromatic_intensity > 0.10 and shadow_grammar in ("tiered", "layered"):
        return "brand-forward"
    # Editorial: airy + soft + large display contrast
    if (
        density == "airy"
        and radius_character != "sharp"
        and (display_ratio is None or display_ratio > 2.0)
    ):
        return "editorial"
    # Dense: compact + sharp
    if density == "compact" and radius_character == "sharp":
        return "dense"
    # Utilitarian: comfortable + sharp + low chroma
    if radius_character == "sharp" and chromatic_intensity < 0.07:
        return "utilitarian"
    # Default: clarity-first
    return "clarity-first"


# -- Vector extraction ---------------------------------------------------


_PX_RX = re.compile(r"^([\d.]+)px")


def _parse_px(s: str | None) -> float | None:
    if not s:
        return None
    m = _PX_RX.match(s)
    return float(m.group(1)) if m else None


def _extract_palette(tokens: dict) -> list[TasteColor]:
    """Pick the top 8 characterizing colors from the foreground + background
    token frequency. Includes both neutrals (for surface/text intent) and
    chromatic colors (for accent intent)."""
    palette: dict[str, TasteColor] = {}
    foreground = (tokens.get("colors") or {}).get("foreground", []) or []
    background = (tokens.get("colors") or {}).get("background", []) or []
    for entry in foreground + background:
        hex_ = to_hex(entry.get("value", ""))
        if not hex_:
            continue
        if hex_ in palette:
            palette[hex_].count += entry.get("count", 0)
            continue
        try:
            L, C, h = hex_to_oklch(hex_)
        except ValueError:
            continue
        palette[hex_] = TasteColor(
            hex=hex_,
            L=round(L, 3),
            C=round(C, 3),
            h_deg=round(h, 1),
            role_hint=_color_role_hint(hex_, L, C),
            count=entry.get("count", 0),
        )
    return sorted(palette.values(), key=lambda c: -c.count)[:8]


def _extract_hue_anchor(palette: list[TasteColor]) -> tuple[float | None, float | None]:
    """Return (dominant chromatic hue, max hue-spread arc) from palette."""
    chromatic = [c for c in palette if c.C >= 0.05]
    if not chromatic:
        return (None, None)
    # Dominant = highest-count chromatic.
    anchor = max(chromatic, key=lambda c: c.count).h_deg
    if len(chromatic) < 2:
        return (anchor, 0.0)
    hues = sorted(c.h_deg for c in chromatic)
    gaps = [hues[i + 1] - hues[i] for i in range(len(hues) - 1)]
    gaps.append(360.0 - hues[-1] + hues[0])
    span = 360.0 - max(gaps)
    return (anchor, round(span, 1))


def _extract_type_signals(tokens: dict) -> dict[str, Any]:
    families = (tokens.get("type") or {}).get("families", []) or []
    sizes = (tokens.get("type") or {}).get("sizes_px", []) or []
    weights = (tokens.get("type") or {}).get("weights", []) or []
    first_tokens: Counter[str] = Counter()
    for entry in families:
        ft = _font_first_token(entry.get("value", ""))
        if ft:
            first_tokens[ft] += entry.get("count", 1)
    ranked = first_tokens.most_common()
    primary = ranked[0][0] if ranked else None
    secondary = next((n for n, _ in ranked[1:] if n.lower() != (primary or "").lower()), None)

    # Body size = mode in the 12–18px band (or 14 if absent).
    body_sizes = [s for s in sizes if 11 <= s.get("value", 0) <= 18]
    body_px = None
    if body_sizes:
        body_px = max(body_sizes, key=lambda s: s.get("count", 0)).get("value")

    # H1 size = the largest size with >= 0.5% of body-size usage.
    h1_px = None
    if body_px is not None and sizes:
        sorted_sizes = sorted(sizes, key=lambda s: -s.get("value", 0))
        h1_px = sorted_sizes[0].get("value")

    display_ratio = None
    if body_px and h1_px and body_px > 0:
        display_ratio = round(h1_px / body_px, 2)

    weight_values = [w.get("value", 400) for w in weights]
    weight_spread = (max(weight_values) - min(weight_values)) if weight_values else None

    return {
        "primary_family": primary,
        "secondary_family": secondary,
        "body_size_px": body_px,
        "display_ratio": display_ratio,
        "weight_spread": weight_spread,
        "family_count": len(first_tokens),
    }


def _extract_shape_signals(tokens: dict) -> dict[str, Any]:
    radii = (tokens.get("shape") or {}).get("border_radii_px", []) or []
    distinct = sorted({int(r.get("value", 0)) for r in radii if r.get("value", 0) > 0})
    return {
        "radius_character": _radius_character(distinct),
        "radius_max_px": max(distinct) if distinct else 0,
        "distinct_radii_count": len(distinct),
    }


def _extract_depth_signals(comps: list[dict]) -> dict[str, Any]:
    shadow_values: Counter[str] = Counter()
    layered = False
    for c in comps:
        s = c.get("styles", {}) or {}
        bs = (s.get("boxShadow") or "").strip()
        if not bs or bs == "none":
            continue
        normalized = re.sub(r"\s+", " ", bs)
        shadow_values[normalized] += 1
        # Layered: multiple comma-separated shadow values on a single element.
        # Naive: count commas not inside parens.
        depth = 0
        commas_outside_parens = 0
        for ch in normalized:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                commas_outside_parens += 1
        if commas_outside_parens >= 1:
            layered = True
    grammar = _shadow_grammar(shadow_values, layered)
    return {
        "shadow_tier_count": len(shadow_values),
        "has_layered_shadows": layered,
        "shadow_grammar": grammar,
    }


def _extract_spacing_signals(tokens: dict) -> dict[str, Any]:
    spacing_values = (tokens.get("spacing") or {}).get("values_px", []) or []
    diag = tokens.get("diagnostics") or {}
    on4 = float(diag.get("spacing_on_4px_grid_pct", 0))
    on8 = float(diag.get("spacing_on_8px_grid_pct", 0))
    grid = 0
    if on8 > 80.0:
        grid = 8
    elif on4 > 80.0:
        grid = 4
    return {
        "spacing_grid_px": grid,
        "spacing_max_px": int(max((s.get("value", 0) for s in spacing_values), default=0)),
        "density": _density_for(spacing_values),
    }


def extract_taste(captures_dir: Path) -> TasteVector:
    """Compute the taste vector for a captures directory."""
    captures_dir = Path(captures_dir)
    comps = _all_components(captures_dir)
    tokens = _tokens(captures_dir)

    palette = _extract_palette(tokens)
    chromatic_pal = [c for c in palette if c.C >= 0.05]
    chromatic_intensity = (
        round(sum(c.C for c in chromatic_pal) / len(chromatic_pal), 3) if chromatic_pal else 0.0
    )
    hue_anchor, hue_spread = _extract_hue_anchor(palette)

    type_signals = _extract_type_signals(tokens)
    shape_signals = _extract_shape_signals(tokens)
    depth_signals = _extract_depth_signals(comps)
    space_signals = _extract_spacing_signals(tokens)

    temperature = _temperature_for(hue_anchor)
    archetype = _archetype_hint(
        space_signals["density"],
        shape_signals["radius_character"],
        depth_signals["shadow_grammar"],
        chromatic_intensity,
        type_signals["display_ratio"],
    )

    vector = TasteVector(
        hue_anchor_deg=hue_anchor,
        hue_spread_deg=hue_spread,
        chromatic_intensity=chromatic_intensity,
        color_temperature=temperature,
        chromatic_count=len(chromatic_pal),
        palette=palette,
        **type_signals,
        **shape_signals,
        **depth_signals,
        **space_signals,
        archetype_hint=archetype,
        scores={
            "distinctiveness": _distinctiveness_score(
                palette, type_signals, shape_signals, depth_signals
            ),
            "boldness": _boldness_score(chromatic_intensity, type_signals.get("display_ratio")),
            "polish": _polish_score(depth_signals, shape_signals),
        },
    )
    return vector


def _distinctiveness_score(
    palette: list[TasteColor],
    type_signals: dict,
    shape_signals: dict,
    depth_signals: dict,
) -> float:
    """Heuristic 0–1: how unusual is this taste vector vs. an LLM default?

    1.0 = fully distinctive. 0 = pure default.
    """
    score = 1.0
    # Penalize Inter monoculture.
    primary = (type_signals.get("primary_family") or "").lower()
    if primary in ("inter", "system-ui", "-apple-system", "roboto", ""):
        score -= 0.25
    # Penalize 8px-only radius character.
    if (
        shape_signals.get("radius_max_px") == 8
        and shape_signals.get("distinct_radii_count", 0) <= 1
    ):
        score -= 0.20
    # Penalize single-tier shadow.
    if depth_signals.get("shadow_grammar") in ("none", "single"):
        score -= 0.15
    # Penalize all-purple palettes.
    purple = sum(1 for c in palette if c.C >= 0.05 and 270 <= c.h_deg <= 320)
    chromatic_total = sum(1 for c in palette if c.C >= 0.05)
    if chromatic_total >= 2 and purple / chromatic_total > 0.5:
        score -= 0.15
    return round(max(0.0, score), 2)


def _boldness_score(chromatic_intensity: float, display_ratio: float | None) -> float:
    """0–1: how loud is the design? Saturated colors + big type contrast."""
    color_bold = min(1.0, chromatic_intensity / 0.25)
    type_bold = 0.0
    if display_ratio is not None:
        type_bold = min(1.0, max(0.0, (display_ratio - 1.5) / 2.5))
    return round(0.6 * color_bold + 0.4 * type_bold, 2)


def _polish_score(depth_signals: dict, shape_signals: dict) -> float:
    """0–1: how 'finished' does the surface treatment feel? Shadow tiers,
    radius scale, etc."""
    score = 0.0
    grammar = depth_signals.get("shadow_grammar")
    if grammar == "layered":
        score += 0.5
    elif grammar == "tiered":
        score += 0.3
    elif grammar == "single":
        score += 0.1
    rk = shape_signals.get("distinct_radii_count", 0)
    if rk >= 3:
        score += 0.4
    elif rk == 2:
        score += 0.2
    return round(min(1.0, score + 0.1), 2)  # +0.1 floor so any captured design isn't 0


def render_taste_card(v: TasteVector) -> str:
    """Render a human-readable 'taste card' for a TasteVector.

    Used as a compact characterization card when a lifecycle workflow needs it.
    """
    lines: list[str] = []
    lines.append("# Taste DNA")
    lines.append("")
    lines.append(f"**Archetype hint:** {v.archetype_hint}")
    lines.append(f"**Temperature:** {v.color_temperature}")
    lines.append(
        f"**Heuristic signals (not quality ratings):** distinctiveness "
        f"{v.scores.get('distinctiveness', 0)} · boldness "
        f"{v.scores.get('boldness', 0)} · structural consistency "
        f"{v.scores.get('polish', 0)}"
    )
    lines.append("")

    lines.append("## Color")
    lines.append("")
    if v.hue_anchor_deg is not None:
        lines.append(
            f"- **Hue anchor:** {v.hue_anchor_deg:.0f}° "
            f"(chromatic intensity {v.chromatic_intensity:.2f})"
        )
    if v.hue_spread_deg is not None:
        lines.append(
            f"- **Hue spread:** {v.hue_spread_deg:.0f}° across {v.chromatic_count} chromatic colors"
        )
    if v.palette:
        lines.append("- **Top characterizing colors:**")
        for c in v.palette[:6]:
            lines.append(
                f"  - `{c.hex}`  L={c.L:.2f}  C={c.C:.2f}  h={c.h_deg:.0f}°  "
                f"({c.role_hint}, used {c.count}×)"
            )
    lines.append("")

    lines.append("## Type")
    lines.append("")
    lines.append(f"- **Primary family:** {v.primary_family or '—'}")
    if v.secondary_family:
        lines.append(f"- **Secondary family:** {v.secondary_family}")
    lines.append(f"- **Family count:** {v.family_count}")
    if v.body_size_px:
        lines.append(f"- **Body size:** {v.body_size_px}px")
    if v.display_ratio is not None:
        lines.append(f"- **Display ratio (h1/body):** {v.display_ratio}")
    if v.weight_spread is not None:
        lines.append(f"- **Weight spread:** {v.weight_spread}")
    lines.append("")

    lines.append("## Shape & depth")
    lines.append("")
    lines.append(
        f"- **Radius character:** {v.radius_character} "
        f"(max {v.radius_max_px}px, {v.distinct_radii_count} distinct)"
    )
    lines.append(
        f"- **Shadow grammar:** {v.shadow_grammar} "
        f"({v.shadow_tier_count} distinct values, "
        f"layered={v.has_layered_shadows})"
    )
    lines.append("")

    lines.append("## Spacing & density")
    lines.append("")
    if v.spacing_grid_px:
        lines.append(f"- **Grid:** {v.spacing_grid_px}px")
    else:
        lines.append("- **Grid:** mixed / unclear")
    lines.append(f"- **Max spacing:** {v.spacing_max_px}px")
    lines.append(f"- **Density:** {v.density}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Taste DNA is measured, not vibes. Use these values directly when "
        "remixing this design's signature into a new system: pass the hue "
        "anchor as the seed, match the archetype, mirror the density and "
        "radius character._"
    )
    return "\n".join(lines) + "\n"


# -- Distance / remix utilities ------------------------------------------


def vector_distance(a: TasteVector, b: TasteVector) -> dict[str, float]:
    """Per-dimension distance between two taste vectors.

    Used by the anti-imitation predicate (when we want to verify a generated
    system isn't too close to a reference) and by Establish (when we want to
    verify the remix is 'inspired by' rather than a copy).

    All sub-scores are in [0, 1], where 0 = identical, 1 = maximally distant.
    The composite `total` is a weighted mean.
    """

    def _hue_dist(h1: float | None, h2: float | None) -> float:
        if h1 is None or h2 is None:
            return 0.5
        d = abs(h1 - h2)
        if d > 180.0:
            d = 360.0 - d
        return d / 180.0

    def _scalar_dist(x: float, y: float, scale: float) -> float:
        if scale == 0:
            return 0.0
        return min(1.0, abs(x - y) / scale)

    def _enum_dist(x: str, y: str) -> float:
        return 0.0 if x == y else 1.0

    hue = _hue_dist(a.hue_anchor_deg, b.hue_anchor_deg)
    intensity = _scalar_dist(a.chromatic_intensity, b.chromatic_intensity, scale=0.3)
    radius = _scalar_dist(float(a.radius_max_px), float(b.radius_max_px), scale=24.0)
    radius_char = _enum_dist(a.radius_character, b.radius_character)
    density = _enum_dist(a.density, b.density)
    shadow = _enum_dist(a.shadow_grammar, b.shadow_grammar)
    archetype = _enum_dist(a.archetype_hint, b.archetype_hint)
    family_a = (a.primary_family or "").lower()
    family_b = (b.primary_family or "").lower()
    family = 0.0 if family_a == family_b else 1.0

    weights = {
        "hue": 0.20,
        "chromatic_intensity": 0.15,
        "radius_px": 0.10,
        "radius_character": 0.10,
        "density": 0.10,
        "shadow_grammar": 0.10,
        "archetype": 0.15,
        "primary_family": 0.10,
    }
    components = {
        "hue": hue,
        "chromatic_intensity": intensity,
        "radius_px": radius,
        "radius_character": radius_char,
        "density": density,
        "shadow_grammar": shadow,
        "archetype": archetype,
        "primary_family": family,
    }
    total = sum(weights[k] * components[k] for k in weights)
    return {**components, "total": round(total, 3)}


def remix_seed_from(vector: TasteVector, *, shift_hue_deg: float = 30.0) -> str:
    """Produce a seed hex 'inspired by' the vector's hue anchor.

    Shifts the dominant hue by `shift_hue_deg` (default 30°, enough to read
    as different but still in the same family). Keeps the anchor's chromatic
    intensity and a midtone lightness so the resulting palette has range to
    walk.
    """
    if vector.hue_anchor_deg is None:
        # No chromatic anchor — return a neutral blue.
        return "#3B82F6"
    from harness._oklch import oklch_to_hex

    new_hue = (vector.hue_anchor_deg + shift_hue_deg) % 360.0
    L = 0.55
    C = max(0.10, min(0.22, vector.chromatic_intensity or 0.16))
    return oklch_to_hex(L, C, new_hue)


# -- Convenience entry point ---------------------------------------------


def extract_and_render(captures_dir: Path) -> tuple[TasteVector, str]:
    """Extract the vector and render its card in one call."""
    v = extract_taste(captures_dir)
    return v, render_taste_card(v)
