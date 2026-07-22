"""Slop detector: measurable AI-design fingerprints.

The premise: AI-generated UIs converge on a recognizable set of defaults —
Tailwind's `gray-100`/`blue-500`, shadcn's 0.5rem radius, the lavender/indigo
gradient, Inter-only typography, the 3-up bento with Lucide icons. None of
those are *bad*, but when a page shows several of them at once the design
reads as generic AI output rather than a deliberate choice.

This module measures those fingerprints against a captured run. Each
predicate is small, deterministic, citable, and contributes a weighted hit
to a composite **slop score** in [0, 100]. The score is calibrated so that:

  0–15   : distinctive; the design has its own opinions
  15–35  : derivative; recognizable family but with some intent
  35–60  : conventional; reads like a template
  60+    : AI slop; the page is mostly defaults stacked

Each finding carries a `signature` (what was detected), `evidence` (the
measured values that triggered it), and a `escape` (the concrete move to
de-slop). The agent uses these directly when writing the critique or
generating fix suggestions.

Pure Python — no model calls, no I/O outside the captures directory.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness._oklch import hex_to_oklch
from harness.colors import is_neutral_hex, to_hex

logger = logging.getLogger("keen.slop")


# -- The slop catalog -----------------------------------------------------
#
# Each entry is a fingerprint we measure. The `weight` is how many slop-score
# points one hit contributes (clamped at 100 total). Weights are deliberately
# tuned so that two unrelated hits don't already saturate — the score is meant
# to grade severity, not just count occurrences.

SLOP_CATALOG: dict[str, dict[str, Any]] = {
    "slop.tailwind-default-palette": {
        "weight": 15,
        "title": "Tailwind default palette used verbatim",
        "why": (
            "When the page's dominant colors match Tailwind's named scale "
            "exactly (`gray-100`, `blue-500`, etc.) the design reads as "
            "untouched defaults. The Tailwind ramps are excellent, but any "
            "two pages that grab them straight will look alike."
        ),
        "escape": (
            "Trace the matches to brand assets and semantic roles. Preserve an "
            "established palette when the evidence supports it; otherwise replace "
            "untouched defaults with product-owned role colors. Do not hue-shift "
            "values mechanically just to evade this detector."
        ),
    },
    "slop.vibecode-purple": {
        "weight": 12,
        "title": "VibeCode purple / lavender / indigo bias",
        "why": (
            "Models default to the indigo→violet→fuchsia band (OKLCH hue "
            "270–320°) because it reads as 'tech' and is the Tailwind UI "
            "default that's never been changed. Almost every shipped LLM "
            "preview wears it; users now read it as 'AI made this.'"
        ),
        "escape": (
            "Keep purple when brand evidence supports it, but reserve it for named "
            "actions or states and remove decorative use. If the hue has no source "
            "evidence, derive the accent from the product rather than choosing an "
            "arbitrary non-purple replacement."
        ),
    },
    "slop.shadcn-default-radius": {
        "weight": 10,
        "title": "shadcn default radius (0.5rem / 8px) appears unchanged",
        "why": (
            "shadcn's `--radius: 0.5rem` is the most-copied design token in "
            "modern web UI. A radius scale where every value is exactly 8px "
            "(or its multiples 4 / 8 / 12) signals the theme has never been "
            "edited."
        ),
        "escape": (
            "Map shape to actual component roles and confirm whether 8px is an "
            "established product token. Keep it when deliberate; otherwise derive a "
            "small, purposeful scale from controls, panels, and overlays instead of "
            "changing the number for novelty."
        ),
    },
    "slop.inter-monoculture": {
        "weight": 8,
        "title": "Inter (or system stack) is the only font",
        "why": (
            "Inter is the LLM's pure default. A site whose entire type "
            "system is one weight family of Inter looks like a "
            "Tailwind/shadcn boilerplate, regardless of how good the rest "
            "of the design is."
        ),
        "escape": (
            "Do not add a display serif merely to evade this finding. First "
            "build hierarchy from content, size, weight, line-height, and rhythm. "
            "Adopt another family only when brand assets, captured usage, or the "
            "reading context supports it."
        ),
    },
    "slop.glassmorphism-overuse": {
        "weight": 10,
        "title": "Backdrop-blur glassmorphism on too many surfaces",
        "why": (
            "Glass surfaces were already a 2022 cliché; they're now the "
            "second-most-common AI tell after lavender gradients. More "
            "than two glass surfaces on one page is fashion, not language."
        ),
        "escape": (
            "Reserve backdrop-blur for ONE surface (usually the floating "
            "nav). Everything else: solid, with a real shadow scale."
        ),
    },
    "slop.uniform-radius": {
        "weight": 8,
        "title": "Unrelated component roles share one radius",
        "why": (
            "One radius can be a coherent product choice. It becomes suspicious when "
            "controls, panels, and overlays with different jobs all inherit it without "
            "an explicit shape rule."
        ),
        "escape": (
            "Audit shape by component role. Keep one value if the system intends a "
            "uniform character; otherwise introduce only the distinctions required by "
            "control, container, and overlay behavior."
        ),
    },
    "slop.single-shadow-tier": {
        "weight": 6,
        "title": "One shadow treatment across unrelated elevations",
        "why": (
            "One shadow across panels, hover states, dialogs, and overlays can erase "
            "elevation meaning. A flat product with few elevated surfaces may not need "
            "a shadow scale at all."
        ),
        "escape": (
            "Name the elevations the product actually uses, then assign the smallest "
            "necessary separator to each. Remove shadows from flat regions; add another "
            "tier only when a real overlay or interaction state requires it."
        ),
    },
    "slop.flat-palette": {
        "weight": 8,
        "title": "Flat palette — low chromatic variation",
        "why": (
            "When every non-neutral color sits within ~30° of the same hue, "
            "the page feels desaturated and same-y even if individual "
            "colors are well-chosen. The eye reads variety as intentional; "
            "uniformity as accidental."
        ),
        "escape": (
            "Do not add color for detector variety. Name the jobs the existing "
            "palette must perform, keep one action accent if the product needs it, "
            "and add semantic colors only for real states with contrast proof."
        ),
    },
    "slop.spacing-monotony": {
        "weight": 6,
        "title": "Spacing scale has no rhythm",
        "why": (
            "When ~80%+ of distinct spacing values fall on a single grid "
            "step (everything 8px or 16px), the page lacks the contrast "
            "that makes hierarchy readable. Tight section breaks and loose "
            "section interiors are the difference."
        ),
        "escape": (
            "Define compact, control, group, and section spacing from the product's "
            "actual density. Create contrast between adjacent levels without "
            "injecting oversized whitespace just to make a screenshot feel premium."
        ),
    },
    "slop.gradient-overuse": {
        "weight": 8,
        "title": "Linear gradients are everywhere",
        "why": (
            "Linear gradients on more than 2–3 surfaces on a page is the "
            "single strongest AI-design tell after lavender. Buttons, "
            "cards, hero, even text — the model defaults to "
            "`bg-gradient-to-br`."
        ),
        "escape": (
            "Use at most ONE gradient surface per screen. Replace others "
            "with solid color + a tier of shadow or a subtle 1px stroke."
        ),
    },
    "slop.colored-left-border-cards": {
        "weight": 6,
        "title": "Cards with colored left borders",
        "why": (
            "A 3–4px chromatic left border on a card is the AI version of "
            "the em-dash — diagnostic on its own. It comes from one viral "
            "shadcn snippet that everyone copied."
        ),
        "escape": (
            "Drop the stripe and expose the underlying status or category as text "
            "in the object's normal row, table, or detail structure. Do not replace "
            "one decorative marker with another."
        ),
    },
    "slop.lucide-card-rhythm": {
        "weight": 8,
        "title": "Repeated icon-card structure (Lucide grid)",
        "why": (
            "3+ adjacent cards each containing icon-on-top + heading + "
            "1-line description + optional CTA reads as a template, not a "
            "designed feature comparison. The Lucide stroke-1.5 24×24 icon "
            "is the giveaway."
        ),
        "escape": (
            "Remove the icon containers and choose structure from the objects: a "
            "comparison table, ranked list, or one primary detail with supporting "
            "rows. Vary layout only when priority or content shape requires it."
        ),
    },
    "slop.bento-grid-default": {
        "weight": 5,
        "title": "Default bento grid (varied cards, uniform style)",
        "why": (
            "Bento was a 2024 innovation; the model now reaches for it on "
            "every landing page. When the cards differ in size but share "
            "all other attributes (same radius, same shadow, same chromatic "
            "treatment), it's bento as decoration, not communication."
        ),
        "escape": (
            "Rebuild the section around information priority. Keep a larger region "
            "only when it holds a primary object or action; convert the rest to "
            "plain rows or sections instead of decorating one cell differently."
        ),
    },
    "slop.three-col-equal-trinity": {
        "weight": 5,
        "title": "Three equal columns under the hero",
        "why": (
            "The 1-hero-then-3-equal-columns layout is the single most-"
            "predictable AI-page structure. Equal-width columns force "
            "every feature to read as equally important."
        ),
        "escape": (
            "Determine whether the items are truly peers. Use a table or list for "
            "peers; otherwise promote the primary item and move supporting material "
            "into the reading order. Do not reshuffle boxes without a task reason."
        ),
    },
    "slop.badge-above-h1": {
        "weight": 4,
        "title": "Capitalized pill badge above every H1",
        "why": (
            "The `[NEW] · [LAUNCH] · [BETA]` mini-pill above the main "
            "heading is a clichéd opener — fine once, slop when it "
            "appears on every section."
        ),
        "escape": (
            "Pick one place to use it (if any). Otherwise let the heading "
            "lead and use a subtitle for context."
        ),
    },
    "slop.stat-banner-row": {
        "weight": 5,
        "title": "Hero stat banner: 3-4 big numbers with tiny labels",
        "why": (
            "The `10k+ users · 99.9% uptime · 24/7 support` row sits under "
            "every AI hero. It's social proof on autopilot."
        ),
        "escape": (
            "If the stats matter, expand them into a real section with "
            "context. If they don't, cut them."
        ),
    },
    "slop.editorial-dossier-cluster": {
        "weight": 12,
        "title": "Editorial dossier styling used as interface chrome",
        "why": (
            "A repeated combination of large serif section headings, a sans body, "
            "and small monospaced metadata has become a common generated-report "
            "template. Each choice can be valid; the cluster is suspect when it "
            "frames evidence rather than serving the product's reading context."
        ),
        "escape": (
            "Keep report chrome in the restrained reading face. Reserve a captured "
            "display family for the product specimens that prove its role, remove "
            "decorative chapter metadata, and organize the document around evidence, "
            "differences, decisions, and unresolved work."
        ),
    },
}

# -- Findings model -------------------------------------------------------


@dataclass
class SlopFinding:
    predicate_id: str
    weight: int
    title: str
    why: str
    escape: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # How many independent hits this predicate found. The score contribution
    # is weight * min(hits, max_hits_for_predicate) so a single noisy
    # predicate can't dominate.
    hits: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SlopReport:
    score: int  # 0..100
    band: str  # "distinctive" | "derivative" | "conventional" | "slop"
    findings: list[SlopFinding] = field(default_factory=list)
    # Per-fingerprint hit count, useful for telemetry without the full payload.
    hit_counts: dict[str, int] = field(default_factory=dict)
    # The escape moves, deduplicated and ordered by impact, for the agent
    # to surface as "next 3 things to fix" in a /keen:ui-deslop critique.
    top_escapes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "findings": [f.to_dict() for f in self.findings],
            "hit_counts": self.hit_counts,
            "top_escapes": self.top_escapes,
        }


# Hits per predicate are capped so one noisy fingerprint can't max the score.
_HITS_CAP = 3


def _band_for(score: int) -> str:
    if score < 15:
        return "distinctive"
    if score < 35:
        return "derivative"
    if score < 60:
        return "conventional"
    return "slop"


# -- Tailwind default palette ---------------------------------------------
#
# Hex values verbatim from the Tailwind v3/v4 default theme. We check whether
# the page's dominant non-neutral foreground colors match any of these.

TAILWIND_PALETTE_HEX: set[str] = {
    # Slate (the most common shadcn neutral)
    "#f8fafc",
    "#f1f5f9",
    "#e2e8f0",
    "#cbd5e1",
    "#94a3b8",
    "#64748b",
    "#475569",
    "#334155",
    "#1e293b",
    "#0f172a",
    # Gray
    "#f9fafb",
    "#f3f4f6",
    "#e5e7eb",
    "#d1d5db",
    "#9ca3af",
    "#6b7280",
    "#4b5563",
    "#374151",
    "#1f2937",
    "#111827",
    # Zinc (shadcn's other neutral default)
    "#fafafa",
    "#f4f4f5",
    "#e4e4e7",
    "#d4d4d8",
    "#a1a1aa",
    "#71717a",
    "#52525b",
    "#3f3f46",
    "#27272a",
    "#18181b",
    # Neutral
    "#f5f5f5",
    "#e5e5e5",
    "#d4d4d4",
    "#a3a3a3",
    "#737373",
    "#525252",
    "#404040",
    "#262626",
    "#171717",
    # Stone
    "#fafaf9",
    "#f5f5f4",
    "#e7e5e4",
    "#d6d3d1",
    "#a8a29e",
    "#78716c",
    "#57534e",
    "#44403c",
    "#292524",
    "#1c1917",
    # Blue (the most-defaulted action color)
    "#eff6ff",
    "#dbeafe",
    "#bfdbfe",
    "#93c5fd",
    "#60a5fa",
    "#3b82f6",
    "#2563eb",
    "#1d4ed8",
    "#1e40af",
    "#1e3a8a",
    # Indigo (the VibeCode default)
    "#eef2ff",
    "#e0e7ff",
    "#c7d2fe",
    "#a5b4fc",
    "#818cf8",
    "#6366f1",
    "#4f46e5",
    "#4338ca",
    "#3730a3",
    "#312e81",
    # Violet
    "#f5f3ff",
    "#ede9fe",
    "#ddd6fe",
    "#c4b5fd",
    "#a78bfa",
    "#8b5cf6",
    "#7c3aed",
    "#6d28d9",
    "#5b21b6",
    "#4c1d95",
    # Purple
    "#faf5ff",
    "#f3e8ff",
    "#e9d5ff",
    "#d8b4fe",
    "#c084fc",
    "#a855f7",
    "#9333ea",
    "#7e22ce",
    "#6b21a8",
    "#581c87",
    # Fuchsia / pink (often used in gradients)
    "#fdf4ff",
    "#fae8ff",
    "#f5d0fe",
    "#f0abfc",
    "#e879f9",
    "#d946ef",
    "#c026d3",
    "#a21caf",
    "#86198f",
    "#701a75",
    "#fdf2f8",
    "#fce7f3",
    "#fbcfe8",
    "#f9a8d4",
    "#f472b6",
    "#ec4899",
    "#db2777",
    "#be185d",
    "#9d174d",
    "#831843",
    # Emerald (success default)
    "#ecfdf5",
    "#d1fae5",
    "#a7f3d0",
    "#6ee7b7",
    "#34d399",
    "#10b981",
    "#059669",
    "#047857",
    "#065f46",
    "#064e3b",
    # Amber (warning default)
    "#fffbeb",
    "#fef3c7",
    "#fde68a",
    "#fcd34d",
    "#fbbf24",
    "#f59e0b",
    "#d97706",
    "#b45309",
    "#92400e",
    "#78350f",
    # Red (danger default)
    "#fef2f2",
    "#fee2e2",
    "#fecaca",
    "#fca5a5",
    "#f87171",
    "#ef4444",
    "#dc2626",
    "#b91c1c",
    "#991b1b",
    "#7f1d1d",
}


# -- Helpers --------------------------------------------------------------


def _components_by_capture(captures_dir: Path) -> list[tuple[str, list[dict]]]:
    """Return decomposed components without erasing their capture boundary."""
    captures: list[tuple[str, list[dict]]] = []
    comp_dir = captures_dir / "components"
    if not comp_dir.exists():
        return captures
    for path in sorted(comp_dir.glob("*.json")):
        # Only the per-capture component lists (file stems like
        # `<slug>-<viewport>-<state>.json`). Skip the per-component crop PNGs
        # and any leftover dump files.
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            captures.append((path.stem, data))
    return captures


def _all_components(captures_dir: Path) -> list[dict]:
    """Flat component list retained for token-only predicate compatibility."""
    return [
        component
        for _, components in _components_by_capture(captures_dir)
        for component in components
    ]


def _normalize_token_counts(value: Any, capture_count: int) -> Any:
    """Convert aggregate token usage counts to per-capture averages.

    Token extraction combines every responsive/state capture. Ratios and
    distinct-value checks survive aggregation, but absolute thresholds (for
    example, spacing usage >= 50) otherwise change merely because the same UI
    was captured three times.
    """
    if capture_count <= 1:
        return value
    if isinstance(value, dict):
        return {
            key: (
                item / capture_count
                if key == "count" and isinstance(item, (int, float))
                else _normalize_token_counts(item, capture_count)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_token_counts(item, capture_count) for item in value]
    return value


def _tokens(captures_dir: Path) -> dict[str, Any]:
    path = captures_dir / "tokens" / "extracted.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_hex(s: str) -> str | None:
    return to_hex(s)


# -- Predicates -----------------------------------------------------------


def _pred_tailwind_palette(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Check whether the page's most-used non-neutral foreground hexes match
    Tailwind's default ramp exactly. Two or more matches are a clear signal."""
    top_fg = (tokens.get("colors") or {}).get("foreground", []) or []
    top_bg = (tokens.get("colors") or {}).get("background", []) or []
    used_colors = top_fg[:24] + top_bg[:24]
    hits: list[dict] = []
    for entry in used_colors:
        raw = entry.get("value", "")
        h = _safe_hex(raw)
        if not h:
            continue
        h_lower = h.lower()
        if h_lower in TAILWIND_PALETTE_HEX:
            # Skip pure whites/blacks (everyone uses them, not a tell).
            if h_lower in ("#ffffff", "#000000"):
                continue
            hits.append({"hex": h_lower, "count": entry.get("count", 0)})
    if len(hits) < 2:
        return []
    cat = SLOP_CATALOG["slop.tailwind-default-palette"]
    return [
        SlopFinding(
            predicate_id="slop.tailwind-default-palette",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"matches": hits[:8], "match_count": len(hits)},
            hits=min(len(hits), _HITS_CAP),
        )
    ]


def _pred_vibecode_purple(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect dominance of the indigo→violet→fuchsia OKLCH band (270–325°)."""
    top_fg = (tokens.get("colors") or {}).get("foreground", []) or []
    top_bg = (tokens.get("colors") or {}).get("background", []) or []
    purple_count = 0
    purple_examples: list[dict] = []
    total_chromatic = 0
    for entry in (top_fg + top_bg)[:30]:
        h = _safe_hex(entry.get("value", ""))
        if not h or is_neutral_hex(h, tolerance=12):
            continue
        total_chromatic += 1
        try:
            _, C, hue = hex_to_oklch(h)
        except ValueError:
            continue
        if C < 0.04:
            continue  # too desaturated to count as chromatic
        if 270.0 <= hue <= 325.0:
            purple_count += 1
            purple_examples.append({"hex": h, "hue_deg": round(hue, 1), "chroma": round(C, 3)})
    if purple_count < 2:
        return []
    cat = SLOP_CATALOG["slop.vibecode-purple"]
    # Stronger signal when purple dominates the chromatic palette.
    dominance = purple_count / max(total_chromatic, 1)
    return [
        SlopFinding(
            predicate_id="slop.vibecode-purple",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "purple_color_count": purple_count,
                "chromatic_color_count": total_chromatic,
                "purple_dominance_pct": round(dominance * 100, 1),
                "examples": purple_examples[:6],
            },
            hits=min(2 if dominance > 0.5 else 1, _HITS_CAP),
        )
    ]


def _pred_shadcn_radius(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when the dominant radius is 8 (shadcn's `--radius: 0.5rem`)."""
    radii = (tokens.get("shape") or {}).get("border_radii_px", []) or []
    # Drop 0 (zero radius isn't a slop tell on its own — many serious systems
    # are sharp).
    radii_nz = [r for r in radii if r.get("value", 0) > 0]
    if not radii_nz:
        return []
    total = sum(r.get("count", 0) for r in radii_nz)
    if total == 0:
        return []
    # Slop if 8px alone is > 60% of non-zero radius usage.
    eight = sum(r.get("count", 0) for r in radii_nz if r.get("value") == 8)
    pct = eight / total
    if pct < 0.60:
        return []
    cat = SLOP_CATALOG["slop.shadcn-default-radius"]
    return [
        SlopFinding(
            predicate_id="slop.shadcn-default-radius",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "eight_px_usage_pct": round(pct * 100, 1),
                "eight_px_count": eight,
                "total_radius_count": total,
                "unique_nonzero_radii": [r.get("value") for r in radii_nz],
            },
        )
    ]


def _pred_uniform_radius(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when fewer than 3 distinct non-zero radii are in use."""
    radii = (tokens.get("shape") or {}).get("border_radii_px", []) or []
    distinct = sorted({r.get("value") for r in radii if r.get("value", 0) > 0})
    if len(distinct) >= 3:
        return []
    if not distinct:
        return []  # all sharp; that's a choice, not slop
    cat = SLOP_CATALOG["slop.uniform-radius"]
    return [
        SlopFinding(
            predicate_id="slop.uniform-radius",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"distinct_radii_px": distinct},
        )
    ]


_INTER_FAMILIES = {
    "inter",
    "system-ui",
    "-apple-system",
    "segoe ui",
    "roboto",
    "helvetica",
    "arial",
    "sans-serif",
}


def _pred_inter_monoculture(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when the only font family in use is Inter (or the generic system stack).

    Heuristic: parse the first family token (before the comma) from each
    fontFamily string in the token data. If the only first-token is Inter or
    one of the generic fallbacks, the page has no committed typeface.
    """
    families = (tokens.get("type") or {}).get("families", []) or []
    if not families:
        return []
    first_tokens: Counter[str] = Counter()
    for entry in families:
        raw = entry.get("value", "")
        # Take the first comma-separated entry and strip quotes/whitespace.
        first = raw.split(",", 1)[0].strip().strip('"').strip("'").lower()
        if first:
            first_tokens[first] += entry.get("count", 1)
    if not first_tokens:
        return []
    distinct = list(first_tokens.keys())
    non_default = [f for f in distinct if f not in _INTER_FAMILIES]
    if non_default:
        return []  # at least one custom family is in use; not slop
    cat = SLOP_CATALOG["slop.inter-monoculture"]
    return [
        SlopFinding(
            predicate_id="slop.inter-monoculture",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"families_first_tokens": distinct[:6]},
        )
    ]


def _pred_glassmorphism(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Count surfaces with a non-trivial backdrop-filter blur."""
    glass: list[dict] = []
    for c in comps:
        s = c.get("styles", {}) or {}
        bf = (s.get("backdropFilter") or s.get("webkitBackdropFilter") or "").strip()
        if not bf or bf == "none":
            continue
        if "blur" in bf.lower():
            glass.append(
                {
                    "kind": c.get("component_kind"),
                    "box": c.get("box"),
                    "backdrop_filter": bf,
                }
            )
    if len(glass) < 3:
        return []
    cat = SLOP_CATALOG["slop.glassmorphism-overuse"]
    return [
        SlopFinding(
            predicate_id="slop.glassmorphism-overuse",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"glass_surface_count": len(glass), "examples": glass[:5]},
            hits=min(len(glass) - 2, _HITS_CAP),
        )
    ]


def _pred_gradient_overuse(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Count surfaces whose backgroundImage is a linear/radial gradient."""
    grads: list[dict] = []
    for c in comps:
        s = c.get("styles", {}) or {}
        bg = (s.get("backgroundImage") or "").strip()
        if not bg or bg == "none":
            continue
        if "gradient(" in bg.lower():
            grads.append(
                {
                    "kind": c.get("component_kind"),
                    "box": c.get("box"),
                    "background_image": bg[:120],
                }
            )
    if len(grads) < 3:
        return []
    cat = SLOP_CATALOG["slop.gradient-overuse"]
    return [
        SlopFinding(
            predicate_id="slop.gradient-overuse",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"gradient_surface_count": len(grads), "examples": grads[:5]},
            hits=min(len(grads) - 2, _HITS_CAP),
        )
    ]


_SHADOW_NONE = {"", "none"}


def _pred_single_shadow_tier(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when many shadowed elements share a single shadow value (no tier)."""
    shadows: Counter[str] = Counter()
    for c in comps:
        s = c.get("styles", {}) or {}
        bs = (s.get("boxShadow") or "").strip()
        if bs in _SHADOW_NONE:
            continue
        # Normalize whitespace so functionally-identical values collide.
        normalized = re.sub(r"\s+", " ", bs)
        shadows[normalized] += 1
    total = sum(shadows.values())
    if total < 5:
        return []  # not enough shadowed elements to draw a conclusion
    if len(shadows) >= 3:
        return []
    most_common_value, most_common_count = shadows.most_common(1)[0]
    pct = most_common_count / total
    if pct < 0.85:
        return []
    cat = SLOP_CATALOG["slop.single-shadow-tier"]
    return [
        SlopFinding(
            predicate_id="slop.single-shadow-tier",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "distinct_shadow_values": len(shadows),
                "dominant_shadow": most_common_value[:120],
                "dominant_usage_pct": round(pct * 100, 1),
                "shadowed_element_count": total,
            },
        )
    ]


def _pred_flat_palette(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when chromatic hues are all within ~30° of each other (no contrast)."""
    top_fg = (tokens.get("colors") or {}).get("foreground", []) or []
    top_bg = (tokens.get("colors") or {}).get("background", []) or []
    chromatic_hues: list[float] = []
    for entry in (top_fg + top_bg)[:30]:
        h = _safe_hex(entry.get("value", ""))
        if not h or is_neutral_hex(h, tolerance=12):
            continue
        try:
            _, C, hue = hex_to_oklch(h)
        except ValueError:
            continue
        if C < 0.05:
            continue
        chromatic_hues.append(hue)
    if len(chromatic_hues) < 3:
        return []
    # Hue is circular; measure the smallest arc that covers all points.
    sorted_h = sorted(chromatic_hues)
    gaps = [sorted_h[i + 1] - sorted_h[i] for i in range(len(sorted_h) - 1)]
    gaps.append(360.0 - sorted_h[-1] + sorted_h[0])  # wrap-around gap
    largest_gap = max(gaps)
    span = 360.0 - largest_gap
    if span > 60.0:
        return []  # palette spans more than 60° of hue; not flat
    cat = SLOP_CATALOG["slop.flat-palette"]
    return [
        SlopFinding(
            predicate_id="slop.flat-palette",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "chromatic_hue_span_deg": round(span, 1),
                "chromatic_color_count": len(chromatic_hues),
                "example_hues_deg": [round(h, 0) for h in chromatic_hues[:6]],
            },
        )
    ]


def _pred_spacing_monotony(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Flag when one spacing value accounts for > 70% of distinct usage."""
    spacing = (tokens.get("spacing") or {}).get("values_px", []) or []
    if not spacing:
        return []
    total = sum(s.get("count", 0) for s in spacing)
    if total < 50:
        return []
    most_common = max(spacing, key=lambda s: s.get("count", 0))
    pct = most_common.get("count", 0) / total
    if pct < 0.70:
        return []
    cat = SLOP_CATALOG["slop.spacing-monotony"]
    return [
        SlopFinding(
            predicate_id="slop.spacing-monotony",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "dominant_spacing_px": most_common.get("value"),
                "dominant_usage_pct": round(pct * 100, 1),
                "distinct_spacing_values": len(spacing),
            },
        )
    ]


def _pred_colored_left_border(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect cards (or card-like containers) with a chromatic left border
    of >= 3px and zero or neutral borders elsewhere."""
    hits: list[dict] = []
    for c in comps:
        s = c.get("styles", {}) or {}
        try:
            left = float(s.get("borderLeftWidth", "0").rstrip("px") or 0)
            top = float(s.get("borderTopWidth", "0").rstrip("px") or 0)
            right = float(s.get("borderRightWidth", "0").rstrip("px") or 0)
            bottom = float(s.get("borderBottomWidth", "0").rstrip("px") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if left < 3:
            continue
        if max(top, right, bottom) >= 2:
            continue  # genuine 4-side border, not a left-stripe accent
        left_color = s.get("borderLeftColor") or ""
        hex_ = _safe_hex(left_color)
        if not hex_ or is_neutral_hex(hex_, tolerance=12):
            continue
        # Component should be card-like: not a button, not tiny.
        box = c.get("box") or {}
        if box.get("w", 0) < 200 or box.get("h", 0) < 60:
            continue
        if c.get("component_kind") in ("button", "icon-button", "link", "tab"):
            continue
        hits.append(
            {
                "kind": c.get("component_kind"),
                "left_color": hex_,
                "left_width_px": left,
            }
        )
    if len(hits) < 2:
        return []
    cat = SLOP_CATALOG["slop.colored-left-border-cards"]
    return [
        SlopFinding(
            predicate_id="slop.colored-left-border-cards",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"card_count": len(hits), "examples": hits[:5]},
            hits=min(len(hits) - 1, _HITS_CAP),
        )
    ]


def _pred_lucide_card_rhythm(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect 3+ structurally-identical 'feature cards': contiguous list-items
    or sections each containing a small svg/img + a heading + a paragraph.

    Heuristic: group components by parent_index, then look at each group of
    >= 3 same-kind containers (list-item, generic landmark) that each have a
    heading-3 or heading-4 child plus a small svg/image child.
    """
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        by_parent[c.get("parent_index", -1)].append(c)

    # Index components by parent for the inner check.
    children_by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        children_by_parent[c.get("parent_index", -1)].append(c)

    suspect_groups: list[dict] = []
    for parent_idx, siblings in by_parent.items():
        # A feature-card grid often shows up as 3+ same-kind siblings, where
        # the kind is one of {list-item, landmark-region, landmark-group}.
        siblings_by_kind: dict[str, list[dict]] = defaultdict(list)
        for s in siblings:
            siblings_by_kind[s.get("component_kind", "")].append(s)
        for kind, group in siblings_by_kind.items():
            if kind not in ("list-item", "landmark-region", "landmark-group"):
                continue
            if len(group) < 3:
                continue
            # For each candidate group, check whether each member has both
            # a small icon-like child AND a heading-class child.
            matching = 0
            for member in group:
                kids = children_by_parent.get(member.get("index", -2), [])
                has_heading = any(k.get("component_kind", "").startswith("heading-") for k in kids)
                has_icon_like = any(
                    k.get("component_kind") in ("image", "icon-button")
                    and (k.get("box") or {}).get("w", 999) <= 56
                    for k in kids
                )
                if has_heading and has_icon_like:
                    matching += 1
            if matching >= 3:
                suspect_groups.append(
                    {
                        "parent_index": parent_idx,
                        "kind": kind,
                        "card_count": matching,
                    }
                )
    if not suspect_groups:
        return []
    cat = SLOP_CATALOG["slop.lucide-card-rhythm"]
    return [
        SlopFinding(
            predicate_id="slop.lucide-card-rhythm",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"group_count": len(suspect_groups), "examples": suspect_groups[:4]},
            hits=min(len(suspect_groups), _HITS_CAP),
        )
    ]


def _pred_three_col_equal(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect a single horizontal row of exactly 3 sibling regions of roughly
    equal width directly under (within the first 1500px y) a single heading-1.
    """
    h1s = [c for c in comps if c.get("component_kind") == "heading-1"]
    if not h1s:
        return []
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        by_parent[c.get("parent_index", -1)].append(c)
    hits = 0
    for _parent_idx, siblings in by_parent.items():
        # Look for 3 region/group siblings whose y is roughly the same.
        regions = [
            s
            for s in siblings
            if s.get("component_kind") in ("landmark-region", "landmark-group", "list-item")
            and (s.get("box") or {}).get("w", 0) > 100
        ]
        if len(regions) != 3:
            continue
        widths = sorted((r.get("box") or {}).get("w", 0) for r in regions)
        ys = [r.get("box", {}).get("y", 0) for r in regions]
        # Same y-row: max ys close to min ys, within ~40px.
        if max(ys) - min(ys) > 40:
            continue
        # Equal widths: max/min within 10%.
        if widths[0] == 0:
            continue
        if widths[-1] / widths[0] > 1.10:
            continue
        hits += 1
    if hits == 0:
        return []
    cat = SLOP_CATALOG["slop.three-col-equal-trinity"]
    return [
        SlopFinding(
            predicate_id="slop.three-col-equal-trinity",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"three_col_groups": hits},
            hits=min(hits, _HITS_CAP),
        )
    ]


_BADGE_RX = re.compile(r"^[A-Z][A-Z0-9·\-\s]{1,30}$")


def _pred_badge_above_h1(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect an all-caps short label immediately preceding (sibling-above) an H1."""
    # Build a quick lookup of components sharing a parent, in document order.
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        by_parent[c.get("parent_index", -1)].append(c)
    for siblings in by_parent.values():
        siblings.sort(key=lambda c: c.get("index", 0))
    hits = 0
    for siblings in by_parent.values():
        for i, s in enumerate(siblings):
            if s.get("component_kind") != "heading-1":
                continue
            if i == 0:
                continue
            prev = siblings[i - 1]
            text = (prev.get("text") or prev.get("name") or "").strip()
            if not text:
                continue
            if _BADGE_RX.match(text):
                hits += 1
    if hits == 0:
        return []
    cat = SLOP_CATALOG["slop.badge-above-h1"]
    return [
        SlopFinding(
            predicate_id="slop.badge-above-h1",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"badge_above_h1_count": hits},
            hits=min(hits, _HITS_CAP),
        )
    ]


_STAT_RX = re.compile(r"^\$?[\d.,]+(?:[KMB]\+?|%|x)?$")


def _pred_stat_banner(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect 3+ adjacent large-text statistic chips (`10k+`, `99.9%`, `$2B`)."""
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        by_parent[c.get("parent_index", -1)].append(c)
    hits = 0
    for siblings in by_parent.values():
        stats = []
        for s in siblings:
            text = (s.get("text") or s.get("name") or "").strip()
            if not text:
                continue
            try:
                size = float(s.get("styles", {}).get("fontSize", "0").rstrip("px"))
            except (ValueError, TypeError, AttributeError):
                continue
            if size < 24:
                continue
            if _STAT_RX.match(text):
                stats.append(s)
        if len(stats) >= 3:
            hits += 1
    if hits == 0:
        return []
    cat = SLOP_CATALOG["slop.stat-banner-row"]
    return [
        SlopFinding(
            predicate_id="slop.stat-banner-row",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"stat_banner_groups": hits},
        )
    ]


def _pred_bento(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect a bento-style grid: 4+ same-kind sibling cards of *different*
    widths but identical radius, identical background-color, and identical
    box-shadow value."""
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in comps:
        by_parent[c.get("parent_index", -1)].append(c)
    hits = 0
    for siblings in by_parent.values():
        same_kind: dict[str, list[dict]] = defaultdict(list)
        for s in siblings:
            same_kind[s.get("component_kind", "")].append(s)
        for kind, group in same_kind.items():
            if kind not in ("list-item", "landmark-region", "landmark-group"):
                continue
            if len(group) < 4:
                continue
            widths = {(g.get("box") or {}).get("w", 0) for g in group}
            if len(widths) < 2:
                continue  # uniform widths = not bento, just a row
            radii = {g.get("styles", {}).get("borderTopLeftRadius", "") for g in group}
            bgs = {g.get("styles", {}).get("backgroundColor", "") for g in group}
            shadows = {g.get("styles", {}).get("boxShadow", "") for g in group}
            if len(radii) == 1 and len(bgs) == 1 and len(shadows) == 1:
                hits += 1
    if hits == 0:
        return []
    cat = SLOP_CATALOG["slop.bento-grid-default"]
    return [
        SlopFinding(
            predicate_id="slop.bento-grid-default",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={"bento_groups": hits},
        )
    ]


_SERIF_HINTS = {
    "baskerville",
    "cambria",
    "constantia",
    "garamond",
    "georgia",
    "palatino",
    "times",
    "times new roman",
}


def _family_kind(stack: str) -> str:
    families = [part.strip().strip("\"'").lower() for part in stack.split(",")]
    if "monospace" in families or any("mono" in family for family in families):
        return "mono"
    if "serif" in families and "sans-serif" not in families:
        return "serif"
    if any(family in _SERIF_HINTS for family in families):
        return "serif"
    return "sans"


def _pred_editorial_dossier_cluster(comps: list[dict], tokens: dict) -> list[SlopFinding]:
    """Detect the contemporary generated-report cluster, not a lone type choice.

    The predicate requires four large serif headings, a dominant sans body,
    and at least three small monospaced metadata/table labels. This keeps one
    serif title or a legitimate code block from becoming an authorship claim.
    """
    large_serif_headings: list[dict[str, Any]] = []
    mono_metadata: list[dict[str, Any]] = []
    for component in comps:
        styles = component.get("styles") or {}
        family = str(styles.get("fontFamily") or "")
        try:
            size = float(str(styles.get("fontSize") or "0").removesuffix("px"))
        except (TypeError, ValueError):
            continue
        kind = str(component.get("component_kind") or "")
        text = str(component.get("text") or component.get("name") or "").strip()
        family_kind = _family_kind(family)
        if kind.startswith("heading-") and family_kind == "serif" and size >= 30:
            large_serif_headings.append(
                {"kind": kind, "text": text[:80], "size_px": size, "family": family[:100]}
            )
        if (
            text
            and family_kind == "mono"
            and size <= 13
            and (
                str(styles.get("textTransform") or "").lower() == "uppercase"
                or kind in {"table-header-cell", "table-cell"}
            )
        ):
            mono_metadata.append(
                {"kind": kind, "text": text[:80], "size_px": size, "family": family[:100]}
            )

    families = (tokens.get("type") or {}).get("families", []) or []
    dominant_family = ""
    if families:
        dominant = max(families, key=lambda item: item.get("count", 0))
        dominant_family = str(dominant.get("value") or "")
    if (
        len(large_serif_headings) < 4
        or len(mono_metadata) < 3
        or _family_kind(dominant_family) != "sans"
    ):
        return []

    cat = SLOP_CATALOG["slop.editorial-dossier-cluster"]
    return [
        SlopFinding(
            predicate_id="slop.editorial-dossier-cluster",
            weight=cat["weight"],
            title=cat["title"],
            why=cat["why"],
            escape=cat["escape"],
            evidence={
                "large_serif_heading_count": len(large_serif_headings),
                "small_mono_metadata_count": len(mono_metadata),
                "dominant_body_family": dominant_family,
                "heading_examples": large_serif_headings[:4],
                "metadata_examples": mono_metadata[:4],
            },
        )
    ]


# -- Registry -------------------------------------------------------------

PREDICATES: list[tuple[str, Callable[[list[dict], dict], list[SlopFinding]]]] = [
    ("slop.tailwind-default-palette", _pred_tailwind_palette),
    ("slop.vibecode-purple", _pred_vibecode_purple),
    ("slop.shadcn-default-radius", _pred_shadcn_radius),
    ("slop.uniform-radius", _pred_uniform_radius),
    ("slop.inter-monoculture", _pred_inter_monoculture),
    ("slop.glassmorphism-overuse", _pred_glassmorphism),
    ("slop.gradient-overuse", _pred_gradient_overuse),
    ("slop.single-shadow-tier", _pred_single_shadow_tier),
    ("slop.flat-palette", _pred_flat_palette),
    ("slop.spacing-monotony", _pred_spacing_monotony),
    ("slop.colored-left-border-cards", _pred_colored_left_border),
    ("slop.lucide-card-rhythm", _pred_lucide_card_rhythm),
    ("slop.three-col-equal-trinity", _pred_three_col_equal),
    ("slop.badge-above-h1", _pred_badge_above_h1),
    ("slop.stat-banner-row", _pred_stat_banner),
    ("slop.bento-grid-default", _pred_bento),
    ("slop.editorial-dossier-cluster", _pred_editorial_dossier_cluster),
]

# These predicates count or group DOM components and must be evaluated within
# one capture. Responsive and interaction-state captures commonly repeat the
# same component tree; flattening them turns one glass surface into three and
# can manufacture a finding that exists in no actual viewport.
_CAPTURE_SCOPED_PREDICATES = {
    "slop.glassmorphism-overuse",
    "slop.gradient-overuse",
    "slop.single-shadow-tier",
    "slop.colored-left-border-cards",
    "slop.lucide-card-rhythm",
    "slop.three-col-equal-trinity",
    "slop.badge-above-h1",
    "slop.stat-banner-row",
    "slop.bento-grid-default",
    "slop.editorial-dossier-cluster",
}


def analyze_slop(captures_dir: Path) -> SlopReport:
    """Run every slop predicate over a captures directory. Returns a SlopReport."""
    captures_dir = Path(captures_dir)
    captures = _components_by_capture(captures_dir)
    comps = [component for _, components in captures for component in components]
    tokens = _normalize_token_counts(_tokens(captures_dir), max(1, len(captures)))
    findings: list[SlopFinding] = []
    for pid, fn in PREDICATES:
        try:
            if pid not in _CAPTURE_SCOPED_PREDICATES:
                findings.extend(fn(comps, tokens))
                continue

            per_capture: list[tuple[str, SlopFinding]] = []
            for capture_id, capture_components in captures:
                per_capture.extend(
                    (capture_id, finding) for finding in fn(capture_components, tokens)
                )
            if not per_capture:
                continue

            # A repeated responsive/state capture is corroboration, not an
            # extra hit. Keep the strongest real capture and annotate how many
            # capture scopes independently triggered the predicate.
            best_capture, best = max(
                per_capture,
                key=lambda item: (item[1].weight * item[1].hits, item[1].hits, item[0]),
            )
            capture_ids = sorted({capture_id for capture_id, _ in per_capture})
            if len(capture_ids) > 1:
                best.evidence = {
                    **best.evidence,
                    "captures_triggered": len(capture_ids),
                    "representative_capture": best_capture,
                    "capture_examples": capture_ids[:3],
                }
            findings.append(best)
        except Exception:
            logger.exception("slop predicate %s raised; skipping", pid)
    findings.sort(key=lambda f: (-f.weight * f.hits, f.predicate_id))
    raw_score = sum(f.weight * f.hits for f in findings)
    score = min(100, raw_score)
    hit_counts = {f.predicate_id: f.hits for f in findings}
    # Top escapes: ordered by impact, unique by predicate id, with the
    # measured evidence so the agent can quote specifics in the critique.
    top_escapes = [
        {
            "predicate_id": f.predicate_id,
            "title": f.title,
            "escape": f.escape,
            "weight": f.weight,
            "hits": f.hits,
            "evidence": f.evidence,
        }
        for f in findings[:6]
    ]
    return SlopReport(
        score=score,
        band=_band_for(score),
        findings=findings,
        hit_counts=hit_counts,
        top_escapes=top_escapes,
    )


def render_markdown(report: SlopReport) -> str:
    """Human-readable summary of a SlopReport.

    Designed to be loaded directly by the agent as the body of a
    `/keen:ui-deslop`
    critique — already cites evidence, already ranks escapes.
    """
    lines: list[str] = []
    lines.append("# Keen — slop report")
    lines.append("")
    lines.append(f"**Slop score:** {report.score} / 100  →  *{report.band}*")
    lines.append("")
    if report.score < 15:
        lines.append("The design has its own voice. No defaults stacked, no template tells.")
    elif report.score < 35:
        lines.append(
            "Recognizable family but with some intent. A few defaults remain — "
            "address the top one or two to read as distinct."
        )
    elif report.score < 60:
        lines.append(
            "Reads as conventional. Multiple template tells are present; "
            "addressing the top three will lift the design out of the slush pile."
        )
    else:
        lines.append(
            "Reads as AI slop. The page is mostly defaults stacked. The "
            "escape moves below are ordered by impact — start with the first."
        )
    lines.append("")

    if not report.findings:
        lines.append("No slop fingerprints detected.")
        return "\n".join(lines) + "\n"

    lines.append("## Detected fingerprints")
    lines.append("")
    lines.append("| Weight × Hits | Fingerprint | Why it's a tell |")
    lines.append("|---------------|-------------|-----------------|")
    for f in report.findings:
        lines.append(f"| {f.weight} × {f.hits} | **{f.title}** | {f.why} |")
    lines.append("")

    lines.append("## Escape moves (in priority order)")
    lines.append("")
    for i, esc in enumerate(report.top_escapes, 1):
        lines.append(f"### {i}. {esc['title']}")
        lines.append("")
        lines.append(f"**Escape:** {esc['escape']}")
        lines.append("")
        ev = esc.get("evidence") or {}
        if ev:
            lines.append("**Measured:**")
            for k, v in ev.items():
                lines.append(f"- `{k}`: {v}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "_Each fingerprint above is measured, not vibes-based. Address the "
        "highest-weighted ones first — they contribute the most to the score._"
    )
    return "\n".join(lines) + "\n"
