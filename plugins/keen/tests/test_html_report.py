"""Tests for harness/_html_report.py.

Covers the tiny markdown converter, the safety guarantees (HTML escaping for
arbitrary user input, no external network calls in the rendered output), and
the end-to-end render via ``write_report`` against a synthetic run dir.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from harness import _html_report as html_report
from harness._html_report import md_to_html, render_report, write_report

# ---------------------------------------------------------------------------
# md_to_html unit tests.
# ---------------------------------------------------------------------------


def test_encode_image_rejects_file_over_size_cap(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    image = tmp_path / "oversized.png"
    image.write_bytes(b"\x89PNG" + b"x" * 32)
    monkeypatch.setattr(html_report, "_MAX_EMBED_IMAGE_BYTES", 8)
    assert html_report._encode_image(image) is None


def test_component_evidence_is_bounded_and_keeps_top_finding_targets(
    tmp_path: Path,
) -> None:
    components = [
        {
            "index": index,
            "component_kind": "button",
            "viewport": "desktop",
            "state": "default",
            "capture_path": "screens/desktop-default.png",
            "box": {"x": 0, "y": index * 50, "w": 100, "h": 40},
            "findings": [
                {
                    "severity": "P2",
                    "predicate_id": f"predicate-{index % 5}",
                    "message": f"finding {index}",
                }
            ],
        }
        for index in range(40)
    ]
    top = [
        {
            "capture_path": "screens/desktop-default.png",
            "component_index": 39,
        }
    ]

    rendered = html_report._render_components(
        components,
        top,
        tmp_path,
        link_images=True,
    )

    assert rendered.count('class="component-card"') <= 24
    assert 'id="component-desktop-default-39"' in rendered
    assert "representative components from 40 flagged" in rendered
    assert "report.json" in rendered


def test_md_to_html_headings_and_paragraphs() -> None:
    src = "# Title\n\nHello world.\n\n## Sub\n\nAnother para."
    out = md_to_html(src)
    assert "<h1>Title</h1>" in out
    assert "<h2>Sub</h2>" in out
    assert "<p>Hello world.</p>" in out
    assert "<p>Another para.</p>" in out


def test_md_to_html_inline_formatting() -> None:
    src = "Some **bold** and _italic_ and `code`."
    out = md_to_html(src)
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out


def test_md_to_html_nested_bold_italic() -> None:
    """Bold containing italic should produce well-nested tags."""
    src = "**bold _italic_ bold**"
    out = md_to_html(src)
    assert out.count("<strong>") == 1
    assert out.count("</strong>") == 1
    assert "<em>italic</em>" in out
    # Italic should sit inside strong.
    assert "<strong>bold <em>italic</em> bold</strong>" in out


def test_md_to_html_bullet_list() -> None:
    src = "- one\n- two\n- three"
    out = md_to_html(src)
    assert out.count("<li>") == 3
    assert "<ul>" in out and "</ul>" in out


def test_md_to_html_fenced_code() -> None:
    src = "```\nx = 1\ny = 2\n```"
    out = md_to_html(src)
    assert "<pre><code>" in out
    assert "x = 1" in out
    assert "y = 2" in out


def test_md_to_html_escapes_script_tags() -> None:
    """Raw HTML in the input must not survive into the output."""
    src = "<script>alert(1)</script>"
    out = md_to_html(src)
    assert "<script" not in out.lower()
    assert "&lt;script&gt;" in out


def test_md_to_html_escapes_style_tags() -> None:
    src = "<style>body{display:none}</style>"
    out = md_to_html(src)
    assert "<style" not in out.lower()
    assert "&lt;style&gt;" in out


def test_md_to_html_blocks_javascript_links() -> None:
    """A `javascript:` link collapses to `#`."""
    src = "[click me](javascript:alert(1))"
    out = md_to_html(src)
    assert "javascript:" not in out
    assert 'href="#"' in out


def test_md_to_html_blocks_data_uri_links() -> None:
    src = "[click](data:text/html;base64,xxx)"
    out = md_to_html(src)
    assert "data:" not in out
    assert 'href="#"' in out


def test_md_to_html_allows_https_links() -> None:
    src = "[anchor](https://example.com/path)"
    out = md_to_html(src)
    assert 'href="https://example.com/path"' in out


def test_md_to_html_table_rendering() -> None:
    src = "| H1 | H2 |\n|------|------|\n| a | b |\n| c | d |"
    out = md_to_html(src)
    assert "<table>" in out
    assert "<thead>" in out
    assert out.count("<tr>") == 3  # header + 2 body rows
    assert "<th>H1</th>" in out
    assert "<td>a</td>" in out


def test_md_to_html_empty_input_returns_empty_string() -> None:
    assert md_to_html("") == ""


def test_md_to_html_does_not_leak_raw_ampersand() -> None:
    """Unescaped & must become &amp; in the output."""
    out = md_to_html("Tom & Jerry")
    assert "Tom &amp; Jerry" in out


# ---------------------------------------------------------------------------
# Helpers for end-to-end render.
# ---------------------------------------------------------------------------


def _minimal_report(*, with_findings: bool = True) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    if with_findings:
        components = [
            {
                "index": 0,
                "component_kind": "button",
                "viewport": "desktop",
                "state": "default",
                "box": {"x": 0, "y": 0, "w": 100, "h": 40},
                "findings": [
                    {
                        "severity": "P0",
                        "predicate_id": "tap-target.too_small",
                        "message": "Button is 24×24, below 44×44.",
                    },
                ],
            },
        ]
        findings = [
            {
                "severity": "P0",
                "predicate_id": "tap-target.too_small",
                "component_kind": "button",
                "viewport": "desktop",
                "state": "default",
                "component_index": 0,
                "message": "Button is 24×24, below 44×44.",
            },
            {
                "severity": "P1",
                "predicate_id": "color.contrast.low",
                "component_kind": "link",
                "viewport": "mobile",
                "state": "default",
                "component_index": 1,
                "message": "Foreground/background contrast ratio is 3.1, below 4.5.",
            },
            {
                "severity": "P2",
                "predicate_id": "spacing.off-grid",
                "component_kind": "container",
                "viewport": "tablet",
                "state": "default",
                "component_index": 2,
                "message": "Padding 13px not on 4px grid.",
            },
        ]
    return {
        "version": "0.8.0",
        "target_system": "material-3",
        "captures": [
            {
                "url": "https://example.com/",
                "title": "Example",
                "viewport": "desktop",
                "state": "default",
                "screen_path": "screens/desktop-default.png",
                "manual_review_needed": False,
            }
        ],
        "annotated_overviews": {},
        "score": {
            "grade": "C",
            "grade_summary": "Functional but rough.",
            "score": 20.5,
            "counts": {"P0": 1, "P1": 1, "P2": 1},
            "by_component_kind": {},
        },
        "top_findings": findings,
        "tokens": {
            "colors": {
                "foreground": [
                    {"value": "rgb(0, 0, 0)", "count": 12},
                    {"value": "rgb(80, 80, 80)", "count": 5},
                ],
                "background": [
                    {"value": "#ffffff", "count": 20},
                ],
            },
            "type": {
                "sizes_px": [
                    {"value": 14, "count": 10},
                    {"value": 16, "count": 18},
                    {"value": 20, "count": 4},
                ],
            },
            "shape": {
                "border_radii_px": [{"value": 4, "count": 6}, {"value": 8, "count": 2}],
            },
            "spacing": {
                "values_px": [{"value": 8, "count": 30}, {"value": 16, "count": 14}],
            },
            "diagnostics": {"distinct_text_colors": 7, "spacing_on_4px_grid_pct": 92.0},
        },
        "components": components,
    }


def test_render_report_returns_self_contained_html(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(
        rep,
        captures_dir=tmp_path,
        summary_md="# Test summary\n\nA paragraph.",
    )
    assert html.startswith("<!doctype html>")
    assert "<main" in html
    assert "<nav" in html
    # Self-contained: no external CSS or JS.
    assert 'href="http' not in html
    assert 'src="http' not in html
    assert 'href="//' not in html
    assert 'src="//' not in html
    # No <link rel="stylesheet"> pointing anywhere external.
    assert not re.search(r'<link\s+[^>]*rel=["\']stylesheet["\'][^>]*href=["\']http', html, re.I)
    # No <script src=...>.
    assert not re.search(r"<script\s+[^>]*src=", html, re.I)


def test_render_report_parses_as_html(tmp_path: Path) -> None:
    """Smoke check: the output is well-formed enough for an HTMLParser."""

    class Counter(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.starts = 0
            self.ends = 0
            self.errors: list[str] = []

        def error(self, message: str) -> None:  # type: ignore[override]
            self.errors.append(message)

        def handle_starttag(self, tag: str, attrs: list) -> None:
            self.starts += 1

        def handle_endtag(self, tag: str) -> None:
            self.ends += 1

    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="# Hello")
    p = Counter()
    p.feed(html)
    p.close()
    assert p.errors == []
    assert p.starts > 20
    assert p.ends > 20


def test_render_report_severity_badges_have_aria_labels(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert 'aria-label="severity P0"' in html
    assert 'aria-label="severity P1"' in html
    assert 'aria-label="severity P2"' in html
    assert 'class="badge badge-P0"' in html
    assert 'class="badge badge-P1"' in html
    assert 'class="badge badge-P2"' in html


def test_render_report_rows_have_data_sort_value(tmp_path: Path) -> None:
    """Every findings row should have data-sort-value attributes for the sorter."""
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    # The severity column uses numeric ranks (0/1/2).
    assert 'data-sort-value="0"' in html  # P0
    assert 'data-sort-value="1"' in html  # P1
    assert 'data-sort-value="2"' in html  # P2
    # Header marked sortable as number for severity.
    assert 'data-sort-type="number"' in html


def test_render_report_uses_native_buttons_for_sortable_headers(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")

    assert html.count('class="sort-button"') == 5
    assert '<th data-sort-type="number" scope="col"><button type="button"' in html
    assert 'heading.setAttribute("role", "button")' not in html
    assert 'control.addEventListener("click", sort)' in html
    assert "table.findings tbody tr {" in html
    assert "grid-template-columns: 58px minmax(0, 1fr)" in html


def test_render_report_escapes_finding_message(tmp_path: Path) -> None:
    """A hostile message must be HTML-escaped before rendering."""
    rep = _minimal_report()
    rep["top_findings"] = [
        {
            "severity": "P0",
            "predicate_id": "x",
            "component_kind": "button",
            "viewport": "desktop",
            "state": "default",
            "message": "<script>alert(1)</script>",
        }
    ]
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_report_no_findings(tmp_path: Path) -> None:
    rep = _minimal_report(with_findings=False)
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    # Should still render section markers.
    assert 'id="findings"' in html
    assert "No findings" in html


def test_render_report_empty_captures(tmp_path: Path) -> None:
    rep = _minimal_report(with_findings=False)
    rep["captures"] = []
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert "No screenshots" in html


def test_render_report_token_swatches(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert "Foreground colors" in html
    assert "Type scale" in html
    assert "Border radii" in html
    # The actual color value appears as a swatch label.
    assert "rgb(0, 0, 0)" in html


def test_render_report_handles_missing_tokens(tmp_path: Path) -> None:
    rep = _minimal_report(with_findings=False)
    rep["tokens"] = {}
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert "No tokens extracted" in html


def test_write_report_creates_file_in_captures_dir(tmp_path: Path) -> None:
    rep = _minimal_report()
    path = write_report(rep, tmp_path, summary_md="# Hello")
    assert path == tmp_path / "report.html"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "<main" in text


def test_write_report_with_real_screenshot_embeds_base64(tmp_path: Path) -> None:
    """An actual PNG on disk should be base64-embedded by default."""
    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("PIL not installed")

    (tmp_path / "screens").mkdir()
    img = Image.new("RGB", (200, 100), "white")
    img.save(tmp_path / "screens" / "desktop-default.png")

    rep = _minimal_report(with_findings=False)
    rep["captures"][0]["screen_path"] = "screens/desktop-default.png"
    path = write_report(rep, tmp_path, summary_md="")
    text = path.read_text(encoding="utf-8")
    assert "data:image/png;base64," in text


def test_write_report_link_images_mode_uses_relative_paths(tmp_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("PIL not installed")

    (tmp_path / "screens").mkdir()
    img = Image.new("RGB", (200, 100), "white")
    img.save(tmp_path / "screens" / "desktop-default.png")

    rep = _minimal_report(with_findings=False)
    rep["captures"][0]["screen_path"] = "screens/desktop-default.png"
    path = write_report(rep, tmp_path, summary_md="", link_images=True)
    text = path.read_text(encoding="utf-8")
    assert "data:image/png;base64," not in text
    assert "screens/desktop-default.png" in text


def test_render_report_includes_signal_band_and_weighted_index(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    assert "Signal band" in html
    assert "Weighted index" in html
    assert ">C<" in html  # grade letter inline
    assert "20.5" in html  # damage value


def test_render_report_uses_document_title_and_contextual_signal_summary(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(
        rep,
        captures_dir=tmp_path,
        summary_md="# Keen review\n\n## Top findings\n\nRepeated details.",
    )

    assert "<h1>Example</h1>" in html
    assert "deterministic candidate signals" in html
    assert "P0 candidates" in html
    assert "Functional but rough." not in html
    assert "Repeated details." not in html


def test_display_title_shortens_local_file_when_document_title_missing(tmp_path: Path) -> None:
    rep = _minimal_report()
    rep["captures"][0]["title"] = ""
    rep["captures"][0]["url"] = "file:///Users/example/design-review.html"

    html = render_report(rep, captures_dir=tmp_path, summary_md="")

    assert "<h1>design-review.html</h1>" in html
    assert "/Users/example" not in html


def test_render_report_path_traversal_blocked(tmp_path: Path) -> None:
    """A relative path that resolves outside captures_dir must not embed."""
    rep = _minimal_report(with_findings=False)
    rep["captures"][0]["screen_path"] = "../../../../etc/hosts"
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    # Path traversal blocked: the figure gets dropped (no img src).
    assert "etc/hosts" not in html


def test_render_report_landmarks_present(tmp_path: Path) -> None:
    rep = _minimal_report()
    html = render_report(rep, captures_dir=tmp_path, summary_md="")
    # ARIA / HTML5 landmarks.
    assert '<main id="main"' in html
    assert '<nav class="report-nav"' in html
    assert 'role="banner"' in html
    # Skip link for keyboard users.
    assert "Skip to report" in html
    assert html.count("<body>") == 1


# ---------------------------------------------------------------------------
# End-to-end via compose.
# ---------------------------------------------------------------------------


def test_compose_writes_report_html(tmp_path: Path) -> None:
    """Going through report.compose produces report.html as a side effect."""
    from harness.report import compose

    (tmp_path / "analysis").mkdir()
    (tmp_path / "dom").mkdir()
    (tmp_path / "analysis" / "desktop-default.json").write_text(
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
                        "findings": [{"severity": "P0", "predicate_id": "x", "message": "m"}],
                    }
                ],
                "summary": {},
            }
        )
    )
    (tmp_path / "dom" / "desktop-default.json").write_text(
        json.dumps(
            {
                "title": "Test",
                "url": "https://example.com/",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/desktop-default.png",
                },
            }
        )
    )
    rep = compose(tmp_path, target_system="material-3")
    html_path = tmp_path / "report.html"
    assert html_path.exists()
    text = html_path.read_text(encoding="utf-8")
    # Self-contained.
    assert 'src="http' not in text
    assert 'href="http' not in text
    # Has all main sections.
    for marker in (
        'id="summary"',
        'id="findings"',
        'id="gallery"',
        'id="components"',
        'id="tokens"',
    ):
        assert marker in text, f"missing {marker}"
    assert rep["version"] == "0.8.0"
