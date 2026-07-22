"""Tests for harness.preview_system."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.preview_system import (
    builtin_reference_system,
    render_comparison,
    render_dashboard_mockup,
    render_form_mockup,
    render_landing_mockup,
    render_preview,
    render_system_md,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _system() -> dict:
    """Validate-system-shaped minimal system used for renderer tests."""
    return {
        "name": "their-system",
        "version": "0.1.0",
        "archetype": "clarity-first",
        "type": {
            "ratio": 1.2,
            "body_px": 16,
            "sizes": [
                {"role": "body", "size_px": 16, "weight": 400, "line_height": 24},
                {"role": "h1", "size_px": 32, "weight": 700, "line_height": 40},
            ],
        },
        "spacing": {"grid_px": 4, "scale": [4, 8, 12, 16, 24, 32]},
        "radii": {
            "scale": [
                {"role": "none", "px": 0},
                {"role": "sm", "px": 4},
                {"role": "md", "px": 8},
            ]
        },
        "colors": {
            "roles": {
                "text.default": {"hex": "#0A1929"},
                "text.subtle": {"hex": "#3D4F66"},
                "text.inverse": {"hex": "#FFFFFF"},
                "surface.default": {"hex": "#FFFFFF"},
                "surface.subtle": {"hex": "#F5F7FA"},
                "surface.bold": {"hex": "#0A1929"},
                "border.default": {"hex": "#D6DDE5"},
                "border.focused": {"hex": "#0A4D8C"},
                "border.focused-inverse": {"hex": "#7AB1E8"},
            }
        },
    }


# -- render_preview / shape adapter ----------------------------------------


def test_render_preview_returns_self_contained_html():
    """The single-system preview is a full HTML document with no external assets."""
    out = render_preview(_system())
    assert "<!doctype html>" in out
    assert "<style>" in out  # inlined CSS
    assert "their-system" in out
    assert 'src="http' not in out
    assert 'href="http' not in out
    assert 'rel="stylesheet"' not in out


def test_render_preview_escapes_malicious_name():
    """User-controlled name is HTML-escaped before reaching the renderer."""
    system = _system()
    system["name"] = "<script>alert(1)</script>"
    out = render_preview(system)
    assert "<script>alert(1)</script>" not in out  # raw tag NOT present
    assert "&lt;script&gt;" in out  # escaped form IS present


def test_render_preview_handles_missing_spacing():
    """The renderer must not raise when the system omits `spacing` entirely."""
    system = _system()
    del system["spacing"]
    # Must not raise
    out = render_preview(system)
    assert isinstance(out, str) and "<html" in out.lower()


def test_render_preview_handles_partial_spacing():
    """Partial spacing (e.g. grid_px without scale) must not raise."""
    system = _system()
    system["spacing"] = {"grid_px": 8}  # no scale key
    out = render_preview(system)
    assert isinstance(out, str) and "<html" in out.lower()


def test_render_preview_maps_legacy_default_font_to_body_and_display():
    system = _system()
    system["fonts"] = {"default": "Aptos, sans-serif"}
    out = render_preview(system)

    assert "--font-body: Aptos, sans-serif" in out
    assert "--font-display: Aptos, sans-serif" in out


# -- render_comparison ----------------------------------------------------


def test_comparison_html_has_three_cells():
    """1 proposal + 2 refs + 3 labels => 3 cells, each labeled."""
    html_out = render_comparison(
        proposal=_system(),
        references=[_system(), _system()],
        labels=["proposal", "material-3", "apple-hig"],
    )
    for label in ("proposal", "material-3", "apple-hig"):
        assert label in html_out
    assert html_out.count("<section") >= 3


def test_comparison_no_external_resources():
    """The comparison HTML is fully self-contained — no fonts, CSS, JS, images."""
    out = render_comparison(_system(), [_system()], ["proposal", "x"])
    assert 'src="http' not in out
    assert 'href="http' not in out
    assert 'rel="stylesheet"' not in out


def test_comparison_label_count_mismatch_raises():
    """If labels don't match systems, raise ValueError."""
    with pytest.raises(ValueError, match="labels has"):
        render_comparison(_system(), [_system()], ["only-one-label"])


def test_comparison_html_escapes_labels():
    """Untrusted labels are HTML-escaped before being embedded."""
    out = render_comparison(_system(), [_system()], ["<script>alert(1)</script>", "ok"])
    # Raw tag must not appear; the escaped form must.
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_builtin_reference_system_is_previewable():
    reference = builtin_reference_system("material-3")

    assert reference is not None
    assert reference["name"] == "material-3"
    assert "primary" in reference["colors"]["roles"]
    body = next(item for item in reference["type"]["sizes"] if item["role"] == "body")
    assert body["size_px"] == 16
    assert "Design system review" in render_preview(reference)
    assert "Bundled reference" in render_preview(reference)
    assert reference["fonts"]["display"] == reference["fonts"]["body"]
    assert reference["fonts"]["pairing"]["mode"] == "single-family"


def test_builtin_reference_system_rejects_unknown_name():
    assert builtin_reference_system("not-a-system") is None


def test_mockups_are_responsive_self_contained_documents():
    for document in (
        render_landing_mockup(_system()),
        render_dashboard_mockup(_system()),
        render_form_mockup(_system()),
    ):
        assert '<meta name="viewport"' in document
        assert "@media" in document
        assert 'src="http' not in document


def test_dashboard_closes_chart_before_activity_table():
    document = render_dashboard_mockup(_system())

    assert "</div>\n    <table>" in document
    assert "Illustrative project activity" in document


# -- render_system_md -----------------------------------------------------


def test_system_md_includes_name_archetype_grade():
    """The markdown summary mentions name, archetype, and each required role."""
    md = render_system_md(_system())
    assert "their-system" in md
    assert "clarity-first" in md
    for role in ("text.default", "surface.default", "border.focused"):
        assert role in md


def test_system_md_documents_typography_pairing():
    system = _system()
    system["fonts"] = {
        "body": "Inter, sans-serif",
        "display": "Georgia, serif",
        "mono": "Menlo, monospace",
        "pairing": {"rationale": "Observed editorial contrast."},
    }

    md = render_system_md(system)

    assert "## Typography pairing" in md
    assert "Display: **Georgia, serif**" in md
    assert "Body / interface: **Inter, sans-serif**" in md
    assert "Observed editorial contrast." in md


# -- CLI: preview-system subcommand ---------------------------------------


def test_cli_preview_system_writes_preview_html(tmp_path: Path):
    """`preview-system <json> --out <dir>` writes preview.html and system.md."""
    sys_json = tmp_path / "sys.json"
    sys_json.write_text(json.dumps(_system()))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "preview-system",
            str(sys_json),
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(PLUGIN_ROOT),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (out_dir / "preview.html").exists()
    assert (out_dir / "system.md").exists()


def test_cli_preview_system_missing_refs_still_succeeds(tmp_path: Path):
    """`--compare-with` names with no reference JSON warn but don't fail.

    No reference JSONs are shipped under ``references/design-systems/`` yet
    (only ``.md`` files). Passing names with no corresponding JSON should
    leave the run successful — preview.html and system.md must still write —
    and no comparison.html should be written.
    """
    sys_json = tmp_path / "sys.json"
    sys_json.write_text(json.dumps(_system()))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "preview-system",
            str(sys_json),
            "--out",
            str(out_dir),
            "--compare-with",
            "does-not-exist,also-missing",
        ],
        capture_output=True,
        text=True,
        cwd=str(PLUGIN_ROOT),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (out_dir / "preview.html").exists()
    assert (out_dir / "system.md").exists()
    assert not (out_dir / "comparison.html").exists()
    # The warning makes it clear what was skipped.
    assert "skipping reference" in result.stderr


def test_cli_preview_system_with_compare_with_writes_comparison_html(tmp_path: Path):
    """Bundled reference names produce a comparison without JSON sidecars."""
    sys_json = tmp_path / "sys.json"
    sys_json.write_text(json.dumps(_system()))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "preview-system",
            str(sys_json),
            "--out",
            str(out_dir),
            "--compare-with",
            "material-3,apple-hig",
        ],
        capture_output=True,
        text=True,
        cwd=str(PLUGIN_ROOT),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (out_dir / "preview.html").exists()
    assert (out_dir / "comparison.html").exists()


def test_cli_preview_system_missing_file_exits_2(tmp_path: Path):
    """A missing system_json path exits with CLI-misuse code 2."""
    missing = tmp_path / "nope.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "preview-system",
            str(missing),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        cwd=str(PLUGIN_ROOT),
    )
    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"


# -- E2E pipeline --------------------------------------------------------


def test_e2e_pipeline_derive_validate_preview(tmp_path: Path):
    """End-to-end: derive a palette, hand-write a system, validate, preview.

    Exercises the same CLI calls the ``/ui-create`` slash command makes,
    in the same order, against a hand-built system JSON. The slash command
    itself is invoked by Claude (not pytest), so this test verifies only
    the deterministic CLI pipeline behind it.
    """
    py = sys.executable

    # 1. Derive palette
    palette_out = tmp_path / "palette"
    result = subprocess.run(
        [
            py,
            "-m",
            "harness",
            "derive-palette",
            "--seed",
            "#0A4D8C",
            "--strategy",
            "monochromatic",
            "--steps",
            "11",
            "--name",
            "e2e",
            "--out",
            str(palette_out),
        ],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"derive-palette failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert (palette_out / "palette.json").exists()

    # 2. Hand-build a system that uses the derived palette family.
    sys_json = tmp_path / "system.json"
    sys_json.write_text(
        json.dumps(
            {
                "name": "e2e",
                "version": "0.1.0",
                "archetype": "clarity-first",
                "type": {
                    "ratio": 1.2,
                    "body_px": 16,
                    "sizes": [
                        {"role": "body", "size_px": 16, "weight": 400, "line_height": 24},
                        {"role": "h1", "size_px": 32, "weight": 700, "line_height": 40},
                    ],
                },
                "spacing": {"grid_px": 4, "scale": [4, 8, 12, 16, 24, 32]},
                "radii": {
                    "scale": [
                        {"role": "sm", "px": 4},
                        {"role": "md", "px": 8},
                    ]
                },
                "fonts": {
                    "body": "Inter, system-ui, sans-serif",
                    "display": "Inter, system-ui, sans-serif",
                    "mono": "ui-monospace, monospace",
                },
                "colors": {
                    "roles": {
                        "text.default": {"hex": "#0A1929"},
                        "text.subtle": {"hex": "#3D4F66"},
                        "text.inverse": {"hex": "#FFFFFF"},
                        "surface.default": {"hex": "#FFFFFF"},
                        "surface.subtle": {"hex": "#F5F7FA"},
                        "surface.bold": {"hex": "#0A1929"},
                        "border.default": {"hex": "#D6DDE5"},
                        "border.focused": {"hex": "#0A4D8C"},
                        "border.focused-inverse": {"hex": "#7AB1E8"},
                    }
                },
            }
        )
    )

    # 3. Validate
    result = subprocess.run(
        [
            py,
            "-m",
            "harness",
            "validate-system",
            str(sys_json),
            "--archetype",
            "clarity-first",
        ],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"validate-system failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # 4. Preview
    preview_out = tmp_path / "preview"
    result = subprocess.run(
        [
            py,
            "-m",
            "harness",
            "preview-system",
            str(sys_json),
            "--out",
            str(preview_out),
        ],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"preview-system failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert (preview_out / "preview.html").exists()
    assert (preview_out / "system.md").exists()
