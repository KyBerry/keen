"""Tests for harness/decompose.py: DOM dump -> Component records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.decompose import Component, decompose

# --- Helpers --------------------------------------------------------------


def _box(w: int = 100, h: int = 40) -> dict[str, int]:
    return {"x": 0, "y": 0, "w": w, "h": h}


def _elem(
    index: int,
    tag: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a single DOM-element dict matching the harness's expected shape."""
    base: dict[str, Any] = {
        "index": index,
        "tag": tag,
        "box": _box(),
        "styles": {},
        "name": "",
        "text": "",
    }
    base.update(overrides)
    return base


def _write_dom(
    tmp_path: Path,
    elements: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    # `meta is None` (default) -> seed with sensible defaults; `meta={}` is
    # treated as the explicit "no meta at all" case so edge-case tests work.
    if meta is None:
        meta = {"viewport": "desktop", "state": "default"}
    data: dict[str, Any] = {
        "meta": meta,
        "elements": elements,
    }
    if extra:
        data.update(extra)
    p = tmp_path / "dom.json"
    p.write_text(json.dumps(data))
    return p


# --- Empty / edge cases ---------------------------------------------------


def test_decompose_empty_dom_returns_empty_list(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [])
    assert decompose(p) == []


def test_decompose_no_elements_key_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "dom.json"
    p.write_text(json.dumps({"meta": {}}))
    assert decompose(p) == []


def test_decompose_skips_aria_hidden(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(0, "button", name="Submit", ariaHidden=True),
            _elem(1, "button", name="Visible"),
        ],
    )
    out = decompose(p)
    assert len(out) == 1
    assert out[0].name == "Visible"


# --- Classification: buttons ----------------------------------------------


def test_decompose_classifies_button_with_text(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "button", text="Submit")])
    out = decompose(p)
    assert len(out) == 1
    assert out[0].component_kind == "button"
    assert out[0].role == "button"


def test_decompose_propagates_document_background(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [_elem(0, "h1", text="Dark report")],
        extra={"documentBackgroundColor": "rgb(21, 21, 18)"},
    )

    out = decompose(p)

    assert out[0].document_background == "rgb(21, 21, 18)"


def test_decompose_propagates_actual_screenshot_bounds(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [_elem(0, "button", text="Open")],
        meta={
            "viewport": "desktop",
            "state": "default",
            "viewport_size": {"width": 1440, "height": 900},
        },
        extra={
            "documentSize": {"width": 1440, "height": 14000},
            "coverage": {
                "screenshot": {
                    "complete": False,
                    "captured_css_px": {"width": 1440, "height": 900},
                }
            },
        },
    )

    out = decompose(p)

    assert out[0].capture_width == 1440
    assert out[0].capture_height == 900


def test_decompose_classifies_small_textless_button_as_icon(tmp_path: Path) -> None:
    # <= 56x56 and no name/text => icon-button
    p = _write_dom(tmp_path, [_elem(0, "button", box={"x": 0, "y": 0, "w": 40, "h": 40})])
    out = decompose(p)
    assert out[0].component_kind == "icon-button"


def test_decompose_large_button_no_text_still_button(tmp_path: Path) -> None:
    # box > 56x56 keeps it as a generic button
    p = _write_dom(tmp_path, [_elem(0, "button", box={"x": 0, "y": 0, "w": 200, "h": 50})])
    out = decompose(p)
    assert out[0].component_kind == "button"


# --- Classification: headings ---------------------------------------------


def test_decompose_classifies_h1_as_heading_1(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "h1", text="Title")])
    out = decompose(p)
    assert out[0].component_kind == "heading-1"
    assert out[0].role == "heading"


def test_decompose_classifies_h6_as_heading_6(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "h6", text="Sub")])
    out = decompose(p)
    assert out[0].component_kind == "heading-6"


def test_nested_header_is_not_a_banner_landmark(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(0, "section", parentIndex=-1),
            _elem(1, "header", parentIndex=0),
            _elem(2, "header", parentIndex=-1),
        ],
    )
    out = decompose(p)
    banners = [item for item in out if item.component_kind == "landmark-banner"]
    assert [item.index for item in banners] == [2]


def test_unlabeled_section_is_not_a_region_landmark(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "section", name="")])
    assert decompose(p) == []


# --- Classification: links ------------------------------------------------


def test_decompose_anchor_with_href_is_link(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "a", href="/about", text="About")])
    out = decompose(p)
    assert out[0].component_kind == "link"
    assert out[0].role == "link"


def test_decompose_preserves_semantic_evidence_fields(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(
                0,
                "a",
                href="/build",
                name="Build status",
                nameSource="browser-accessibility-tree",
                hasVisibleText=False,
                textStyleDivergent=True,
                isInlineTextLink=False,
                hasHorizontalOverflowAncestor=True,
            )
        ],
    )

    out = decompose(p)

    assert out[0].name_source == "browser-accessibility-tree"
    assert out[0].has_visible_text is False
    assert out[0].text_style_divergent is True
    assert out[0].is_inline_text_link is False
    assert out[0].has_horizontal_overflow_ancestor is True


def test_decompose_anchor_without_href_is_dropped(tmp_path: Path) -> None:
    # No href -> resolved role is "generic" -> _classify returns None -> dropped
    p = _write_dom(tmp_path, [_elem(0, "a", text="No href")])
    out = decompose(p)
    assert out == []


# --- Classification: inputs ----------------------------------------------


def test_decompose_text_input(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="text")])
    out = decompose(p)
    assert out[0].component_kind == "text-input"
    assert out[0].role == "textbox"


def test_decompose_email_input_is_text_input(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="email")])
    out = decompose(p)
    assert out[0].component_kind == "text-input"


def test_decompose_search_input(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="search")])
    out = decompose(p)
    assert out[0].component_kind == "search-input"


def test_decompose_checkbox(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="checkbox")])
    out = decompose(p)
    assert out[0].component_kind == "checkbox"


def test_decompose_radio(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="radio")])
    out = decompose(p)
    assert out[0].component_kind == "radio"


def test_decompose_submit_input_is_button(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="submit", name="Go")])
    out = decompose(p)
    assert out[0].component_kind == "button"


def test_decompose_number_input(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "input", type="number")])
    out = decompose(p)
    assert out[0].component_kind == "number-input"


def test_decompose_textarea(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "textarea")])
    out = decompose(p)
    assert out[0].component_kind == "textarea"
    assert out[0].role == "textbox"


def test_decompose_select_is_combobox(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "select")])
    out = decompose(p)
    assert out[0].component_kind == "select"


# --- Classification: landmarks / structure --------------------------------


def test_decompose_nav_landmark(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "nav")])
    out = decompose(p)
    assert out[0].component_kind == "landmark-navigation"


def test_decompose_main_landmark(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "main")])
    out = decompose(p)
    assert out[0].component_kind == "landmark-main"


def test_decompose_dialog_landmark(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "dialog")])
    out = decompose(p)
    assert out[0].component_kind == "landmark-dialog"


def test_decompose_table_row_cell(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(0, "table"),
            _elem(1, "tr"),
            _elem(2, "td"),
            _elem(3, "th"),
        ],
    )
    out = decompose(p)
    kinds = [c.component_kind for c in out]
    assert kinds == ["table", "table-row", "table-cell", "table-header-cell"]


def test_decompose_list_and_listitem(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(0, "ul"),
            _elem(1, "li"),
        ],
    )
    out = decompose(p)
    assert [c.component_kind for c in out] == ["list", "list-item"]


# --- Explicit role override ----------------------------------------------


def test_decompose_explicit_role_overrides_tag(tmp_path: Path) -> None:
    # A <div role="button"> should classify as button.
    p = _write_dom(tmp_path, [_elem(0, "div", role="button", text="Custom")])
    out = decompose(p)
    assert out[0].component_kind == "button"
    assert out[0].role == "button"


def test_decompose_explicit_role_tab(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "div", role="tab", text="Tab 1")])
    out = decompose(p)
    assert out[0].component_kind == "tab"


def test_decompose_unknown_role_is_dropped(tmp_path: Path) -> None:
    # A bare <div> with no role/known tag => generic => dropped.
    p = _write_dom(tmp_path, [_elem(0, "div")])
    out = decompose(p)
    assert out == []


def test_decompose_explicit_alert_role(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "div", role="alert", text="Warning")])
    out = decompose(p)
    assert out[0].component_kind == "alert"


# --- Meta propagation -----------------------------------------------------


def test_decompose_propagates_viewport_and_state(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [_elem(0, "button", text="Hi")],
        meta={
            "viewport": "mobile",
            "state": "hover",
            "screen_path": "screens/foo.png",
            "viewport_size": {"width": 375, "deviceScaleFactor": 2.0},
        },
    )
    out = decompose(p)
    assert out[0].viewport == "mobile"
    assert out[0].state == "hover"
    assert out[0].capture_path == "screens/foo.png"
    assert out[0].viewport_width == 375
    assert out[0].device_pixel_ratio == 2.0


def test_decompose_default_meta_when_missing(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [_elem(0, "button", text="Hi")],
        meta={},
    )
    out = decompose(p)
    assert out[0].viewport == ""
    assert out[0].state == ""
    assert out[0].device_pixel_ratio == 1.0
    assert out[0].viewport_width == 0


def test_decompose_focused_index_marks_is_focused(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(0, "button", text="One"),
            _elem(1, "button", text="Two"),
        ],
        extra={"focused_index": 1},
    )
    out = decompose(p)
    assert out[0].is_focused is False
    assert out[1].is_focused is True


def test_decompose_preserves_attributes(tmp_path: Path) -> None:
    p = _write_dom(
        tmp_path,
        [
            _elem(
                0,
                "input",
                type="email",
                required=True,
                autocomplete="email",
                inputmode="email",
                disabled=False,
                ariaDisabled=False,
                tabIndex=2,
                hasUserFocusRule=True,
                parentIndex=5,
            )
        ],
    )
    out = decompose(p)
    c = out[0]
    assert c.required is True
    assert c.autocomplete == "email"
    assert c.inputmode == "email"
    assert c.tab_index == 2
    assert c.has_user_focus_rule is True
    assert c.parent_index == 5
    assert c.type == "email"


# --- Component dataclass --------------------------------------------------


def test_component_to_dict_round_trip(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "button", text="Go")])
    out = decompose(p)
    d = out[0].to_dict()
    assert isinstance(d, dict)
    assert d["component_kind"] == "button"
    assert d["findings"] == []
    # Must include box/styles too
    assert "box" in d and "styles" in d


def test_decompose_returns_component_instances(tmp_path: Path) -> None:
    p = _write_dom(tmp_path, [_elem(0, "button", text="Go")])
    out = decompose(p)
    assert all(isinstance(c, Component) for c in out)
