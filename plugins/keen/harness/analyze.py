"""Analyze stage: run deterministic predicates against every component.

A "predicate" is a small, named, well-defined check that produces a `Finding`
when it fails. Predicates only fail when something is *measurably* wrong — they
never flag taste calls.

Each predicate is registered with:
    - id          stable id used in reports (e.g. "contrast.text")
    - severity    P0 (blocking) | P1 (system violation) | P2 (recommended)
    - applies_to  set of component_kinds this predicate runs against
    - rule        a short citation string for the report

When `target_system` is set, the rubric narrows: predicates whose canonical
value differs between systems read system-specific thresholds.

Pure Python — no model calls. Pillow is optional and only used for the pixel
sampling predicates; without it those checks no-op.
"""

from __future__ import annotations

import heapq
import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness._sanitize import sanitize_finding
from harness.colors import (
    contrast_ratio,
    parse_color,
)

logger = logging.getLogger("keen.analyze")


def _resolve_background_dom(comp: dict, all_comps: list[dict]) -> str:
    """Walk up the parent chain to find the first non-transparent background."""
    seen: set[int] = set()
    cur: dict | None = comp
    while cur:
        current_index = cur.get("index")
        if not isinstance(current_index, int) or current_index in seen:
            break
        seen.add(current_index)
        bg = cur.get("styles", {}).get("backgroundColor", "")
        parsed = parse_color(bg)
        if parsed and parsed[3] > 0.01:
            return bg
        parent_idx = cur.get("parent_index", -1)
        if parent_idx < 0:
            break
        cur = next((c for c in all_comps if c.get("index") == parent_idx), None)
    document_background = comp.get("document_background")
    if isinstance(document_background, str) and parse_color(document_background):
        return document_background
    return "rgb(255, 255, 255)"


def _resolve_background(comp: dict, ctx: dict) -> str:
    """Return the background CSS string. Prefers a pixel sample from the screenshot
    when available; falls back to walking the DOM parent chain."""
    sampler = ctx.get("pixel_sampler")
    if sampler is not None:
        sampled = sampler(comp)
        if sampled is not None:
            return sampled
    return _resolve_background_dom(comp, ctx["all_comps"])


# -- Findings model --------------------------------------------------------

SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


@dataclass
class Finding:
    predicate_id: str
    severity: str
    rule: str
    message: str
    measured: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PREDICATES: list[tuple[str, set[str], Callable[[dict, dict], Finding | None]]] = []
GLOBAL_PREDICATES: list[Callable[[list[dict], dict], list[Finding]]] = []


def predicate(pid: str, applies_to: set[str]) -> Callable:
    """Decorator: register a per-component predicate."""

    def deco(fn: Callable[[dict, dict], Finding | None]) -> Callable:
        PREDICATES.append((pid, applies_to, fn))
        return fn

    return deco


def global_predicate(fn: Callable[[list[dict], dict], list[Finding]]) -> Callable:
    """Decorator: register a global predicate that sees the full component list once."""
    GLOBAL_PREDICATES.append(fn)
    return fn


# -- System-aware thresholds ----------------------------------------------

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "hit_target_min_px": 24,  # WCAG 2.2 AA fallback; named systems override it
    "contrast_text_aa_normal": 4.5,  # WCAG 2.2 AA normal text
    "contrast_text_aa_large": 3.0,  # WCAG 2.2 AA large text
    "contrast_nontext_aa": 3.0,  # WCAG 2.2 AA non-text
    "large_text_min_px": 24,  # ~18pt
    "bold_text_min_px": 18.66,  # ~14pt
    "spacing_grid_px": 4,
    "max_line_length_chars": 75,
    "min_line_length_chars": 45,
    "tap_overlap_pad_px": 8,  # min gap between adjacent tap targets
    "visual_dom_color_delta": 64,  # max channel-distance allowed for declared vs sampled
}

SYSTEM_THRESHOLDS: dict[str, dict[str, Any]] = {
    "material-3": {"hit_target_min_px": 48, "spacing_grid_px": 4},
    "apple-hig": {"hit_target_min_px": 44, "spacing_grid_px": 8},
    "fluent-2": {"hit_target_min_px": 32, "spacing_grid_px": 4},
    "polaris": {"hit_target_min_px": 44, "spacing_grid_px": 4},
    "carbon": {"hit_target_min_px": 40, "spacing_grid_px": 8},
    "atlassian": {"hit_target_min_px": 40, "spacing_grid_px": 4},
}


def _thresholds_for(system: str | None) -> dict[str, Any]:
    t = dict(DEFAULT_THRESHOLDS)
    if system and system in SYSTEM_THRESHOLDS:
        t.update(SYSTEM_THRESHOLDS[system])
    return t


# -- Helpers ---------------------------------------------------------------


def _font_px(comp: dict) -> float:
    fs = comp.get("styles", {}).get("fontSize", "")
    m = re.match(r"([\d.]+)px", fs)
    return float(m.group(1)) if m else 0.0


def _font_weight(comp: dict) -> int:
    fw = comp.get("styles", {}).get("fontWeight", "400")
    try:
        return int(float(fw))
    except (ValueError, TypeError):
        return 400


def _is_large_text(comp: dict, ctx: dict) -> bool:
    px = _font_px(comp)
    if px == 0:
        return False
    if px >= ctx["thresholds"]["large_text_min_px"]:
        return True
    return px >= ctx["thresholds"]["bold_text_min_px"] and _font_weight(comp) >= 700


INTERACTIVE_KINDS = {
    "button",
    "icon-button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "search-input",
    "select",
    "text-input",
    "textarea",
    "number-input",
    "slider",
    "tab",
    "menu-item",
}


# -- Predicates: hit targets ----------------------------------------------


@predicate(
    "hit-target.size",
    INTERACTIVE_KINDS,
)
def _hit_target_size(comp: dict, ctx: dict) -> Finding | None:
    target_system = ctx.get("target_system")
    if not target_system:
        # The generic WCAG AA predicate below owns the unnamed-review case.
        # Do not present a named-system preference as generic WCAG conformance.
        return None
    box = comp["box"]
    minimum = ctx["thresholds"]["hit_target_min_px"]
    if box["w"] >= minimum and box["h"] >= minimum:
        return None
    # Inline links inside a paragraph are exempt from the touch-target rule
    # (WCAG 2.2 carves them out explicitly).
    if comp["component_kind"] == "link" and box["h"] < minimum and box["w"] >= minimum:
        return None
    return Finding(
        predicate_id="hit-target.size",
        severity="P1",
        rule=f"{target_system} interactive target ≥{minimum}×{minimum}px",
        message=(
            f"{comp['component_kind']} measures {box['w']}×{box['h']}px; "
            f"{target_system} requires ≥{minimum}×{minimum}px."
        ),
        measured={"width": box["w"], "height": box["h"]},
        expected={"width": minimum, "height": minimum},
    )


# -- Predicates: contrast --------------------------------------------------


@predicate(
    "contrast.text",
    {
        "button",
        "link",
        "heading-1",
        "heading-2",
        "heading-3",
        "heading-4",
        "heading-5",
        "heading-6",
        "label",
        "tab",
        "menu-item",
        "list-item",
        "table-cell",
        "table-header-cell",
    },
)
def _contrast_text(comp: dict, ctx: dict) -> Finding | None:
    fg = comp.get("styles", {}).get("color", "")
    bg = _resolve_background(comp, ctx)
    ratio = contrast_ratio(fg, bg)
    if ratio is None:
        return None
    needed = (
        ctx["thresholds"]["contrast_text_aa_large"]
        if _is_large_text(comp, ctx)
        else ctx["thresholds"]["contrast_text_aa_normal"]
    )
    if ratio >= needed:
        return None
    return Finding(
        predicate_id="contrast.text",
        severity="P0",
        rule=f"Text contrast ≥{needed}:1 (WCAG 2.2 SC 1.4.3)",
        message=(
            f"Text contrast is {ratio:.2f}:1 against the resolved background; required: {needed}:1."
        ),
        measured={"foreground": fg, "background": bg, "ratio": round(ratio, 2)},
        expected={"ratio": needed},
    )


# -- Predicates: accessible names -----------------------------------------


@predicate("name.icon-button", {"icon-button"})
def _icon_button_has_name(comp: dict, ctx: dict) -> Finding | None:
    if (comp.get("name") or "").strip():
        return None
    return Finding(
        predicate_id="name.icon-button",
        severity="P0",
        rule="Icon-only button must have an accessible name (aria-label or visually hidden text)",
        message="Icon-only button has no accessible name; screen reader users cannot identify it.",
        measured={"name": ""},
    )


@predicate("name.link", {"link"})
def _link_has_name(comp: dict, ctx: dict) -> Finding | None:
    name = (comp.get("name") or "").strip()
    if name:
        if name.lower() in {"click here", "here", "more", "read more", "link"}:
            return Finding(
                predicate_id="name.link.generic",
                severity="P1",
                rule="Link text should describe the destination",
                message=f"Link text '{name}' is generic; use destination-descriptive text.",
                measured={"name": name},
            )
        return None
    return Finding(
        predicate_id="name.link",
        severity="P0",
        rule="Link must have accessible name",
        message="Link has no text content or aria-label.",
    )


@predicate("name.image", {"image"})
def _image_has_alt(comp: dict, ctx: dict) -> Finding | None:
    has_alt = comp.get("has_alt")
    name = (comp.get("name") or "").strip()
    if has_alt is False:
        return Finding(
            predicate_id="name.image",
            severity="P0",
            rule="<img> must have an alt attribute (use alt='' for decorative)",
            message="Image element has no alt attribute. Add alt text or alt='' if decorative.",
        )
    if name or has_alt is True:
        return None
    return Finding(
        predicate_id="name.image",
        severity="P1",
        rule="Image needs accessible name (alt text)",
        message="Image has no accessible name. Verify it's decorative; if not, add alt text.",
    )


# -- Predicates: focus visibility -----------------------------------------


@predicate("focus.visible", INTERACTIVE_KINDS)
def _focus_visible(comp: dict, ctx: dict) -> Finding | None:
    """Flag interactive elements that no user-authored :focus rule will style.

    We rely on stylesheet introspection done at capture time. If the page has
    any :focus-visible coverage and this element matches at least one rule,
    we trust the developer; otherwise flag.
    """
    if comp.get("has_user_focus_rule"):
        return None
    coverage = ctx.get("focus_coverage") or {}
    # If page has zero user focus rules at all, the global predicate handles
    # that; per-element flagging would just be noise.
    if (coverage.get("focus_visible_rules", 0) + coverage.get("total_focus_rules", 0)) == 0:
        return None
    return Finding(
        predicate_id="focus.visible",
        severity="P2",
        rule="Verify the interactive element has a visible focus indicator (WCAG 2.2 SC 2.4.7)",
        message=(
            "No detected authored :focus or :focus-visible rule matches this element. "
            "Native focus may still be conforming; verify the rendered focused state."
        ),
        measured={"has_user_focus_rule": False, "evidence_complete": False},
    )


@global_predicate
def _no_focus_styles_at_all(components: list[dict], ctx: dict) -> list[Finding]:
    """If the page has zero :focus rules anywhere, emit one global finding."""
    coverage = ctx.get("focus_coverage") or {}
    if (coverage.get("focus_visible_rules", 0) + coverage.get("total_focus_rules", 0)) > 0:
        return []
    if not any(c.get("component_kind") in INTERACTIVE_KINDS for c in components):
        return []
    return [
        Finding(
            predicate_id="focus.no-styles",
            severity="P2",
            rule="Verify native or authored focus indicators are visible (WCAG 2.2 SC 2.4.7)",
            message=(
                "No authored :focus or :focus-visible rules were detected. "
                "The browser's native outline may be sufficient; verify it visually."
            ),
            measured={"evidence_complete": False},
        )
    ]


# -- Predicates: forms ----------------------------------------------------


@predicate(
    "label.association", {"text-input", "textarea", "select", "search-input", "number-input"}
)
def _input_has_label(comp: dict, ctx: dict) -> Finding | None:
    if (comp.get("name") or "").strip():
        return None
    return Finding(
        predicate_id="label.association",
        severity="P0",
        rule="Form control must be labeled (label[for], aria-label, or aria-labelledby)",
        message="Form control has no associated label.",
    )


@predicate("input.autocomplete", {"text-input", "search-input", "number-input"})
def _input_autocomplete(comp: dict, ctx: dict) -> Finding | None:
    """Common name fields should specify autocomplete to help password managers."""
    type_ = (comp.get("type") or "").lower()
    name = (comp.get("name") or "").lower()
    auto = (comp.get("autocomplete") or "").lower()
    needs_auto = type_ in {"email", "tel", "password", "url"} or any(
        k in name for k in ("email", "phone", "address", "name", "zip", "postal", "city", "country")
    )
    if not needs_auto:
        return None
    if auto and auto != "off":
        return None
    return Finding(
        predicate_id="input.autocomplete",
        severity="P2",
        rule="Personal-info inputs should declare autocomplete (WCAG SC 1.3.5)",
        message=f"Input '{comp.get('name')}' looks like personal info but has no autocomplete attribute.",
        measured={"type": type_, "name": comp.get("name")},
    )


@predicate("input.numeric-mode", {"number-input", "text-input"})
def _input_numeric_mode(comp: dict, ctx: dict) -> Finding | None:
    """Numeric inputs on mobile should set inputmode for the right keyboard."""
    type_ = (comp.get("type") or "").lower()
    name = (comp.get("name") or "").lower()
    if type_ != "number" and not any(k in name for k in ("zip", "postal", "phone", "code")):
        return None
    if comp.get("inputmode"):
        return None
    return Finding(
        predicate_id="input.numeric-mode",
        severity="P2",
        rule="Numeric inputs should set inputmode for mobile keyboards",
        message="Numeric input has no inputmode attribute; mobile users get the alphanumeric keyboard.",
    )


# -- Predicates: heading hierarchy ----------------------------------------


@global_predicate
def _heading_hierarchy(components: list[dict], ctx: dict) -> list[Finding]:
    headings = [c for c in components if c["component_kind"].startswith("heading-")]
    headings = sorted(headings, key=lambda c: (c["box"]["y"], c["box"]["x"]))
    out: list[Finding] = []
    prev_level: int | None = None
    for h in headings:
        try:
            lvl = int(h["component_kind"].split("-")[1])
        except (ValueError, IndexError):
            continue
        if prev_level is not None and lvl - prev_level > 1:
            f = Finding(
                predicate_id="heading.hierarchy",
                severity="P1",
                rule="Heading levels should not skip (h2 -> h4 jumps a level)",
                message=f"Heading jumps from h{prev_level} to h{lvl}.",
                measured={"previous": prev_level, "current": lvl},
            )
            h.setdefault("findings", []).append(f.to_dict())
            out.append(f)
        prev_level = lvl
    return out


@predicate(
    "heading.empty", {"heading-1", "heading-2", "heading-3", "heading-4", "heading-5", "heading-6"}
)
def _heading_empty(comp: dict, ctx: dict) -> Finding | None:
    if (comp.get("text") or comp.get("name") or "").strip():
        return None
    return Finding(
        predicate_id="heading.empty",
        severity="P0",
        rule="Heading must not be empty",
        message="Heading element has no text content.",
    )


@global_predicate
def _multiple_h1(components: list[dict], ctx: dict) -> list[Finding]:
    h1s = [c for c in components if c["component_kind"] == "heading-1"]
    if len(h1s) <= 1:
        return []
    out: list[Finding] = []
    # Tag every h1 after the first; first one is the canonical.
    for c in h1s[1:]:
        f = Finding(
            predicate_id="heading.duplicate-h1",
            severity="P1",
            rule="Page should have a single h1",
            message=f"Multiple h1 elements detected ({len(h1s)} total).",
            measured={"count": len(h1s)},
        )
        c.setdefault("findings", []).append(f.to_dict())
        out.append(f)
    return out


# -- Predicates: link affordance ------------------------------------------


@predicate("link.distinguishable", {"link"})
def _link_distinguishable(comp: dict, ctx: dict) -> Finding | None:
    s = comp.get("styles", {})
    if s.get("textDecorationLine", "none") not in ("none", ""):
        return None
    fg = s.get("color", "")
    bg = _resolve_background(comp, ctx)
    ratio = contrast_ratio(fg, bg)
    if ratio is None or ratio >= 3.0:
        return Finding(
            predicate_id="link.distinguishable",
            severity="P1",
            rule="Links in body text need a non-color distinguisher (WCAG SC 1.4.1)",
            message="Link has no underline; verify it's distinguishable from surrounding text by more than color alone.",
            measured={"textDecorationLine": s.get("textDecorationLine", "none")},
        )
    return None


# -- Predicates: spacing rhythm -------------------------------------------


@predicate(
    "spacing.grid",
    {
        "button",
        "icon-button",
        "text-input",
        "textarea",
        "select",
        "search-input",
        "number-input",
        "checkbox",
        "radio",
    },
)
def _spacing_on_grid(comp: dict, ctx: dict) -> Finding | None:
    grid = ctx["thresholds"]["spacing_grid_px"]
    s = comp.get("styles", {})
    offenders = []
    for prop in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"):
        m = re.match(r"([\d.]+)px", s.get(prop, ""))
        if not m:
            continue
        v = float(m.group(1))
        if v > 0 and (v % grid) != 0:
            offenders.append((prop, v))
    if not offenders:
        return None
    return Finding(
        predicate_id="spacing.grid",
        severity="P2",
        rule=f"Spacing should align to a {grid}px grid",
        message=f"Off-grid padding: {', '.join(f'{p}={v}' for p, v in offenders)}",
        measured={"grid_px": grid, "offenders": [{"prop": p, "value": v} for p, v in offenders]},
    )


# -- Predicates: layout / overflow ----------------------------------------


@predicate("layout.off-canvas", set())  # registered manually to apply to all
def _off_canvas(comp: dict, ctx: dict) -> Finding | None:
    vw = comp.get("viewport_width") or 0
    if vw <= 0:
        return None
    box = comp["box"]
    if box["x"] < -2 or (box["x"] + box["w"]) > vw + 2:
        return Finding(
            predicate_id="layout.off-canvas",
            severity="P1",
            rule="Content must not extend past the viewport (WCAG SC 1.4.10 Reflow)",
            message=(
                f"Element extends from x={box['x']} to x={box['x'] + box['w']} "
                f"but viewport width is {vw}; horizontal scrolling required."
            ),
            measured={"x": box["x"], "right": box["x"] + box["w"], "viewport_width": vw},
        )
    return None


# Apply the off-canvas check to a useful subset of kinds (avoid noise on
# landmarks, which are intentionally edge-to-edge).
_OFF_CANVAS_KINDS = INTERACTIVE_KINDS | {
    "image",
    "list-item",
    "table-cell",
    "table-header-cell",
    "heading-1",
    "heading-2",
    "heading-3",
    "heading-4",
    "heading-5",
    "heading-6",
}


@global_predicate
def _tap_target_overlap(components: list[dict], ctx: dict) -> list[Finding]:
    """Flag interactive elements whose hit areas come too close to each other."""
    pad = ctx["thresholds"]["tap_overlap_pad_px"]
    interactives = [c for c in components if c.get("component_kind") in INTERACTIVE_KINDS]
    out: list[Finding] = []
    if len(interactives) < 2:
        return out

    # Sweep top-to-bottom and spatially bucket the active x intervals. The old
    # all-pairs loop made a long form or navigation inventory quadratic even
    # when every control was hundreds of pixels apart. Exact dx/dy checks stay
    # below, so bucketing changes only the work required, not the predicate.
    cell_size = max(float(pad), 32.0)
    ordered = sorted(
        enumerate(interactives),
        key=lambda item: (
            item[1]["box"]["y"],
            item[1]["box"]["x"],
            item[0],
        ),
    )
    active: set[int] = set()
    expiry_heap: list[tuple[float, int]] = []
    x_buckets: defaultdict[int, set[int]] = defaultdict(set)
    bucket_ranges: dict[int, range] = {}
    seen_pairs: set[tuple[int, int]] = set()

    def expanded_x_cells(box: dict) -> range:
        first = int((box["x"] - pad) // cell_size)
        last = int((box["x"] + box["w"] + pad) // cell_size)
        return range(first, last + 1)

    def occupied_x_cells(box: dict) -> range:
        first = int(box["x"] // cell_size)
        last = int((box["x"] + box["w"]) // cell_size)
        return range(first, last + 1)

    for current_pos, current in ordered:
        current_box = current["box"]
        current_top = current_box["y"]

        while expiry_heap and expiry_heap[0][0] <= current_top:
            _, expired_pos = heapq.heappop(expiry_heap)
            if expired_pos not in active:
                continue
            active.remove(expired_pos)
            for cell in bucket_ranges.pop(expired_pos):
                x_buckets[cell].discard(expired_pos)
                if not x_buckets[cell]:
                    del x_buckets[cell]

        candidate_positions: set[int] = set()
        for cell in occupied_x_cells(current_box):
            candidate_positions.update(x_buckets.get(cell, ()))

        for previous_pos in sorted(candidate_positions):
            first_pos, second_pos = sorted((previous_pos, current_pos))
            if (first_pos, second_pos) in seen_pairs:
                continue
            seen_pairs.add((first_pos, second_pos))
            a = interactives[first_pos]
            b = interactives[second_pos]
            ab = a["box"]
            bb = b["box"]
            dx = max(0, max(ab["x"], bb["x"]) - min(ab["x"] + ab["w"], bb["x"] + bb["w"]))
            dy = max(0, max(ab["y"], bb["y"]) - min(ab["y"] + ab["h"], bb["y"] + bb["h"]))
            if dx >= pad or dy >= pad:
                continue
            if (
                dx == 0
                and dy == 0
                # Likely intentional adjacency (segmented control). Only flag
                # when both elements are small enough to be ambiguous.
                and min(ab["w"], ab["h"], bb["w"], bb["h"]) >= 32
            ):
                continue
            f = Finding(
                predicate_id="tap-target.overlap",
                severity="P1",
                rule=f"Adjacent interactive targets should be ≥{pad}px apart",
                message=(
                    f"{a['component_kind']} (idx {a['index']}) and "
                    f"{b['component_kind']} (idx {b['index']}) are within {pad}px "
                    f"(dx={dx}, dy={dy})."
                ),
                measured={"dx": dx, "dy": dy, "pad": pad},
            )
            a.setdefault("findings", []).append(f.to_dict())
            out.append(f)

        cells = expanded_x_cells(current_box)
        bucket_ranges[current_pos] = cells
        for cell in cells:
            x_buckets[cell].add(current_pos)
        active.add(current_pos)
        heapq.heappush(
            expiry_heap,
            (current_box["y"] + current_box["h"] + pad, current_pos),
        )
    return out


# -- Predicates: dialog ----------------------------------------------------


@predicate("dialog.aria-modal", {"landmark-dialog"})
def _dialog_aria_modal(comp: dict, ctx: dict) -> Finding | None:
    if comp.get("aria_modal"):
        return None
    return Finding(
        predicate_id="dialog.aria-modal",
        severity="P1",
        rule="Modal dialogs should set aria-modal='true'",
        message="<dialog> element is missing aria-modal='true'; assistive tech may treat content behind it as available.",
    )


# -- Predicates: visual-DOM cross-check -----------------------------------


@predicate(
    "visual-dom.background-mismatch",
    {
        "button",
        "icon-button",
        "text-input",
        "textarea",
        "select",
        "search-input",
        "number-input",
    },
)
def _visual_dom_bg(comp: dict, ctx: dict) -> Finding | None:
    """Compare declared background to the pixel actually rendered at the center.

    Catches z-index covers, broken background-image fallbacks, gradient overlays
    that the styles-only check misses.
    """
    sampler = ctx.get("pixel_sampler")
    if sampler is None:
        return None
    declared = comp.get("styles", {}).get("backgroundColor", "")
    declared_p = parse_color(declared)
    sampled = sampler(comp)
    sampled_p = parse_color(sampled) if sampled else None
    if not declared_p or not sampled_p:
        return None
    # If declared is transparent we have nothing to compare.
    if declared_p[3] < 0.05:
        return None
    delta = sum(abs(declared_p[i] * 255 - sampled_p[i] * 255) for i in range(3))
    threshold = ctx["thresholds"]["visual_dom_color_delta"]
    if delta < threshold:
        return None
    return Finding(
        predicate_id="visual-dom.background-mismatch",
        severity="P1",
        rule="Rendered background should match declared CSS",
        message=(
            f"Declared backgroundColor={declared} but rendered pixel sampled at center is {sampled} "
            f"(channel-distance {int(delta)}>{threshold}). Likely an overlay, z-index cover, or background-image."
        ),
        measured={"declared": declared, "sampled": sampled, "delta": int(delta)},
    )


# -- Predicates: link purpose (extended) ----------------------------------

# Extension of WCAG 2.4.4: catch broader generic-link patterns beyond the
# four phrases already covered by `name.link.generic`. axe-core's
# `link-name` rule also flags single-word "go", "view", "details", and
# "learn more" patterns without surrounding context.
# https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context.html
_GENERIC_LINK_PHRASES: frozenset[str] = frozenset(
    {
        # Already covered by existing name.link.generic; included here for
        # completeness, but we skip them so we don't double-fire.
        # "click here", "here", "more", "read more", "link",
        "learn more",
        "view",
        "view more",
        "details",
        "see details",
        "see more",
        "more info",
        "more information",
        "find out more",
        "continue",
        "go",
        "this link",
        "this page",
        "this article",
        "download",
    }
)

_ALREADY_FLAGGED_GENERIC: frozenset[str] = frozenset(
    {"click here", "here", "more", "read more", "link"}
)


@predicate("name.link.context", {"link"})
def _link_purpose_from_context(comp: dict, ctx: dict) -> Finding | None:
    """WCAG 2.4.4 Link Purpose (In Context) — Level A.

    Flag link text that is too generic to communicate the destination
    without surrounding context. This extends the existing
    `name.link.generic` check with the additional phrases that axe-core
    and the WCAG 2.4.4 Understanding doc call out as common failures.
    https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context.html
    """
    raw = (comp.get("name") or comp.get("text") or "").strip().lower()
    if not raw:
        # `name.link` already handles missing names.
        return None
    # Strip surrounding punctuation that doesn't add meaning.
    normalised = re.sub(r"^[^\w]+|[^\w]+$", "", raw)
    if normalised in _ALREADY_FLAGGED_GENERIC:
        # The existing name.link.generic predicate covers these. Skip to
        # avoid double-firing on the same component.
        return None
    if normalised not in _GENERIC_LINK_PHRASES:
        return None
    return Finding(
        predicate_id="name.link.context",
        severity="P1",
        rule="Link text should communicate destination without surrounding context (WCAG 2.2 SC 2.4.4)",
        message=(
            f"Link text '{comp.get('name') or comp.get('text')}' is generic; users navigating "
            "by link list (screen readers) cannot tell where it leads."
        ),
        measured={"name": comp.get("name") or comp.get("text")},
    )


# -- Predicates: non-text contrast (WCAG 1.4.11) --------------------------


@predicate(
    "contrast.non-text",
    {"text-input", "textarea", "select", "search-input", "number-input"},
)
def _non_text_contrast(comp: dict, ctx: dict) -> Finding | None:
    """WCAG 2.2 SC 1.4.11 Non-text Contrast — Level AA.

    UI component boundaries (form input borders here) need ≥3:1 contrast
    against the adjacent background so users with low vision can locate
    the control. Disabled controls are exempt per the SC.
    https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html
    """
    if comp.get("disabled") or comp.get("aria_disabled"):
        return None
    styles = comp.get("styles", {})
    # Pick the strongest visible border: any side with a non-zero width
    # and a non-transparent color counts. If `borderColor` is set as a
    # shorthand we still see it via the per-side properties.
    border_color = (
        styles.get("borderTopColor")
        or styles.get("borderColor")
        or styles.get("borderRightColor")
        or styles.get("borderBottomColor")
        or styles.get("borderLeftColor")
        or ""
    )
    # If no border declared we can't measure non-text contrast for this
    # input via CSS alone; skip rather than guess.
    if not border_color:
        return None
    parsed = parse_color(border_color)
    if parsed is None or parsed[3] < 0.05:
        # Fully transparent border — nothing to evaluate.
        return None
    bg = _resolve_background(comp, ctx)
    ratio = contrast_ratio(border_color, bg)
    if ratio is None:
        return None
    needed = ctx["thresholds"]["contrast_nontext_aa"]
    if ratio >= needed:
        return None
    return Finding(
        predicate_id="contrast.non-text",
        severity="P1",
        rule=f"Non-text UI components need ≥{needed}:1 contrast (WCAG 2.2 SC 1.4.11)",
        message=(
            f"Input border contrast is {ratio:.2f}:1 against the resolved background; "
            f"users with low vision may not see the control's boundary."
        ),
        measured={"border": border_color, "background": bg, "ratio": round(ratio, 2)},
        expected={"ratio": needed},
    )


# -- Predicates: label in name (WCAG 2.5.3) -------------------------------


@predicate(
    "label-in-name",
    {
        "button",
        "link",
        "checkbox",
        "radio",
        "switch",
        "tab",
        "menu-item",
    },
)
def _label_in_name(comp: dict, ctx: dict) -> Finding | None:
    """WCAG 2.2 SC 2.5.3 Label in Name — Level A.

    When a control has a visible text label, the accessible name must
    contain that visible text. Otherwise speech-input users who say the
    visible label cannot activate the control.
    https://www.w3.org/WAI/WCAG22/Understanding/label-in-name.html
    """
    visible = (comp.get("text") or "").strip()
    accessible = (comp.get("name") or "").strip()
    if not visible:
        # No visible label means nothing to compare. `name.icon-button`
        # already handles the icon-only-button case.
        return None
    if not accessible:
        # The dedicated `name.*` predicates handle absent accessible names.
        return None

    # Case and surrounding punctuation are explicitly allowed to differ
    # per the SC. Normalise both sides to a stable form: lowercase, strip
    # punctuation, collapse internal whitespace.
    def _norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^\w\s]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    v_norm = _norm(visible)
    a_norm = _norm(accessible)
    if not v_norm or not a_norm:
        return None
    if v_norm in a_norm:
        return None
    return Finding(
        predicate_id="label-in-name",
        severity="P1",
        rule="Accessible name must contain visible label text (WCAG 2.2 SC 2.5.3)",
        message=(
            f"Visible label '{visible}' is not contained in the accessible name '{accessible}'; "
            "speech-input users saying the visible label cannot activate this control."
        ),
        measured={"visible": visible, "accessible": accessible},
    )


# -- Predicates: focus order (WCAG 2.4.3) ---------------------------------


@predicate("focus.positive-tabindex", INTERACTIVE_KINDS)
def _focus_positive_tabindex(comp: dict, ctx: dict) -> Finding | None:
    """WCAG 2.2 SC 2.4.3 Focus Order — Level A (Failure F44).

    Positive `tabindex` values (>0) force tab order to diverge from DOM
    order. The WCAG Understanding doc names this as a common failure,
    and the WAI ARIA APG explicitly recommends only `tabindex="0"` or
    `tabindex="-1"`. We flag any positive value as a maintenance hazard.
    https://www.w3.org/WAI/WCAG22/Understanding/focus-order.html
    https://www.w3.org/TR/WCAG20-TECHS/F44.html
    """
    ti = comp.get("tab_index", 0)
    try:
        ti_int = int(ti)
    except (TypeError, ValueError):
        return None
    if ti_int <= 0:
        return None
    return Finding(
        predicate_id="focus.positive-tabindex",
        severity="P1",
        rule="Avoid positive tabindex; it overrides DOM order (WCAG 2.2 SC 2.4.3, Failure F44)",
        message=(
            f"Element has tabindex={ti_int}; this forces focus order to diverge from DOM "
            "order, breaks naturally as the page evolves, and confuses assistive tech."
        ),
        measured={"tab_index": ti_int},
    )


# -- Predicates: target size (WCAG 2.5.8 AA, 24x24) -----------------------


@predicate("target.size-aa", INTERACTIVE_KINDS)
def _target_size_aa(comp: dict, ctx: dict) -> Finding | None:
    """WCAG 2.2 SC 2.5.8 Target Size (Minimum) - Level AA, 24x24 CSS px.

    When a named target system is selected, `hit-target.size` independently
    enforces that system's preferred minimum. This predicate owns the generic
    WCAG 2.2 AA floor and also runs when no design system was selected.

    Inline links inside body text are explicitly exempted by the SC.
    https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
    """
    box = comp.get("box", {})
    w = box.get("w", 0)
    h = box.get("h", 0)
    if w >= 24 and h >= 24:
        return None
    # Inline-text-link exception (same carve-out as hit-target.size).
    if comp.get("component_kind") == "link" and h < 24 and w >= 24:
        return None
    # If a named-system rule has already fired, this AA-level check would
    # duplicate the finding. Skip to keep the report uncluttered.
    if any(f.get("predicate_id") == "hit-target.size" for f in comp.get("findings", [])):
        return None
    return Finding(
        predicate_id="target.size-aa",
        severity="P1",
        rule="Interactive target ≥24×24 CSS px (WCAG 2.2 SC 2.5.8 AA)",
        message=(
            f"{comp.get('component_kind')} measures {w}×{h}px; WCAG 2.2 AA requires "
            "≥24×24px so users with motor impairments can activate it reliably."
        ),
        measured={"width": w, "height": h},
        expected={"width": 24, "height": 24},
    )


# -- Predicates: dialog focus management (WAI ARIA APG) -------------------


@global_predicate
def _dialog_focus_trap_affordance(components: list[dict], ctx: dict) -> list[Finding]:
    """WAI ARIA Authoring Practices: modal dialogs must trap focus.

    Static analysis can't prove focus is trapped, but a missing focusable
    child is a strong signal that focus management is broken — the dialog
    has no place to put initial focus and Tab will escape to the page
    behind it.
    https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
    """
    out: list[Finding] = []
    dialogs = [c for c in components if c.get("component_kind") == "landmark-dialog"]
    if not dialogs:
        return out
    for dlg in dialogs:
        idx = dlg["index"]
        # A focusable descendant is one whose `parent_index` chain leads
        # back to the dialog index *and* whose component_kind is
        # interactive (or it's an explicit tabindex>=0 element, but we
        # don't track that for non-interactive comps).
        focusable = [
            c
            for c in components
            if c is not dlg
            and c.get("component_kind") in INTERACTIVE_KINDS
            and _is_descendant_of(c, idx, components)
        ]
        if focusable:
            continue
        f = Finding(
            predicate_id="dialog.focus-trap-affordance",
            severity="P1",
            rule="Modal dialogs must contain a focusable element to trap focus (WAI ARIA APG)",
            message=(
                "Dialog contains no focusable child; opening it will leave focus on "
                "page content behind the modal and assistive tech cannot navigate it."
            ),
            measured={"focusable_descendants": 0},
        )
        dlg.setdefault("findings", []).append(f.to_dict())
        out.append(f)
    return out


def _is_descendant_of(comp: dict, ancestor_index: int, all_comps: list[dict]) -> bool:
    """Walk the parent_index chain looking for `ancestor_index`."""
    seen: set[int] = set()
    cur: dict | None = comp
    while cur:
        current_index = cur.get("index")
        if not isinstance(current_index, int) or current_index in seen:
            break
        seen.add(current_index)
        parent = cur.get("parent_index", -1)
        if parent == ancestor_index:
            return True
        if parent < 0:
            return False
        cur = next((c for c in all_comps if c.get("index") == parent), None)
    return False


@global_predicate
def _dialog_initial_focus(components: list[dict], ctx: dict) -> list[Finding]:
    """WAI ARIA APG: initial focus in a modal should not be the close button.

    When the only focusable thing in a dialog is the close button, every
    user who opens the dialog with the keyboard immediately closes it
    again if they press Enter. This is a common usability defect that
    static analysis can detect by counting focusable descendants and
    classifying the first one.

    Heuristic: if the dialog has exactly one focusable descendant and
    that descendant's name matches a close/dismiss pattern, flag.
    https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
    """
    out: list[Finding] = []
    dialogs = [c for c in components if c.get("component_kind") == "landmark-dialog"]
    if not dialogs:
        return out
    close_pat = re.compile(r"\b(close|dismiss|cancel|x)\b", re.IGNORECASE)
    for dlg in dialogs:
        idx = dlg["index"]
        focusable = [
            c
            for c in components
            if c is not dlg
            and c.get("component_kind") in INTERACTIVE_KINDS
            and _is_descendant_of(c, idx, components)
        ]
        if len(focusable) != 1:
            continue
        only = focusable[0]
        name = (only.get("name") or only.get("text") or "").strip()
        if not name or not close_pat.search(name):
            continue
        f = Finding(
            predicate_id="dialog.initial-focus",
            severity="P2",
            rule="Modal dialog's only focusable element shouldn't be the close button (WAI ARIA APG)",
            message=(
                f"Dialog has a single focusable descendant labelled '{name}'; opening the "
                "dialog and pressing Enter will immediately close it. Add a primary action "
                "or move initial focus to the heading."
            ),
            measured={"focusable_count": 1, "first_focusable_name": name},
        )
        dlg.setdefault("findings", []).append(f.to_dict())
        out.append(f)
    return out


# -- Predicates: landmarks (axe-core landmark-one-main) -------------------


@global_predicate
def _landmark_one_main(components: list[dict], ctx: dict) -> list[Finding]:
    """axe-core `landmark-one-main` / WCAG 1.3.1: exactly one main landmark.

    A `<main>` element is the primary landmark screen-reader users jump
    to with the "main content" shortcut. Zero or multiple main landmarks
    each have different failure modes:
      - 0 main landmarks: no skip target for assistive tech.
      - 2+ main landmarks: ambiguous which one is the primary content.
    https://dequeuniversity.com/rules/axe/4.10/landmark-one-main
    """
    out: list[Finding] = []
    mains = [c for c in components if c.get("component_kind") == "landmark-main"]
    if len(mains) == 1:
        return out
    if len(mains) == 0:
        # Only flag if the page has *any* substantial content — we don't
        # want to fire on fragments / iframes captured in isolation.
        if not any(c.get("component_kind") in ("heading-1", "heading-2") for c in components):
            return out
        out.append(
            Finding(
                predicate_id="landmark.one-main",
                severity="P1",
                rule="Page should have exactly one <main> landmark (axe landmark-one-main)",
                message=(
                    "No <main> landmark found; screen-reader users cannot skip directly to "
                    "the primary content."
                ),
                measured={"main_count": 0},
            )
        )
        return out
    # 2+ main landmarks.
    for c in mains[1:]:
        f = Finding(
            predicate_id="landmark.one-main",
            severity="P1",
            rule="Page should have exactly one <main> landmark (axe landmark-one-main)",
            message=f"Multiple <main> landmarks detected ({len(mains)} total).",
            measured={"main_count": len(mains)},
        )
        c.setdefault("findings", []).append(f.to_dict())
        out.append(f)
    return out


@global_predicate
def _landmark_banner_contentinfo_once(components: list[dict], ctx: dict) -> list[Finding]:
    """axe-core `landmark-banner-is-top-level` / `landmark-no-duplicate-*`.

    Banner (`<header>` at top level) and contentinfo (`<footer>` at top
    level) landmarks should appear at most once per page. Duplicates
    confuse the screen-reader landmark menu.
    https://dequeuniversity.com/rules/axe/4.10/landmark-no-duplicate-banner
    https://dequeuniversity.com/rules/axe/4.10/landmark-no-duplicate-contentinfo
    """
    out: list[Finding] = []
    for role, label in (
        ("landmark-banner", "<header> / banner"),
        ("landmark-contentinfo", "<footer> / contentinfo"),
    ):
        instances = [c for c in components if c.get("component_kind") == role]
        if len(instances) <= 1:
            continue
        for c in instances[1:]:
            f = Finding(
                predicate_id="landmark.duplicate",
                severity="P2",
                rule="Top-level banner/contentinfo landmarks should be unique (axe landmark-no-duplicate-*)",
                message=(
                    f"Multiple {label} landmarks detected ({len(instances)} total); "
                    "screen-reader landmark navigation becomes ambiguous."
                ),
                measured={"role": role, "count": len(instances)},
            )
            c.setdefault("findings", []).append(f.to_dict())
            out.append(f)
    return out


# -- Predicates: form required-without-indicator --------------------------


@predicate(
    "form.required-indicator",
    {"text-input", "textarea", "select", "search-input", "number-input"},
)
def _required_visible_indicator(comp: dict, ctx: dict) -> Finding | None:
    """Heuristic: a required input should advertise its required state visibly.

    WCAG SC 3.3.2 (Labels or Instructions, Level A) calls for instructions
    when input is required. Common patterns: an asterisk in the label, a
    "(required)" suffix, or a "required" word in the accessible name.

    We approximate this by looking for an asterisk or the word "required"
    in the accessible name — if a field is required but its name has
    neither, sighted users won't know until validation fires.
    https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions.html
    """
    if not comp.get("required"):
        return None
    name = (comp.get("name") or comp.get("text") or "").strip()
    if not name:
        # `label.association` already handles missing names.
        return None
    lowered = name.lower()
    if "*" in name or "required" in lowered or "(req" in lowered:
        return None
    return Finding(
        predicate_id="form.required-indicator",
        severity="P2",
        rule="Required form fields should advertise their required state visibly (WCAG 2.2 SC 3.3.2)",
        message=(
            f"Input '{name}' is required but its label contains no '*', '(required)', "
            "or 'required' indicator. Sighted users won't know it's required until they "
            "submit."
        ),
        measured={"name": name, "required": True},
    )


# -- Pixel sampler --------------------------------------------------------


def _build_pixel_sampler(captures_dir: Path):
    """Return a callable comp -> 'rgb(r,g,b)' string by sampling the screenshot.

    Returns None if Pillow isn't installed (so callers can skip pixel-aware
    predicates gracefully).

    Sampling strategy: take 9 points (4 corners, 4 edge midpoints, 1 center)
    and return the mode (most-frequent color). This avoids two failure modes:

    - Heading text: center sample lands inside a glyph, returning text color
      instead of background.
    - Iconography: center sample lands on a transparent stroke gap, returning
      whatever is behind the button (often page background, not button fill).

    For both cases, the corner/edge samples vote for the actual fill color.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None

    cache: dict[str, Any] = {}
    captures_root = captures_dir.resolve()

    def sampler(comp: dict) -> str | None:
        rel = comp.get("capture_path")
        if not rel:
            return None
        path = (captures_dir / rel).resolve()
        try:
            path.relative_to(captures_root)
        except ValueError:
            logger.warning("blocked path traversal: %s outside %s", path, captures_root)
            return None
        if not path.exists():
            return None
        if str(path) not in cache:
            try:
                with Image.open(path) as im:
                    cache[str(path)] = im.convert("RGB")
            except (OSError, UnidentifiedImageError):
                logger.warning(
                    "failed to load screenshot for sampling: %s",
                    path,
                    exc_info=True,
                )
                cache[str(path)] = None
        img = cache[str(path)]
        if img is None:
            return None
        scale = float(comp.get("device_pixel_ratio") or 1.0)
        b = comp["box"]
        x0 = b["x"] * scale
        y0 = b["y"] * scale
        w = b["w"] * scale
        h = b["h"] * scale
        # 9 sample offsets: insets by 15% so we stay inside the box but avoid
        # the very edges where antialiasing happens.
        inset_x = w * 0.15
        inset_y = h * 0.15
        offsets = [
            (inset_x, inset_y),  # top-left
            (w / 2, inset_y),  # top-mid
            (w - inset_x, inset_y),  # top-right
            (inset_x, h / 2),  # mid-left
            (w / 2, h / 2),  # center
            (w - inset_x, h / 2),  # mid-right
            (inset_x, h - inset_y),  # bottom-left
            (w / 2, h - inset_y),  # bottom-mid
            (w - inset_x, h - inset_y),  # bottom-right
        ]
        from collections import Counter

        votes: Counter = Counter()
        for ox, oy in offsets:
            px = int(x0 + ox)
            py = int(y0 + oy)
            if px < 0 or py < 0 or px >= img.width or py >= img.height:
                continue
            try:
                r, g, bl = img.getpixel((px, py))
            except (IndexError, OSError, ValueError):
                logger.warning(
                    "failed to sample pixel (%d, %d) from %s",
                    px,
                    py,
                    img,
                    exc_info=True,
                )
                continue
            votes[(r, g, bl)] += 1
        if not votes:
            return None
        # Text can occupy enough sample points to tie the true background,
        # especially in large multi-line headings. A Counter tie preserves
        # insertion order and can therefore return the foreground as a 1:1
        # "background". When another candidate exists, exclude pixels close
        # to the computed foreground before selecting the mode.
        foreground = parse_color(comp.get("styles", {}).get("color", ""))
        if foreground is not None:
            fg_rgb = tuple(round(channel * 255) for channel in foreground[:3])
            background_votes: Counter = Counter(
                {
                    color: count
                    for color, count in votes.items()
                    if sum(abs(color[index] - fg_rgb[index]) for index in range(3)) >= 36
                }
            )
            if background_votes:
                votes = background_votes
        (r, g, bl), _ = votes.most_common(1)[0]
        return f"rgb({r}, {g}, {bl})"

    return sampler


# -- Runner ---------------------------------------------------------------


def analyze_components(
    components: list[dict],
    target_system: str | None = None,
    pixel_sampler: Callable[[dict], str | None] | None = None,
    focus_coverage: dict | None = None,
) -> dict[str, Any]:
    """Run all applicable predicates against every component."""
    thresholds = _thresholds_for(target_system)
    ctx: dict[str, Any] = {
        "thresholds": thresholds,
        "target_system": target_system,
        "all_comps": components,
        "pixel_sampler": pixel_sampler,
        "focus_coverage": focus_coverage or {},
    }

    counts: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0}
    by_predicate: dict[str, int] = {}
    global_findings: list[dict[str, Any]] = []

    def _emit(comp: dict | None, finding: Finding) -> None:
        if comp is not None:
            comp.setdefault("findings", []).append(finding.to_dict())
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
        by_predicate[finding.predicate_id] = by_predicate.get(finding.predicate_id, 0) + 1

    # Per-component predicates
    for comp in components:
        kind = comp["component_kind"]
        for pid, applies_to, fn in PREDICATES:
            # `layout.off-canvas` registered with empty applies_to; route via _OFF_CANVAS_KINDS.
            if pid == "layout.off-canvas":
                if kind not in _OFF_CANVAS_KINDS:
                    continue
            elif kind not in applies_to:
                continue
            try:
                finding = fn(comp, ctx)
            except Exception as e:  # don't let a buggy predicate kill the run
                finding = Finding(
                    predicate_id=f"{pid}.error",
                    severity="P2",
                    rule="harness internal",
                    message=f"predicate {pid} raised {type(e).__name__}: {e}",
                )
            if finding:
                _emit(comp, finding)

    # Global predicates (already mutate components themselves)
    for global_fn in GLOBAL_PREDICATES:
        try:
            findings = global_fn(components, ctx)
        except Exception as e:
            findings = [
                Finding(
                    predicate_id="global.error",
                    severity="P2",
                    rule="harness internal",
                    message=(
                        f"global predicate {global_fn.__name__} raised {type(e).__name__}: {e}"
                    ),
                )
            ]
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
            by_predicate[f.predicate_id] = by_predicate.get(f.predicate_id, 0) + 1
            finding_dict = f.to_dict()
            attached_to_component = any(
                finding_dict in (component.get("findings", []) or []) for component in components
            )
            if not attached_to_component:
                global_findings.append(finding_dict)

    # Defense-in-depth: predicate messages routinely interpolate component
    # text (e.g. ``f"Link text '{name}' is generic"``). Component text was
    # already sanitized at decompose time, but a buggy or future predicate
    # might pull untrusted content from somewhere we haven't audited. Run a
    # final sweep so every finding the report consumer sees is clean.
    for comp in components:
        for f in comp.get("findings", []):
            sanitize_finding(f)
    for global_finding_dict in global_findings:
        sanitize_finding(global_finding_dict)

    return {
        "components": components,
        "global_findings": global_findings,
        "summary": {
            "target_system": target_system,
            "thresholds": thresholds,
            "counts": counts,
            "by_predicate": by_predicate,
            "total_components": len(components),
            "components_with_findings": sum(1 for c in components if c.get("findings")),
            "global_findings": len(global_findings),
            "focus_coverage": focus_coverage or {},
        },
    }


def analyze_file(
    components_path: Path,
    target_system: str | None = None,
    captures_dir: Path | None = None,
    focus_coverage: dict | None = None,
) -> dict[str, Any]:
    """Convenience wrapper used by the CLI.

    If `captures_dir` is provided, builds a pixel sampler so pixel-aware
    predicates can run against the screenshots.
    """
    components = json.loads(components_path.read_text())
    sampler = _build_pixel_sampler(captures_dir) if captures_dir else None
    return analyze_components(
        components,
        target_system=target_system,
        pixel_sampler=sampler,
        focus_coverage=focus_coverage,
    )
