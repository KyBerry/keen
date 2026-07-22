"""Tests for harness.taste: taste-DNA vector extraction."""

from __future__ import annotations

import json
from pathlib import Path

from harness.taste import (
    TasteVector,
    extract_taste,
    remix_seed_from,
    render_taste_card,
    vector_distance,
)


def _write_run(tmp_path: Path, *, components: list[dict], tokens: dict) -> Path:
    (tmp_path / "components").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tokens").mkdir(parents=True, exist_ok=True)
    (tmp_path / "components" / "p-desktop-default.json").write_text(json.dumps(components))
    (tmp_path / "tokens" / "extracted.json").write_text(json.dumps(tokens))
    return tmp_path


def test_extract_taste_empty_run(tmp_path: Path) -> None:
    run = _write_run(tmp_path, components=[], tokens={})
    v = extract_taste(run)
    assert isinstance(v, TasteVector)
    assert v.palette == []
    assert v.hue_anchor_deg is None
    assert v.chromatic_intensity == 0.0
    assert v.color_temperature == "neutral"
    assert v.archetype_hint in (
        "utilitarian",
        "dense",
        "clarity-first",
        "brand-forward",
        "editorial",
    )


def test_extract_taste_minimal_purple_brand(tmp_path: Path) -> None:
    """A run with a strong purple brand should classify cool + nonzero intensity."""
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#000000", "count": 1000},
                {"value": "#6750A4", "count": 200},  # material purple
                {"value": "#ffffff", "count": 80},
            ],
            "background": [],
        },
        "type": {
            "families": [{"value": "Inter, sans-serif", "count": 500}],
            "sizes_px": [
                {"value": 14, "count": 100},
                {"value": 16, "count": 600},
                {"value": 32, "count": 50},
            ],
            "weights": [
                {"value": 400, "count": 500},
                {"value": 600, "count": 200},
            ],
        },
        "shape": {
            "border_radii_px": [
                {"value": 4, "count": 50},
                {"value": 8, "count": 200},
                {"value": 16, "count": 30},
            ],
        },
        "spacing": {
            "values_px": [
                {"value": 8, "count": 200},
                {"value": 16, "count": 100},
                {"value": 24, "count": 60},
            ],
        },
        "diagnostics": {
            "spacing_on_4px_grid_pct": 100.0,
            "spacing_on_8px_grid_pct": 100.0,
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    v = extract_taste(run)
    assert v.color_temperature == "cool"
    assert v.hue_anchor_deg is not None and 270.0 <= v.hue_anchor_deg <= 320.0
    assert v.chromatic_intensity > 0
    assert v.primary_family == "Inter"
    assert v.body_size_px == 16
    assert v.display_ratio is not None and v.display_ratio == 2.0
    assert v.radius_max_px == 16
    assert v.distinct_radii_count == 3
    assert v.radius_character == "soft"
    assert v.spacing_grid_px == 8
    # Density should be comfortable for a weighted-mean spacing of ~13–14.
    assert v.density in ("compact", "comfortable")


def test_render_taste_card_contains_dimensions(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#000000", "count": 100},
                {"value": "#6750A4", "count": 100},
            ],
        },
        "type": {"families": [{"value": "Inter", "count": 100}]},
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    v = extract_taste(run)
    card = render_taste_card(v)
    # Sections all present.
    for marker in (
        "# Taste DNA",
        "## Color",
        "## Type",
        "## Shape & depth",
        "## Spacing & density",
        "Archetype hint:",
        "Distinctiveness:",
        "Boldness:",
        "Polish:",
    ):
        assert marker in card, f"missing {marker!r}"


def test_vector_distance_self_is_zero(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#000000", "count": 100},
                {"value": "#0066ff", "count": 100},
            ],
        },
        "type": {"families": [{"value": "Inter", "count": 100}]},
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    v = extract_taste(run)
    d = vector_distance(v, v)
    assert d["total"] == 0.0


def test_vector_distance_different_hue_is_large(tmp_path: Path) -> None:
    blue_run = _write_run(
        tmp_path / "blue",
        components=[],
        tokens={
            "colors": {"foreground": [{"value": "#0066ff", "count": 100}]},
            "type": {"families": [{"value": "Inter", "count": 100}]},
        },
    )
    red_run = _write_run(
        tmp_path / "red",
        components=[],
        tokens={
            "colors": {"foreground": [{"value": "#ff0033", "count": 100}]},
            "type": {"families": [{"value": "Inter", "count": 100}]},
        },
    )
    a = extract_taste(blue_run)
    b = extract_taste(red_run)
    d = vector_distance(a, b)
    # Blue vs red is roughly the maximum hue distance (≈180°).
    assert d["hue"] > 0.5
    assert d["total"] > 0


def test_remix_seed_from_shifts_hue(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [{"value": "#0066ff", "count": 100}],
        },
        "type": {"families": [{"value": "Inter", "count": 100}]},
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    v = extract_taste(run)
    seed = remix_seed_from(v, shift_hue_deg=30.0)
    assert seed.startswith("#") and len(seed) == 7
    # Re-parse and verify the hue moved.
    from harness._oklch import hex_to_oklch

    _, _, new_h = hex_to_oklch(seed)
    diff = abs(new_h - (v.hue_anchor_deg or 0)) % 360.0
    diff = min(diff, 360.0 - diff)
    assert 15.0 < diff < 60.0


def test_remix_seed_falls_back_when_no_anchor(tmp_path: Path) -> None:
    run = _write_run(tmp_path, components=[], tokens={})
    v = extract_taste(run)
    assert v.hue_anchor_deg is None
    seed = remix_seed_from(v)
    assert seed.startswith("#")


def test_distinctiveness_penalizes_inter_only(tmp_path: Path) -> None:
    """A pure Inter + 8-only-radius + no-shadow run should score < 0.6 on
    distinctiveness; a non-Inter family with a real radius scale should not."""
    inter_tokens = {
        "colors": {"foreground": [{"value": "#0066ff", "count": 100}]},
        "type": {"families": [{"value": "Inter", "count": 100}]},
        "shape": {"border_radii_px": [{"value": 8, "count": 100}]},
    }
    custom_tokens = {
        "colors": {"foreground": [{"value": "#0066ff", "count": 100}]},
        "type": {"families": [{"value": "GeistSans", "count": 100}]},
        "shape": {
            "border_radii_px": [
                {"value": 2, "count": 50},
                {"value": 8, "count": 50},
                {"value": 16, "count": 50},
            ]
        },
    }
    inter = extract_taste(_write_run(tmp_path / "i", components=[], tokens=inter_tokens))
    custom = extract_taste(_write_run(tmp_path / "c", components=[], tokens=custom_tokens))
    assert custom.scores["distinctiveness"] > inter.scores["distinctiveness"]
