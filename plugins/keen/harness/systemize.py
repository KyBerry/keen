"""Systemize stage: turn observed tokens into a coherent proposed design system.

The pipeline so far has been about *finding* drift. This stage proposes a
canonical scale that the product can adopt as its own system. The flow:

    capture -> tokens (observed) -> systemize (proposed)

Output:
    system/<name>.json     — machine-readable token bundle
    system/<name>.md       — markdown reference doc, same shape as references/design-systems/*.md
    system/<name>-preview.html — self-contained visual preview

What this module does deterministically (so the agent doesn't have to guess):
    - Cluster nearby font sizes / spacing / radii values into representative buckets
    - Detect the dominant grid (4 or 8 px) from observed spacing values
    - Snap proposed type scale to a clean modular ratio when one fits
    - Pick the most-used colors as candidates for system roles
    - Compute contrast pairs for the agent to pick from

What the agent does:
    - Names color roles (which gray is `text.default`, which blue is `primary`)
    - Picks a system archetype (utilitarian / dense / clarity-first / brand-forward)
    - Writes the markdown doc using the proposal as ground truth
    - Decides what to *cut* from the proposal — the goal is fewer values, not more
"""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from harness._sanitize import sanitize_untrusted_text
from harness.colors import parse_color, rel_luminance, to_hex

_SYSTEM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FONT_STACK_RE = re.compile(r"^[A-Za-z0-9 \"'_,.-]{1,200}$")
_DEFAULT_FONT_STACK = "system-ui, -apple-system, sans-serif"
_DEFAULT_MONO_STACK = 'ui-monospace, "SFMono-Regular", Menlo, monospace'
_SERIF_FAMILIES = {
    "baskerville",
    "book antiqua",
    "cambria",
    "constantia",
    "garamond",
    "georgia",
    "palatino",
    "times",
    "times new roman",
}
_MONO_FAMILIES = {
    "consolas",
    "courier",
    "courier new",
    "ibm plex mono",
    "menlo",
    "monaco",
    "sfmono-regular",
    "ui-monospace",
}
_ICON_FONT_HINTS = {
    "bootstrap icons",
    "font awesome",
    "material icons",
    "material symbols",
    "phosphor",
}


def validate_system_name(name: str) -> str:
    """Validate a system name before using it in text or artifact paths."""
    if not isinstance(name, str) or not _SYSTEM_NAME_RE.fullmatch(name):
        raise ValueError(
            "system name must be 1-64 letters, numbers, underscores, or hyphens "
            "and must start with a letter or number"
        )
    return name


def _safe_font_stack(value: Any) -> str:
    """Return a conservative CSS font-family value from observed page data."""
    if not isinstance(value, str):
        return _DEFAULT_FONT_STACK
    value = re.sub(r"\s+", " ", value).strip()
    if not _FONT_STACK_RE.fullmatch(value):
        return _DEFAULT_FONT_STACK
    return value


def _font_family_name(stack: str) -> str:
    """Return the human-readable first family in a CSS font stack."""
    return stack.split(",", 1)[0].strip().strip("\"'") or "System UI"


def _font_kind(stack: str) -> str:
    """Classify a stack coarsely enough to detect a meaningful pairing."""
    families = [part.strip().strip("\"'").lower() for part in stack.split(",")]
    if any(any(hint in family for hint in _ICON_FONT_HINTS) for family in families):
        return "icon"
    if "monospace" in families or any(family in _MONO_FAMILIES for family in families):
        return "mono"
    if "serif" in families and "sans-serif" not in families:
        return "serif"
    if any(family in _SERIF_FAMILIES for family in families):
        return "serif"
    return "sans"


def _propose_font_pairing(families: Any, text_samples: Any = None) -> dict[str, Any]:
    """Assign observed font stacks to display, body, and mono roles.

    The most-used non-code, non-icon stack is the body face. A second observed
    stack becomes the display face only when it creates a clear serif/sans
    contrast *and* captured heading-scale text proves that role. This preserves
    real typographic intent without turning a one-off serif or icon font into a
    fashionable but unsupported pairing.
    """
    observed: list[dict[str, Any]] = []
    if isinstance(families, list):
        for family in families:
            if not isinstance(family, dict):
                continue
            raw_stack = family.get("value")
            safe_stack = _safe_font_stack(raw_stack)
            if (
                not isinstance(raw_stack, str)
                or safe_stack != re.sub(r"\s+", " ", raw_stack).strip()
            ):
                continue
            try:
                count = max(0, int(family.get("count", 0)))
            except (TypeError, ValueError):
                count = 0
            observed.append(
                {
                    "stack": safe_stack,
                    "family": _font_family_name(safe_stack),
                    "kind": _font_kind(safe_stack),
                    "count": count,
                }
            )

    observed.sort(key=lambda item: item["count"], reverse=True)
    if not observed:
        return {
            "primary": _DEFAULT_FONT_STACK,
            "body": _DEFAULT_FONT_STACK,
            "display": _DEFAULT_FONT_STACK,
            "mono": _DEFAULT_MONO_STACK,
            "candidates": [],
            "pairing": {
                "mode": "system-fallback",
                "display_family": "System UI",
                "body_family": "System UI",
                "rationale": (
                    "No reliable font-family evidence was captured, so display and body "
                    "share a neutral system stack instead of inventing a brand pairing."
                ),
                "evidence": [],
            },
        }

    normalized_samples: list[dict[str, Any]] = []
    if isinstance(text_samples, list):
        for sample in text_samples:
            if not isinstance(sample, dict):
                continue
            text = sanitize_untrusted_text(sample.get("text"), max_len=120)
            raw_stack = sample.get("family")
            safe_stack = _safe_font_stack(raw_stack)
            if not text or not isinstance(raw_stack, str):
                continue
            if safe_stack != re.sub(r"\s+", " ", raw_stack).strip():
                continue
            try:
                size_px = float(sample.get("size_px", 0))
                weight = int(float(sample.get("weight", 400)))
                capture_count = max(1, int(sample.get("capture_count", 1)))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(size_px) or size_px <= 0:
                continue
            normalized_samples.append(
                {
                    "text": text,
                    "family": safe_stack,
                    "size_px": round(size_px, 2),
                    "weight": min(1000, max(1, weight)),
                    "tag": str(sample.get("tag") or "")[:12].lower(),
                    "capture_count": capture_count,
                    "source": "captured-ui",
                }
            )

    readable_families = [item for item in observed if item["kind"] not in {"mono", "icon"}]
    body = readable_families[0] if readable_families else observed[0]

    def has_display_evidence(stack: str) -> bool:
        return any(
            sample["family"] == stack
            and (str(sample["tag"]).startswith("h") or float(sample["size_px"]) >= 24)
            for sample in normalized_samples
        )

    display = next(
        (
            candidate
            for candidate in readable_families
            if candidate["stack"] != body["stack"]
            and candidate["kind"] != body["kind"]
            and has_display_evidence(str(candidate["stack"]))
        ),
        None,
    )
    if display is None:
        display = body
        mode = "single-family"
        rationale = (
            f"Captured evidence supports one reading family: {body['family']}. Use size, weight, "
            "and spacing for hierarchy unless another capture proves a separate display role."
        )
    else:
        mode = "observed-contrast"
        display_samples = [
            sample for sample in normalized_samples if sample["family"] == display["stack"]
        ]
        confidence = "high" if display["count"] >= 8 and len(display_samples) >= 2 else "medium"
        rationale = (
            f"Observed use separates {display['family']} for heading-scale text from "
            f"{body['family']} for reading and interface text "
            f"({display['count']} and {body['count']} captured elements respectively)."
        )

    specimens: list[dict[str, Any]] = []

    def choose_specimen(stack: str, *, display_role: bool) -> dict[str, Any] | None:
        matching = [sample for sample in normalized_samples if sample["family"] == stack]
        if not matching:
            return None
        if display_role:
            matching.sort(
                key=lambda sample: (
                    -int(str(sample["tag"]).startswith("h")),
                    -float(sample["size_px"]),
                    -int(sample["capture_count"]),
                    len(str(sample["text"])),
                )
            )
        else:
            matching.sort(
                key=lambda sample: (
                    -int(sample["tag"] in {"p", "td", "label", "button", "a"}),
                    -int(len(str(sample["text"])) >= 24),
                    abs(float(sample["size_px"]) - 16),
                    -int(sample["capture_count"]),
                    -len(str(sample["text"])),
                )
            )
        return matching[0]

    display_specimen = choose_specimen(display["stack"], display_role=True)
    body_specimen = choose_specimen(body["stack"], display_role=False)
    if display_specimen:
        specimens.append({"role": "display", **display_specimen})
    if body_specimen and (
        not display_specimen
        or body_specimen["text"] != display_specimen["text"]
        or body_specimen["family"] != display_specimen["family"]
    ):
        specimens.append({"role": "body", **body_specimen})

    return {
        # Keep primary as a compatibility alias for older consumers.
        "primary": body["stack"],
        "body": body["stack"],
        "display": display["stack"],
        "mono": _DEFAULT_MONO_STACK,
        "candidates": [item["stack"] for item in observed[:3]],
        "pairing": {
            "mode": mode,
            "confidence": confidence if mode == "observed-contrast" else "high",
            "display_family": display["family"],
            "body_family": body["family"],
            "rationale": rationale,
            "evidence": [dict(item) for item in observed[:3]],
            "specimens": specimens,
        },
    }


def _safe_number(value: Any, fallback: float, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return min(maximum, max(minimum, number))


def _safe_color(value: Any, fallback: str) -> str:
    normalized = to_hex(value) if isinstance(value, str) else None
    return normalized or fallback


# -- Clustering primitives ------------------------------------------------


def _cluster_1d(values: list[float], counts: list[int], gap: float) -> list[dict[str, Any]]:
    """Cluster values that are within `gap` of each other.

    Each cluster's representative is the count-weighted mean rounded to int
    (px values are integers in 99% of CSS — fractional values are usually
    sub-pixel artifacts and should snap up).
    """
    if not values:
        return []
    pairs = sorted(zip(values, counts, strict=True), key=lambda p: p[0])
    clusters: list[list[tuple[float, int]]] = [[pairs[0]]]
    for v, c in pairs[1:]:
        if v - clusters[-1][-1][0] <= gap:
            clusters[-1].append((v, c))
        else:
            clusters.append([(v, c)])
    out = []
    for cl in clusters:
        total = sum(c for _, c in cl)
        if total == 0:
            continue
        mean = sum(v * c for v, c in cl) / total
        out.append(
            {
                "value": round(mean),
                "count": total,
                "members": [{"value": v, "count": c} for v, c in cl],
            }
        )
    return sorted(out, key=lambda d: d["value"])


# -- Type scale ----------------------------------------------------------

# Standard modular ratios. We try to fit the proposed scale to one of these.
MODULAR_RATIOS = {
    "minor second": 1.067,
    "major second": 1.125,
    "minor third": 1.200,
    "major third": 1.250,
    "perfect fourth": 1.333,
    "augmented fourth": 1.414,
    "perfect fifth": 1.500,
}


def _propose_type_scale(observed: list[dict[str, Any]]) -> dict[str, Any]:
    """Propose a 5-7-step type scale from observed sizes.

    Strategy:
        1. Cluster within 1px (drops sub-pixel and 1-off noise).
        2. Find the most-used cluster — that's "body".
        3. Walk down/up to find the closest matches to a clean modular ratio
           around body, pruning to 6 sizes total by default.
    """
    raw = [(d["value"], d["count"]) for d in observed]
    if not raw:
        return {"sizes": [], "ratio": None, "body_px": None}
    values, counts = zip(*raw, strict=True)
    clusters = _cluster_1d(list(values), list(counts), gap=1.0)
    # Body = most-used cluster, but constrained to 12-18px (otherwise it's a heading masquerading)
    body_candidates = [c for c in clusters if 12 <= c["value"] <= 18]
    body = (
        max(body_candidates, key=lambda c: c["count"])
        if body_candidates
        else max(clusters, key=lambda c: c["count"])
    )
    body_px = body["value"]

    # Score each modular ratio by how well it fits the cluster centers
    cluster_vals = [c["value"] for c in clusters]
    best_ratio_name = next(iter(MODULAR_RATIOS))
    best_ratio_score = math.inf
    for name, r in MODULAR_RATIOS.items():
        # Build the ideal scale around body
        ideal = []
        for exponent in range(-3, 4):
            ideal.append(body_px * (r**exponent))
        # Score = sum of min distance from each observed cluster to nearest ideal step
        s = sum(min(abs(v - i) / v for i in ideal) for v in cluster_vals)
        if s < best_ratio_score:
            best_ratio_score = s
            best_ratio_name = name

    ratio = MODULAR_RATIOS[best_ratio_name]

    # Do not turn modular arithmetic into unreadably small UI copy. A proposed
    # system is allowed to improve on observed drift: body anchors below 14px
    # lift to 14px, caption stays >=12px, and body-sm remains between them.
    body_px = max(14, body_px)
    caption_px = max(12, min(body_px - 2, round(body_px / (ratio**2))))
    body_sm_floor = 14 if body_px >= 16 else 13 if body_px == 15 else 12
    body_sm_px = max(body_sm_floor, caption_px + 1, min(body_px - 1, round(body_px / ratio)))
    raw_scale = [
        caption_px,
        body_sm_px,
        body_px,
        round(body_px * ratio),
        round(body_px * (ratio**2)),
        round(body_px * (ratio**3)),
    ]
    names = ["caption", "body-sm", "body", "h3", "h2", "h1"]
    sizes = []
    for role, size_px in zip(names, raw_scale[: len(names)], strict=True):
        sizes.append(
            {
                "name": role,
                "role": role,
                "size_px": size_px,
                "line_height": _line_height_for(size_px, role),
                "weight": _weight_for(role),
            }
        )
    return {
        "sizes": sizes,
        "ratio_name": best_ratio_name,
        "ratio": round(ratio, 4),
        "body_px": body_px,
    }


def _line_height_for(size_px: int, role: str) -> int:
    if role.startswith("h"):
        return round(size_px * 1.2)
    if role == "caption":
        return round(size_px * 1.45)
    return round(size_px * 1.5)


def _weight_for(role: str) -> int:
    # 700 is broadly supported by static serif and sans families. Avoid
    # fractional weights that browsers synthesize for non-variable display faces.
    return 700 if role.startswith("h") else 400


# -- Spacing scale --------------------------------------------------------


def _propose_spacing(observed: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a 4 or 8 px base grid and emit a clean scale."""
    if not observed:
        return {"grid_px": 4, "scale": [4, 8, 12, 16, 24, 32, 48, 64]}
    # Tally how well each grid fits
    fit4 = sum(d["count"] for d in observed if d["value"] % 4 == 0)
    fit8 = sum(d["count"] for d in observed if d["value"] % 8 == 0)
    total = sum(d["count"] for d in observed)
    # Prefer 8 only if it fits noticeably better than 4 (fit4 always >= fit8 by definition)
    grid = 8 if (fit8 / total) >= 0.7 else 4
    # Build the canonical scale on that grid
    scale = [grid, grid * 2, grid * 3, grid * 4, grid * 6, grid * 8, grid * 12, grid * 16]
    # If grid is 4, common increments include 2px for hairline insets — only include it
    # if the data shows it.
    if grid == 4 and any(d["value"] == 2 for d in observed):
        scale = [2, *scale]
    return {
        "grid_px": grid,
        "scale": scale,
        "fit_pct_4": round(100 * fit4 / total, 1),
        "fit_pct_8": round(100 * fit8 / total, 1),
    }


# -- Radius scale ---------------------------------------------------------


def _propose_radii(observed: list[dict[str, Any]]) -> dict[str, Any]:
    raw = [(d["value"], d["count"]) for d in observed]
    if not raw:
        return {
            "scale": [
                {"name": "none", "role": "none", "px": 0},
                {"name": "sm", "role": "sm", "px": 4},
                {"name": "md", "role": "md", "px": 8},
                {"name": "lg", "role": "lg", "px": 12},
                {"name": "full", "role": "full", "px": 9999},
            ]
        }
    values, counts = zip(*raw, strict=True)
    clusters = _cluster_1d(list(values), list(counts), gap=1.0)
    # Strip clusters below 1px (effectively "no rounding")
    sharp = next((c for c in clusters if c["value"] == 0), None)
    rounded = [c for c in clusters if 1 <= c["value"] < 100]
    pill = [c for c in clusters if c["value"] >= 100]

    # Pick up to 3 representative non-zero radii by count, sorted by size
    rounded_sorted = sorted(rounded, key=lambda c: -c["count"])[:3]
    rounded_sorted = sorted(rounded_sorted, key=lambda c: c["value"])

    scale = []
    if sharp:
        scale.append({"name": "none", "role": "none", "px": 0})
    name_pool = ["sm", "md", "lg"]
    for c, n in zip(rounded_sorted, name_pool, strict=False):
        scale.append({"name": n, "role": n, "px": c["value"]})
    if pill:
        scale.append({"name": "full", "role": "full", "px": 9999})
    if not scale:
        scale = [
            {"name": "none", "role": "none", "px": 0},
            {"name": "sm", "role": "sm", "px": 4},
            {"name": "md", "role": "md", "px": 8},
        ]
    return {"scale": scale}


# -- Colors ---------------------------------------------------------------


def _hex_from_rgb_string(s: str) -> str | None:
    """Convert an opaque CSS color string to '#rrggbb'.

    Transparent and translucent values do not prove an opaque color role. They
    require a known backdrop before compositing, so exclude them instead of
    silently turning transparent black into a dominant ``#000000`` candidate.
    """
    parsed = parse_color(s)
    if not parsed or parsed[3] < 0.99:
        return None
    r, g, b = (round(parsed[i] * 255) for i in (0, 1, 2))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rel_lum(hex_color: str) -> float:
    """WCAG relative luminance for sorting / contrast."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return rel_luminance((rgb[0], rgb[1], rgb[2], 1.0))


def _contrast(a: str, b: str) -> float:
    la, lb = _rel_lum(a), _rel_lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _is_neutral(hex_color: str, tolerance: int = 8) -> bool:
    """Roughly: a color where r/g/b are within `tolerance` of each other is neutral."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return max(r, g, b) - min(r, g, b) <= tolerance


def _propose_colors(
    fg_observed: list[dict[str, Any]], bg_observed: list[dict[str, Any]]
) -> dict[str, Any]:
    """Bucket observed colors into neutrals (text/surface) and accents (brand/semantic)."""
    fg_raw = [(_hex_from_rgb_string(d["value"]), d["count"]) for d in fg_observed]
    bg_raw = [(_hex_from_rgb_string(d["value"]), d["count"]) for d in bg_observed]
    fg_hex: list[tuple[str, int]] = [(h, c) for h, c in fg_raw if h is not None]
    bg_hex: list[tuple[str, int]] = [(h, c) for h, c in bg_raw if h is not None]

    # Foreground neutrals remain useful evidence, but text-role inference below
    # considers every high-contrast foreground. Branded navy copy is not a
    # mathematical neutral and should not be displaced by a rare pure black.
    fg_neutrals = sorted(
        [(h, c) for h, c in fg_hex if _is_neutral(h)],
        key=lambda p: (_rel_lum(p[0]), -p[1]),
    )
    # Background neutrals = candidate surface colors. Sort by lightness * frequency.
    bg_neutrals = sorted(
        [(h, c) for h, c in bg_hex if _is_neutral(h)],
        key=lambda p: (-_rel_lum(p[0]), -p[1]),
    )
    # Accents = non-neutral colors anywhere, aggregated by value so one color
    # used in text and fills is not presented twice as separate candidates.
    accent_counts: dict[str, dict[str, int]] = {}
    for h, count in fg_hex:
        if not _is_neutral(h):
            accent_counts.setdefault(h, {"fg": 0, "bg": 0})["fg"] += count
    for h, count in bg_hex:
        if not _is_neutral(h):
            accent_counts.setdefault(h, {"fg": 0, "bg": 0})["bg"] += count
    accents = sorted(
        (
            (h, counts["fg"] + counts["bg"], counts["fg"], counts["bg"])
            for h, counts in accent_counts.items()
        ),
        key=lambda item: (-item[1], -item[3], item[0]),
    )

    # Propose canonical roles. The agent will refine, but Python provides candidates.
    proposal: dict[str, Any] = {
        "neutrals": {
            "candidates_text": [
                {"hex": h, "count": c, "luminance": round(_rel_lum(h), 4)}
                for h, c in fg_neutrals[:6]
            ],
            "candidates_surface": [
                {"hex": h, "count": c, "luminance": round(_rel_lum(h), 4)}
                for h, c in bg_neutrals[:6]
            ],
        },
        "accents": [
            {
                "hex": h,
                "count": total,
                "foreground_count": fg_count,
                "background_count": bg_count,
                "source": "fg+bg" if fg_count and bg_count else ("fg" if fg_count else "bg"),
            }
            for h, total, fg_count, bg_count in accents[:10]
        ],
    }

    # If we have a clear "darkest neutral" and "lightest neutral", offer suggested roles
    if fg_hex and bg_neutrals:
        readable_foregrounds = sorted(
            ((h, count) for h, count in fg_hex if _contrast(h, bg_neutrals[0][0]) >= 4.5),
            key=lambda item: (-item[1], _rel_lum(item[0])),
        )
        darkest = (
            readable_foregrounds[0][0]
            if readable_foregrounds
            else min(fg_hex, key=lambda item: _rel_lum(item[0]))[0]
        )
        lightest = bg_neutrals[0][0]
        proposal["suggested_roles"] = {
            "text.default": {
                "hex": darkest,
                "contrast_on_surface_default": round(_contrast(darkest, lightest), 2),
                "confidence": "high",
            },
            "surface.default": {"hex": lightest, "confidence": "high"},
        }
        # Find the next frequently observed readable foreground for subtle text.
        for h, count in readable_foregrounds[1:]:
            if h != darkest:
                proposal["suggested_roles"]["text.subtle"] = {
                    "hex": h,
                    "contrast_on_surface_default": round(_contrast(h, lightest), 2),
                    "confidence": "medium",
                    "evidence": {"foreground_count": count},
                }
                break
        # Primary action is inferred from an observed fill with >=3:1 boundary
        # contrast. Prefer a value that is also used as foreground/icon color;
        # this distinguishes a repeated brand action from pale status surfaces.
        primary_candidates = sorted(
            (
                (h, total, fg_count, bg_count)
                for h, total, fg_count, bg_count in accents
                if bg_count > 0 and _contrast(h, lightest) >= 3.0
            ),
            key=lambda item: (-int(item[2] > 0), -item[3], -item[1], item[0]),
        )
        if primary_candidates:
            primary_hex, _total, primary_fg_count, primary_bg_count = primary_candidates[0]
            proposal["suggested_roles"]["primary"] = {
                "hex": primary_hex,
                "contrast_on_surface_default": round(_contrast(primary_hex, lightest), 2),
                "confidence": "medium",
                "evidence": {
                    "foreground_count": primary_fg_count,
                    "background_count": primary_bg_count,
                    "method": "repeated high-contrast fill",
                },
            }

    return proposal


# -- Public API -----------------------------------------------------------


@dataclass
class SystemProposal:
    name: str
    version: str = "0.1.0"
    type: dict[str, Any] = field(default_factory=dict)
    spacing: dict[str, Any] = field(default_factory=dict)
    radii: dict[str, Any] = field(default_factory=dict)
    colors: dict[str, Any] = field(default_factory=dict)
    fonts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def propose_system(extracted_tokens: dict[str, Any], name: str = "custom") -> dict[str, Any]:
    """Propose a coherent system from the output of tokens.extract_from_captures()."""
    proposal = SystemProposal(name=validate_system_name(name))

    proposal.type = _propose_type_scale(extracted_tokens.get("type", {}).get("sizes_px", []))
    proposal.spacing = _propose_spacing(extracted_tokens.get("spacing", {}).get("values_px", []))
    proposal.radii = _propose_radii(extracted_tokens.get("shape", {}).get("border_radii_px", []))
    proposal.colors = _propose_colors(
        extracted_tokens.get("colors", {}).get("foreground", []),
        extracted_tokens.get("colors", {}).get("background", []),
    )
    if proposal.colors.get("suggested_roles"):
        # `roles` is the canonical validate-system key. Keep
        # `suggested_roles` to make the provisional nature explicit to humans.
        proposal.colors["roles"] = dict(proposal.colors["suggested_roles"])
    families = extracted_tokens.get("type", {}).get("families", [])
    text_samples = extracted_tokens.get("type", {}).get("text_samples", [])
    proposal.fonts = _propose_font_pairing(families, text_samples)

    diag = extracted_tokens.get("diagnostics", {})
    elements_examined = int(extracted_tokens.get("elements_examined") or 0)
    pairing = proposal.fonts.get("pairing") or {}
    role_count = len(proposal.colors.get("suggested_roles") or {})
    proposal.evidence = {
        "source": extracted_tokens.get("source", "unknown"),
        "elements_examined": elements_examined,
        "observed": {
            "font_families": len(families),
            "captured_text_samples": len(text_samples),
            "font_sizes": len(extracted_tokens.get("type", {}).get("sizes_px", [])),
            "spacing_values": len(extracted_tokens.get("spacing", {}).get("values_px", [])),
            "radius_values": len(extracted_tokens.get("shape", {}).get("border_radii_px", [])),
            "foreground_colors": len(extracted_tokens.get("colors", {}).get("foreground", [])),
            "background_colors": len(extracted_tokens.get("colors", {}).get("background", [])),
        },
    }
    proposal.decision = {
        "status": "provisional",
        "recommendation": "revise-before-adoption",
        "confidence": "medium" if elements_examined >= 50 else "low",
        "strengths": [
            {
                "decision": "Preserve the observed display/body contrast",
                "confidence": pairing.get("confidence", "medium"),
            },
            {
                "decision": f"Consolidate spacing on a {proposal.spacing['grid_px']}px grid",
                "confidence": "high"
                if diag.get("spacing_on_4px_grid_pct") is not None
                else "medium",
            },
        ],
        "risks": [
            "Color roles are inferred candidates until brand and semantic intent are confirmed.",
            "Generated type roles need real-font and product-density verification.",
            "Component states and dark surfaces are not certified by token extraction alone.",
        ],
        "next_actions": [
            "Confirm the primary action color and complete semantic/focus roles.",
            "Verify the proposed type roles in real product copy at mobile and desktop widths.",
            "Run strict validation and applied preview capture before adoption.",
        ],
        "suggested_role_count": role_count,
    }

    # Diagnostic notes for the agent
    fs_count = len(extracted_tokens.get("type", {}).get("sizes_px", []))
    sp_count = len(extracted_tokens.get("spacing", {}).get("values_px", []))
    proposed_fs = len(proposal.type.get("sizes", []))
    proposed_sp = len(proposal.spacing.get("scale", []))

    if fs_count > proposed_fs:
        proposal.notes.append(
            f"Observed {fs_count} distinct font sizes; proposed scale uses {proposed_fs}. "
            f"That's a target reduction of {fs_count - proposed_fs} sizes."
        )
    if sp_count > proposed_sp:
        proposal.notes.append(
            f"Observed {sp_count} distinct spacing values; proposed scale uses {proposed_sp}. "
            f"Most off-grid values can snap to {proposal.spacing['grid_px']}px multiples."
        )
    if diag.get("spacing_off_grid_pct", 0) > 20:
        proposal.notes.append(
            f"{diag['spacing_off_grid_pct']}% of observed spacing is off-grid. "
            f"This is the highest-leverage cleanup."
        )

    return asdict(proposal)


# -- Markdown reference doc -----------------------------------------------


def render_markdown(proposal: dict[str, Any]) -> str:
    """Render a concise, decision-led proposal for humans and agents."""
    name = validate_system_name(str(proposal["name"]))
    type_ = proposal["type"]
    sp = proposal["spacing"]
    rad = proposal["radii"]
    col = proposal["colors"]
    fonts = proposal["fonts"]
    raw_decision = proposal.get("decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    raw_evidence = proposal.get("evidence")
    evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
    raw_capture_scope = evidence.get("capture_scope")
    capture_scope = raw_capture_scope if isinstance(raw_capture_scope, dict) else {}

    out: list[str] = []
    out.append(f"# {name.title()} design system review\n")
    out.append(
        "> **Provisional proposal.** This document translates captured UI evidence into "
        "product roles. It is a decision aid, not a validated or registered review target.\n"
    )
    out.append("## Executive decision\n")
    out.append(f"- **Recommendation:** {decision.get('recommendation', 'revise-before-adoption')}")
    out.append(f"- **Confidence:** {decision.get('confidence', 'low')}")
    out.append(f"- **Status:** {decision.get('status', 'provisional')}")
    strengths = [item for item in (decision.get("strengths") or []) if isinstance(item, dict)]
    if strengths:
        out.append("- **Supported decisions:**")
        for item in strengths[:3]:
            out.append(
                f"  - {item.get('decision', 'Provisional decision')} "
                f"({item.get('confidence', 'unrated')} confidence)"
            )
    out.append("")
    out.append("## Evidence basis\n")
    out.append(f"- Source: `{evidence.get('source', 'unknown')}`")
    out.append(f"- Elements examined: **{evidence.get('elements_examined', 0)}**")
    if capture_scope:
        out.append(
            f"- Capture coverage: **{capture_scope.get('succeeded', 0)}/"
            f"{capture_scope.get('requested', 0)}** succeeded; "
            f"complete = **{str(bool(capture_scope.get('complete'))).lower()}**"
        )
    out.append(
        "- Limits: token extraction does not certify component states, dark surfaces, "
        "font availability, or product intent.\n"
    )
    out.append("## Tokens\n")

    # Spacing
    out.append("### Spacing")
    out.append(f"- Base grid: **{sp['grid_px']}px**.")
    if "fit_pct_4" in sp:
        out.append(f"- Observed grid fit: 4px {sp['fit_pct_4']}%, 8px {sp['fit_pct_8']}%.")
    out.append(f"- Scale: {', '.join(str(v) for v in sp['scale'])}.\n")

    # Type
    if type_.get("sizes"):
        out.append("### Type scale (px)")
        out.append("| Role | Size | Weight | Line height |")
        out.append("|------|------|--------|-------------|")
        for s in type_["sizes"]:
            out.append(f"| {s['name']} | {s['size_px']} | {s['weight']} | {s['line_height']} |")
        out.append("")
        if type_.get("ratio_name"):
            out.append(
                f"Modular ratio: **{type_['ratio']}** ({type_['ratio_name']}). "
                f"Body anchor: **{type_['body_px']}px**.\n"
            )
        pairing = fonts.get("pairing") or {}
        out.append("### Typography pairing")
        out.append(f"- Display: **{fonts.get('display', fonts['primary'])}**")
        out.append(f"- Body / interface: **{fonts.get('body', fonts['primary'])}**")
        out.append(f"- Code / data: **{fonts.get('mono', _DEFAULT_MONO_STACK)}**")
        if pairing.get("rationale"):
            out.append(f"- Rationale: {pairing['rationale']}")
        pairing_evidence = pairing.get("evidence") or []
        if pairing_evidence:
            observed = ", ".join(
                f"{item.get('family', 'Unknown')} ({item.get('count', 0)} uses)"
                for item in pairing_evidence
                if isinstance(item, dict)
            )
            out.append(f"- Captured evidence: {observed}.")
        out.append("")

    # Shape
    if rad.get("scale"):
        out.append("### Shape (corner radius)")
        for r in rad["scale"]:
            label = r["name"]
            px = r["px"] if r["px"] < 9999 else "9999 (capsule / circle)"
            out.append(f"- {label}: **{px}**")
        out.append("")

    # Colors
    out.append("## Color system\n")
    roles = col.get("suggested_roles") or {}
    if roles:
        out.append(
            "Suggested role mappings (refine these — the names are the load-bearing part):\n"
        )
        for role_name, info in roles.items():
            extra = ""
            if "contrast_on_surface_default" in info:
                extra = f" (contrast {info['contrast_on_surface_default']}:1 on surface.default)"
            out.append(f"- `{role_name}`: **{info['hex']}**{extra}")
        out.append("")

    if col.get("neutrals", {}).get("candidates_text"):
        out.append("Text neutrals observed (darkest first):")
        for n in col["neutrals"]["candidates_text"][:4]:
            out.append(f"- `{n['hex']}` (used {n['count']}x)")
        out.append("")

    if col.get("accents"):
        out.append("Accent candidates (most-used first):")
        for a in col["accents"][:6]:
            out.append(f"- `{a['hex']}` ({a['source']}, used {a['count']}x)")
        out.append("")

    # Notes
    if proposal.get("notes"):
        out.append("## Cleanup targets\n")
        for n in proposal["notes"]:
            out.append(f"- {n}")
        out.append("")

    # Closing
    out.append("## Adoption decision\n")
    if decision.get("risks"):
        out.append("### Unresolved risks")
        for risk in decision["risks"][:3]:
            out.append(f"- {risk}")
        out.append("")
    if decision.get("next_actions"):
        out.append("### Next actions")
        for index, action in enumerate(decision["next_actions"][:3], start=1):
            out.append(f"{index}. {action}")
        out.append("")

    out.append(f'## What "in {name}" means in a critique\n')
    out.append(
        f"A product on the {name} system commits to:\n"
        f"- {sp['grid_px']}px spacing grid\n"
        f"- {len(type_.get('sizes', []))}-step type scale at the values above\n"
        f"- {len(rad.get('scale', []))}-step radius scale\n"
        f"- Explicit display, body, and mono typography roles with verified font fallbacks\n"
        f"- A coherent neutral ramp with named roles, not raw hex per component\n"
        f"- Semantic, focus, disabled, and inverse states that pass strict validation\n\n"
        "Drift on any one is normal; drift on three or more means the system isn't actually adopted."
    )

    return "\n".join(out) + "\n"


# -- HTML preview ---------------------------------------------------------


def render_preview_html(proposal: dict[str, Any]) -> str:
    """Render a self-contained HTML preview page. No external assets, no JS."""
    # Rendering can also be called for an external system JSON whose display
    # name is not a filename. Escape and bound it; path-producing entry points
    # apply the stricter validate_system_name gate.
    name = html.escape(re.sub(r"\s+", " ", str(proposal["name"])).strip()[:128])
    type_ = proposal["type"]
    sp = proposal["spacing"]
    rad = proposal["radii"]
    col = proposal["colors"]
    fonts = proposal["fonts"]

    # Pull suggested roles. Candidate values stay inside evidence specimens;
    # the review document itself uses a fixed, accessible neutral chrome so a
    # broken proposal cannot make its own decision surface unreadable.
    roles = col.get("suggested_roles") or {}
    body_font_stack = _safe_font_stack(fonts.get("body", fonts.get("primary")))
    display_font_stack = _safe_font_stack(fonts.get("display", body_font_stack))
    mono_font_stack = _safe_font_stack(fonts.get("mono", _DEFAULT_MONO_STACK))
    pairing = fonts.get("pairing") if isinstance(fonts.get("pairing"), dict) else {}
    display_family = html.escape(
        str(pairing.get("display_family") or _font_family_name(display_font_stack))
    )
    body_family = html.escape(str(pairing.get("body_family") or _font_family_name(body_font_stack)))
    pairing_mode = html.escape(str(pairing.get("mode") or "single-family"))
    pairing_confidence = html.escape(str(pairing.get("confidence") or "unrated"))
    pairing_rationale = html.escape(
        str(
            pairing.get("rationale")
            or "Display and body roles share one family until the capture provides evidence for a pairing."
        )
    )
    raw_specimens = pairing.get("specimens")
    specimens = raw_specimens if isinstance(raw_specimens, list) else []

    def specimen_for(role: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in specimens
                if isinstance(item, dict) and item.get("role") == role and item.get("text")
            ),
            None,
        )

    display_specimen = specimen_for("display")
    body_specimen = specimen_for("body")
    display_sample_copy = html.escape(
        str(display_specimen.get("text"))
        if display_specimen
        else "Review the longest heading at the narrowest supported width"
    )
    body_sample_copy = html.escape(
        str(body_specimen.get("text"))
        if body_specimen
        else "Synthetic stress copy. Replace this sentence with captured product language."
    )

    def specimen_source(item: dict[str, Any] | None) -> str:
        if not item:
            return "Synthetic stress copy"
        tag = html.escape(str(item.get("tag") or "text")[:12])
        try:
            count = max(1, min(9999, int(item.get("capture_count") or 1)))
        except (TypeError, ValueError):
            count = 1
        noun = "occurrence" if count == 1 else "occurrences"
        return f"Captured {tag} · {count} rendered {noun}"

    display_sample_source = specimen_source(display_specimen)
    body_sample_source = specimen_source(body_specimen)
    grid_px = _safe_number(sp.get("grid_px"), 4, minimum=1, maximum=64)
    # Type scale rows
    type_rows = []
    for s in type_.get("sizes", []):
        size_px = _safe_number(s.get("size_px"), 16, minimum=6, maximum=256)
        line_height = _safe_number(s.get("line_height"), 24, minimum=6, maximum=384)
        weight = _safe_number(s.get("weight"), 400, minimum=1, maximum=1000)
        type_name = html.escape(str(s.get("name", "unnamed")))
        sample_role = "display" if type_name.lower().startswith("h") else "body"
        sample_copy = display_sample_copy if sample_role == "display" else body_sample_copy
        family_label = display_family if sample_role == "display" else body_family
        source_label = display_sample_source if sample_role == "display" else body_sample_source
        type_rows.append(
            f"<tr>"
            f"<td><code>{type_name}</code></td>"
            f"<td>{family_label}</td>"
            f'<td><span class="type-sample type-sample--{sample_role}" style="--sample-size:{size_px}px; '
            f'--sample-line:{line_height}px; --sample-weight:{weight}; --sample-color:#172033">'
            f"{sample_copy}</span><small>{source_label}</small></td>"
            f"<td><code>{size_px}px / {weight} / {line_height}px</code></td>"
            f"</tr>"
        )

    # Spacing visualization
    spacing_bars = []
    for v in sp["scale"]:
        spacing_px = _safe_number(v, 0, minimum=0, maximum=512)
        spacing_bars.append(
            '<div class="spacing-row">'
            f"<code>{spacing_px}px</code>"
            f'<div class="spacing-bar" style="--space:{spacing_px}px"></div>'
            f"</div>"
        )

    # Radii
    radii_samples = []
    for r in rad.get("scale", []):
        radius_px = _safe_number(r.get("px"), 0, minimum=0, maximum=9999)
        radius_css = "9999px" if radius_px >= 9999 else f"{radius_px}px"
        radius_name = html.escape(str(r.get("name", "unnamed")))
        component_use = {
            "none": "data rule",
            "sm": "input",
            "md": "card",
            "lg": "panel",
            "full": "status",
        }.get(radius_name, "surface")
        radii_samples.append(
            '<div class="radius-sample">'
            f'<div class="radius-shape" style="border-radius:{radius_css}"></div>'
            f"<strong>{radius_name}</strong>"
            f'<span class="muted">{component_use} / '
            f"{radius_px if radius_px < 9999 else 'full'}</span>"
            f"</div>"
        )

    # Colors
    role_swatches = []
    for role, info in roles.items():
        hex_ = _safe_color(info.get("hex"), "#808080")
        contrast = info.get("contrast_on_surface_default")
        contrast_str = html.escape(f"{contrast}:1 on surface") if contrast else ""
        role_label = html.escape(str(role))
        role_purpose = {
            "text.default": "Primary reading and data",
            "text.subtle": "Supporting context",
            "surface.default": "Canvas and panels",
            "primary": "Action candidate — confirm before adoption",
        }.get(str(role), "Provisional role mapping")
        confidence = html.escape(str(info.get("confidence") or "unrated"))
        role_swatches.append(
            "<tr>"
            f"<td><strong>{role_label}</strong><small>{html.escape(role_purpose)}</small></td>"
            f'<td><span class="color-value"><span class="color-chip" '
            f'style="--swatch-color:{hex_}" aria-hidden="true"></span>'
            f"<code>{hex_}</code></span></td>"
            f'<td><span class="role-evidence">{contrast_str or "Not computed"}'
            f"<small>{confidence} confidence</small></span></td>"
            "</tr>"
        )

    accent_swatches = []
    for a in col.get("accents", [])[:8]:
        accent = _safe_color(a.get("hex"), "#808080")
        count = html.escape(str(a.get("count", 0)))
        accent_swatches.append(
            "<tr>"
            f'<td><span class="color-value"><span class="accent-chip" '
            f'style="--swatch-color:{accent}" aria-hidden="true"></span>'
            f"<code>{accent}</code></span></td>"
            f"<td>{count} uses</td>"
            "</tr>"
        )

    raw_evidence = proposal.get("evidence")
    evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
    raw_decision = proposal.get("decision")
    decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
    is_reference = proposal.get("archetype") == "bundled-reference"
    raw_capture_scope = evidence.get("capture_scope")
    capture_scope = raw_capture_scope if isinstance(raw_capture_scope, dict) else {}
    capture_succeeded = int(capture_scope.get("succeeded") or 0)
    capture_requested = int(capture_scope.get("requested") or 0)
    elements_examined = int(evidence.get("elements_examined") or 0)
    scope_display = (
        "Bundled"
        if is_reference
        else f"{capture_succeeded}/{capture_requested}"
        if capture_requested
        else "Not supplied"
    )
    elements_display = "Reference" if is_reference else str(elements_examined or "Not supplied")
    scope_label = "Source" if is_reference else "Capture scope"
    elements_label = "Status" if is_reference else "Elements read"
    recommendation_value = str(
        decision.get("recommendation")
        or ("comparison-reference" if is_reference else "review-before-adoption")
    )
    recommendation = html.escape(recommendation_value)
    recommendation_label = html.escape(
        {
            "revise-before-adoption": "Revise before adoption",
            "insufficient-evidence": "Collect more evidence",
            "ready-for-validation": "Ready for validation",
            "comparison-reference": "Use as a comparison reference",
            "review-before-adoption": "Review before adoption",
        }.get(recommendation_value, "Review before adoption")
    )
    decision_confidence = html.escape(
        str(decision.get("confidence") or ("established" if is_reference else "unrated"))
    )
    cover_status = (
        "Bundled reference"
        if is_reference
        else f"Provisional / {recommendation}"
        if decision
        else f"Project system / {recommendation}"
    )
    cover_lede = (
        "A compact specimen of the bundled reference tokens, rendered for visual comparison."
        if is_reference
        else "An observed-token proposal organized for review. Captured evidence, proposed roles, "
        "and unresolved decisions stay separate."
        if decision
        else "A project-owned system organized into roles and adoption work for review."
    )
    if is_reference:
        verdict_body = (
            "Use this specimen to compare hierarchy, rhythm, and component feel; product-specific "
            "adoption still requires local validation."
        )
    elif recommendation_value == "insufficient-evidence":
        verdict_body = (
            "Capture coverage is incomplete. Collect the missing viewport and state evidence "
            "before treating any proposed role as an adoption decision."
        )
    elif recommendation_value == "ready-for-validation":
        verdict_body = (
            "The proposal has enough evidence for strict validation and applied component proof. "
            "It is not a registered review target yet."
        )
    elif decision:
        verdict_body = (
            "The observed hierarchy is coherent enough to prototype, but semantic color, "
            "component-state coverage, and responsive proof must be resolved before adoption."
        )
    else:
        verdict_body = (
            "Review semantic color, component-state coverage, font availability, and responsive "
            "proof before adoption."
        )
    strengths = [item for item in (decision.get("strengths") or []) if isinstance(item, dict)]
    strength_items = "".join(
        "<li><strong>"
        + html.escape(str(item.get("decision") or "Provisional decision"))
        + "</strong><span>"
        + html.escape(str(item.get("confidence") or "unrated"))
        + " confidence</span></li>"
        for item in strengths[:3]
    )
    if is_reference and not strength_items:
        strength_items = (
            "<li><strong>Canonical token vocabulary</strong><span>bundled reference</span></li>"
            "<li><strong>Comparison-ready specimen</strong><span>visual proof</span></li>"
        )
    risk_items = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in (decision.get("risks") or [])[:3]
    )
    next_action_items = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in (decision.get("next_actions") or [])[:3]
    )
    cleanup_items = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in (proposal.get("notes") or [])[:4]
    )
    raw_name = str(proposal.get("name") or "")
    json_href = "data:application/json;charset=utf-8," + quote(
        json.dumps(proposal, ensure_ascii=False, separators=(",", ":")), safe=""
    )
    download_name = (
        f"{raw_name}.json" if _SYSTEM_NAME_RE.fullmatch(raw_name) else "design-system.json"
    )
    json_download = f' download="{download_name}"'
    verdict_intro = (
        "This bundled specimen is a visual comparison aid, not a claim that the reference "
        "fits every product context."
        if is_reference
        else "This is a decision aid, not a certification. The proposal is derived from "
        "available evidence and still needs product ownership."
    )
    nav_note = (
        "Bundled comparison specimen. Validate product-specific decisions in the consuming project."
        if is_reference
        else "Neutral review chrome. Proposed values appear in the specimens; complete and "
        "validate the roles before using this system as a review target."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{name} — Design system review</title>
<style>
  :root {{
    color-scheme: light;
    --text: #172033;
    --text-subtle: #5c6678;
    --surface: #ffffff;
    --primary: #1b5fcc;
    --line: color-mix(in srgb, var(--text) 20%, var(--surface));
    --panel: color-mix(in srgb, var(--text) 3%, var(--surface));
    --input: color-mix(in srgb, var(--text) 9%, var(--surface));
    --font-display: {display_font_stack};
    --font-body: {body_font_stack};
    --mono: {mono_font_stack};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: var(--font-body);
    font-size: 15px;
    line-height: 1.55;
    color: var(--text);
    background: var(--surface);
  }}
  a {{ color: inherit; }}
  .skip-link {{ position: fixed; top: 8px; left: 8px; z-index: 10; padding: 8px 12px; background: var(--surface); transform: translateY(-160%); }}
  .skip-link:focus {{ transform: translateY(0); }}
  button, input {{ font: inherit; }}
  a:focus-visible, button:focus-visible, input:focus-visible {{
    outline: 3px solid var(--primary);
    outline-offset: 3px;
  }}
  code {{ font-family: var(--mono); font-size: .82em; }}
  h1, h2, h3 {{ font-family: var(--font-body); }}
  .type-sample--display, .pairing-display .sample {{ font-family: var(--font-display); }}
  .masthead {{
    color: var(--text);
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }}
  .masthead-inner {{
    width: min(1180px, calc(100% - 40px));
    margin: 0 auto;
    padding: 28px 0 24px;
  }}
  .cover-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(420px, .9fr); gap: 48px; align-items: end; }}
  .document-kind {{ margin: 0 0 8px; color: var(--text-subtle); font-size: 13px; }}
  .cover-facts {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--line); }}
  .cover-facts div {{ min-width: 0; padding: 12px 12px 0 0; }}
  .cover-facts dt {{ color: var(--text-subtle); font-size: 11px; }}
  .cover-facts dd {{ margin: 4px 0 0; color: var(--text); font-size: 14px; font-weight: 650; overflow-wrap: anywhere; }}
  h1 {{
    margin: 0;
    font-size: clamp(26px, 4vw, 36px);
    font-weight: 720;
    letter-spacing: -.025em;
    line-height: 1.2;
    overflow-wrap: anywhere;
  }}
  .masthead .lede {{ max-width: 68ch; margin: 8px 0 0; color: var(--text-subtle); font-size: 14px; }}
  .document-shell {{ display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: clamp(28px, 5vw, 72px); width: min(1180px, calc(100% - 40px)); margin: 0 auto; }}
  .doc-nav {{ position: sticky; top: 0; align-self: start; padding: 38px 0; }}
  .doc-nav-label {{ margin: 0 0 10px; color: var(--text-subtle); font-size: 12px; font-weight: 650; }}
  .doc-nav nav {{ display: grid; }}
  .doc-nav a {{ padding: 8px 0; color: var(--text-subtle); text-decoration: none; border-bottom: 1px solid var(--line); font-size: 13px; }}
  .doc-nav a:hover, .doc-nav a:focus-visible {{ color: var(--text); }}
  .doc-nav-note {{ margin: 24px 0 0; color: var(--text-subtle); font-size: 12px; }}
  main {{ min-width: 0; padding: 38px 0 80px; }}
  section {{ scroll-margin-top: 20px; margin: 0 0 46px; }}
  .section-head {{
    display: grid;
    grid-template-columns: minmax(150px, 220px) minmax(0, 1fr);
    gap: 24px;
    align-items: start;
    padding-top: 14px;
    border-top: 1px solid var(--text);
    margin-bottom: 18px;
  }}
  .section-copy {{ max-width: 72ch; margin: 0; color: var(--text-subtle); font-size: 13px; }}
  h2 {{ margin: 0; font-size: 18px; letter-spacing: -.01em; line-height: 1.3; }}
  h3 {{ margin: 24px 0 8px; font-size: 14px; }}
  .muted {{ color: var(--text-subtle); font-size: 13px; }}
  .verdict-grid {{ display: grid; grid-template-columns: minmax(220px, .65fr) minmax(0, 1.35fr); gap: 32px; }}
  .verdict-main {{ padding: 16px; background: var(--panel); border-left: 3px solid var(--text); }}
  .verdict-kicker {{ margin: 0; color: var(--text-subtle); font-size: 12px; }}
  .verdict-title {{ margin: 5px 0 0; font-size: 20px; line-height: 1.3; }}
  .verdict-main > p:last-child {{ max-width: 54ch; margin: 12px 0 0; color: var(--text-subtle); font-size: 13px; }}
  .verdict-side {{ padding: 0; }}
  .decision-list {{ margin: 0; padding: 0; list-style: none; }}
  .decision-list li {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; padding: 10px 0; border-bottom: 1px solid var(--line); }}
  .decision-list li:first-child {{ padding-top: 0; }}
  .decision-list strong {{ font-size: 13px; }}
  .decision-list span {{ color: var(--text-subtle); font-size: 11px; white-space: nowrap; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ padding: 9px 10px; color: var(--text-subtle); border-bottom: 1px solid var(--text); font-size: 11px; font-weight: 650; text-align: left; }}
  td {{ padding: 13px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
  td:first-child {{ width: 110px; color: var(--text-subtle); }}
  td:last-child {{ color: var(--text-subtle); font-size: 11px; }}
  td small {{ display: block; margin-top: 4px; color: var(--text-subtle); font-size: 10px; }}
  .type-sample {{
    display: block;
    color: var(--sample-color);
    font-size: var(--sample-size);
    font-weight: var(--sample-weight);
    line-height: var(--sample-line);
  }}
  .pairing-card {{ display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(260px, .8fr); gap: 32px; margin-bottom: 24px; }}
  .pairing-display {{ padding: 18px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
  .pairing-display .sample {{
    max-width: 28ch;
    margin: 12px 0 0;
    font-size: clamp(28px, 4vw, 44px);
    line-height: 1.2;
    letter-spacing: -.02em;
  }}
  .pairing-body {{ padding: 18px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
  .pairing-label {{ margin: 0; color: var(--text-subtle); font-size: 11px; font-weight: 650; }}
  .pairing-name {{ margin: 8px 0 0; font-size: 14px; font-weight: 700; }}
  .pairing-copy {{ max-width: 52ch; margin: 10px 0 0; color: var(--text-subtle); font-size: 13px; }}
  .pairing-meta {{ margin: 12px 0 0; color: var(--text-subtle); font-size: 11px; }}
  .two-col {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: clamp(28px, 6vw, 72px); }}
  .spacing-row {{ display: grid; grid-template-columns: 62px 1fr; align-items: center; min-height: 34px; }}
  .spacing-bar {{ width: min(calc(var(--space) * 3), 100%); height: 8px; background: var(--text); }}
  .sample-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 14px; }}
  .radius-sample, .accent-sample {{ display: flex; flex-direction: column; align-items: flex-start; gap: 7px; }}
  .radius-shape {{ width: 84px; height: 48px; background: var(--panel); border: 1px solid var(--text); }}
  .color-chip, .accent-chip {{ display: block; width: 34px; height: 24px; background: var(--swatch-color); border: 1px solid var(--line); }}
  .color-value {{ display: flex; align-items: center; gap: 9px; min-width: 116px; }}
  .role-evidence {{ display: block; min-width: 112px; }}
  .export-link {{ color: var(--text); font-weight: 700; text-underline-offset: 4px; }}
  .adoption-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 48px; }}
  .adoption-panel {{ padding: 0; }}
  .adoption-panel + .adoption-panel {{ border-left: 0; background: transparent; }}
  .adoption-panel h3 {{ margin-top: 0; }}
  .adoption-panel ol, .adoption-panel ul {{ margin: 16px 0 0; padding-left: 20px; }}
  .adoption-panel li {{ margin: 0 0 12px; color: var(--text-subtle); }}
  .evidence-foot {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--line); }}
  .evidence-foot div {{ padding: 0; }}
  .evidence-foot strong {{ display: block; font-size: 15px; }}
  .evidence-foot span {{ color: var(--text-subtle); font-size: 11px; }}
  @media (max-width: 760px) {{
    .cover-grid, .verdict-grid, .two-col, .pairing-card, .adoption-grid {{ grid-template-columns: 1fr; }}
    .masthead-inner, .document-shell {{ width: min(100% - 32px, 1180px); }}
    .document-shell {{ display: block; }}
    .doc-nav {{ position: static; margin: 0 -16px; padding: 0 16px; border-bottom: 1px solid var(--line); }}
    .doc-nav-label, .doc-nav-note {{ display: none; }}
    .doc-nav nav {{ display: flex; flex-wrap: wrap; gap: 0 18px; }}
    .doc-nav a {{ min-width: max-content; padding: 10px 0; border: 0; }}
    main {{ padding-top: 30px; }}
    section {{ margin-bottom: 42px; }}
    .section-head {{ grid-template-columns: 1fr; gap: 6px; }}
    .type-table, .type-table tbody {{ display: block; width: 100%; }}
    .type-table thead {{ display: none; }}
    .type-table tr {{
      display: grid;
      grid-template-columns: 74px minmax(0, 1fr);
      column-gap: 18px;
      padding: 15px 0;
      border-bottom: 1px solid var(--line);
    }}
    .type-table td {{ width: auto; padding: 0; border: 0; }}
    .type-table td:first-child {{ grid-row: 1 / span 3; width: auto; padding-top: 4px; }}
    .type-table td:nth-child(n+2) {{ grid-column: 2; margin-top: 5px; }}
    .type-table td:last-child {{ white-space: normal; }}
    .type-sample {{ font-size: min(var(--sample-size), 32px); line-height: 1.2; }}
    .role-table, .role-table tbody {{ display: block; width: 100%; }}
    .role-table thead {{ display: none; }}
    .role-table tr {{
      display: grid;
      grid-template-columns: minmax(112px, .9fr) minmax(0, 1.1fr);
      gap: 5px 10px;
      padding: 11px 0;
      border-bottom: 1px solid var(--line);
    }}
    .role-table td {{ width: auto; padding: 0; border: 0; }}
    .role-table td:first-child {{ grid-row: 1 / span 2; }}
    .role-table td:nth-child(n+2) {{ grid-column: 2; }}
    .role-table td:nth-child(3) {{ font-size: 11px; }}
    .adoption-panel + .adoption-panel {{ padding-top: 20px; border-top: 1px solid var(--line); }}
    .evidence-foot {{ grid-template-columns: 1fr; }}
  }}
  @media print {{
    .doc-nav {{ display: none; }}
    .masthead-inner, .document-shell {{ width: 100%; }}
    .document-shell {{ display: block; }}
    main {{ padding: 24px 0 0; }}
    section, tr, .pairing-card, .radius-sample {{ break-inside: avoid; }}
    .export-link {{ color: inherit; text-decoration: none; }}
  }}
</style>
</head>
<body>
  <a class="skip-link" href="#verdict">Skip to decision</a>
  <header class="masthead">
    <div class="masthead-inner">
      <div class="cover-grid">
        <div>
          <p class="document-kind">Design system review · {cover_status}</p>
          <h1>{name}</h1>
          <p class="lede">{cover_lede}</p>
        </div>
        <dl class="cover-facts">
          <div><dt>{scope_label}</dt><dd>{scope_display}</dd></div>
          <div><dt>{elements_label}</dt><dd>{elements_display}</dd></div>
          <div><dt>Confidence</dt><dd>{decision_confidence}</dd></div>
          <div><dt>Proposed roles</dt><dd>{len(roles)}</dd></div>
        </dl>
      </div>
    </div>
  </header>
  <div class="document-shell">
    <aside class="doc-nav" aria-label="Review contents">
      <p class="doc-nav-label">Contents</p>
      <nav>
        <a href="#verdict">Decision</a>
        <a href="#typography">Typography</a>
        <a href="#color">Color roles</a>
        <a href="#rhythm">Spacing and shape</a>
        <a href="#adoption">Adoption work</a>
      </nav>
      <p class="doc-nav-note">{nav_note}</p>
    </aside>
    <main id="main-content">
    <section id="verdict">
      <header class="section-head"><h2>Decision</h2><p class="section-copy">{verdict_intro}</p></header>
      <div class="verdict-grid">
        <div class="verdict-main">
          <p class="verdict-kicker">{decision_confidence} confidence / {recommendation}</p>
          <p class="verdict-title">{recommendation_label}.</p>
          <p>{verdict_body}</p>
        </div>
        <div class="verdict-side">
          <h3>Supported by the capture</h3>
          <ul class="decision-list">{strength_items or "<li><strong>No stable decision yet</strong><span>collect more evidence</span></li>"}</ul>
        </div>
      </div>
    </section>
    <section id="typography">
      <header class="section-head"><h2>Typography</h2><p class="section-copy">Observed family use, proposed roles, and the strings used to test them. This document loads no remote font files.</p></header>
      <div class="pairing-card">
        <div class="pairing-display">
          <p class="pairing-label">Display role · {display_family}</p>
          <p class="sample">{display_sample_copy}</p>
          <p class="pairing-meta">{display_sample_source}</p>
        </div>
        <div class="pairing-body">
          <p class="pairing-label">Body and interface role · {body_family}</p>
          <p class="pairing-name">{body_sample_copy}</p>
          <p class="pairing-copy">{pairing_rationale}</p>
          <p class="pairing-meta">{body_sample_source}. Pairing mode: {pairing_mode}; confidence: {pairing_confidence}. Font files are not embedded.</p>
        </div>
      </div>
      <div class="table-wrap"><table class="type-table"><thead><tr><th>Role</th><th>Family</th><th>Test string</th><th>Size / weight / line</th></tr></thead><tbody>{"".join(type_rows) or "<tr><td colspan=4 class=muted>No sizes proposed.</td></tr>"}</tbody></table></div>
    </section>
    <section id="color">
      <header class="section-head"><h2>Color roles</h2><p class="section-copy">Provisional mappings from observed values. Semantic, destructive, focus, and disabled roles still require product review.</p></header>
      <div class="two-col">
        <div class="table-wrap"><table class="role-table"><thead><tr><th>Role</th><th>Value</th><th>Evidence</th></tr></thead><tbody>{"".join(role_swatches) or "<tr><td colspan=3>No role mappings proposed.</td></tr>"}</tbody></table></div>
        <div>
          <h3>Observed accents</h3>
          <div class="table-wrap"><table class="accent-table"><thead><tr><th>Value</th><th>Frequency</th></tr></thead><tbody>{"".join(accent_swatches) or "<tr><td colspan=2>No accents observed.</td></tr>"}</tbody></table></div>
        </div>
      </div>
    </section>
    <section id="rhythm">
      <header class="section-head"><h2>Spacing and shape</h2><p class="section-copy">Reduced working scales. Verify them against dense tables, forms, navigation, and narrow layouts before adoption.</p></header>
      <div class="two-col">
        <div><h3>Spacing scale</h3><p class="muted">Proposed base grid: <strong>{grid_px}px</strong>. The bars show relative rhythm, not usage frequency.</p><div>{"".join(spacing_bars)}</div></div>
        <div><h3>Corner roles</h3><p class="muted">Each radius is paired with a component use so the values do not become decorative choices.</p><div class="sample-grid">{"".join(radii_samples)}</div></div>
      </div>
    </section>
    <section id="adoption">
      <header class="section-head"><h2>Adoption work</h2><p class="section-copy">Resolve these gaps before registering the proposal as a design-system target. The JSON remains the editable source.</p></header>
      <div class="adoption-grid">
        <div class="adoption-panel"><h3>Next actions</h3><ol>{next_action_items or "<li>Review the proposed roles with a product owner.</li><li>Add semantic and interaction-state tokens.</li><li>Validate and recapture mobile and desktop proofs.</li>"}</ol><a class="export-link" href="{json_href}"{json_download}>Download proposed tokens</a></div>
        <div class="adoption-panel"><h3>Unresolved risks</h3><ul>{risk_items or "<li>No explicit risks were recorded; perform a manual role review.</li>"}</ul><h3>Cleanup targets</h3><ul>{cleanup_items or "<li>No cleanup targets were emitted.</li>"}</ul></div>
      </div>
      <div class="evidence-foot">
        <div><strong>{scope_display}</strong><span>{scope_label}</span></div>
        <div><strong>{elements_display}</strong><span>{elements_label}</span></div>
        <div><strong>{len(type_.get("sizes", []))}</strong><span>type roles proposed</span></div>
      </div>
    </section>
    </main>
  </div>
</body>
</html>
"""


# -- Orchestration --------------------------------------------------------


def systemize_run(run_dir: Path, name: str = "custom") -> dict[str, Any]:
    """Read the tokens artifacts from a previous capture+tokens run, propose a system,
    and write the three deliverables under <run_dir>/system/."""
    run_dir = Path(run_dir)
    name = validate_system_name(name)
    tokens_path = run_dir / "tokens" / "extracted.json"
    legacy_path = run_dir / "tokens.json"

    if tokens_path.exists():
        extracted = json.loads(tokens_path.read_text())
    elif legacy_path.exists():
        extracted = json.loads(legacy_path.read_text())
    else:
        # Fall back to extracting from DOM dumps in this run.
        from . import tokens as tokens_module

        if not (run_dir / "dom").exists() and not list(run_dir.glob("**/dom-*.json")):
            raise FileNotFoundError(
                f"No tokens or DOM dumps found under {run_dir}. "
                f"Run `keen capture` and `keen tokens` first."
            )
        extracted = tokens_module.extract_from_captures(run_dir)
        # Cache for next time
        tokens_path.parent.mkdir(parents=True, exist_ok=True)
        tokens_path.write_text(json.dumps(extracted, indent=2))

    proposal = propose_system(extracted, name=name)

    manifest_path = run_dir / "capture-manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            requested = manifest.get("requested") if isinstance(manifest, dict) else []
            succeeded = manifest.get("succeeded") if isinstance(manifest, dict) else []
            failures = manifest.get("failures") if isinstance(manifest, dict) else []
            requested = requested if isinstance(requested, list) else []
            succeeded = succeeded if isinstance(succeeded, list) else []
            failures = failures if isinstance(failures, list) else []
            proposal["evidence"]["capture_scope"] = {
                "requested": len(requested),
                "succeeded": len(succeeded),
                "failed": len(failures),
                "complete": bool(manifest.get("complete")) and len(succeeded) == len(requested),
                "matrix": [
                    {
                        "viewport": str(item.get("viewport", "unknown"))[:64],
                        "state": str(item.get("state", "unknown"))[:64],
                    }
                    for item in requested
                    if isinstance(item, dict)
                ],
            }
            if not proposal["evidence"]["capture_scope"]["complete"]:
                proposal["decision"]["confidence"] = "low"
                proposal["decision"]["recommendation"] = "insufficient-evidence"
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            proposal["evidence"]["capture_scope"] = {
                "requested": 0,
                "succeeded": 0,
                "failed": 0,
                "complete": False,
                "warning": f"capture manifest unavailable: {type(exc).__name__}",
            }
            proposal["decision"]["confidence"] = "low"
            proposal["decision"]["recommendation"] = "insufficient-evidence"

    out_dir = run_dir / "system"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(proposal, indent=2))
    (out_dir / f"{name}.md").write_text(render_markdown(proposal))
    (out_dir / f"{name}-preview.html").write_text(render_preview_html(proposal))

    return {
        "proposal": proposal,
        "artifacts": {
            "json": str(out_dir / f"{name}.json"),
            "markdown": str(out_dir / f"{name}.md"),
            "preview_html": str(out_dir / f"{name}-preview.html"),
        },
    }
