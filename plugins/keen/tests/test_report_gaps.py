"""Gap tests for harness/report.py.

Exercise the report pipeline: render_summary (pure-string output) and
compose (full assembly from an on-disk captures dir).
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.design_context import new_context
from harness.report import _select_annotations, build_agent_brief, compose, render_summary


def test_annotation_selection_is_prioritized_grouped_and_bounded() -> None:
    components = [
        {
            "index": index,
            "component_kind": "button",
            "viewport": "desktop",
            "state": "default",
            "capture_path": "screens/desktop-default.png",
            "device_pixel_ratio": 1,
            "viewport_width": 1_000,
            "box": {"x": index * 10, "y": 0, "w": 8, "h": 8},
        }
        for index in range(12)
    ]
    findings = [
        {
            "severity": "P0" if index == 0 else "P1",
            "predicate_id": "repeated" if index < 5 else f"predicate-{index}",
            "component_index": index,
            "capture_path": "screens/desktop-default.png",
            "message": f"finding {index}",
        }
        for index in range(12)
    ]
    findings.insert(
        1,
        {
            "severity": "P1",
            "predicate_id": "second-on-same-component",
            "component_index": 0,
            "capture_path": "screens/desktop-default.png",
            "message": "group me",
        },
    )
    findings.append(
        {
            "severity": "P2",
            "predicate_id": "polish",
            "component_index": 11,
            "capture_path": "screens/desktop-default.png",
            "message": "do not map",
        }
    )

    annotations = _select_annotations(components, findings)
    markers = annotations["desktop-default"]

    assert len(markers) == 8
    assert markers[0]["id"] == "A01"
    assert markers[0]["predicate_ids"] == ["repeated", "second-on-same-component"]
    assert sum("repeated" in marker["predicate_ids"] for marker in markers) == 2
    assert "annotation_id" not in findings[-1]


def test_annotation_selection_skips_wholly_offscreen_components() -> None:
    component = {
        "index": 1,
        "component_kind": "link",
        "viewport": "mobile",
        "state": "default",
        "capture_path": "screens/mobile-default.png",
        "viewport_width": 390,
        "capture_height": 844,
        "device_pixel_ratio": 1,
        "box": {"x": 410, "y": 10, "w": 40, "h": 20},
    }
    finding = {
        "severity": "P1",
        "predicate_id": "layout.off-canvas",
        "component_index": 1,
        "capture_path": "screens/mobile-default.png",
        "message": "off screen",
    }

    assert _select_annotations([component], [finding]) == {}
    assert "annotation_id" not in finding


def test_annotation_selection_skips_below_partial_screenshot() -> None:
    component = {
        "index": 1,
        "component_kind": "heading-2",
        "viewport": "desktop",
        "state": "default",
        "capture_path": "screens/desktop-default.png",
        "capture_width": 1440,
        "capture_height": 900,
        "device_pixel_ratio": 1,
        "box": {"x": 100, "y": 1200, "w": 400, "h": 80},
    }
    finding = {
        "severity": "P0",
        "predicate_id": "contrast.text",
        "component_index": 1,
        "capture_path": "screens/desktop-default.png",
        "message": "below captured viewport",
    }

    assert _select_annotations([component], [finding]) == {}


def test_render_summary_basic() -> None:
    report = {
        "score": {
            "grade": "B",
            "grade_summary": "Solid.",
            "score": 14.0,
            "counts": {"P0": 1, "P1": 1, "P2": 1},
            "by_component_kind": {
                "button": {
                    "total": 2,
                    "with_findings": 1,
                    "P0": 1,
                    "P1": 0,
                    "P2": 0,
                    "density_pct": 50.0,
                },
            },
        },
        "target_system": "material-3",
        "captures": [
            {
                "viewport": "desktop",
                "state": "default",
                "screen_path": "screens/d.png",
                "manual_review_needed": False,
            }
        ],
        "annotated_overviews": {"d": "screens/d-annotated.png"},
        "top_findings": [
            {
                "severity": "P0",
                "predicate_id": "x",
                "component_kind": "button",
                "viewport": "desktop",
                "state": "default",
                "message": "bad",
            },
        ],
        "tokens": {"diagnostics": {"distinct_text_colors": 7}},
    }
    out = render_summary(report)
    assert "Signal band:** B" in out
    assert "Weighted candidate index:** 14.0" in out
    assert "Candidate findings" in out
    assert "not an overall verdict" in out
    assert "Issue density" in out
    assert "Token diagnostics" in out


def test_render_summary_minimal_report() -> None:
    """Empty report still renders without crashing."""
    out = render_summary({})
    assert "Keen review" in out
    assert "not scored" in out


def test_render_summary_manual_review_flag() -> None:
    """A capture with manual_review_needed gets the warning marker."""
    report = {
        "score": {},
        "captures": [
            {
                "viewport": "mobile",
                "state": "default",
                "screen_path": "x.png",
                "manual_review_needed": True,
            }
        ],
    }
    out = render_summary(report)
    assert "manual-review-needed" in out


def _setup_compose_inputs(captures_dir: Path) -> None:
    """Create the minimum on-disk layout that compose() expects."""
    (captures_dir / "analysis").mkdir(parents=True, exist_ok=True)
    (captures_dir / "dom").mkdir(parents=True, exist_ok=True)
    (captures_dir / "analysis" / "desktop-default.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "index": 0,
                        "component_kind": "button",
                        "name": "Submit",
                        "viewport": "desktop",
                        "state": "default",
                        "box": {"x": 0, "y": 0, "w": 100, "h": 40},
                        "styles": {},
                        "findings": [
                            {"severity": "P0", "predicate_id": "test", "message": "test message"}
                        ],
                    },
                ],
                "summary": {"total_components": 1, "counts": {"P0": 1}},
            }
        )
    )
    (captures_dir / "dom" / "desktop-default.json").write_text(
        json.dumps(
            {
                "title": "Test Page",
                "url": "https://example.com/",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/desktop-default.png",
                    "banner_dismissal": {
                        "requested": True,
                        "dismissed": True,
                        "accepted_selector": "#accept",
                        "followup_selector": None,
                    },
                },
                "documentSize": {"w": 1280, "h": 800},
            }
        )
    )


def test_compose_assembles_report(tmp_path: Path) -> None:
    _setup_compose_inputs(tmp_path)
    report = compose(tmp_path, target_system="material-3")
    assert report["version"] == "0.8.0"
    assert report["target_system"] == "material-3"
    assert len(report["captures"]) == 1
    assert report["captures"][0]["title"] == "Test Page"
    # Score is populated by rubric.score.
    assert "grade" in report["score"]
    # Components carried through.
    assert len(report["components"]) == 1
    assert report["captures"][0]["banner_dismissal"]["dismissed"] is True


def test_compose_loads_nearest_project_design_context(tmp_path: Path) -> None:
    context_path = tmp_path / ".keen" / "design-context.json"
    context_path.parent.mkdir()
    payload = new_context("Northstar", stage="refine")
    payload["project"]["jobs"] = ["Choose a deployment"]
    context_path.write_text(json.dumps(payload))
    run = tmp_path / ".keen" / "review" / "run"
    _setup_compose_inputs(run)

    report = compose(run)

    assert report["design_context"]["project"]["name"] == "Northstar"
    brief = json.loads((run / "agent-brief.json").read_text())
    assert brief["design_context"]["project"]["jobs"] == ["Choose a deployment"]


def test_compose_with_no_inputs_returns_empty(tmp_path: Path) -> None:
    """A captures dir with no analysis/dom contents is explicitly unscored."""
    (tmp_path / "analysis").mkdir()
    (tmp_path / "dom").mkdir()
    report = compose(tmp_path)
    assert report["components"] == []
    assert report["captures"] == []
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["score"]["score"] is None
    assert report["coverage"]["provisional"] is True


def test_compose_scores_and_surfaces_global_findings(tmp_path: Path) -> None:
    """A page-wide P0 cannot disappear between analysis and report composition."""
    (tmp_path / "analysis").mkdir()
    (tmp_path / "dom").mkdir()
    (tmp_path / "analysis" / "desktop-default.json").write_text(
        json.dumps(
            {
                "components": [],
                "global_findings": [
                    {
                        "predicate_id": "focus.no-styles",
                        "severity": "P0",
                        "rule": "focus",
                        "message": "No authored focus styles.",
                    }
                ],
                "summary": {"counts": {"P0": 1}},
            }
        )
    )

    report = compose(tmp_path)

    assert report["score"]["counts"]["P0"] == 1
    assert report["score"]["grade"] != "A"
    assert report["global_findings"][0]["predicate_id"] == "focus.no-styles"
    assert report["top_findings"][0]["component_kind"] == "page"


def test_compose_loads_tokens_when_present(tmp_path: Path) -> None:
    """If tokens/extracted.json exists, it lands in report['tokens']."""
    _setup_compose_inputs(tmp_path)
    (tmp_path / "tokens").mkdir()
    (tmp_path / "tokens" / "extracted.json").write_text(
        json.dumps({"colors": {"foreground": [{"value": "rgb(0,0,0)", "count": 5}]}})
    )
    report = compose(tmp_path)
    assert report["tokens"]["colors"]["foreground"][0]["count"] == 5


def test_compose_legacy_tokens_path(tmp_path: Path) -> None:
    """Falls back to legacy tokens.json if tokens/extracted.json missing."""
    _setup_compose_inputs(tmp_path)
    (tmp_path / "tokens.json").write_text(json.dumps({"legacy": True}))
    report = compose(tmp_path)
    assert report["tokens"].get("legacy") is True


def test_compose_with_screenshot_exercises_crop_and_annotate(tmp_path: Path) -> None:
    """A real PNG present at the analyzed capture_path triggers PIL crop /
    annotate codepaths, raising overall report coverage."""
    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("PIL not installed")

    # Layout: screens/desktop-default.png at 800x600, plus a finding whose
    # box is fully inside the image.
    (tmp_path / "screens").mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 600), "white")
    img.save(tmp_path / "screens" / "desktop-default.png")

    (tmp_path / "analysis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dom").mkdir(parents=True, exist_ok=True)
    (tmp_path / "analysis" / "desktop-default.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "index": 0,
                        "component_kind": "button",
                        "name": "X",
                        "viewport": "desktop",
                        "state": "default",
                        "capture_path": "screens/desktop-default.png",
                        "device_pixel_ratio": 1.0,
                        "box": {"x": 100, "y": 100, "w": 100, "h": 40},
                        "styles": {},
                        "findings": [{"severity": "P0", "predicate_id": "p", "message": "m"}],
                    }
                ],
                "summary": {},
            }
        )
    )
    (tmp_path / "dom" / "desktop-default.json").write_text(
        json.dumps(
            {
                "title": "T",
                "url": "https://example.com/",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/desktop-default.png",
                },
            }
        )
    )
    report = compose(tmp_path)
    # Crop and annotated paths populated.
    assert any("crop_path" in c for c in report["components"] if c.get("findings"))
    assert "desktop-default" in report["annotated_overviews"]
    assert report["annotations"]["desktop-default"][0]["id"] == "A01"
    assert report["top_findings"][0]["annotation_id"] == "A01"
    annotated_path = tmp_path / report["annotated_overviews"]["desktop-default"]
    with Image.open(annotated_path) as annotated_image:
        assert annotated_image.height > img.height
    html_text = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Show evidence map" in html_text
    assert "Inspect detail" in html_text
    assert 'id="annotation-A01"' in html_text
    assert "locate A01" in html_text


def test_render_summary_with_top_findings_crop_path() -> None:
    """A top finding with a crop_path renders the crop hint."""
    report = {
        "score": {},
        "top_findings": [
            {
                "severity": "P1",
                "predicate_id": "p",
                "component_kind": "link",
                "viewport": "mobile",
                "state": "default",
                "crop_path": "crops/x.png",
                "message": "msg",
            }
        ],
    }
    out = render_summary(report)
    assert "crops/x.png" in out


def test_compose_surfaces_truncated_capture_as_provisional(tmp_path: Path) -> None:
    _setup_compose_inputs(tmp_path)
    dom_path = tmp_path / "dom" / "desktop-default.json"
    dom = json.loads(dom_path.read_text())
    dom["meta"]["truncated"] = True
    dom["coverage"] = {"complete": False, "reason": "visible-node-cap"}
    dom_path.write_text(json.dumps(dom))

    report = compose(tmp_path)
    assert report["coverage"]["provisional"] is True
    assert report["score"]["provisional"] is True
    assert "Partial audit" in render_summary(report)


def test_agent_brief_stays_bounded_for_large_component_inventory() -> None:
    report = {
        "version": "0.8.0",
        "coverage": {"complete": True, "provisional": False},
        "score": {"grade": "B", "score": 10, "counts": {"P1": 2}},
        "captures": [],
        "top_findings": [
            {
                "severity": "P1",
                "predicate_id": "spacing.off-grid",
                "message": "One bounded finding",
            }
        ],
        "components": [{"index": i, "styles": {"fontFamily": "x" * 500}} for i in range(5000)],
        "tokens": {"diagnostics": {"distinct_font_sizes": 9}, "raw": "x" * 100_000},
    }
    encoded = json.dumps(build_agent_brief(report))
    assert len(encoded) < 20_000
    assert "components" not in build_agent_brief(report)


def test_agent_brief_groups_responsive_duplicates_and_caps_unique_findings() -> None:
    findings = []
    for viewport in ("mobile", "desktop"):
        findings.append(
            {
                "severity": "P1",
                "predicate_id": "spacing.off-grid",
                "component_kind": "button",
                "message": "Button is off grid",
                "viewport": viewport,
                "state": "default",
            }
        )
    for index in range(12):
        findings.append(
            {
                "severity": "P2",
                "predicate_id": f"unique.{index}",
                "component_kind": "card",
                "message": f"Unique finding {index}",
                "viewport": "desktop",
                "state": "default",
            }
        )
    brief = build_agent_brief({"score": {}, "captures": [], "top_findings": findings})
    assert len(brief["top_findings"]) == 8
    grouped = brief["top_findings"][0]
    assert grouped["occurrences"] == 2
    assert {scope["viewport"] for scope in grouped["responsive_scopes"]} == {
        "mobile",
        "desktop",
    }


def test_agent_brief_carries_named_element_measurement_and_model_boundary() -> None:
    report = {
        "score": {"grade": "B", "score": 8, "counts": {"P1": 1}},
        "captures": [{"url": "https://example.com", "title": "Example"}],
        "components": [
            {
                "index": 7,
                "capture_path": "screens/mobile.png",
                "tag": "button",
                "role": "button",
                "name": "Save changes",
                "name_source": "browser-accessibility-tree",
                "text": "Save changes",
                "box": {"x": 10, "y": 20, "w": 90, "h": 28},
            }
        ],
        "top_findings": [
            {
                "severity": "P1",
                "predicate_id": "target.size-aa",
                "component_index": 7,
                "component_kind": "button",
                "capture_path": "screens/mobile.png",
                "message": "button measures 90x28px",
                "measured": {"width": 90, "height": 28},
                "expected": {"minimum_square": 44},
                "finding_id": "finding-01",
            }
        ],
        "design_context": {"project": {"name": "Example", "stage": "refine"}},
    }
    brief = build_agent_brief(report)
    finding = brief["top_findings"][0]
    assert finding["element"]["name"] == "Save changes"
    assert finding["measured"] == {"width": 90, "height": 28}
    assert finding["expected"] == {"minimum_square": 44}
    assert finding["judgment_required"] is True
    assert brief["design_context"]["project"]["stage"] == "refine"
    assert "not an overall visual-quality verdict" in brief["automated_signal_summary"]["meaning"]
    assert "model_decides" in brief["decision_contract"]


def test_capture_manifest_is_authoritative_for_report_coverage(tmp_path: Path) -> None:
    _setup_compose_inputs(tmp_path)
    (tmp_path / "capture-manifest.json").write_text(
        json.dumps(
            {
                "requested": [
                    {"viewport": "desktop", "state": "default"},
                    {"viewport": "mobile", "state": "default"},
                ],
                "succeeded": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "screen": "screens/desktop-default.png",
                        "dom": "dom/desktop-default.json",
                    }
                ],
                "failures": [{"viewport": "mobile", "state": "default", "reason": "timeout"}],
                "complete": False,
            }
        )
    )
    report = compose(tmp_path)
    assert report["coverage"]["captures_requested"] == 2
    assert report["coverage"]["captures_succeeded"] == 1
    assert report["coverage"]["capture_failures"] == 1
    assert report["coverage"]["provisional"] is True
