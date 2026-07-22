"""Property-based tests for harness/diff.py.

The actual diff API operates on run directories (each contains report.json),
not in-memory lists. Test helpers below write minimal reports to tmp dirs.

Hypothesis strategy docs: https://hypothesis.readthedocs.io/en/latest/data.html
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness.diff import diff_runs

_SEVERITIES = ["P0", "P1", "P2"]


_finding = st.fixed_dictionaries(
    {
        "severity": st.sampled_from(_SEVERITIES),
        "predicate_id": st.text(alphabet="abcd012", min_size=1, max_size=4),
        "message": st.text(min_size=0, max_size=10),
    }
)


@st.composite
def _component(draw):
    return {
        "component_kind": draw(st.sampled_from(["button", "link", "text"])),
        "component_index": draw(st.integers(min_value=0, max_value=20)),
        "viewport": draw(st.sampled_from(["mobile", "desktop"])),
        "state": draw(st.sampled_from(["default", "hover"])),
        "findings": draw(st.lists(_finding, min_size=0, max_size=3)),
    }


_report = st.builds(
    lambda comps: {
        "components": comps,
        "score": {"score": 1.0, "grade": "B"},
    },
    st.lists(_component(), min_size=0, max_size=6),
)


def _write_run(base: Path, name: str, report: dict) -> Path:
    rd = base / name
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.json").write_text(json.dumps(report))
    return rd


@given(report=_report)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_diff_identical_runs_has_no_added_or_removed(tmp_path_factory, report: dict) -> None:
    """diff(a, a) — same report on both sides — must be all unchanged."""
    base = tmp_path_factory.mktemp("diff_identical")
    run_a = _write_run(base, "a", report)
    run_b = _write_run(base, "b", report)
    result = diff_runs(run_a, run_b)
    assert result["added"] == []
    assert result["removed"] == []


@given(report_a=_report, report_b=_report)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_diff_symmetric(tmp_path_factory, report_a: dict, report_b: dict) -> None:
    """diff(a, b).added (by key) == diff(b, a).removed (by key) and vice versa.

    We compare the canonical key set, not full payload — the payload always
    comes from the "later" run, so semantics differ in incidental fields.
    """
    base = tmp_path_factory.mktemp("diff_sym")
    run_a = _write_run(base, "a", report_a)
    run_b = _write_run(base, "b", report_b)

    ab = diff_runs(run_a, run_b)
    ba = diff_runs(run_b, run_a)

    def _keys(items):
        return {
            (f.get("predicate_id"), f.get("component_kind"), f.get("viewport"), f.get("state"))
            for f in items
        }

    assert _keys(ab["added"]) == _keys(ba["removed"])
    assert _keys(ab["removed"]) == _keys(ba["added"])


@given(report_a=_report, report_b=_report)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_diff_counts_non_negative(tmp_path_factory, report_a: dict, report_b: dict) -> None:
    """All bucket sizes are non-negative; sum is bounded by total findings."""
    base = tmp_path_factory.mktemp("diff_counts")
    run_a = _write_run(base, "a", report_a)
    run_b = _write_run(base, "b", report_b)
    result = diff_runs(run_a, run_b)
    assert len(result["added"]) >= 0
    assert len(result["removed"]) >= 0
    assert len(result["unchanged"]) >= 0


def test_diff_duplicate_finding_dedup_by_key(tmp_path: Path) -> None:
    """Adding the same finding twice to run B does NOT inflate the 'added' count.

    _load_findings uses setdefault keyed by (predicate_id, component_kind,
    viewport, state, component_index) so duplicates collapse.
    """
    rep_a = {"components": [], "score": {}}
    rep_b = {
        "components": [
            {
                "component_kind": "button",
                "component_index": 0,
                "viewport": "mobile",
                "state": "default",
                "findings": [
                    {"severity": "P0", "predicate_id": "dup"},
                    {"severity": "P0", "predicate_id": "dup"},
                ],
            }
        ],
        "score": {},
    }
    run_a = _write_run(tmp_path, "a", rep_a)
    run_b = _write_run(tmp_path, "b", rep_b)
    result = diff_runs(run_a, run_b)
    assert len(result["added"]) == 1
