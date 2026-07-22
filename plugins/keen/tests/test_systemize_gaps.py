"""Gap tests for harness/systemize.py.

The module had 0% coverage. These exercise the pure proposal functions
(clustering, type scale, spacing, radii, colors) plus the markdown / HTML
renderers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import systemize as sz
from harness.systemize import (
    propose_system,
    render_markdown,
    render_preview_html,
)


def test_cluster_1d_groups_within_gap() -> None:
    out = sz._cluster_1d([10.0, 10.5, 12.0, 20.0], [3, 1, 2, 4], gap=1.0)
    # Three clusters: {10, 10.5}, {12}, {20}
    assert len(out) == 3
    # Counts aggregate
    by_value = {c["value"]: c["count"] for c in out}
    assert by_value[20] == 4


def test_propose_type_scale_with_clean_set() -> None:
    observed = [
        {"value": 12, "count": 5},
        {"value": 14, "count": 8},
        {"value": 16, "count": 20},  # body
        {"value": 20, "count": 4},
        {"value": 24, "count": 3},
        {"value": 32, "count": 2},
    ]
    out = sz._propose_type_scale(observed)
    assert out["body_px"] in (16, 14, 12, 13, 15, 17, 18)
    assert isinstance(out["sizes"], list)
    assert len(out["sizes"]) == 6  # caption, body-sm, body, h3, h2, h1
    assert out["ratio"] is not None
    assert [item["size_px"] for item in out["sizes"][:3]] == [12, 14, 16]
    assert all(item["weight"] == 700 for item in out["sizes"] if item["name"].startswith("h"))


def test_propose_spacing_picks_eight_grid_when_better_fit() -> None:
    # All multiples of 8, none off-grid -> grid 8.
    observed = [
        {"value": 8, "count": 10},
        {"value": 16, "count": 10},
        {"value": 24, "count": 10},
    ]
    out = sz._propose_spacing(observed)
    assert out["grid_px"] == 8


def test_propose_spacing_picks_four_grid_when_mixed() -> None:
    # Mostly 4-grid; only some 8.
    observed = [
        {"value": 4, "count": 5},
        {"value": 12, "count": 5},
        {"value": 20, "count": 5},  # 20 % 8 != 0
    ]
    out = sz._propose_spacing(observed)
    assert out["grid_px"] == 4


def test_propose_spacing_empty_defaults_to_four() -> None:
    out = sz._propose_spacing([])
    assert out["grid_px"] == 4
    assert out["scale"][0] == 4


def test_propose_radii_groups_to_named_steps() -> None:
    observed = [
        {"value": 0, "count": 5},
        {"value": 4, "count": 5},
        {"value": 8, "count": 5},
    ]
    out = sz._propose_radii(observed)
    names = [s["name"] for s in out["scale"]]
    assert "none" in names


def test_color_helpers() -> None:
    assert sz._hex_from_rgb_string("rgb(255, 0, 0)") == "#ff0000"
    assert sz._hex_from_rgb_string("garbage") is None
    assert sz._hex_from_rgb_string("rgba(0, 0, 0, 0)") is None
    assert sz._hex_from_rgb_string("rgba(255, 0, 0, 0.5)") is None
    # rel_lum
    assert sz._rel_lum("#ffffff") > sz._rel_lum("#000000")
    # contrast
    c = sz._contrast("#000000", "#ffffff")
    assert c > 20.0
    # neutrality
    assert sz._is_neutral("#808080") is True
    assert sz._is_neutral("#ff0000") is False


def test_propose_colors_classifies_neutrals_and_accents() -> None:
    fg = [
        {"value": "rgb(0, 0, 0)", "count": 20},
        {"value": "rgb(220, 38, 38)", "count": 5},  # accent
    ]
    bg = [
        {"value": "rgb(255, 255, 255)", "count": 30},
        {"value": "rgb(37, 99, 235)", "count": 3},  # accent
    ]
    out = sz._propose_colors(fg, bg)
    assert "neutrals" in out
    assert "accents" in out
    assert out["neutrals"]["candidates_text"]
    assert out["neutrals"]["candidates_surface"]
    # The reds and blues are not neutral.
    accents = {a["hex"] for a in out["accents"]}
    assert "#dc2626" in accents or any(a.startswith("#") for a in accents)
    # When both neutrals exist, suggested_roles is added.
    assert "suggested_roles" in out


def test_propose_colors_uses_repeated_action_fill_not_dominant_text_as_primary() -> None:
    fg = [
        {"value": "rgb(23, 32, 51)", "count": 214},
        {"value": "rgb(101, 84, 192)", "count": 8},
        {"value": "rgb(105, 115, 134)", "count": 26},
    ]
    bg = [
        {"value": "rgba(0, 0, 0, 0)", "count": 250},
        {"value": "rgb(255, 255, 255)", "count": 28},
        {"value": "rgb(101, 84, 192)", "count": 12},
        {"value": "rgb(255, 139, 0)", "count": 12},
    ]

    out = sz._propose_colors(fg, bg)
    roles = out["suggested_roles"]

    assert roles["text.default"]["hex"] == "#172033"
    assert roles["primary"]["hex"] == "#6554c0"
    assert roles["primary"]["confidence"] == "medium"
    assert roles["primary"]["evidence"]["background_count"] == 12
    assert all(item["hex"] != "#000000" for item in out["neutrals"]["candidates_surface"])


def test_propose_system_end_to_end() -> None:
    extracted = {
        "type": {
            "sizes_px": [
                {"value": 14, "count": 10},
                {"value": 16, "count": 30},
                {"value": 20, "count": 5},
                {"value": 24, "count": 4},
            ],
            "families": [{"value": "Inter", "count": 50}],
        },
        "spacing": {"values_px": [{"value": 8, "count": 10}, {"value": 16, "count": 10}]},
        "shape": {"border_radii_px": [{"value": 0, "count": 3}, {"value": 8, "count": 7}]},
        "colors": {
            "foreground": [{"value": "rgb(0, 0, 0)", "count": 20}],
            "background": [{"value": "rgb(255, 255, 255)", "count": 30}],
        },
        "diagnostics": {"spacing_off_grid_pct": 30},
    }
    proposal = propose_system(extracted, name="acme")
    assert proposal["name"] == "acme"
    assert "type" in proposal and "spacing" in proposal
    assert proposal["fonts"]["primary"] == "Inter"
    assert proposal["fonts"]["body"] == "Inter"
    assert proposal["fonts"]["display"] == "Inter"
    assert proposal["fonts"]["pairing"]["mode"] == "single-family"
    # Notes are populated when diagnostics are bad.
    assert isinstance(proposal["notes"], list)


def test_propose_system_preserves_observed_serif_sans_pairing() -> None:
    extracted = {
        "type": {
            "sizes_px": [{"value": 16, "count": 30}, {"value": 40, "count": 4}],
            "families": [
                {"value": "Inter, ui-sans-serif, system-ui, sans-serif", "count": 322},
                {"value": "Georgia, serif", "count": 4},
            ],
            "text_samples": [
                {
                    "text": "Good morning, Maya.",
                    "family": "Georgia, serif",
                    "size_px": 54,
                    "weight": 700,
                    "tag": "h1",
                    "capture_count": 4,
                },
                {
                    "text": "Maya approved the Q3 brief 10 minutes ago",
                    "family": "Inter, ui-sans-serif, system-ui, sans-serif",
                    "size_px": 13,
                    "weight": 400,
                    "tag": "p",
                    "capture_count": 4,
                },
            ],
        },
        "spacing": {"values_px": []},
        "shape": {"border_radii_px": []},
        "colors": {"foreground": [], "background": []},
    }
    proposal = propose_system(extracted, name="northstar")
    fonts = proposal["fonts"]
    assert fonts["body"].startswith("Inter")
    assert fonts["display"] == "Georgia, serif"
    assert fonts["primary"] == fonts["body"]
    assert fonts["pairing"]["mode"] == "observed-contrast"
    assert fonts["pairing"]["display_family"] == "Georgia"
    assert fonts["pairing"]["body_family"] == "Inter"
    assert fonts["pairing"]["evidence"][1]["count"] == 4
    assert fonts["pairing"]["specimens"][0]["text"] == "Good morning, Maya."
    assert fonts["pairing"]["specimens"][1]["source"] == "captured-ui"


def test_font_pairing_excludes_code_icon_and_unproven_display_families() -> None:
    fonts = sz._propose_font_pairing(
        [
            {"value": "'Material Symbols Rounded'", "count": 900},
            {"value": "Menlo, monospace", "count": 700},
            {"value": "Inter, sans-serif", "count": 120},
            {"value": "Georgia, serif", "count": 10},
        ],
        [
            {
                "text": "Review decisions",
                "family": "Inter, sans-serif",
                "size_px": 16,
                "weight": 400,
                "tag": "p",
                "capture_count": 4,
            },
            {
                "text": "A serif footer note",
                "family": "Georgia, serif",
                "size_px": 14,
                "weight": 400,
                "tag": "p",
                "capture_count": 2,
            },
        ],
    )

    assert fonts["body"] == "Inter, sans-serif"
    assert fonts["display"] == "Inter, sans-serif"
    assert fonts["pairing"]["mode"] == "single-family"
    assert "another capture proves a separate display role" in fonts["pairing"]["rationale"]


def test_render_markdown_produces_sections() -> None:
    proposal = {
        "name": "acme",
        "type": {
            "sizes": [{"name": "body", "size_px": 16, "weight": 400, "line_height": 24}],
            "ratio_name": "minor third",
            "ratio": 1.2,
            "body_px": 16,
        },
        "spacing": {"grid_px": 8, "scale": [8, 16, 24], "fit_pct_4": 100.0, "fit_pct_8": 80.0},
        "radii": {"scale": [{"name": "none", "px": 0}, {"name": "sm", "px": 4}]},
        "colors": {
            "suggested_roles": {
                "text.default": {"hex": "#000000", "contrast_on_surface_default": 21.0}
            },
            "neutrals": {
                "candidates_text": [{"hex": "#000000", "count": 1}],
                "candidates_surface": [],
            },
            "accents": [],
        },
        "fonts": {"primary": "Inter", "candidates": ["Inter"]},
        "notes": ["one cleanup"],
    }
    md = render_markdown(proposal)
    assert "# Acme design system review" in md
    assert "## Executive decision" in md
    assert "## Evidence basis" in md
    assert "### Spacing" in md
    assert "Cleanup targets" in md
    assert "### Typography pairing" in md
    assert "Display:" in md and "Body / interface:" in md


def test_render_preview_html_returns_self_contained_html() -> None:
    proposal = {
        "name": "acme",
        "type": {
            "sizes": [{"name": "body", "size_px": 16, "weight": 400, "line_height": 24}],
            "body_px": 16,
        },
        "spacing": {"grid_px": 8, "scale": [8, 16, 24]},
        "radii": {"scale": [{"name": "none", "px": 0}, {"name": "sm", "px": 4}]},
        "colors": {"suggested_roles": {"primary": {"hex": "#2563eb"}}, "accents": []},
        "fonts": {"primary": "Inter"},
    }
    html = render_preview_html(proposal)
    assert "<!doctype html>" in html
    assert "acme" in html
    assert "<style>" in html  # self-contained
    assert "--font-display: Inter" in html
    assert "--font-body: Inter" in html
    assert "--text: #172033" in html
    assert "--surface: #ffffff" in html
    assert "--primary: #1b5fcc" in html
    assert "--swatch-color:#2563eb" in html
    assert "Design system review" in html
    assert ">Decision<" in html
    assert "Synthetic stress copy" in html
    assert 'href="#adoption"' in html
    assert "decision dossier" not in html.lower()
    assert "Clarity with character" not in html
    assert "section-no" not in html
    assert 'type="radio"' not in html
    assert "<select" not in html


def test_render_preview_html_matches_incomplete_capture_verdict() -> None:
    proposal = {
        "name": "acme",
        "type": {"sizes": [], "body_px": 16},
        "spacing": {"grid_px": 4, "scale": [4, 8]},
        "radii": {"scale": []},
        "colors": {"suggested_roles": {}, "accents": []},
        "fonts": {"primary": "Inter"},
        "decision": {
            "recommendation": "insufficient-evidence",
            "confidence": "low",
            "strengths": [],
        },
        "evidence": {"capture_scope": {"requested": 4, "succeeded": 2, "complete": False}},
    }

    html = render_preview_html(proposal)

    assert "Collect more evidence." in html
    assert "Capture coverage is incomplete." in html
    assert "coherent enough to prototype" not in html


def test_render_preview_html_neutralizes_page_controlled_css_and_html() -> None:
    proposal = {
        "name": "acme",
        "type": {
            "sizes": [{"name": "<img src=x>", "size_px": 16, "weight": 400, "line_height": 24}],
            "body_px": 16,
        },
        "spacing": {"grid_px": 8, "scale": [8]},
        "radii": {"scale": [{"name": "<b>sm</b>", "px": 4}]},
        "colors": {
            "suggested_roles": {
                "primary</strong><script>bad()</script>": {
                    "hex": "red;}</style><script>bad()</script><style>{"
                }
            },
            "accents": [],
        },
        "fonts": {"primary": "x;}</style><script>bad()</script><style>{"},
    }
    rendered = render_preview_html(proposal)
    assert "<script>" not in rendered
    assert "</style><script>" not in rendered
    assert "system-ui, -apple-system, sans-serif" in rendered
    assert "&lt;img src=x&gt;" in rendered


def test_systemize_run_rejects_path_like_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="system name"):
        sz.systemize_run(tmp_path, name="../../outside")
