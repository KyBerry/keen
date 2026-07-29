"""Decompose stage: turn the raw DOM dump into a flat list of `Component`
records, one per element worth analyzing.

Classification priority:
    1. ARIA role (explicit role= or implicit role of the tag)
    2. Tag name + attributes (input[type=email] -> "email-input")
    3. Visual heuristics (small, no text, contains svg -> "icon-button")

Output is deterministic given the same DOM dump.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness import _artifacts as artifacts_mod
from harness._sanitize import sanitize_artifact_href, sanitize_untrusted_text

IMPLICIT_ROLES: dict[str, str] = {
    "a": "link",  # only when href is set; we check that below
    "button": "button",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "input": "textbox",  # overridden by type below
    "textarea": "textbox",
    "select": "combobox",
    "img": "img",
    "nav": "navigation",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
    "aside": "complementary",
    "section": "region",
    "form": "form",
    "ul": "list",
    "ol": "list",
    "li": "listitem",
    "table": "table",
    "tr": "row",
    "th": "columnheader",
    "td": "cell",
    "label": "label",
    "dialog": "dialog",
    "details": "group",
    "summary": "button",
    "progress": "progressbar",
    "meter": "meter",
}

INPUT_TYPE_ROLES: dict[str, str] = {
    "button": "button",
    "submit": "button",
    "reset": "button",
    "checkbox": "checkbox",
    "radio": "radio",
    "range": "slider",
    "search": "searchbox",
    "email": "textbox",
    "tel": "textbox",
    "url": "textbox",
    "number": "spinbutton",
    "password": "textbox",  # pragma: allowlist secret  # nosec B105
    "text": "textbox",
    "date": "textbox",
    "color": "textbox",
    "file": "button",
}


@dataclass
class Component:
    """One analyzable unit of UI."""

    index: int
    component_kind: str
    role: str
    tag: str
    name: str
    text: str

    box: dict[str, int]
    styles: dict[str, str]

    name_source: str = "dom-heuristic"
    has_visible_text: bool = False
    text_style_divergent: bool = False
    is_inline_text_link: bool = False
    has_horizontal_overflow_ancestor: bool = False
    disabled: bool = False
    aria_hidden: bool = False
    aria_disabled: bool = False
    aria_modal: bool = False
    aria_current: str | None = None
    has_alt: bool | None = None
    autocomplete: str | None = None
    inputmode: str | None = None
    required: bool = False
    tab_index: int = 0
    type: str | None = None
    href: str | None = None
    has_user_focus_rule: bool = False
    effective_opacity: float = 1.0

    parent_index: int = -1

    viewport: str = ""
    state: str = ""
    capture_path: str = ""
    device_pixel_ratio: float = 1.0
    viewport_width: int = 0
    capture_width: int = 0
    capture_height: int = 0
    document_background: str = "rgb(255, 255, 255)"
    is_focused: bool = False

    crop_path: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- Classification --------------------------------------------------------


def _resolve_role(
    elem: dict[str, Any], elements_by_index: dict[int, dict[str, Any]] | None = None
) -> str:
    if elem.get("role"):
        return elem["role"]
    tag = elem["tag"]
    if tag == "section" and not (elem.get("name") or "").strip():
        # An unlabeled section is sectioning content, not a region landmark.
        return "generic"
    if tag in {"header", "footer"} and elements_by_index:
        parent_index = elem.get("parentIndex", -1)
        seen: set[int] = set()
        while isinstance(parent_index, int) and parent_index >= 0 and parent_index not in seen:
            seen.add(parent_index)
            parent = elements_by_index.get(parent_index)
            if parent is None:
                break
            if parent.get("tag") in {"article", "aside", "main", "nav", "section"}:
                # Nested header/footer elements do not expose banner or
                # contentinfo landmarks unless an explicit role says so.
                return "generic"
            parent_index = parent.get("parentIndex", -1)
    if tag == "a" and not elem.get("href"):
        return "generic"
    if tag == "input":
        t = (elem.get("type") or "text").lower()
        return INPUT_TYPE_ROLES.get(t, "textbox")
    return IMPLICIT_ROLES.get(tag, "generic")


def _classify(elem: dict[str, Any], role: str) -> str | None:
    """Bucket an element into a component_kind. Return None to drop it."""
    tag = elem["tag"]
    box = elem["box"]

    if role == "heading":
        return f"heading-{tag[-1]}" if tag.startswith("h") else "heading"

    if role == "button":
        text = (elem.get("name") or elem.get("text") or "").strip()
        return "icon-button" if not text and box["w"] <= 56 and box["h"] <= 56 else "button"
    if role == "link":
        return "link"
    if role == "checkbox":
        return "checkbox"
    if role == "radio":
        return "radio"
    if role == "searchbox":
        return "search-input"
    if role == "combobox":
        return "select"
    if role == "slider":
        return "slider"
    if role == "spinbutton":
        return "number-input"
    if role == "textbox":
        return "textarea" if tag == "textarea" else "text-input"
    if role == "switch":
        return "switch"

    if role == "img":
        return "image"

    if role in (
        "navigation",
        "main",
        "banner",
        "contentinfo",
        "complementary",
        "region",
        "form",
        "dialog",
        "group",
    ):
        return f"landmark-{role}"

    if role == "list":
        return "list"
    if role == "listitem":
        return "list-item"
    if role == "table":
        return "table"
    if role == "row":
        return "table-row"
    if role == "columnheader":
        return "table-header-cell"
    if role == "cell":
        return "table-cell"

    if role == "tablist":
        return "tablist"
    if role == "tab":
        return "tab"
    if role == "tabpanel":
        return "tabpanel"
    if role == "menu":
        return "menu"
    if role == "menuitem":
        return "menu-item"
    if role == "menubar":
        return "menubar"

    if role == "alert":
        return "alert"
    if role == "status":
        return "status"
    if role == "tooltip":
        return "tooltip"
    if role == "progressbar":
        return "progressbar"
    if role == "meter":
        return "meter"

    if role == "label":
        return "label"

    return None


# -- Public API ------------------------------------------------------------


def decompose_document(data: dict[str, Any]) -> list[Component]:
    """Decompose an already parsed DOM capture into Components."""
    expectation = (data.get("coverage") or {}).get("expectation") or {}
    if expectation.get("status") == "blocked":
        # Diagnostic captures document why review could not proceed. They are
        # never product evidence and must not enter analysis through the
        # library API or a later manual audit.
        return []
    meta = data.get("meta", {})
    viewport_name = meta.get("viewport", "")
    state = meta.get("state", "")
    capture_path = meta.get("screen_path", "")
    viewport_size = meta.get("viewport_size", {}) or {}
    dpr = float(viewport_size.get("deviceScaleFactor") or data.get("devicePixelRatio") or 1.0)
    vw = int(viewport_size.get("width") or data.get("viewport", {}).get("width") or 0)
    document_background = str(data.get("documentBackgroundColor") or "rgb(255, 255, 255)")
    focused_index = data.get("focused_index", -1)
    screenshot_coverage = (data.get("coverage") or {}).get("screenshot") or {}
    captured_size = screenshot_coverage.get("captured_css_px") or {}
    capture_width = int(captured_size.get("width") or vw)
    capture_height = int(
        captured_size.get("height")
        or (data.get("documentSize") or {}).get("height")
        or viewport_size.get("height")
        or 0
    )
    raw_elements = data.get("elements", [])
    elements_by_index = {
        elem["index"]: elem
        for elem in raw_elements
        if isinstance(elem, dict) and isinstance(elem.get("index"), int)
    }

    components: list[Component] = []
    for elem in raw_elements:
        if elem.get("ariaHidden") or elem.get("effectiveAriaHidden"):
            continue
        try:
            effective_opacity = float(elem.get("effectiveOpacity", 1.0))
        except (TypeError, ValueError):
            effective_opacity = 1.0
        if not math.isfinite(effective_opacity):
            effective_opacity = 1.0
        effective_opacity = max(0.0, min(1.0, effective_opacity))
        if effective_opacity <= 0:
            continue
        # Sanitize untrusted page-derived strings before any downstream
        # decision uses them. Classification only looks at emptiness/length
        # of name/text so sanitizing here is safe; sanitization never grows
        # a string and only erases content that was purely control/bidi
        # tricks to begin with. We also overwrite elem in place so the
        # classification helpers see the cleaned values consistently.
        safe_name = sanitize_untrusted_text(elem.get("name") or "")
        safe_text = sanitize_untrusted_text(elem.get("text") or "")
        safe_href = sanitize_artifact_href(elem.get("href") or "") if elem.get("href") else None
        elem["name"] = safe_name
        elem["text"] = safe_text

        captured_visible_text = elem.get("hasVisibleText")
        if isinstance(captured_visible_text, bool):
            has_visible_text = captured_visible_text
        else:
            # Legacy capture fallback. Name is a reasonable proxy for most
            # text-bearing controls, but never treat image alternatives as
            # rendered text when deciding whether text-only predicates apply.
            has_visible_text = bool(
                safe_text
                or (safe_name and str(elem.get("tag") or "").lower() not in {"img", "svg"})
            )

        role = _resolve_role(elem, elements_by_index)
        kind = _classify(elem, role)
        if kind is None:
            continue
        components.append(
            Component(
                index=elem["index"],
                component_kind=kind,
                role=role,
                tag=elem["tag"],
                name=safe_name,
                text=safe_text,
                box=elem["box"],
                styles=elem.get("styles", {}),
                name_source=str(elem.get("nameSource") or "dom-heuristic"),
                has_visible_text=has_visible_text,
                text_style_divergent=bool(elem.get("textStyleDivergent", False)),
                is_inline_text_link=bool(elem.get("isInlineTextLink", False)),
                has_horizontal_overflow_ancestor=bool(
                    elem.get("hasHorizontalOverflowAncestor", False)
                ),
                disabled=elem.get("disabled", False),
                aria_hidden=elem.get("ariaHidden", False),
                aria_disabled=bool(
                    elem.get("ariaDisabled", False) or elem.get("effectiveAriaDisabled", False)
                ),
                aria_modal=elem.get("ariaModal", False),
                aria_current=elem.get("ariaCurrent"),
                has_alt=elem.get("hasAlt"),
                autocomplete=elem.get("autocomplete"),
                inputmode=elem.get("inputmode"),
                required=elem.get("required", False),
                tab_index=elem.get("tabIndex", 0),
                type=elem.get("type"),
                href=safe_href or None,
                has_user_focus_rule=elem.get("hasUserFocusRule", False),
                effective_opacity=effective_opacity,
                parent_index=elem.get("parentIndex", -1),
                viewport=viewport_name,
                state=state,
                capture_path=capture_path,
                device_pixel_ratio=dpr,
                viewport_width=vw,
                capture_width=capture_width,
                capture_height=capture_height,
                document_background=document_background,
                is_focused=(elem["index"] == focused_index),
            )
        )

    return components


def decompose(dom_path: Path) -> list[Component]:
    """Read and decompose a single dom/<...>.json file into Components."""
    data = artifacts_mod.read_dom_document(dom_path)
    return decompose_document(data)
