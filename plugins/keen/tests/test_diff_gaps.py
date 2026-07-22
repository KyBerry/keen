"""Gap tests for harness/diff.py.

The property tests cover the algorithmic invariants; these target
render_markdown specifically (different in/out semantics — markdown
formatting, score arrow, truncation) and a few diff_runs edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.diff import diff_runs, render_markdown


def _write_run(base: Path, name: str, report: dict) -> Path:
    rd = base / name
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.json").write_text(json.dumps(report))
    return rd


def test_render_markdown_score_arrow_decreased() -> None:
    """Damage drop -> ↘ arrow."""
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 20, "grade": "C"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "↘" in md
    assert "C → A" in md


def test_render_markdown_score_arrow_increased() -> None:
    """Damage rise -> ↗ arrow."""
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 20, "grade": "C"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "↗" in md


def test_render_markdown_no_change_arrow_flat() -> None:
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "→" in md
    assert "_(none — clean run)_" in md


def test_render_markdown_renders_added_removed_sections() -> None:
    """Each finding category gets its own section heading."""
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 3, "grade": "A"},
        "added": [
            {
                "severity": "P0",
                "predicate_id": "new",
                "component_kind": "btn",
                "viewport": "m",
                "state": "d",
                "message": "n",
            }
        ],
        "removed": [
            {
                "severity": "P1",
                "predicate_id": "fixed",
                "component_kind": "lnk",
                "viewport": "m",
                "state": "d",
                "message": "x",
            }
        ],
        "unchanged": [
            {
                "severity": "P2",
                "predicate_id": "still",
                "component_kind": "txt",
                "viewport": "m",
                "state": "d",
                "message": "y",
            }
        ],
    }
    md = render_markdown(result)
    assert "## Fixed in B" in md
    assert "## New in B" in md
    assert "## Still present" in md
    assert "fixed" in md
    assert "new" in md
    assert "still" in md


def test_render_markdown_truncates_long_lists() -> None:
    """Lists longer than the per-section cap show 'and N more'."""
    added = [
        {
            "severity": "P0",
            "predicate_id": f"p{i}",
            "component_kind": "btn",
            "viewport": "m",
            "state": "d",
            "message": "m",
        }
        for i in range(60)
    ]
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": added,
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "10 more" in md  # 60 - 50 = 10


def test_diff_runs_with_score_blocks(tmp_path: Path) -> None:
    """diff_runs propagates score_a/score_b from each report."""
    rep_a = {"components": [], "score": {"score": 1, "grade": "A"}}
    rep_b = {"components": [], "score": {"score": 5, "grade": "B"}}
    run_a = _write_run(tmp_path, "a", rep_a)
    run_b = _write_run(tmp_path, "b", rep_b)
    result = diff_runs(run_a, run_b)
    assert result["score_a"] == {"score": 1, "grade": "A"}
    assert result["score_b"] == {"score": 5, "grade": "B"}


def test_diff_runs_unchanged_findings(tmp_path: Path) -> None:
    """Findings present in both runs land in the unchanged bucket."""
    finding = {
        "severity": "P1",
        "predicate_id": "stable",
    }
    component = {
        "component_kind": "button",
        "component_index": 0,
        "viewport": "mobile",
        "state": "default",
        "findings": [finding],
    }
    report = {"components": [component], "score": {}}
    run_a = _write_run(tmp_path, "a", report)
    run_b = _write_run(tmp_path, "b", report)
    result = diff_runs(run_a, run_b)
    assert len(result["unchanged"]) == 1
    assert result["added"] == [] and result["removed"] == []


def test_diff_uses_production_index_without_collapsing_components(tmp_path: Path) -> None:
    base = {
        "component_kind": "button",
        "viewport": "desktop",
        "state": "default",
        "capture_path": "screens/page-desktop-default.png",
        "findings": [{"predicate_id": "contrast.text", "severity": "P0"}],
    }
    report = {
        "components": [{**base, "index": 10}, {**base, "index": 11}],
        "score": {},
    }
    run_a = _write_run(tmp_path, "a", report)
    run_b = _write_run(tmp_path, "b", {"components": [], "score": {}})

    result = diff_runs(run_a, run_b)

    assert len(result["removed"]) == 2
    assert {finding["component_index"] for finding in result["removed"]} == {10, 11}


def test_diff_production_index_takes_precedence_over_legacy_field(tmp_path: Path) -> None:
    component = {
        "component_kind": "button",
        "viewport": "desktop",
        "state": "default",
        "index": 8,
        "component_index": 999,
        "findings": [{"predicate_id": "name.icon-button", "severity": "P0"}],
    }
    report = {"components": [component], "score": {}}
    run_a = _write_run(tmp_path, "a", report)
    run_b = _write_run(tmp_path, "b", report)

    result = diff_runs(run_a, run_b)

    assert result["unchanged"][0]["component_index"] == 8


def test_diff_capture_path_scopes_reused_component_indexes(tmp_path: Path) -> None:
    base = {
        "component_kind": "button",
        "viewport": "desktop",
        "state": "default",
        "index": 4,
        "findings": [{"predicate_id": "hit-target.size", "severity": "P0"}],
    }
    report = {
        "components": [
            {**base, "capture_path": "screens/home-desktop-default.png"},
            {**base, "capture_path": "screens/settings-desktop-default.png"},
        ],
        "score": {},
    }
    run_a = _write_run(tmp_path, "a", report)
    run_b = _write_run(tmp_path, "b", {"components": [], "score": {}})

    result = diff_runs(run_a, run_b)

    assert len(result["removed"]) == 2
    assert len({finding["capture_scope"] for finding in result["removed"]}) == 2
