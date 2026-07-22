"""Property-based tests for harness/rubric.py.

The actual rubric API:
- `score(analysis_dict)`: takes {"components": [...]}, NOT a flat finding list.
- `top_findings(analysis_dict, limit, per_predicate_cap)`: same shape.
- `grade_for(damage, thresholds)`: numeric damage -> grade entry.

Hypothesis strategy docs: https://hypothesis.readthedocs.io/en/latest/data.html
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from harness.rubric import (
    DEFAULT_GRADE_THRESHOLDS,
    DEFAULT_WEIGHTS,
    grade_for,
    score,
    top_findings,
)

_SEVERITIES = ["P0", "P1", "P2"]
_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


# --- Strategy builders ----------------------------------------------------

_finding = st.fixed_dictionaries(
    {
        "severity": st.sampled_from(_SEVERITIES),
        "predicate_id": st.text(alphabet="abcdef0123", min_size=1, max_size=6),
        "message": st.text(min_size=0, max_size=20),
    }
)

_component_kind = st.sampled_from(["button", "link", "text", "image", "label", "input"])


@st.composite
def _component(draw):
    return {
        "component_kind": draw(_component_kind),
        "index": draw(st.integers(min_value=0, max_value=100)),
        "viewport": draw(st.sampled_from(["mobile", "desktop"])),
        "state": draw(st.sampled_from(["default", "hover", "focus"])),
        "findings": draw(st.lists(_finding, min_size=0, max_size=4)),
    }


_analysis = st.builds(
    lambda comps: {"components": comps},
    st.lists(_component(), min_size=0, max_size=8),
)


# --- grade_for properties -------------------------------------------------


def test_grade_for_zero_damage_is_A() -> None:
    """No damage -> top grade A."""
    g = grade_for(0, DEFAULT_GRADE_THRESHOLDS)
    assert g["grade"] == "A"


@given(damage=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False))
@settings(max_examples=200)
def test_grade_for_returns_valid_letter(damage: float) -> None:
    """grade_for must always return a letter grade from the threshold list."""
    g = grade_for(damage, DEFAULT_GRADE_THRESHOLDS)
    assert g["grade"] in {"A", "B", "C", "D", "F"}


@given(
    d1=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
    d2=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
)
@settings(max_examples=200)
def test_grade_for_monotone_in_damage(d1: float, d2: float) -> None:
    """Higher damage -> grade no better.

    Letter order: A < B < C < D < F (rank ascending = worse).
    """
    rank = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    g1 = grade_for(d1, DEFAULT_GRADE_THRESHOLDS)
    g2 = grade_for(d2, DEFAULT_GRADE_THRESHOLDS)
    if d1 <= d2:
        assert rank[g1["grade"]] <= rank[g2["grade"]]
    else:
        assert rank[g1["grade"]] >= rank[g2["grade"]]


# --- score properties ----------------------------------------------------


@given(analysis=_analysis)
@settings(max_examples=200)
def test_score_required_keys_present(analysis: dict) -> None:
    """score() returns dict with required keys for any analysis."""
    out = score(analysis)
    for k in ("score", "counts", "weights", "by_component_kind", "grade", "grade_summary"):
        assert k in out


@given(analysis=_analysis)
@settings(max_examples=200)
def test_score_value_non_negative(analysis: dict) -> None:
    """Damage score >= 0 for any list of findings."""
    out = score(analysis)
    assert out["score"] >= 0


def test_score_all_p0_outweighs_all_p2() -> None:
    """Same-length all-P0 findings yield higher damage than all-P2."""
    p0_analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": i,
                "findings": [{"severity": "P0", "predicate_id": "x"}],
            }
            for i in range(5)
        ]
    }
    p2_analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": i,
                "findings": [{"severity": "P2", "predicate_id": "x"}],
            }
            for i in range(5)
        ]
    }
    p0 = score(p0_analysis)
    p2 = score(p2_analysis)
    assert p0["score"] > p2["score"]


@given(analysis=_analysis)
@settings(max_examples=100)
def test_score_no_findings_means_zero_damage(analysis: dict) -> None:
    """If no component has any findings, damage is zero and grade is A."""
    # Replace all findings with empty lists.
    clean = {"components": [{**c, "findings": []} for c in analysis["components"]]}
    out = score(clean)
    assert out["score"] == 0
    assert out["grade"] == "A"


def test_score_weights_match_default() -> None:
    """When using defaults, weights mirror DEFAULT_WEIGHTS for severities present."""
    analysis = {"components": [{"component_kind": "button", "index": 0, "findings": []}]}
    out = score(analysis)
    assert out["weights"] == DEFAULT_WEIGHTS


# --- top_findings properties --------------------------------------------


@given(analysis=_analysis, limit=st.integers(min_value=0, max_value=20))
@settings(max_examples=200)
def test_top_findings_respects_limit(analysis: dict, limit: int) -> None:
    """top_findings returns at most `limit` items (including limit=0)."""
    out = top_findings(analysis, limit=limit)
    assert len(out) <= limit


def test_top_findings_zero_limit_returns_empty() -> None:
    """Regression test for the limit=0 short-circuit (fixed in rubric.py).

    Originally found by hypothesis: the main loop appended one finding before
    the `len(out) >= limit` break check fired, so limit=0 leaked a single item.
    """
    analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": 0,
                "viewport": "mobile",
                "state": "default",
                "findings": [{"severity": "P0", "predicate_id": "x"}],
            }
        ]
    }
    out = top_findings(analysis, limit=0)
    assert out == []


@given(analysis=_analysis)
@settings(max_examples=200)
def test_top_findings_preserves_severity_order(analysis: dict) -> None:
    """Within the cap-respecting selection, severities are non-decreasing
    in rank (P0 before P1 before P2)."""
    out = top_findings(analysis, limit=100, per_predicate_cap=100)
    ranks = [_SEVERITY_ORDER.get(f["severity"], 9) for f in out]
    assert ranks == sorted(ranks)


def test_top_findings_empty() -> None:
    """Empty analysis -> empty list."""
    assert top_findings({"components": []}) == []
