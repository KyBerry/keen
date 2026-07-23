"""Tests covering the rubric/severity registration and design-system-related
behaviour of the new audit-pass predicates.

The accessibility-focused per-predicate tests live in
``tests/test_predicates_a11y.py``; this file covers cross-cutting concerns
that don't belong with a single predicate:

  - Every new predicate id is registered in PREDICATE_SEVERITIES
  - Severities are well-formed (P0/P1/P2)
  - The new predicates wire into ``analyze_components()`` correctly
  - Findings are scored by ``rubric.score()`` with the right weights
  - Design-system threshold overrides interact correctly with new
    target-size predicate
"""

from __future__ import annotations

from harness.analyze import analyze_components
from harness.rubric import PREDICATE_SEVERITIES, score

# --- Severity registry sanity checks --------------------------------------

NEW_PREDICATE_IDS = {
    "name.link.context",
    "contrast.non-text",
    "label-in-name",
    "focus.positive-tabindex",
    "target.size-aa",
    "dialog.focus-trap-affordance",
    "dialog.initial-focus",
    "landmark.one-main",
    "landmark.duplicate",
    "form.required-indicator",
}


def test_every_new_predicate_has_registered_severity() -> None:
    """Each new predicate id must appear in PREDICATE_SEVERITIES."""
    missing = NEW_PREDICATE_IDS - set(PREDICATE_SEVERITIES.keys())
    assert not missing, f"Missing severities: {missing}"


def test_all_severities_well_formed() -> None:
    """All registered severities must be P0, P1, or P2."""
    bad = {pid: sev for pid, sev in PREDICATE_SEVERITIES.items() if sev not in {"P0", "P1", "P2"}}
    assert not bad, f"Malformed severities: {bad}"


def test_predicate_severities_match_conformance_confidence() -> None:
    """Deterministic failures outrank design-system and partial heuristics."""
    assert PREDICATE_SEVERITIES["hit-target.size"] == "P2"
    assert PREDICATE_SEVERITIES["contrast.text"] == "P0"
    assert PREDICATE_SEVERITIES["name.image"] == "P0"
    assert PREDICATE_SEVERITIES["focus.no-styles"] == "P2"
    assert PREDICATE_SEVERITIES["label.association"] == "P0"
    assert PREDICATE_SEVERITIES["spacing.grid"] == "P2"
    assert PREDICATE_SEVERITIES["link.distinguishable"] == "P2"


# --- analyze_components() integration -------------------------------------


def _comp(**overrides) -> dict:
    base = {
        "index": 0,
        "component_kind": "button",
        "role": "button",
        "tag": "button",
        "name": "Submit",
        "text": "Submit",
        "box": {"x": 100, "y": 200, "w": 80, "h": 80},
        "styles": {
            "color": "rgb(0, 0, 0)",
            "backgroundColor": "rgb(255, 255, 255)",
            "fontSize": "16px",
            "fontWeight": "400",
        },
        "viewport_width": 1280,
        "viewport": "desktop",
        "state": "default",
        "tab_index": 0,
    }
    base.update(overrides)
    return base


def test_new_predicates_appear_in_summary_counts() -> None:
    """When a new predicate fires, the analysis summary should count it."""
    comps = [_comp(tab_index=5)]  # triggers focus.positive-tabindex
    out = analyze_components(comps)
    assert out["summary"]["by_predicate"].get("focus.positive-tabindex", 0) >= 1
    assert out["summary"]["counts"]["P1"] >= 1


def test_label_in_name_does_not_fire_on_irrelevant_kinds() -> None:
    """A heading isn't an interactive control; the predicate should skip."""
    comps = [
        _comp(
            component_kind="heading-2",
            role="heading",
            tag="h2",
            name="Welcome",
            text="Goodbye",  # would otherwise look like a label-in-name fail
        )
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "label-in-name" not in pids


def test_dialog_predicates_skip_non_dialog_pages() -> None:
    """A button-only page must not produce dialog findings."""
    comps = [_comp()]
    out = analyze_components(comps)
    pids = set(out["summary"]["by_predicate"].keys())
    assert "dialog.focus-trap-affordance" not in pids
    assert "dialog.initial-focus" not in pids


# --- Scoring integration --------------------------------------------------


def test_new_predicates_contribute_to_damage_score() -> None:
    """A P1 finding from a new predicate must count for 3 damage by default."""
    comps = [_comp(tab_index=5)]  # P1
    analysis = analyze_components(comps)
    scored = score(analysis)
    # at least 3 damage points from the P1 finding
    assert scored["score"] >= 3.0
    assert scored["counts"].get("P1", 0) >= 1


def test_landmark_one_main_fires_global() -> None:
    """Verify the global landmark.one-main predicate produces a finding for
    pages with zero main landmarks but substantial content."""
    comps = [
        _comp(
            index=0,
            component_kind="heading-1",
            role="heading",
            tag="h1",
            name="Big Title",
            text="Big Title",
            box={"x": 0, "y": 0, "w": 300, "h": 36},
        )
    ]
    out = analyze_components(comps)
    assert out["summary"]["by_predicate"].get("landmark.one-main", 0) >= 1


# --- Design-system threshold interaction ----------------------------------


def test_target_size_aa_does_not_duplicate_hit_target_findings() -> None:
    """Unnamed reviews emit only the WCAG AA rule, with no system duplicate."""
    comps = [
        _comp(index=0, box={"x": 0, "y": 0, "w": 20, "h": 20}),
        _comp(index=1, box={"x": 22, "y": 0, "w": 20, "h": 20}),
    ]
    analyze_components(comps)
    pids = [f["predicate_id"] for f in comps[0].get("findings", [])]
    assert "hit-target.size" not in pids
    assert "target.size-aa" in pids


def test_non_text_contrast_uses_threshold_from_context() -> None:
    """Verify the predicate reads contrast_nontext_aa from the thresholds dict."""
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email",
            text="",
            styles={
                "color": "rgb(0,0,0)",
                "backgroundColor": "rgb(255, 255, 255)",
                "borderColor": "rgb(200, 200, 200)",
                "borderTopColor": "rgb(200, 200, 200)",
                "fontSize": "16px",
            },
        )
    ]
    out = analyze_components(comps, target_system="material-3")
    # The default contrast_nontext_aa stays 3.0 across systems; the
    # finding should still fire at material-3 levels.
    pids = {f["predicate_id"] for c in out["components"] for f in c.get("findings", [])}
    assert "contrast.non-text" in pids


# --- Edge cases / robustness ----------------------------------------------


def test_dialog_focus_trap_handles_missing_parent_index() -> None:
    """If a comp has parent_index=-1, the descendant walk must terminate."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="X",
            text="X",
        ),
        _comp(
            index=1,
            component_kind="button",
            role="button",
            tag="button",
            name="Sibling",
            text="Sibling",
            parent_index=-1,  # not a descendant of the dialog
        ),
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for c in comps for f in c.get("findings", [])}
    # No focusable child of the dialog → fires.
    assert "dialog.focus-trap-affordance" in pids


def test_required_indicator_handles_empty_name_gracefully() -> None:
    """Empty name doesn't crash; label.association handles that case anyway."""
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="",
            text="",
            required=True,
        )
    ]
    out = analyze_components(comps)
    pids = {f["predicate_id"] for c in out["components"] for f in c.get("findings", [])}
    # label.association fires, form.required-indicator stays quiet.
    assert "label.association" in pids
    assert "form.required-indicator" not in pids


def test_label_in_name_handles_punctuation_only_visible_text() -> None:
    """A visible label that's all punctuation normalizes to empty and is
    treated as 'no visible label' rather than a spurious finding."""
    comps = [
        _comp(
            component_kind="button",
            name="Submit",
            text="...",  # punctuation only
        )
    ]
    out = analyze_components(comps)
    pids = {f["predicate_id"] for c in out["components"] for f in c.get("findings", [])}
    assert "label-in-name" not in pids
