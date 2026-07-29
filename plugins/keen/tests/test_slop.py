"""Tests for harness.slop: the AI-design slop detector.

Coverage strategy: each predicate has at least one positive case (synthetic
inputs that should fire) and one negative case (inputs that shouldn't).
Score banding gets its own pair of tests.

The fixtures build minimal `components` lists and `tokens` dicts in the
shape that the real pipeline emits — `components[*].styles.*` and
`tokens.colors.foreground[]` / `tokens.shape.border_radii_px[]` etc.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.slop import (
    PREDICATES,
    SLOP_CATALOG,
    analyze_slop,
    render_markdown,
)


def _with_identity(
    components: list[dict],
    *,
    capture: str = "desktop-default",
) -> list[dict]:
    return [
        {
            **component,
            "viewport": component.get("viewport", "desktop"),
            "state": component.get("state", "default"),
            "capture_path": component.get(
                "capture_path",
                f"screens/p-{capture}.png",
            ),
        }
        for component in components
    ]


def _write_run(tmp_path: Path, *, components: list[dict], tokens: dict) -> Path:
    """Lay out a minimal run directory the slop module can read."""
    (tmp_path / "components").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tokens").mkdir(parents=True, exist_ok=True)
    normalized = _with_identity(components)
    (tmp_path / "components" / "p-desktop-default.json").write_text(json.dumps(normalized))
    (tmp_path / "tokens" / "extracted.json").write_text(json.dumps(tokens))
    return tmp_path


# --- catalog integrity ----------------------------------------------------


def test_catalog_covers_every_registered_predicate() -> None:
    """Every predicate in PREDICATES must have a catalog entry, and vice
    versa. Drift here means the agent gets a finding it can't explain."""
    pred_ids = {pid for pid, _ in PREDICATES}
    cat_ids = set(SLOP_CATALOG)
    assert pred_ids == cat_ids


def test_catalog_entries_have_required_fields() -> None:
    for pid, entry in SLOP_CATALOG.items():
        assert "weight" in entry, pid
        assert entry.get("title"), pid
        assert entry.get("why"), pid
        assert entry.get("escape"), pid
        assert isinstance(entry["weight"], int), pid
        assert 1 <= entry["weight"] <= 20, pid


# --- score banding --------------------------------------------------------


def test_empty_run_scores_zero_and_distinctive(tmp_path: Path) -> None:
    run = _write_run(tmp_path, components=[], tokens={})
    report = analyze_slop(run)
    assert report.score == 0
    assert report.band == "distinctive"
    assert report.findings == []


def test_score_is_capped_at_100(tmp_path: Path) -> None:
    """Stack enough fingerprints that raw_score > 100; verify the cap."""
    # Tailwind default palette: 2+ matches needed.
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#3b82f6", "count": 200},
                {"value": "#6366f1", "count": 150},
                {"value": "#a855f7", "count": 130},
            ],
            "background": [],
        },
        "shape": {
            "border_radii_px": [
                {"value": 8, "count": 100},
            ],
        },
        "type": {
            "families": [{"value": "Inter", "count": 200}],
        },
    }
    components = [
        # 3 gradient surfaces.
        {
            "styles": {"backgroundImage": "linear-gradient(...)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 1,
        },
        {
            "styles": {"backgroundImage": "linear-gradient(...)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 2,
        },
        {
            "styles": {"backgroundImage": "linear-gradient(...)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 3,
        },
        # 3 glass surfaces.
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 4,
        },
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 5,
        },
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 6,
        },
    ]
    run = _write_run(tmp_path, components=components, tokens=tokens)
    report = analyze_slop(run)
    # Tailwind (15) + VibeCode purple (12) + Shadcn radius (10) + Uniform
    # radius (8) + Inter monoculture (8) + Glassmorphism (10) + Gradient (8)
    # × hits = > 100; verify the cap actually trips.
    assert report.score == 100
    assert report.band == "slop"


# --- per-predicate cases --------------------------------------------------


def test_tailwind_palette_fires_on_two_default_hexes(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#3b82f6", "count": 200},  # blue-500
                {"value": "#6366f1", "count": 150},  # indigo-500
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.tailwind-default-palette" in pids


def test_tailwind_palette_silent_on_custom_hex(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#0a5e9e", "count": 200},
                {"value": "#bc8c33", "count": 150},
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.tailwind-default-palette" not in pids


def test_vibecode_purple_fires_on_two_purples(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#7c3aed", "count": 200},  # 295°
                {"value": "#6366f1", "count": 150},  # 280°
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.vibecode-purple" in pids


def test_vibecode_purple_silent_on_warm_palette(tmp_path: Path) -> None:
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#e74c3c", "count": 200},  # red
                {"value": "#f39c12", "count": 150},  # orange
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.vibecode-purple" not in pids


def test_shadcn_radius_fires_on_8px_dominance(tmp_path: Path) -> None:
    tokens = {
        "shape": {
            "border_radii_px": [
                {"value": 8, "count": 800},
                {"value": 4, "count": 50},
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.shadcn-default-radius" in pids


def test_shadcn_radius_silent_on_diverse_radii(tmp_path: Path) -> None:
    tokens = {
        "shape": {
            "border_radii_px": [
                {"value": 4, "count": 200},
                {"value": 8, "count": 200},
                {"value": 16, "count": 200},
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.shadcn-default-radius" not in pids


def test_inter_monoculture_fires_when_only_inter(tmp_path: Path) -> None:
    tokens = {
        "type": {
            "families": [
                {"value": "Inter, sans-serif", "count": 800},
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.inter-monoculture" in pids


def test_inter_monoculture_silent_with_custom_family(tmp_path: Path) -> None:
    tokens = {
        "type": {
            "families": [
                {"value": "GeistSans, sans-serif", "count": 800},
            ],
        },
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.inter-monoculture" not in pids


def test_glassmorphism_fires_at_three_blurred_surfaces(tmp_path: Path) -> None:
    components = [
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": i,
        }
        for i in range(3)
    ]
    run = _write_run(tmp_path, components=components, tokens={})
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.glassmorphism-overuse" in pids


def test_glassmorphism_silent_at_one_surface(tmp_path: Path) -> None:
    components = [
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 0,
        }
    ]
    run = _write_run(tmp_path, components=components, tokens={})
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.glassmorphism-overuse" not in pids


def test_repeated_capture_does_not_inflate_glass_surface_count(tmp_path: Path) -> None:
    components_dir = tmp_path / "components"
    tokens_dir = tmp_path / "tokens"
    components_dir.mkdir()
    tokens_dir.mkdir()
    glass = [
        {
            "styles": {"backdropFilter": "blur(12px)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": 0,
        }
    ]
    for capture in ("desktop-default", "tablet-default", "mobile-default"):
        (components_dir / f"page-{capture}.json").write_text(
            json.dumps(_with_identity(glass, capture=capture))
        )
    (tokens_dir / "extracted.json").write_text("{}")

    report = analyze_slop(tmp_path)

    assert "slop.glassmorphism-overuse" not in report.hit_counts


def test_cross_capture_slop_uses_strongest_capture_instead_of_summing(tmp_path: Path) -> None:
    def glass_surfaces() -> list[dict]:
        return [
            {
                "styles": {"backdropFilter": "blur(12px)"},
                "component_kind": "div",
                "box": {"x": 0, "y": 0, "w": 100, "h": 100},
                "parent_index": -1,
                "index": index,
            }
            for index in range(3)
        ]

    single = _write_run(tmp_path / "single", components=glass_surfaces(), tokens={})
    multi = tmp_path / "multi"
    (multi / "components").mkdir(parents=True)
    (multi / "tokens").mkdir()
    for capture in ("desktop-default", "tablet-default", "mobile-default"):
        (multi / "components" / f"page-{capture}.json").write_text(
            json.dumps(_with_identity(glass_surfaces(), capture=capture))
        )
    (multi / "tokens" / "extracted.json").write_text("{}")

    single_report = analyze_slop(single)
    multi_report = analyze_slop(multi)

    assert multi_report.score == single_report.score
    assert multi_report.hit_counts["slop.glassmorphism-overuse"] == 1
    finding = next(
        finding
        for finding in multi_report.findings
        if finding.predicate_id == "slop.glassmorphism-overuse"
    )
    assert finding.evidence["captures_triggered"] == 3


def test_aggregate_token_count_threshold_is_normalized_per_capture(tmp_path: Path) -> None:
    components_dir = tmp_path / "components"
    tokens_dir = tmp_path / "tokens"
    components_dir.mkdir()
    tokens_dir.mkdir()
    for capture in ("desktop-default", "tablet-default", "mobile-default"):
        (components_dir / f"page-{capture}.json").write_text("[]")
    tokens = {
        "spacing": {
            "values_px": [
                {"value": 8, "count": 54},
                {"value": 16, "count": 6},
            ]
        }
    }
    (tokens_dir / "extracted.json").write_text(json.dumps(tokens))

    report = analyze_slop(tmp_path)

    assert "slop.spacing-monotony" not in report.hit_counts


def test_gradient_fires_at_three(tmp_path: Path) -> None:
    components = [
        {
            "styles": {"backgroundImage": "linear-gradient(45deg, red, blue)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": i,
        }
        for i in range(3)
    ]
    run = _write_run(tmp_path, components=components, tokens={})
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.gradient-overuse" in pids


def test_single_shadow_tier_fires_at_dominance(tmp_path: Path) -> None:
    shadow = "0 1px 3px rgba(0,0,0,0.1)"
    components = [
        {
            "styles": {"boxShadow": shadow},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": i,
        }
        for i in range(10)
    ]
    run = _write_run(tmp_path, components=components, tokens={})
    report = analyze_slop(run)
    pids = {f.predicate_id for f in report.findings}
    assert "slop.single-shadow-tier" in pids


def test_editorial_dossier_cluster_requires_the_combination(tmp_path: Path) -> None:
    components: list[dict] = []
    for index in range(4):
        components.append(
            {
                "index": index,
                "parent_index": -1,
                "component_kind": "heading-2",
                "text": f"Section {index + 1}",
                "styles": {"fontFamily": "Georgia, serif", "fontSize": "34px"},
                "box": {"x": 0, "y": index * 100, "w": 600, "h": 50},
            }
        )
    for index, text in enumerate(("ROLE", "PRODUCT COPY", "SPECIFICATION"), start=10):
        components.append(
            {
                "index": index,
                "parent_index": 9,
                "component_kind": "table-header-cell",
                "text": text,
                "styles": {
                    "fontFamily": "ui-monospace, Menlo, monospace",
                    "fontSize": "10px",
                    "textTransform": "uppercase",
                },
                "box": {"x": 0, "y": 500, "w": 120, "h": 24},
            }
        )
    tokens = {
        "type": {
            "families": [
                {"value": "Inter, sans-serif", "count": 200},
                {"value": "Georgia, serif", "count": 8},
            ]
        }
    }

    report = analyze_slop(_write_run(tmp_path, components=components, tokens=tokens))

    assert "slop.editorial-dossier-cluster" in report.hit_counts


def test_editorial_dossier_cluster_does_not_ban_one_serif_heading(tmp_path: Path) -> None:
    components = [
        {
            "index": 1,
            "parent_index": -1,
            "component_kind": "heading-1",
            "text": "Annual report",
            "styles": {"fontFamily": "Georgia, serif", "fontSize": "42px"},
            "box": {"x": 0, "y": 0, "w": 600, "h": 60},
        }
    ]
    tokens = {"type": {"families": [{"value": "Inter, sans-serif", "count": 100}]}}

    report = analyze_slop(_write_run(tmp_path, components=components, tokens=tokens))

    assert "slop.editorial-dossier-cluster" not in report.hit_counts


def test_render_markdown_includes_candidate_index_and_escapes(tmp_path: Path) -> None:
    tokens = {
        "type": {"families": [{"value": "Inter", "count": 100}]},
        "shape": {"border_radii_px": [{"value": 8, "count": 100}]},
    }
    run = _write_run(tmp_path, components=[], tokens=tokens)
    report = analyze_slop(run)
    md = render_markdown(report)
    assert "Candidate index:" in md
    assert "not a verdict" in md
    assert str(report.score) in md
    assert report.band in md
    if report.findings:
        assert "## Escape moves" in md


def test_render_markdown_handles_zero_score() -> None:
    """A report with no findings should still render cleanly."""
    from harness.slop import SlopReport

    empty = SlopReport(score=0, band="distinctive")
    md = render_markdown(empty)
    assert "No slop fingerprints detected" in md
    assert "distinctive" in md


def test_top_escapes_caps_at_six(tmp_path: Path) -> None:
    # Stack a lot of triggers; verify top_escapes never exceeds 6.
    tokens = {
        "colors": {
            "foreground": [
                {"value": "#3b82f6", "count": 200},
                {"value": "#6366f1", "count": 150},
                {"value": "#a855f7", "count": 130},  # purple
                {"value": "#8b5cf6", "count": 100},  # purple
            ],
        },
        "shape": {"border_radii_px": [{"value": 8, "count": 200}]},
        "type": {"families": [{"value": "Inter", "count": 200}]},
    }
    components = [
        {
            "styles": {"backgroundImage": "linear-gradient(...)"},
            "component_kind": "div",
            "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "parent_index": -1,
            "index": i,
        }
        for i in range(3)
    ]
    run = _write_run(tmp_path, components=components, tokens=tokens)
    report = analyze_slop(run)
    assert len(report.top_escapes) <= 6
