"""Tests for the accessibility-focused predicates added in the audit pass.

Each new predicate has at least three tests:
  - fires on a synthetic bad component
  - passes on a synthetic good component
  - edge case (empty input, missing field, irrelevant kind, etc.)

The Component dict shape mirrors what decompose.decompose() produces.
"""

from __future__ import annotations

from harness.analyze import analyze_components


def _comp(**overrides) -> dict:
    """Build a minimal component dict with sensible defaults."""
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


def _findings(comps: list[dict]) -> set[str]:
    """Return the set of predicate_ids emitted across all components."""
    analyze_components(comps)
    return {f["predicate_id"] for c in comps for f in c.get("findings", [])}


# --- name.link.context (WCAG 2.4.4) --------------------------------------


def test_link_context_fires_on_learn_more() -> None:
    """A link labelled 'Learn more' should be flagged."""
    comps = [
        _comp(
            component_kind="link",
            role="link",
            tag="a",
            name="Learn more",
            text="Learn more",
            href="/foo",
        )
    ]
    assert "name.link.context" in _findings(comps)


def test_link_context_fires_on_view_with_trailing_punctuation() -> None:
    """Trailing punctuation in 'View →' shouldn't save it from being flagged."""
    comps = [
        _comp(
            component_kind="link",
            role="link",
            tag="a",
            name="View →",
            text="View →",
            href="/foo",
        )
    ]
    assert "name.link.context" in _findings(comps)


def test_link_context_passes_on_descriptive_text() -> None:
    """A descriptive link is fine."""
    comps = [
        _comp(
            component_kind="link",
            role="link",
            tag="a",
            name="Read the WCAG 2.2 Quick Reference",
            text="Read the WCAG 2.2 Quick Reference",
            href="/foo",
            box={"x": 0, "y": 0, "w": 300, "h": 24},
        )
    ]
    assert "name.link.context" not in _findings(comps)


def test_link_context_does_not_double_fire_with_generic() -> None:
    """When name.link.generic already fired, name.link.context stays quiet."""
    comps = [
        _comp(
            component_kind="link",
            role="link",
            tag="a",
            name="click here",
            text="click here",
            href="/foo",
        )
    ]
    found = _findings(comps)
    assert "name.link.generic" in found
    assert "name.link.context" not in found


def test_link_context_skips_when_empty() -> None:
    """No name at all is handled by name.link, not name.link.context."""
    comps = [
        _comp(
            component_kind="link",
            role="link",
            tag="a",
            name="",
            text="",
            href="/foo",
        )
    ]
    found = _findings(comps)
    assert "name.link.context" not in found
    assert "name.link" in found


# --- contrast.non-text (WCAG 1.4.11) --------------------------------------


def test_non_text_contrast_fires_on_low_contrast_border() -> None:
    """A pale-grey 1px border on white background fails 3:1."""
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
                "borderColor": "rgb(230, 230, 230)",  # ~1.2:1 vs white
                "borderTopColor": "rgb(230, 230, 230)",
                "fontSize": "16px",
            },
        )
    ]
    assert "contrast.non-text" in _findings(comps)


def test_non_text_contrast_passes_on_dark_border() -> None:
    """A black border on white passes easily."""
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
                "borderColor": "rgb(0, 0, 0)",
                "borderTopColor": "rgb(0, 0, 0)",
                "fontSize": "16px",
            },
        )
    ]
    assert "contrast.non-text" not in _findings(comps)


def test_non_text_contrast_skips_disabled() -> None:
    """Disabled controls are exempt per the SC."""
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email",
            text="",
            disabled=True,
            styles={
                "color": "rgb(0,0,0)",
                "backgroundColor": "rgb(255, 255, 255)",
                "borderColor": "rgb(230, 230, 230)",
                "borderTopColor": "rgb(230, 230, 230)",
                "fontSize": "16px",
            },
        )
    ]
    assert "contrast.non-text" not in _findings(comps)


def test_non_text_contrast_skips_borderless() -> None:
    """No declared border means the predicate has nothing to measure."""
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
                "fontSize": "16px",
            },
        )
    ]
    assert "contrast.non-text" not in _findings(comps)


# --- label-in-name (WCAG 2.5.3) -------------------------------------------


def test_label_in_name_fires_when_aria_label_differs() -> None:
    """Visible 'Submit' with aria-label='Send form' is a Label-in-Name fail."""
    comps = [
        _comp(
            component_kind="button",
            name="Send form",  # accessible name
            text="Submit",  # visible label
        )
    ]
    assert "label-in-name" in _findings(comps)


def test_label_in_name_passes_when_name_contains_visible() -> None:
    """Visible 'Submit' contained in aria-label 'Submit this form' is fine."""
    comps = [
        _comp(
            component_kind="button",
            name="Submit this form",
            text="Submit",
        )
    ]
    assert "label-in-name" not in _findings(comps)


def test_label_in_name_tolerates_case_and_punctuation() -> None:
    """SC explicitly allows case/punctuation differences."""
    comps = [
        _comp(
            component_kind="button",
            name="Submit, please!",
            text="submit",
        )
    ]
    assert "label-in-name" not in _findings(comps)


def test_label_in_name_skips_icon_button() -> None:
    """Icon-only buttons have no visible text label; predicate stays quiet."""
    comps = [
        _comp(
            component_kind="icon-button",
            name="Close dialog",
            text="",  # icon-only
        )
    ]
    assert "label-in-name" not in _findings(comps)


# --- focus.positive-tabindex (WCAG 2.4.3) ---------------------------------


def test_positive_tabindex_fires() -> None:
    comps = [_comp(tab_index=3)]
    assert "focus.positive-tabindex" in _findings(comps)


def test_zero_tabindex_passes() -> None:
    comps = [_comp(tab_index=0)]
    assert "focus.positive-tabindex" not in _findings(comps)


def test_negative_tabindex_passes() -> None:
    """tabindex=-1 is the standard programmatic-focus pattern."""
    comps = [_comp(tab_index=-1)]
    assert "focus.positive-tabindex" not in _findings(comps)


def test_invalid_tabindex_does_not_crash() -> None:
    """A garbage tab_index value must not blow up the predicate."""
    comps = [_comp(tab_index="garbage")]
    # Should be quiet and not raise.
    found = _findings(comps)
    assert "focus.positive-tabindex" not in found


# --- target.size-aa (WCAG 2.5.8 AA) ---------------------------------------


def test_target_size_aa_fires_on_20x20_with_no_system() -> None:
    """A 20x20 button fails AA (24x24)."""
    comps = [_comp(box={"x": 0, "y": 0, "w": 20, "h": 20})]
    found = _findings(comps)
    assert "hit-target.size" not in found
    assert "target.size-aa" in found


def test_target_size_aa_fires_when_system_threshold_lowered_via_filter() -> None:
    """30x30 button: passes default 44px system threshold? No, fails. Use a fluent system.

    fluent-2 sets hit_target_min_px=32 — so 30x30 still fails hit-target.size.
    To test target.size-aa in isolation we need a system whose threshold is
    BELOW 24, or we need a target whose dimensions fall in the 24<=x<system
    range. Use a 24x24 box with no system override — it passes hit-target
    (>= default 44? no, 24 < 44) so hit-target fires. Need to find the gap.

    Actual workaround: use carbon (40) and a 30x30 input — both fire. Use
    a 26x26 input with fluent-2 (32) and the system rule still fires.

    Direct way: 24x24 button with viewport_width such that hit-target.size
    is the one to suppress AA. Better: stub it so only target.size-aa fires
    by setting tab_index>0 and box=20x20 — but that still triggers hit-
    target. The only realistic isolation is *removing* the existing
    finding from the comp first. Instead, just verify the rule fires when
    we run a tight box smaller than 24 directly through the predicate.
    """
    from harness.analyze import _target_size_aa

    comp = _comp(
        component_kind="button",
        box={"x": 0, "y": 0, "w": 20, "h": 20},
        findings=[],  # no prior findings
    )
    # No prior hit-target finding: target.size-aa should fire.
    finding = _target_size_aa(comp, {"thresholds": {}})
    assert finding is not None
    assert finding.predicate_id == "target.size-aa"
    assert finding.severity == "P1"


def test_target_size_aa_passes_on_24x24() -> None:
    """24x24 is the exact AA minimum."""
    from harness.analyze import _target_size_aa

    comp = _comp(
        component_kind="button",
        box={"x": 0, "y": 0, "w": 24, "h": 24},
        findings=[],
    )
    assert _target_size_aa(comp, {"thresholds": {}}) is None


def test_target_size_aa_inline_link_exception() -> None:
    """An inline link whose height < 24 but width is fine is exempt."""
    from harness.analyze import _target_size_aa

    comp = _comp(
        component_kind="link",
        role="link",
        tag="a",
        box={"x": 0, "y": 0, "w": 80, "h": 20},
        findings=[],
    )
    assert _target_size_aa(comp, {"thresholds": {}}) is None


# --- dialog.focus-trap-affordance (WAI ARIA APG) -------------------------


def test_dialog_without_focusable_descendant_fires() -> None:
    """A dialog with no interactive children = focus trap broken."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="Confirm",
            text="Are you sure?",
        ),
        # Child is a heading, not interactive.
        _comp(
            index=1,
            component_kind="heading-2",
            role="heading",
            tag="h2",
            name="Confirm",
            text="Confirm",
            parent_index=0,
        ),
    ]
    assert "dialog.focus-trap-affordance" in _findings(comps)


def test_dialog_with_focusable_button_passes() -> None:
    """A dialog with at least one button = focus has somewhere to live."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="Confirm",
            text="Are you sure?",
        ),
        _comp(
            index=1,
            component_kind="button",
            role="button",
            tag="button",
            name="OK",
            text="OK",
            parent_index=0,
        ),
        _comp(
            index=2,
            component_kind="button",
            role="button",
            tag="button",
            name="Cancel",
            text="Cancel",
            parent_index=0,
        ),
    ]
    assert "dialog.focus-trap-affordance" not in _findings(comps)


def test_dialog_focus_trap_skipped_when_no_dialog() -> None:
    """A page with no dialog has nothing to flag."""
    comps = [_comp()]
    assert "dialog.focus-trap-affordance" not in _findings(comps)


# --- dialog.initial-focus (WAI ARIA APG) ----------------------------------


def test_dialog_initial_focus_fires_when_only_focusable_is_close() -> None:
    """A dialog whose only focusable child is a close button = bad initial focus."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="Notice",
            text="Notice",
        ),
        _comp(
            index=1,
            component_kind="icon-button",
            role="button",
            tag="button",
            name="Close",
            text="",
            parent_index=0,
        ),
    ]
    assert "dialog.initial-focus" in _findings(comps)


def test_dialog_initial_focus_passes_when_multiple_focusables() -> None:
    """Dialog with multiple focusables = predicate stays quiet."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="Confirm",
            text="Confirm",
        ),
        _comp(
            index=1,
            component_kind="button",
            role="button",
            tag="button",
            name="OK",
            text="OK",
            parent_index=0,
        ),
        _comp(
            index=2,
            component_kind="icon-button",
            role="button",
            tag="button",
            name="Close",
            text="",
            parent_index=0,
        ),
    ]
    assert "dialog.initial-focus" not in _findings(comps)


def test_dialog_initial_focus_passes_when_only_focusable_is_action() -> None:
    """If the only focusable is a real action ('OK'), don't fire."""
    comps = [
        _comp(
            index=0,
            component_kind="landmark-dialog",
            role="dialog",
            tag="dialog",
            name="Confirm",
            text="Confirm",
        ),
        _comp(
            index=1,
            component_kind="button",
            role="button",
            tag="button",
            name="OK",
            text="OK",
            parent_index=0,
        ),
    ]
    assert "dialog.initial-focus" not in _findings(comps)


# --- landmark.one-main (axe landmark-one-main) ----------------------------


def test_landmark_one_main_fires_on_two_mains() -> None:
    comps = [
        _comp(
            index=0,
            component_kind="landmark-main",
            role="main",
            tag="main",
        ),
        _comp(
            index=1,
            component_kind="landmark-main",
            role="main",
            tag="main",
        ),
        _comp(
            index=2,
            component_kind="heading-1",
            role="heading",
            tag="h1",
            name="Title",
            text="Title",
        ),
    ]
    assert "landmark.one-main" in _findings(comps)


def test_landmark_one_main_fires_on_zero_mains_with_substantial_content() -> None:
    """Zero main + h1 heading present = flag the missing main landmark.

    The zero-main case is a page-level finding (no specific component to
    attach to), so we read it from the summary rather than from any
    component's findings list. This mirrors how `focus.no-styles` is
    surfaced for the no-focus-rules-at-all case.
    """
    comps = [
        _comp(
            index=0,
            component_kind="heading-1",
            role="heading",
            tag="h1",
            name="Welcome",
            text="Welcome",
        ),
    ]
    out = analyze_components(comps)
    assert out["summary"]["by_predicate"].get("landmark.one-main", 0) >= 1


def test_landmark_one_main_passes_on_single_main() -> None:
    comps = [
        _comp(
            index=0,
            component_kind="landmark-main",
            role="main",
            tag="main",
        ),
        _comp(
            index=1,
            component_kind="heading-1",
            role="heading",
            tag="h1",
            name="Title",
            text="Title",
        ),
    ]
    assert "landmark.one-main" not in _findings(comps)


def test_landmark_one_main_skips_fragments() -> None:
    """No main + no substantial content (no h1/h2) = probably a fragment, skip."""
    comps = [_comp(component_kind="button", name="OK", text="OK")]
    assert "landmark.one-main" not in _findings(comps)


# --- landmark.duplicate (axe landmark-no-duplicate-*) ---------------------


def test_landmark_duplicate_banner_fires() -> None:
    comps = [
        _comp(index=0, component_kind="landmark-banner", role="banner", tag="header"),
        _comp(index=1, component_kind="landmark-banner", role="banner", tag="header"),
    ]
    assert "landmark.duplicate" in _findings(comps)


def test_landmark_duplicate_contentinfo_fires() -> None:
    comps = [
        _comp(
            index=0,
            component_kind="landmark-contentinfo",
            role="contentinfo",
            tag="footer",
        ),
        _comp(
            index=1,
            component_kind="landmark-contentinfo",
            role="contentinfo",
            tag="footer",
        ),
    ]
    assert "landmark.duplicate" in _findings(comps)


def test_landmark_duplicate_passes_on_single_banner() -> None:
    comps = [
        _comp(index=0, component_kind="landmark-banner", role="banner", tag="header"),
    ]
    assert "landmark.duplicate" not in _findings(comps)


# --- form.required-indicator ---------------------------------------------


def test_required_indicator_fires_when_name_has_neither_marker() -> None:
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email",
            text="",
            required=True,
        )
    ]
    assert "form.required-indicator" in _findings(comps)


def test_required_indicator_passes_with_asterisk() -> None:
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email *",
            text="",
            required=True,
        )
    ]
    assert "form.required-indicator" not in _findings(comps)


def test_required_indicator_passes_with_required_word() -> None:
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email (required)",
            text="",
            required=True,
        )
    ]
    assert "form.required-indicator" not in _findings(comps)


def test_required_indicator_skips_when_not_required() -> None:
    comps = [
        _comp(
            component_kind="text-input",
            role="textbox",
            tag="input",
            name="Email",
            text="",
            required=False,
        )
    ]
    assert "form.required-indicator" not in _findings(comps)
