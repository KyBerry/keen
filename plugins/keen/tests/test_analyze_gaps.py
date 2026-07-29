"""Gap tests for harness/analyze.py.

Exercise the predicate runner and a handful of representative predicates
end-to-end via analyze_components(). Each component below is a minimal
dict matching the shape that decompose.decompose() produces.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from harness.analyze import (
    _build_pixel_sampler,
    _tap_target_overlap,
    analyze_components,
    analyze_file,
)


def _comp(**overrides) -> dict:
    """Build a minimal component dict with sensible defaults."""
    base = {
        "index": 0,
        "component_kind": "button",
        "role": "button",
        "tag": "button",
        "name": "Submit",
        "text": "Submit",
        "box": {"x": 100, "y": 200, "w": 60, "h": 60},
        "styles": {
            "color": "rgb(0, 0, 0)",
            "backgroundColor": "rgb(255, 255, 255)",
            "fontSize": "16px",
            "fontWeight": "400",
        },
        "viewport_width": 1280,
        "viewport": "desktop",
        "state": "default",
        "capture_path": "screens/desktop-default.png",
    }
    base.update(overrides)
    return base


def test_unnamed_review_uses_wcag_target_rule_without_system_p0() -> None:
    """Crowded 20x20 buttons fail WCAG AA, not an invented system rule."""
    comps = [
        _comp(index=0, box={"x": 0, "y": 0, "w": 20, "h": 20}),
        _comp(index=1, box={"x": 22, "y": 0, "w": 20, "h": 20}),
    ]
    out = analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0]["findings"]}
    assert "hit-target.size" not in pids
    assert "target.size-aa" in pids
    target_finding = next(f for f in comps[0]["findings"] if f["predicate_id"] == "target.size-aa")
    assert target_finding["severity"] == "P1"
    assert out["summary"]["counts"]["P1"] >= 1


def test_contrast_text_low_contrast_emits_finding() -> None:
    """Light grey text on white triggers contrast.text P0."""
    comps = [
        _comp(
            component_kind="link",
            styles={
                "color": "rgb(200, 200, 200)",
                "backgroundColor": "rgb(255, 255, 255)",
                "fontSize": "14px",
                "fontWeight": "400",
            },
        )
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "contrast.text" in pids


def test_image_only_link_does_not_invent_text_contrast_or_affordance_failure() -> None:
    comps = [
        _comp(
            component_kind="link",
            name="Build status",
            text="",
            has_visible_text=False,
            is_inline_text_link=False,
            styles={
                "color": "rgb(200, 200, 200)",
                "backgroundColor": "rgb(255, 255, 255)",
                "fontSize": "14px",
                "fontWeight": "400",
                "textDecorationLine": "none",
            },
        )
    ]

    analyze_components(comps)

    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "contrast.text" not in pids
    assert "link.distinguishable" not in pids


def test_inline_text_link_without_underline_is_manual_p2() -> None:
    comps = [
        _comp(
            component_kind="link",
            name="Read the policy",
            text="Read the policy",
            has_visible_text=True,
            is_inline_text_link=True,
            styles={
                "color": "rgb(0, 0, 0)",
                "backgroundColor": "rgb(255, 255, 255)",
                "fontSize": "16px",
                "fontWeight": "400",
                "textDecorationLine": "none",
            },
        )
    ]

    analyze_components(comps)

    finding = next(
        f for f in comps[0].get("findings", []) if f["predicate_id"] == "link.distinguishable"
    )
    assert finding["severity"] == "P2"
    assert finding["measured"]["evidence_complete"] is False


def test_mixed_descendant_text_styles_do_not_invent_parent_contrast() -> None:
    comps = [
        _comp(
            component_kind="heading-1",
            has_visible_text=True,
            text_style_divergent=True,
            styles={
                "color": "rgb(230, 230, 230)",
                "backgroundColor": "rgb(255, 255, 255)",
                "fontSize": "48px",
                "fontWeight": "400",
            },
        )
    ]

    analyze_components(comps)

    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "contrast.text" not in pids


def test_off_canvas_child_in_horizontal_rail_is_not_page_overflow() -> None:
    comps = [
        _comp(
            box={"x": 400, "y": 0, "w": 100, "h": 40},
            viewport_width=390,
            has_horizontal_overflow_ancestor=True,
        )
    ]

    analyze_components(comps)

    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "layout.off-canvas" not in pids


def test_contrast_text_uses_captured_document_background_when_ancestors_are_transparent() -> None:
    comps = [
        _comp(
            component_kind="heading-2",
            parent_index=-1,
            document_background="rgb(21, 21, 18)",
            styles={
                "color": "rgb(242, 238, 229)",
                "backgroundColor": "transparent",
                "fontSize": "20px",
                "fontWeight": "700",
            },
        )
    ]

    analyze_components(comps)

    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "contrast.text" not in pids


def test_icon_button_no_name_emits_p0() -> None:
    """Icon button without accessible name -> P0."""
    comps = [_comp(component_kind="icon-button", name="", text="")]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "name.icon-button" in pids


def test_link_generic_text_emits_p1() -> None:
    """Link with 'click here' text -> P1 generic-name finding."""
    comps = [_comp(component_kind="link", name="click here", text="click here")]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "name.link.generic" in pids


def test_image_no_alt_emits_p0() -> None:
    """Image with has_alt=False -> P0."""
    comps = [_comp(component_kind="image", has_alt=False, name="")]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "name.image" in pids


def test_input_no_label_emits_p0() -> None:
    """Text input with no name -> P0 label.association."""
    comps = [_comp(component_kind="text-input", name="", text="")]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "label.association" in pids


def test_off_canvas_layout_finding() -> None:
    """Element extending past viewport width -> layout.off-canvas P1."""
    comps = [
        _comp(
            component_kind="button",
            box={"x": 1200, "y": 0, "w": 200, "h": 60},  # extends to 1400 > 1280
            viewport_width=1280,
        )
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "layout.off-canvas" in pids


def test_multiple_h1_emits_findings() -> None:
    """Two h1 elements -> heading.duplicate-h1 on the second."""
    comps = [
        _comp(
            component_kind="heading-1",
            name="Title",
            text="Title",
            box={"x": 0, "y": 0, "w": 200, "h": 30},
        ),
        _comp(
            component_kind="heading-1",
            name="Other",
            text="Other",
            index=1,
            box={"x": 0, "y": 40, "w": 200, "h": 30},
        ),
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for fc in comps for f in fc.get("findings", [])}
    assert "heading.duplicate-h1" in pids


def test_target_system_overrides_thresholds() -> None:
    """material-3 raises hit_target_min_px to 48; 44px button now fails."""
    comps = [_comp(box={"x": 0, "y": 0, "w": 44, "h": 44})]
    out = analyze_components(comps, target_system="material-3")
    pids = {f["predicate_id"] for f in comps[0].get("findings", [])}
    assert "hit-target.size" in pids
    finding = next(f for f in comps[0]["findings"] if f["predicate_id"] == "hit-target.size")
    assert finding["severity"] == "P2"
    assert "material-3" in finding["rule"]
    assert out["summary"]["thresholds"]["hit_target_min_px"] == 48


def test_analyze_file_round_trip(tmp_path: Path) -> None:
    """analyze_file reads JSON from disk and returns an analysis."""
    comps_path = tmp_path / "comps.json"
    comps_path.write_text(json.dumps([_comp()]))
    out = analyze_file(comps_path)
    assert "components" in out
    assert out["summary"]["total_components"] == 1


def test_analyze_components_preserves_0_8_positional_focus_coverage() -> None:
    coverage = {"observed": 3, "eligible": 4}

    out = analyze_components([], None, None, coverage)

    assert out["summary"]["focus_coverage"] == coverage


def test_unattached_global_finding_is_preserved() -> None:
    """Page-wide findings must survive even when no component owns them."""
    out = analyze_components([_comp()], focus_coverage={})

    assert out["summary"]["counts"]["P2"] >= 1
    assert out["summary"]["global_findings"] >= 1
    assert any(f["predicate_id"] == "focus.no-styles" for f in out["global_findings"])


def test_tap_target_overlap_detects_adjacent_small_buttons() -> None:
    """Two crowded 20x20 buttons fail the WCAG target spacing rule."""
    comps = [
        _comp(index=0, box={"x": 0, "y": 0, "w": 20, "h": 20}),
        _comp(index=1, box={"x": 22, "y": 0, "w": 20, "h": 20}),
    ]
    analyze_components(comps)
    pids = {f["predicate_id"] for c in comps for f in c.get("findings", [])}
    assert "target.size-aa" in pids


def test_tap_target_overlap_ignores_adjacent_targets_that_are_already_24px() -> None:
    comps = [
        _comp(index=0, box={"x": 0, "y": 0, "w": 24, "h": 24}),
        _comp(index=1, box={"x": 25, "y": 0, "w": 24, "h": 24}),
    ]

    analyze_components(comps)

    pids = {f["predicate_id"] for c in comps for f in c.get("findings", [])}
    assert "target.size-aa" not in pids


def test_tap_target_overlap_handles_large_sparse_page_without_all_pairs() -> None:
    components = [
        _comp(
            index=index,
            box={"x": 0, "y": index * 100, "w": 20, "h": 20},
        )
        for index in range(5_000)
    ]

    started = time.perf_counter()
    findings = _tap_target_overlap(
        components,
        {"thresholds": {"tap_overlap_pad_px": 4}},
    )

    assert findings == []
    assert time.perf_counter() - started < 2.0


def test_analyze_file_with_captures_dir_builds_pixel_sampler(tmp_path: Path) -> None:
    """analyze_file with captures_dir attaches a pixel sampler that runs
    visual-dom check on appropriate components."""
    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("PIL not installed")

    # Create a screenshot at a known path with a known color.
    screens = tmp_path / "screens"
    screens.mkdir(parents=True)
    img = Image.new("RGB", (400, 400), (255, 0, 0))  # solid red
    img.save(screens / "desktop-default.png")

    # Component declares white background but sampled pixel is red -> mismatch.
    comp = _comp(
        component_kind="button",
        styles={
            "backgroundColor": "rgb(255, 255, 255)",
            "color": "rgb(0,0,0)",
            "fontSize": "16px",
            "fontWeight": "400",
        },
        capture_path="screens/desktop-default.png",
        device_pixel_ratio=1.0,
        box={"x": 100, "y": 100, "w": 60, "h": 60},
    )
    comps_path = tmp_path / "comps.json"
    comps_path.write_text(json.dumps([comp]))

    out = analyze_file(comps_path, captures_dir=tmp_path)
    pids = {f["predicate_id"] for c in out["components"] for f in c.get("findings", [])}
    assert "visual-dom.background-mismatch" in pids


def test_visual_dom_skips_intentionally_translucent_disabled_control() -> None:
    comp = _comp(
        component_kind="button",
        disabled=True,
        styles={
            "backgroundColor": "rgb(20, 80, 200)",
            "color": "rgb(255, 255, 255)",
            "fontSize": "16px",
            "fontWeight": "400",
            "opacity": "0.5",
        },
    )

    analyze_components([comp], pixel_sampler=lambda _comp: "rgb(138, 168, 228)")

    pids = {f["predicate_id"] for f in comp.get("findings", [])}
    assert "visual-dom.background-mismatch" not in pids


def test_visual_dom_skips_translucent_declared_background() -> None:
    comp = _comp(
        component_kind="button",
        styles={
            "backgroundColor": "rgba(20, 80, 200, 0.5)",
            "color": "rgb(255, 255, 255)",
            "fontSize": "16px",
            "fontWeight": "400",
            "opacity": "1",
        },
    )

    analyze_components([comp], pixel_sampler=lambda _comp: "rgb(138, 168, 228)")

    pids = {f["predicate_id"] for f in comp.get("findings", [])}
    assert "visual-dom.background-mismatch" not in pids


def test_visual_dom_still_checks_opaque_disabled_control() -> None:
    comp = _comp(
        component_kind="button",
        disabled=True,
        styles={
            "backgroundColor": "rgb(255, 255, 255)",
            "color": "rgb(0, 0, 0)",
            "fontSize": "16px",
            "fontWeight": "400",
            "opacity": "1",
        },
    )

    analyze_components([comp], pixel_sampler=lambda _comp: "rgb(255, 0, 0)")

    pids = {f["predicate_id"] for f in comp.get("findings", [])}
    assert "visual-dom.background-mismatch" in pids


def test_visual_dom_skips_effective_ancestor_opacity() -> None:
    comp = _comp(
        component_kind="button",
        effective_opacity=0.5,
        styles={
            "backgroundColor": "rgb(20, 80, 200)",
            "backgroundImage": "none",
            "color": "white",
            "fontSize": "16px",
            "fontWeight": "400",
            "opacity": "1",
        },
    )
    analyze_components([comp], pixel_sampler=lambda _comp: "rgb(138, 168, 228)")
    assert "visual-dom.background-mismatch" not in {
        finding["predicate_id"] for finding in comp.get("findings", [])
    }


def test_visual_dom_skips_intentional_gradient() -> None:
    comp = _comp(
        component_kind="button",
        styles={
            "backgroundColor": "rgb(20, 80, 200)",
            "backgroundImage": "linear-gradient(90deg, red, blue)",
            "color": "white",
            "fontSize": "16px",
            "fontWeight": "400",
            "opacity": "1",
        },
    )
    analyze_components([comp], pixel_sampler=lambda _comp: "rgb(255, 0, 0)")
    assert "visual-dom.background-mismatch" not in {
        finding["predicate_id"] for finding in comp.get("findings", [])
    }


def test_pixel_sampler_does_not_choose_foreground_on_a_tie(tmp_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("PIL not installed")

    screens = tmp_path / "screens"
    screens.mkdir()
    image = Image.new("RGB", (100, 100), "white")
    pixels = image.load()
    points = [(15, 15), (50, 15), (85, 15), (15, 50)]
    for point in points:
        pixels[point] = (23, 32, 51)
    pixels[50, 50] = (100, 106, 119)
    image.save(screens / "sample.png")

    sampler = _build_pixel_sampler(tmp_path)
    assert sampler is not None
    sampled = sampler(
        _comp(
            component_kind="heading-1",
            capture_path="screens/sample.png",
            device_pixel_ratio=1,
            box={"x": 0, "y": 0, "w": 100, "h": 100},
            styles={"color": "rgb(23, 32, 51)", "backgroundColor": "transparent"},
        )
    )
    assert sampled == "rgb(255, 255, 255)"
