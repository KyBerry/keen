"""Gap tests for harness/tokens.py.

The module had 0% coverage. These exercise:
- _to_px helper (px parsing)
- extract_from_captures with a fake captures dir
- _diagnostics computation
- SYSTEM_TOKENS lookup via compare_to_system
- render_drift markdown formatting
- write_palette no-op without swatches
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import tokens as tokens_mod
from harness.tokens import (
    SYSTEM_TOKENS,
    compare_to_system,
    extract_from_captures,
    render_drift,
    write_palette,
)


def _write_dom(captures_dir: Path, name: str, elements: list[dict]) -> None:
    dom_dir = captures_dir / "dom"
    dom_dir.mkdir(parents=True, exist_ok=True)
    (dom_dir / name).write_text(json.dumps({"elements": elements}))


def test_extract_from_captures_aggregates_styles(tmp_path: Path) -> None:
    """Walks dom/*.json and aggregates color/size/spacing counts."""
    _write_dom(
        tmp_path,
        "dom-1.json",
        [
            {
                "styles": {
                    "color": "rgb(0, 0, 0)",
                    "backgroundColor": "rgb(255, 255, 255)",
                    "fontSize": "16px",
                    "fontFamily": "Inter",
                    "fontWeight": "400",
                    "borderTopLeftRadius": "8px",
                    "borderTopRightRadius": "8px",
                    "borderBottomLeftRadius": "8px",
                    "borderBottomRightRadius": "8px",
                    "paddingTop": "12px",
                    "paddingLeft": "16px",
                    "marginTop": "8px",
                }
            },
            {
                "styles": {
                    "color": "rgb(20, 20, 20)",
                    "fontSize": "14px",
                    "fontWeight": "600",
                }
            },
        ],
    )
    out = extract_from_captures(tmp_path)
    assert out["source"] == "dom"
    assert out["elements_examined"] == 2
    assert any(p["value"] == "rgb(0, 0, 0)" for p in out["colors"]["foreground"])
    assert any(p["value"] == 16.0 for p in out["type"]["sizes_px"])
    assert any(p["value"] == 400 for p in out["type"]["weights"])
    assert any(p["value"] == 8.0 for p in out["shape"]["border_radii_px"])
    # diagnostics block populated
    diag = out["diagnostics"]
    assert "distinct_text_colors" in diag
    assert "spacing_on_4px_grid_pct" in diag


def test_extract_from_captures_invalid_font_weight_skipped(tmp_path: Path) -> None:
    """A garbage fontWeight value is skipped without raising."""
    _write_dom(
        tmp_path,
        "dom-1.json",
        [
            {"styles": {"fontWeight": "garbage"}},
        ],
    )
    out = extract_from_captures(tmp_path)
    # Should not crash and should record zero weights.
    assert out["type"]["weights"] == []


def test_extract_from_captures_keeps_sanitized_visible_text_samples(tmp_path: Path) -> None:
    _write_dom(
        tmp_path,
        "dom-1.json",
        [
            {
                "tag": "h1",
                "text": "Good morning, Maya.",
                "ariaHidden": False,
                "box": {"w": 500, "h": 64},
                "styles": {
                    "display": "block",
                    "fontSize": "54px",
                    "fontFamily": "Georgia, serif",
                    "fontWeight": "700",
                },
            },
            {
                "tag": "span",
                "text": "Nested fragment",
                "ariaHidden": False,
                "box": {"w": 100, "h": 20},
                "styles": {
                    "display": "inline",
                    "fontSize": "13px",
                    "fontFamily": "Inter, sans-serif",
                    "fontWeight": "400",
                },
            },
            {
                "tag": "p",
                "text": "Hidden content must not become a specimen",
                "ariaHidden": False,
                "box": {"w": 300, "h": 30},
                "styles": {
                    "display": "block",
                    "visibility": "hidden",
                    "fontSize": "16px",
                    "fontFamily": "Inter, sans-serif",
                    "fontWeight": "400",
                },
            },
            {
                "tag": "p",
                "text": "Transparent content must not become a specimen",
                "ariaHidden": False,
                "box": {"w": 300, "h": 30},
                "styles": {
                    "display": "block",
                    "opacity": "0",
                    "fontSize": "16px",
                    "fontFamily": "Inter, sans-serif",
                    "fontWeight": "400",
                },
            },
        ],
    )

    out = extract_from_captures(tmp_path)

    assert out["type"]["text_samples"] == [
        {
            "text": "Good morning, Maya.",
            "family": "Georgia, serif",
            "size_px": 54.0,
            "weight": 700,
            "tag": "h1",
            "capture_count": 1,
            "source": "captured-ui",
        }
    ]


def test_extract_from_captures_empty_dir_returns_zero_elements(tmp_path: Path) -> None:
    """No dom/*.json -> zero elements examined."""
    out = extract_from_captures(tmp_path)
    assert out["elements_examined"] == 0


def test_diagnostics_type_scale_consistent_with_clean_sizes(tmp_path: Path) -> None:
    """Four font sizes in tight ratio -> type_scale_ratio_consistent True
    (needs >= 3 ratios, i.e. >= 4 sizes)."""
    _write_dom(
        tmp_path,
        "dom-1.json",
        [
            {"styles": {"fontSize": "16px"}},
            {"styles": {"fontSize": "20px"}},
            {"styles": {"fontSize": "25px"}},
            {"styles": {"fontSize": "31.25px"}},
        ],
    )
    out = extract_from_captures(tmp_path)
    # ratios: 20/16=1.25, 25/20=1.25, 31.25/25=1.25 — consistent
    assert out["diagnostics"]["type_scale_ratio_consistent"] is True


def test_compare_to_system_known_system_returns_drift() -> None:
    """compare_to_system for a known system produces a drift report."""
    detected = {
        "type": {"sizes_px": [{"value": 16, "count": 5}, {"value": 13, "count": 3}]},
        "spacing": {"values_px": [{"value": 8, "count": 5}, {"value": 7, "count": 1}]},
        "shape": {"border_radii_px": [{"value": 8, "count": 1}, {"value": 3, "count": 1}]},
    }
    drift = compare_to_system(detected, "material-3")
    assert drift["system"] == "material-3"
    # 16 is canonical for material-3, 13 is not.
    type_rows = {row["value"]: row for row in drift["type"]}
    assert type_rows[16.0]["match"] is True
    assert type_rows[13.0]["match"] is False


def test_compare_to_system_unknown_raises() -> None:
    """An unknown system name raises ValueError."""
    with pytest.raises((ValueError, ImportError)):
        compare_to_system({"type": {"sizes_px": []}}, "nope-not-a-system-xyz")


def test_system_tokens_contains_canonical_systems() -> None:
    """SYSTEM_TOKENS includes the six built-ins."""
    for name in ("material-3", "apple-hig", "fluent-2", "polaris", "carbon", "atlassian"):
        assert name in SYSTEM_TOKENS


def test_render_drift_emits_three_sections() -> None:
    """render_drift produces a Type/Spacing/Shape section table."""
    drift = {
        "system": "test",
        "type": [{"value": 16, "match": True}],
        "spacing": [{"value": 9, "nearest": 8, "delta": 1, "match": False}],
        "shape": [],
    }
    md = render_drift(drift)
    assert "## Type" in md
    assert "## Spacing" in md
    assert "## Shape" in md
    assert "test" in md


def test_write_palette_no_swatches_returns_silently(tmp_path: Path) -> None:
    """When no swatches, write_palette returns without creating a file."""
    out = tmp_path / "palette.png"
    write_palette({"colors": {}}, out)
    assert not out.exists()


def test_to_px_helper() -> None:
    """_to_px parses '12px' -> 12.0 and returns None for non-px."""
    # Access via module to confirm a coverage-touching call.
    assert tokens_mod._to_px("12px") == 12.0
    assert tokens_mod._to_px("14.5px") == 14.5
    assert tokens_mod._to_px("12em") is None
    assert tokens_mod._to_px("") is None
