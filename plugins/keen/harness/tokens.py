"""Tokens stage: extract the *de facto* design tokens used in a capture.

Two input modes:

1. From a captures directory — uses the structured DOM dumps under dom/.
   Computed styles give exact colors, spacing, font sizes. No clustering needed.
2. From an image alone — fall back to pixel sampling. Less accurate but useful
   for screenshots without DOM dumps (Figma exports, competitor analysis).
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from harness import _artifacts as artifacts_mod
from harness import _safeio as safeio_mod
from harness._sanitize import sanitize_untrusted_text
from harness.colors import parse_color

logger = logging.getLogger("keen.tokens")


_PX = re.compile(r"([\d.]+)px")


def _to_px(v: str) -> float | None:
    m = _PX.match(v or "")
    return float(m.group(1)) if m else None


# --- DOM-based extraction (preferred) -------------------------------------


def _iter_dom_files(captures_dir: Path):
    """Find dom-*.json under captures_dir/dom/ first, fall back to flat layout.

    Glob depth is bounded to 2 levels to avoid pathological deep recursion when
    the captures directory accidentally points somewhere large.
    """
    sub = captures_dir / "dom"
    if sub.exists():
        yield from sorted(sub.glob("*.json"))
        return
    patterns: list = [
        captures_dir.glob("dom/*.json"),
        captures_dir.glob("dom-*.json"),
        captures_dir.glob("*/dom/*.json"),
        captures_dir.glob("*/dom-*.json"),
    ]
    seen: set[Path] = set()
    found: list[Path] = []
    for it in patterns:
        for p in it:
            if p not in seen:
                seen.add(p)
                found.append(p)
    yield from sorted(found)


def extract_from_captures(captures_dir: Path) -> dict[str, Any]:
    """Walk every dom-*.json under captures_dir and aggregate token usage."""
    captures_dir = Path(captures_dir)
    evidence = artifacts_mod.capture_evidence(captures_dir)
    colors: Counter[str] = Counter()
    bg_colors: Counter[str] = Counter()
    font_sizes: Counter[float] = Counter()
    font_families: Counter[str] = Counter()
    font_weights: Counter[int] = Counter()
    border_radii: Counter[float] = Counter()
    spacings: Counter[float] = Counter()
    text_samples: Counter[tuple[str, str, float, int, str]] = Counter()

    n_elements = 0
    for dom_path in evidence.dom_paths:
        data = artifacts_mod.read_dom_document(dom_path)
        for el in data.get("elements", []):
            s = el.get("styles", {})
            if s.get("display") == "none" or str(s.get("visibility") or "").lower() in {
                "hidden",
                "collapse",
            }:
                continue
            try:
                effective_opacity = float(el.get("effectiveOpacity", s.get("opacity", "1")))
            except (TypeError, ValueError):
                effective_opacity = 1.0
            if effective_opacity <= 0:
                continue
            n_elements += 1
            if s.get("color"):
                colors[s["color"]] += 1
            if s.get("backgroundColor"):
                bg_colors[s["backgroundColor"]] += 1
            fs = _to_px(s.get("fontSize", ""))
            if fs:
                font_sizes[fs] += 1
            if s.get("fontFamily"):
                font_families[s["fontFamily"]] += 1
            with contextlib.suppress(ValueError, TypeError):
                font_weights[int(float(s.get("fontWeight", "400")))] += 1
            for prop in (
                "borderTopLeftRadius",
                "borderTopRightRadius",
                "borderBottomLeftRadius",
                "borderBottomRightRadius",
            ):
                v = _to_px(s.get(prop, ""))
                if v is not None:
                    border_radii[v] += 1
            for prop in (
                "paddingTop",
                "paddingRight",
                "paddingBottom",
                "paddingLeft",
                "marginTop",
                "marginRight",
                "marginBottom",
                "marginLeft",
                "gap",
                "rowGap",
                "columnGap",
            ):
                v = _to_px(s.get(prop, ""))
                if v is not None and v > 0:
                    spacings[v] += 1

            # Preserve a small, sanitized set of real interface strings for
            # typography proof. This keeps system previews grounded in the
            # captured product instead of inventing portfolio-style slogans.
            # Limit the eligible tags to self-contained reading/action units;
            # nested spans and strong tags otherwise duplicate fragments.
            tag = str(el.get("tag") or "").lower()
            if tag not in {
                "a",
                "button",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "label",
                "p",
                "td",
                "th",
            }:
                continue
            if (
                el.get("ariaHidden")
                or s.get("display") == "none"
                or str(s.get("visibility") or "").lower() in {"hidden", "collapse"}
            ):
                continue
            try:
                if float(s.get("opacity", "1")) <= 0:
                    continue
            except (TypeError, ValueError):
                pass
            if el.get("hidden"):
                continue
            box = el.get("box") if isinstance(el.get("box"), dict) else {}
            if float(box.get("w") or 0) <= 0 or float(box.get("h") or 0) <= 0:
                continue
            text = sanitize_untrusted_text(el.get("text"), max_len=120)
            if len(text) < 3 or sum(ch.isalpha() for ch in text) < 2:
                continue
            family = str(s.get("fontFamily") or "").strip()
            size = _to_px(str(s.get("fontSize") or ""))
            if not family or size is None:
                continue
            try:
                weight = int(float(s.get("fontWeight", "400")))
            except (TypeError, ValueError):
                weight = 400
            text_samples[(text, family, size, weight, tag)] += 1

    def _topn(c: Counter, n: int = 24) -> list[dict]:
        return [{"value": k, "count": v} for k, v in c.most_common(n)]

    ranked_samples = sorted(
        text_samples.items(),
        key=lambda item: (
            -int(item[0][4].startswith("h")),
            -item[0][2],
            -item[1],
            len(item[0][0]),
            item[0][0].casefold(),
        ),
    )[:24]

    return {
        "source": "dom",
        "elements_examined": n_elements,
        "colors": {
            "foreground": _topn(colors),
            "background": _topn(bg_colors),
        },
        "type": {
            "sizes_px": [{"value": k, "count": v} for k, v in sorted(font_sizes.items())],
            "families": _topn(font_families, 8),
            "weights": [{"value": k, "count": v} for k, v in sorted(font_weights.items())],
            "text_samples": [
                {
                    "text": key[0],
                    "family": key[1],
                    "size_px": key[2],
                    "weight": key[3],
                    "tag": key[4],
                    "capture_count": count,
                    "source": "captured-ui",
                }
                for key, count in ranked_samples
            ],
        },
        "shape": {
            "border_radii_px": [{"value": k, "count": v} for k, v in sorted(border_radii.items())],
        },
        "spacing": {
            "values_px": [{"value": k, "count": v} for k, v in sorted(spacings.items())],
        },
        "diagnostics": _diagnostics(font_sizes, spacings, colors, bg_colors),
    }


def _diagnostics(
    font_sizes: Counter[float],
    spacings: Counter[float],
    colors: Counter[str],
    bg_colors: Counter[str],
) -> dict[str, Any]:
    spacing_values = list(spacings.keys())
    on_4 = (
        sum(1 for v in spacing_values if v % 4 == 0) / len(spacing_values)
        if spacing_values
        else 0.0
    )
    on_8 = (
        sum(1 for v in spacing_values if v % 8 == 0) / len(spacing_values)
        if spacing_values
        else 0.0
    )

    type_ratios: list[float] = []
    sorted_sizes = sorted(font_sizes.keys())
    for i in range(1, len(sorted_sizes)):
        if sorted_sizes[i - 1] > 0:
            type_ratios.append(sorted_sizes[i] / sorted_sizes[i - 1])
    consistent_ratio = max(type_ratios) - min(type_ratios) < 0.15 if len(type_ratios) >= 3 else None

    return {
        "distinct_text_colors": len(colors),
        "distinct_bg_colors": len(bg_colors),
        "distinct_font_sizes": len(font_sizes),
        "distinct_spacing_values": len(spacing_values),
        "spacing_on_4px_grid_pct": round(on_4 * 100, 1),
        "spacing_on_8px_grid_pct": round(on_8 * 100, 1),
        "spacing_off_grid_pct": round(100 - on_4 * 100, 1),
        "type_scale_ratio_consistent": consistent_ratio,
    }


# --- Image-based extraction (fallback) ------------------------------------


def extract_from_image(path: Path) -> dict[str, Any]:
    """Quick palette extraction from an image."""
    try:
        from PIL import Image
    except ImportError as e:
        raise SystemExit(
            "Pillow is required for image-mode token extraction.\nInstall with: pip install Pillow"
        ) from e

    with Image.open(path) as im:
        img = im.convert("RGB")
    quant = img.quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    if not palette or not isinstance(palette[0], int):
        logger.warning("unexpected palette format for %s; skipping", path)
        return {
            "source": "image",
            "image": str(path),
            "colors": {"all": []},
            "diagnostics": {"distinct_clusters": 0},
        }
    counts = Counter(quant.getdata())
    swatches: list[dict] = []
    for color_index, count in counts.most_common():
        if color_index * 3 + 2 >= len(palette):
            continue
        r, g, b = (
            palette[color_index * 3],
            palette[color_index * 3 + 1],
            palette[color_index * 3 + 2],
        )
        swatches.append({"value": f"rgb({r}, {g}, {b})", "count": count})

    return {
        "source": "image",
        "image": str(path),
        "colors": {"all": swatches[:32]},
        "diagnostics": {"distinct_clusters": len(swatches)},
    }


# --- Compare to system ----------------------------------------------------

SYSTEM_TOKENS: dict[str, dict[str, Any]] = {
    "material-3": {
        "spacing_grid_px": 4,
        "font_sizes_px": [11, 12, 14, 16, 22, 24, 28, 32, 36, 45, 57],
        "border_radii_px": [0, 4, 8, 12, 16, 28],
    },
    "apple-hig": {
        "spacing_grid_px": 8,
        "font_sizes_px": [11, 12, 13, 15, 17, 20, 22, 28, 34],
        "border_radii_px": [0, 4, 6, 8, 10, 12, 14, 16, 20],
    },
    "fluent-2": {
        "spacing_grid_px": 4,
        "font_sizes_px": [10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 68],
        "border_radii_px": [0, 2, 4, 6, 8, 12],
    },
    "polaris": {
        "spacing_grid_px": 4,
        "font_sizes_px": [11, 12, 13, 14, 16, 20, 24, 28, 32, 40],
        "border_radii_px": [4, 6, 8, 12],
    },
    "carbon": {
        "spacing_grid_px": 8,
        "font_sizes_px": [12, 14, 16, 18, 20, 24, 28, 32, 36, 42, 54],
        "border_radii_px": [0, 4, 8],
    },
    "atlassian": {
        "spacing_grid_px": 4,
        "font_sizes_px": [11, 12, 14, 16, 20, 24, 29, 35],
        "border_radii_px": [3, 4, 8],
    },
}


def compare_to_system(detected: dict[str, Any], system: str) -> dict[str, Any]:
    canonical = SYSTEM_TOKENS.get(system)
    if not canonical:
        # Could be a custom system — try loading from references/design-systems/
        from harness.cli import load_custom_system_tokens

        canonical = load_custom_system_tokens(system)
        if not canonical:
            raise ValueError(f"unknown system: {system}")

    detected_sizes = sorted({p["value"] for p in detected.get("type", {}).get("sizes_px", [])})
    detected_spacings = sorted(
        {p["value"] for p in detected.get("spacing", {}).get("values_px", [])}
    )
    detected_radii = sorted(
        {p["value"] for p in detected.get("shape", {}).get("border_radii_px", [])}
    )

    def _drift(detected_set: list[float], canonical_set: list[float]) -> list[dict]:
        out = []
        for v in detected_set:
            if v in canonical_set:
                out.append({"value": v, "delta": 0.0, "match": True})
            else:
                nearest = min(canonical_set, key=lambda c: abs(c - v)) if canonical_set else v
                out.append({"value": v, "nearest": nearest, "delta": v - nearest, "match": False})
        return out

    return {
        "system": system,
        "type": _drift(detected_sizes, canonical["font_sizes_px"]),
        "spacing": _drift(
            detected_spacings, [canonical["spacing_grid_px"] * i for i in range(1, 16)]
        ),
        "shape": _drift(detected_radii, canonical["border_radii_px"]),
    }


def render_drift(drift: dict[str, Any]) -> str:
    lines = [f"# Token drift vs. {drift['system']}", ""]
    for dim in ("type", "spacing", "shape"):
        lines.append(f"## {dim.title()}")
        lines.append("")
        lines.append("| Detected | Match? | Nearest canonical | Delta |")
        lines.append("|----------|--------|-------------------|-------|")
        for row in drift.get(dim, []):
            if row["match"]:
                lines.append(f"| {row['value']} | ✓ | — | 0 |")
            else:
                lines.append(f"| {row['value']} | ✗ | {row['nearest']} | {row['delta']:+g} |")
        lines.append("")
    return "\n".join(lines)


def write_palette(
    detected: dict[str, Any],
    path: Path,
    *,
    output_root: Path | None = None,
) -> None:
    """Render a palette PNG. No-op if Pillow isn't available."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    swatches = (
        detected.get("colors", {}).get("background", []) + detected.get("colors", {}).get("all", [])
    )[:48]
    if not swatches:
        return
    cell, cols = 80, 8
    rows = (len(swatches) + cols - 1) // cols
    img = Image.new("RGB", (cols * cell, rows * cell), "white")
    draw = ImageDraw.Draw(img)
    for i, sw in enumerate(swatches):
        r, c = divmod(i, cols)
        x, y = c * cell, r * cell
        parsed = parse_color(sw["value"])
        if not parsed:
            continue
        rgb = tuple(int(parsed[k] * 255) for k in (0, 1, 2))
        try:
            draw.rectangle([x, y, x + cell - 4, y + cell - 4], fill=rgb)
        except (TypeError, ValueError):
            logger.warning(
                "failed to draw palette swatch at (%d, %d) with rgb=%s",
                x,
                y,
                rgb,
                exc_info=True,
            )
            continue
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    safeio_mod.atomic_write_bytes(output_root or path.parent, path, buffer.getvalue())
