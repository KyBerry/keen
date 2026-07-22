"""System-level predicates.

Unlike harness.analyze, which evaluates predicates against a captured PAGE,
this module evaluates predicates against a proposed SYSTEM JSON. Predicates
emit findings using the same shape as analyze, so downstream tooling
(rubric.py, report.py, the agent-side refine loop) can consume them uniformly.

Predicate IDs are stable strings registered in harness.rubric.PREDICATE_SEVERITIES.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from harness._oklch import hex_to_oklch
from harness.colors import contrast_ratio

logger = logging.getLogger("keen")

# Required color roles every system must declare.
REQUIRED_ROLES: tuple[str, ...] = (
    "text.default",
    "text.subtle",
    "text.inverse",
    "surface.default",
    "surface.subtle",
    "surface.bold",
    "border.default",
    "border.focused",
)

_AA_RATIO = 4.5
_AA_LARGE_RATIO = 3.0
_RATIO_TOLERANCE = 0.05  # ±5%


def _finding(predicate_id: str, severity: str, message: str, **extra: object) -> dict:
    return {
        "predicate_id": predicate_id,
        "severity": severity,
        "message": message,
        **extra,
    }


def _hex(system: dict, role: str) -> str | None:
    return system.get("colors", {}).get("roles", {}).get(role, {}).get("hex")


def _hue(hex_str: str) -> float:
    return hex_to_oklch(hex_str)[2]


# OKLCH L threshold for "dark" surfaces. Any surface with L <= 0.5 needs a
# paired `border.focused-inverse` role per spec §4.2 and Appendix B.
_DARK_SURFACE_L_MAX = 0.5

# -- Reference system fingerprints for the anti-imitation predicate --------
#
# Each entry captures (primary_hue_deg, type_ratio, radii_tuple,
# spacing_grid_px). The predicate flags a proposed system that matches a
# reference on 3+ of those dimensions — that's "you accidentally regenerated
# Material 3" territory, not honest inspiration.
#
# Hues are sampled from each system's signature primary color in OKLCH.
# Ratios + radii are read off the tokens we already encode in
# harness/tokens.py.

_REFERENCE_FINGERPRINTS: dict[str, dict[str, Any]] = {
    "material-3": {
        "hue_deg": 271.0,
        "type_ratio": 1.333,
        "radii": (0, 4, 8, 12, 16, 28),
        "grid": 4,
    },
    "apple-hig": {
        "hue_deg": 232.0,
        "type_ratio": 1.125,
        "radii": (0, 4, 6, 8, 10, 12, 14, 16, 20),
        "grid": 8,
    },
    "fluent-2": {"hue_deg": 232.0, "type_ratio": 1.125, "radii": (0, 2, 4, 6, 8, 12), "grid": 4},
    "polaris": {"hue_deg": 146.0, "type_ratio": 1.200, "radii": (4, 6, 8, 12), "grid": 4},
    "carbon": {"hue_deg": 254.0, "type_ratio": 1.125, "radii": (0, 4, 8), "grid": 8},
    "atlassian": {"hue_deg": 246.0, "type_ratio": 1.200, "radii": (3, 4, 8), "grid": 4},
}

_HUE_MATCH_DEG = 15.0
_RATIO_MATCH = 0.03  # within 3% counts as a hit
_RADII_OVERLAP_PCT = 0.66  # 2/3 of proposed radii must match a reference's


def _surfaces(system: dict) -> list[tuple[str, str]]:
    """Return [(role_name, hex), ...] for every defined surface role."""
    roles = system.get("colors", {}).get("roles", {}) or {}
    out: list[tuple[str, str]] = []
    for name, spec in roles.items():
        if not name.startswith("surface."):
            continue
        hx = spec.get("hex") if isinstance(spec, dict) else None
        if hx:
            out.append((name, hx))
    return out


def _is_dark_surface(hex_str: str) -> bool:
    """A surface is 'dark' (needs inverse focus indicator) when OKLCH L <= 0.5."""
    return hex_to_oklch(hex_str)[0] <= _DARK_SURFACE_L_MAX


# -- Color-role predicates -----------------------------------------------


def _pred_required_present(system: dict) -> list[dict]:
    """Every system must declare the universally-required color roles
    (Appendix B). Without these, downstream tooling and the refine loop
    have no shared vocabulary to reason about color decisions.
    """
    roles = system.get("colors", {}).get("roles", {}) or {}
    missing = [r for r in REQUIRED_ROLES if r not in roles]
    if not missing:
        return []
    return [
        _finding(
            "system.roles.required-present",
            "P0",
            f"missing required color roles: {', '.join(sorted(missing))}",
            missing_roles=missing,
        )
    ]


def _pred_text_default_aa(system: dict) -> list[dict]:
    """WCAG 2.2 SC 1.4.3 (AA): body text needs >= 4.5:1 contrast against its
    background. `text.default` on `surface.default` is the default pairing.
    """
    fg = _hex(system, "text.default")
    bg = _hex(system, "surface.default")
    if not (fg and bg):
        return []  # required-present already flagged this.
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio >= _AA_RATIO:
        return []
    return [
        _finding(
            "system.roles.text-default-aa",
            "P0",
            f"text.default vs surface.default is {ratio:.2f}:1 (need >= {_AA_RATIO}:1)",
            fg=fg,
            bg=bg,
            ratio=round(ratio, 2),
        )
    ]


def _pred_text_subtle_aa(system: dict) -> list[dict]:
    """WCAG 2.2 SC 1.4.3 (AA): secondary text still needs >= 4.5:1. Calling
    a color `subtle` is a usage hint, not a license to skip AA.
    """
    fg = _hex(system, "text.subtle")
    bg = _hex(system, "surface.default")
    if not (fg and bg):
        return []
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio >= _AA_RATIO:
        return []
    return [
        _finding(
            "system.roles.text-subtle-aa",
            "P0",
            f"text.subtle vs surface.default is {ratio:.2f}:1 (need >= {_AA_RATIO}:1)",
            fg=fg,
            bg=bg,
            ratio=round(ratio, 2),
        )
    ]


def _pred_text_inverse_aa(system: dict) -> list[dict]:
    """WCAG 2.2 SC 1.4.3 (AA) on inverse pairings: `text.inverse` is the
    foreground for the bold/dark surface and still needs >= 4.5:1 there.
    """
    fg = _hex(system, "text.inverse")
    bg = _hex(system, "surface.bold")
    if not (fg and bg):
        return []
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio >= _AA_RATIO:
        return []
    return [
        _finding(
            "system.roles.text-inverse-aa",
            "P0",
            f"text.inverse vs surface.bold is {ratio:.2f}:1 (need >= {_AA_RATIO}:1)",
            fg=fg,
            bg=bg,
            ratio=round(ratio, 2),
        )
    ]


def _pred_text_subtlest_large(system: dict) -> list[dict]:
    """WCAG 2.2 SC 1.4.3 (AA) large-text exception: when the role is
    explicitly reserved for large/UI text, the floor is 3:1 instead of 4.5:1.
    Optional role; absent role yields no finding.
    """
    fg = _hex(system, "text.subtlest")
    bg = _hex(system, "surface.default")
    if not (fg and bg):
        return []  # optional role
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio >= _AA_LARGE_RATIO:
        return []
    return [
        _finding(
            "system.roles.text-subtlest-large-only",
            "P1",
            f"text.subtlest vs surface.default is {ratio:.2f}:1 "
            f"(need >= {_AA_LARGE_RATIO}:1 for large/UI text)",
            fg=fg,
            bg=bg,
            ratio=round(ratio, 2),
        )
    ]


def _pred_text_disabled_not_aa(system: dict) -> list[dict]:
    """Disabled text is intentionally low-contrast: if it meets 4.5:1 it does
    not visibly read as disabled. WCAG 2.2 carves disabled controls out of
    the contrast requirement; this predicate enforces the design convention.
    """
    fg = _hex(system, "text.disabled")
    bg = _hex(system, "surface.default")
    if not (fg and bg):
        return []
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio < _AA_RATIO:
        return []
    return [
        _finding(
            "system.roles.text-disabled-not-aa",
            "P1",
            f"text.disabled vs surface.default is {ratio:.2f}:1 "
            f"(must be < {_AA_RATIO}:1 to read as disabled)",
            fg=fg,
            bg=bg,
            ratio=round(ratio, 2),
        )
    ]


def _pred_border_focused_aa(system: dict) -> list[dict]:
    """WCAG 2.2 SC 1.4.11 Non-text Contrast (AA): the default focus indicator
    must reach 3:1 against every LIGHT surface (OKLCH L > 0.5). A single
    `border.focused` color cannot satisfy 3:1 against both light and dark
    surfaces, so dark surfaces are policed by the paired inverse predicate.
    """
    fg = _hex(system, "border.focused")
    light_surfaces = [(n, h) for n, h in _surfaces(system) if not _is_dark_surface(h)]
    if not fg:
        # required-present already flags this generally; emit one targeted
        # finding so the refine loop knows exactly which role is missing.
        if light_surfaces:
            return [
                _finding(
                    "system.roles.border-focused-aa",
                    "P0",
                    "missing `border.focused` role required for focus visibility on light surfaces",
                )
            ]
        return []
    findings: list[dict] = []
    for surface_name, surface_hex in light_surfaces:
        ratio = contrast_ratio(fg, surface_hex)
        if ratio is None or ratio >= _AA_LARGE_RATIO:
            continue
        findings.append(
            _finding(
                "system.roles.border-focused-aa",
                "P0",
                f"border.focused vs {surface_name} is {ratio:.2f}:1 (need >= {_AA_LARGE_RATIO}:1)",
                fg=fg,
                bg=surface_hex,
                surface=surface_name,
                ratio=round(ratio, 2),
            )
        )
    return findings


def _pred_border_focused_inverse_aa(system: dict) -> list[dict]:
    """Paired inverse of `_pred_border_focused_aa`: when the system defines
    any dark surface (OKLCH L <= 0.5), a `border.focused-inverse` role must
    exist AND reach 3:1 against every dark surface (WCAG 2.2 SC 1.4.11 AA).
    """
    dark_surfaces = [(n, h) for n, h in _surfaces(system) if _is_dark_surface(h)]
    if not dark_surfaces:
        return []  # No dark surface, no inverse focus role required.
    fg = _hex(system, "border.focused-inverse")
    if not fg:
        names = sorted(n for n, _ in dark_surfaces)
        return [
            _finding(
                "system.roles.border-focused-inverse-aa",
                "P0",
                f"system defines dark surface(s) {', '.join(names)} but no "
                "`border.focused-inverse` role; a single `border.focused` cannot "
                "physically satisfy 3:1 on both light and dark surfaces",
                dark_surfaces=names,
            )
        ]
    findings: list[dict] = []
    for surface_name, surface_hex in dark_surfaces:
        ratio = contrast_ratio(fg, surface_hex)
        if ratio is None or ratio >= _AA_LARGE_RATIO:
            continue
        findings.append(
            _finding(
                "system.roles.border-focused-inverse-aa",
                "P0",
                f"border.focused-inverse vs {surface_name} is {ratio:.2f}:1 "
                f"(need >= {_AA_LARGE_RATIO}:1)",
                fg=fg,
                bg=surface_hex,
                surface=surface_name,
                ratio=round(ratio, 2),
            )
        )
    return findings


# -- Type-scale predicates -----------------------------------------------


def _pred_type_scale_monotonic(system: dict) -> list[dict]:
    """A coherent type scale must be strictly monotonically increasing.
    A non-monotonic scale (e.g. h2 smaller than body) breaks visual hierarchy
    and the assumption that role order maps to size order.
    """
    sizes = [s.get("size_px") for s in system.get("type", {}).get("sizes", []) if "size_px" in s]
    if len(sizes) < 2:
        return []
    for prev, curr in pairwise(sizes):
        if curr <= prev:
            return [
                _finding(
                    "system.type.scale-monotonic",
                    "P0",
                    f"type scale not strictly increasing: {prev} -> {curr}",
                    sizes=sizes,
                )
            ]
    return []


def _pred_type_ratio_consistent(system: dict) -> list[dict]:
    """Check a declared modular ratio without treating utility text as headings.

    Caption/body-small steps commonly use optical utility sizes rather than the
    display ratio. When roles and an explicit ratio exist, compare h3/h2/h1 to
    body at exponents 1/2/3. Legacy role-less scales retain the adjacent-ratio
    check. Integer rounding gets a +/-5% tolerance.
    """
    type_ = system.get("type", {})
    entries = type_.get("sizes", [])
    declared_ratio = type_.get("ratio")
    by_role: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        size_value = entry.get("size_px")
        if isinstance(size_value, (int, float)):
            by_role[str(entry.get("role") or entry.get("name"))] = float(size_value)
    headings = [(role, by_role[role]) for role in ("h3", "h2", "h1") if role in by_role]
    body_value = by_role.get("body")
    if (
        isinstance(declared_ratio, (int, float))
        and declared_ratio > 1
        and body_value is not None
        and len(headings) >= 2
    ):
        ratio_value = float(declared_ratio)
        body = body_value
        drifts = [
            abs(size - body * (ratio_value ** {"h3": 1, "h2": 2, "h1": 3}[role]))
            / (body * (ratio_value ** {"h3": 1, "h2": 2, "h1": 3}[role]))
            for role, size in headings
        ]
        worst = max(drifts)
        if worst <= _RATIO_TOLERANCE:
            return []
        return [
            _finding(
                "system.type.ratio-consistent",
                "P1",
                f"heading scale drifts {worst * 100:.1f}% from declared ratio "
                f"{ratio_value} (limit {_RATIO_TOLERANCE * 100:.0f}%)",
                declared_ratio=ratio_value,
                heading_drifts=[round(value, 3) for value in drifts],
            )
        ]

    sizes: list[float] = [
        float(entry["size_px"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("size_px"), (int, float))
    ]
    if len(sizes) < 3:
        return []
    ratios = [b / a for a, b in pairwise(sizes) if a > 0]
    if not ratios:
        return []
    mean = sum(ratios) / len(ratios)
    worst = max(abs(r - mean) / mean for r in ratios)
    if worst <= _RATIO_TOLERANCE:
        return []
    return [
        _finding(
            "system.type.ratio-consistent",
            "P1",
            f"type ratio inconsistent: max drift {worst * 100:.1f}% "
            f"(limit {_RATIO_TOLERANCE * 100:.0f}%)",
            ratios=[round(r, 3) for r in ratios],
            mean=round(mean, 3),
        )
    ]


def _pred_type_line_height_prose(system: dict, archetype: str | None = None) -> list[dict]:
    """Editorial archetypes (long-form reading) need >= 1.5 body line-height
    for prose comfort (Butterick, Bringhurst). Early-returns silently when
    `size_px` or `line_height` is absent on the body entry, since callers
    may emit partially-specified type scales during drafting.
    """
    if archetype != "editorial":
        return []
    for entry in system.get("type", {}).get("sizes", []):
        if entry.get("role") != "body":
            continue
        size = entry.get("size_px")
        lh = entry.get("line_height")
        if not (size and lh):
            return []
        if lh / size >= 1.5:
            return []
        return [
            _finding(
                "system.type.line-height-prose",
                "P1",
                f"editorial body line-height ratio is {lh / size:.2f}; "
                "editorial systems should be >= 1.5",
                line_height=lh,
                size_px=size,
            )
        ]
    return []


# -- Spacing / radii predicates ------------------------------------------


def _pred_spacing_grid(system: dict) -> list[dict]:
    """Every spacing value must be a multiple of a single grid base (4 or 8).
    Off-grid values defeat the predictability that makes a spacing system
    useful at all and create the "13px gap" rounding-error problem.
    """
    sp = system.get("spacing", {})
    grid = sp.get("grid_px")
    scale = sp.get("scale", []) or []
    if not (grid and scale):
        return []
    off = [x for x in scale if x % grid != 0]
    if not off:
        return []
    return [
        _finding(
            "system.spacing.single-grid",
            "P0",
            f"spacing values off the {grid}px grid: {off}",
            grid_px=grid,
            off_grid=off,
        )
    ]


def _pred_spacing_monotonic(system: dict) -> list[dict]:
    """The spacing scale must increase strictly. A non-monotonic scale forces
    callers to guess which step is "bigger" and breaks the assumption that
    `space.4` > `space.3` everywhere.
    """
    scale = system.get("spacing", {}).get("scale", []) or []
    if len(scale) < 2:
        return []
    for prev, curr in pairwise(scale):
        if curr <= prev:
            return [
                _finding(
                    "system.spacing.scale-monotonic",
                    "P0",
                    f"spacing scale not strictly increasing: {prev} -> {curr}",
                    scale=scale,
                )
            ]
    return []


def _pred_radii_monotonic(system: dict) -> list[dict]:
    """The radii scale must be non-decreasing (`sm <= md <= lg`). Unlike
    spacing, equal-radii pairs are tolerated (some systems have `none == 0`
    and a separate `0px` token used elsewhere) — we only flag a regression.
    """
    scale = system.get("radii", {}).get("scale", []) or []
    pxs = [r.get("px") for r in scale if "px" in r]
    if len(pxs) < 2:
        return []
    for prev, curr in pairwise(pxs):
        if curr < prev:
            return [
                _finding(
                    "system.radii.scale-monotonic",
                    "P0",
                    f"radii scale not monotonically increasing: {prev} -> {curr}",
                    scale=pxs,
                )
            ]
    return []


# -- Semantic-color predicates -------------------------------------------


def _pred_semantic_success_not_red(system: dict) -> list[dict]:
    """Semantic conventions (Nielsen, ISO 9241-110): "success" reads as
    green/teal, never red. A red success color collides with universal
    error conventions and breaks scanability for users.
    """
    spec = system.get("colors", {}).get("semantic", {}).get("success")
    if not spec or "hex" not in spec:
        return []
    h = _hue(spec["hex"])
    # Red range: [0, 30] or [330, 360].
    if not (0.0 <= h <= 30.0 or 330.0 <= h <= 360.0):
        return []
    return [
        _finding(
            "system.semantic.success-not-red",
            "P1",
            f"semantic.success has red-ish hue {h:.0f}deg; success conventions read as green/teal",
            hex=spec["hex"],
            hue_deg=round(h, 1),
        )
    ]


def _pred_semantic_error_not_green(system: dict) -> list[dict]:
    """The mirror of `success-not-red`: "error" reads as red/orange.
    A green error color collides with success conventions and is
    particularly hostile to red-green colorblind users.
    """
    spec = system.get("colors", {}).get("semantic", {}).get("error")
    if not spec or "hex" not in spec:
        return []
    h = _hue(spec["hex"])
    if not (90.0 <= h <= 150.0):
        return []
    return [
        _finding(
            "system.semantic.error-not-green",
            "P1",
            f"semantic.error has green-ish hue {h:.0f}deg; error conventions read as red/orange",
            hex=spec["hex"],
            hue_deg=round(h, 1),
        )
    ]


# -- Archetype predicates ------------------------------------------------


def _pred_archetype_primary_required(system: dict, archetype: str | None = None) -> list[dict]:
    """Brand-forward archetype (per `references/system-archetypes.md`) is
    defined by a saturated brand color. Without a `primary` role with
    chroma >= 0.05 in OKLCH, the archetype contract is unmet.
    """
    if archetype != "brand-forward":
        return []
    primary = _hex(system, "primary")
    if not primary:
        return [
            _finding(
                "system.archetype.primary-required",
                "P0",
                "brand-forward archetype requires a `primary` color role",
            )
        ]
    # Also require saturation: OKLCH C > 0.05.
    _, C, _ = hex_to_oklch(primary)
    if C < 0.05:
        return [
            _finding(
                "system.archetype.primary-required",
                "P0",
                f"brand-forward `primary` is too desaturated (chroma {C:.3f}; need >= 0.05)",
                primary=primary,
                chroma=round(C, 3),
            )
        ]
    return []


# -- Anti-imitation ------------------------------------------------------


def _hue_diff(a: float, b: float) -> float:
    """Smallest arc between two hues (degrees, 0–180)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _pred_distinctiveness(system: dict) -> list[dict]:
    """Flag a proposed system that's too close to one of the six reference
    systems on 3+ of {primary hue, type ratio, radii set, spacing grid}.

    The escape isn't "be different at all costs" — it's "if you're going to
    look like one of these, be honest about it and audit against it." The
    finding's message names the reference, so the agent can decide whether
    to regenerate or pivot to `--against <name>`.
    """
    primary_hex = _hex(system, "primary")
    if not primary_hex:
        return []  # no primary, nothing to compare
    try:
        primary_hue = _hue(primary_hex)
    except ValueError:
        return []

    type_sizes = [
        s.get("size_px") for s in (system.get("type") or {}).get("sizes", []) if "size_px" in s
    ]
    type_ratio = None
    if len(type_sizes) >= 3:
        ratios = [
            type_sizes[i + 1] / type_sizes[i]
            for i in range(len(type_sizes) - 1)
            if type_sizes[i] > 0
        ]
        if ratios:
            type_ratio = sum(ratios) / len(ratios)

    radii = tuple(
        sorted(
            {
                int(r.get("px", 0))
                for r in (system.get("radii") or {}).get("scale", []) or []
                if isinstance(r.get("px"), int)
            }
        )
    )
    grid = (system.get("spacing") or {}).get("grid_px")

    findings: list[dict] = []
    for ref_name, ref in _REFERENCE_FINGERPRINTS.items():
        matches: list[str] = []

        if _hue_diff(primary_hue, ref["hue_deg"]) <= _HUE_MATCH_DEG:
            matches.append(f"primary hue within {_HUE_MATCH_DEG:.0f}° of {ref_name}")

        if (
            type_ratio is not None
            and abs(type_ratio - ref["type_ratio"]) / ref["type_ratio"] <= _RATIO_MATCH
        ):
            matches.append(f"type ratio matches {ref_name} ({ref['type_ratio']:.3f})")

        ref_radii_set = set(ref["radii"])
        if radii:
            overlap = sum(1 for r in radii if r in ref_radii_set) / len(radii)
            if overlap >= _RADII_OVERLAP_PCT:
                matches.append(
                    f"radii set ≥{int(_RADII_OVERLAP_PCT * 100)}% overlap with {ref_name}"
                )

        if grid == ref["grid"]:
            matches.append(f"spacing grid matches {ref_name} ({ref['grid']}px)")

        if len(matches) >= 3:
            findings.append(
                _finding(
                    "system.distinctiveness.too-close-to-reference",
                    "P1",
                    f"proposed system matches {ref_name} on {len(matches)} dimensions: "
                    + "; ".join(matches)
                    + ". Either regenerate with a distinct hue/ratio/radii, or "
                    + f"adopt {ref_name} outright via `--against {ref_name}`.",
                    reference=ref_name,
                    matches=matches,
                    primary_hue_deg=round(primary_hue, 1),
                    proposed_type_ratio=round(type_ratio, 3) if type_ratio else None,
                    proposed_radii=list(radii),
                    proposed_grid_px=grid,
                )
            )
    return findings


# -- Predicate registry --------------------------------------------------
#
# Each entry is (predicate_id, callable). The callable returns a list of
# findings (may be empty). The list is the SINGLE source of truth for which
# predicates run. Predicates take EITHER `(system)` or `(system, archetype)`
# — the dispatch in `validate_system()` reads `co_argcount` to decide.


def _pred_required_sections(system: dict) -> list[dict]:
    """Fail closed when a token bundle omits a foundational system section."""
    missing: list[str] = []
    for key in ("name", "version"):
        if not isinstance(system.get(key), str) or not system.get(key):
            missing.append(key)

    type_ = system.get("type")
    if (
        not isinstance(type_, dict)
        or not isinstance(type_.get("sizes"), list)
        or not type_["sizes"]
    ):
        missing.append("type.sizes")
    elif any(
        not isinstance(item, dict)
        or not isinstance(item.get("role") or item.get("name"), str)
        or not isinstance(item.get("size_px"), (int, float))
        for item in type_["sizes"]
    ):
        missing.append("type.sizes[].role/size_px")
    spacing = system.get("spacing")
    if (
        not isinstance(spacing, dict)
        or not isinstance(spacing.get("grid_px"), (int, float))
        or spacing["grid_px"] <= 0
        or not isinstance(spacing.get("scale"), list)
        or not spacing["scale"]
    ):
        missing.append("spacing.grid_px/scale")
    radii = system.get("radii")
    if (
        not isinstance(radii, dict)
        or not isinstance(radii.get("scale"), list)
        or not radii["scale"]
    ):
        missing.append("radii.scale")
    elif any(
        not isinstance(item, dict)
        or not isinstance(item.get("role") or item.get("name"), str)
        or not isinstance(item.get("px"), (int, float))
        for item in radii["scale"]
    ):
        missing.append("radii.scale[].role/px")
    colors = system.get("colors")
    if (
        not isinstance(colors, dict)
        or not isinstance(colors.get("roles"), dict)
        or not colors["roles"]
    ):
        missing.append("colors.roles")
    fonts = system.get("fonts")
    if not isinstance(fonts, dict):
        missing.append("fonts")
    else:
        body = fonts.get("body") or fonts.get("default") or fonts.get("primary")
        if not isinstance(body, str) or not body:
            missing.append("fonts.body/default")
        if not isinstance(fonts.get("display"), str) or not fonts.get("display"):
            missing.append("fonts.display")
        if not isinstance(fonts.get("mono"), str) or not fonts.get("mono"):
            missing.append("fonts.mono")

    if not missing:
        return []
    return [
        _finding(
            "system.structure.required-sections",
            "P0",
            f"system is incomplete; missing required sections: {', '.join(missing)}",
            missing=missing,
        )
    ]


PREDICATES: list[tuple[str, Callable[..., list[dict]]]] = [
    ("system.structure.required-sections", _pred_required_sections),
    ("system.roles.required-present", _pred_required_present),
    ("system.roles.text-default-aa", _pred_text_default_aa),
    ("system.roles.text-subtle-aa", _pred_text_subtle_aa),
    ("system.roles.text-subtlest-large-only", _pred_text_subtlest_large),
    ("system.roles.text-disabled-not-aa", _pred_text_disabled_not_aa),
    ("system.roles.text-inverse-aa", _pred_text_inverse_aa),
    ("system.roles.border-focused-aa", _pred_border_focused_aa),
    ("system.roles.border-focused-inverse-aa", _pred_border_focused_inverse_aa),
    ("system.type.scale-monotonic", _pred_type_scale_monotonic),
    ("system.type.ratio-consistent", _pred_type_ratio_consistent),
    ("system.type.line-height-prose", _pred_type_line_height_prose),
    ("system.spacing.single-grid", _pred_spacing_grid),
    ("system.spacing.scale-monotonic", _pred_spacing_monotonic),
    ("system.radii.scale-monotonic", _pred_radii_monotonic),
    ("system.semantic.success-not-red", _pred_semantic_success_not_red),
    ("system.semantic.error-not-green", _pred_semantic_error_not_green),
    ("system.archetype.primary-required", _pred_archetype_primary_required),
    ("system.distinctiveness.too-close-to-reference", _pred_distinctiveness),
]


def validate_system(system: dict, archetype: str | None = None, strict: bool = False) -> list[dict]:
    """Run every registered predicate against the system; return findings.

    Args:
        system: parsed system JSON (a dict).
        archetype: optional archetype name. Predicates that take an archetype
            into account read this; predicates that don't ignore it.
        strict: if True, escalates P1 findings to P0 (used by the slash
            command to drive tighter convergence).

    Returns:
        Flat list of finding dicts. Empty list means the system passed.
    """
    findings: list[dict] = []
    for pred_id, fn in PREDICATES:
        try:
            if fn.__code__.co_argcount == 1:
                findings.extend(fn(system))
            else:
                findings.extend(fn(system, archetype))
        except Exception as e:
            # Validation is a release gate. A broken predicate must fail closed
            # rather than turn an unvalidated section into a silent pass.
            logger.exception("predicate %s raised %r", pred_id, e)
            findings.append(
                _finding(
                    f"{pred_id}.internal-error",
                    "P0",
                    f"validator predicate {pred_id} failed with {type(e).__name__}",
                    exception_type=type(e).__name__,
                )
            )
    if strict:
        for f in findings:
            if f.get("severity") == "P1":
                f["severity"] = "P0"
                f.setdefault("escalated_from", "P1")
    return findings


def emit_contrast_matrix(system: dict) -> dict:
    """Return a nested dict: text_role -> surface_role -> contrast ratio."""
    roles = system.get("colors", {}).get("roles", {}) or {}
    texts = {n: r.get("hex") for n, r in roles.items() if n.startswith("text.")}
    surfaces = {n: r.get("hex") for n, r in roles.items() if n.startswith("surface.")}
    out: dict[str, dict[str, float]] = {}
    for t_name, t_hex in texts.items():
        out[t_name] = {}
        for s_name, s_hex in surfaces.items():
            if not (t_hex and s_hex):
                continue
            ratio = contrast_ratio(t_hex, s_hex)
            out[t_name][s_name] = round(float(ratio), 2) if ratio is not None else 0.0
    return out
