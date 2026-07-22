"""Unit tests for harness.validate_system."""

import json
import subprocess
import sys
from pathlib import Path

from harness import validate_system as validate_module
from harness.validate_system import validate_system


def _good_system() -> dict:
    """Minimal complete system used as the base for negative tests."""
    return {
        "name": "test-system",
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
                # Required because the system defines `surface.bold` (a dark
                # surface). #7AB1E8 reaches ~7.8:1 against #0A1929.
                "border.focused-inverse": {"hex": "#7AB1E8"},
            },
        },
        "fonts": {
            "body": "Inter, system-ui, sans-serif",
            "display": "Inter, system-ui, sans-serif",
            "mono": "ui-monospace, monospace",
        },
    }


def test_minimal_good_system_passes():
    findings = validate_system(_good_system())
    # No P0 findings on a hand-crafted good system.
    p0s = [f for f in findings if f["severity"] == "P0"]
    assert p0s == [], f"unexpected P0s: {p0s}"


def test_missing_foundational_sections_fails_closed():
    findings = validate_system(
        {
            "name": "color-only",
            "version": "0.1.0",
            "colors": {"roles": _good_system()["colors"]["roles"]},
        }
    )
    finding = next(
        item for item in findings if item["predicate_id"] == "system.structure.required-sections"
    )
    assert finding["severity"] == "P0"
    assert "type.sizes" in finding["missing"]
    assert "fonts" in finding["missing"]


def test_malformed_type_and_radius_entries_fail_closed():
    system = _good_system()
    system["type"]["sizes"][0].pop("role")
    system["radii"]["scale"][0].pop("role")
    findings = validate_system(system)
    finding = next(
        item for item in findings if item["predicate_id"] == "system.structure.required-sections"
    )
    assert "type.sizes[].role/size_px" in finding["missing"]
    assert "radii.scale[].role/px" in finding["missing"]


def test_predicate_exception_becomes_p0_internal_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def broken(_system: dict) -> list[dict]:
        raise RuntimeError("boom")

    monkeypatch.setattr(validate_module, "PREDICATES", [("system.test-broken", broken)])
    findings = validate_system(_good_system())

    assert findings == [
        {
            "predicate_id": "system.test-broken.internal-error",
            "severity": "P0",
            "message": "validator predicate system.test-broken failed with RuntimeError",
            "exception_type": "RuntimeError",
        }
    ]


def test_missing_required_role_fires_p0():
    sys = _good_system()
    del sys["colors"]["roles"]["text.default"]
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.roles.required-present" in ids


# -- Task 12: text contrast (AA on surface.default) ---------------------


def test_text_default_low_contrast_fires_p0():
    sys = _good_system()
    sys["colors"]["roles"]["text.default"]["hex"] = "#BBBBBB"  # ~2:1 vs white
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.roles.text-default-aa" in ids


def test_text_subtle_low_contrast_fires_p0():
    sys = _good_system()
    sys["colors"]["roles"]["text.subtle"]["hex"] = "#CCCCCC"
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.roles.text-subtle-aa" in ids


def test_text_inverse_on_bold_must_be_aa():
    sys = _good_system()
    sys["colors"]["roles"]["text.inverse"]["hex"] = "#444444"  # too dark on dark
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.roles.text-inverse-aa" in ids


# -- Task 13: text.subtlest, text.disabled, border.focused --------------


def test_text_subtlest_low_contrast_fires_p1():
    sys = _good_system()
    sys["colors"]["roles"]["text.subtlest"] = {"hex": "#DDDDDD"}
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.text-subtlest-large-only"]
    assert rec and rec[0]["severity"] == "P1"


def test_text_disabled_too_readable_fires_p1():
    # text.disabled SHOULD NOT meet 4.5:1, otherwise it doesn't look disabled.
    sys = _good_system()
    sys["colors"]["roles"]["text.disabled"] = {"hex": "#222222"}  # 12:1 on white
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.text-disabled-not-aa"]
    assert rec and rec[0]["severity"] == "P1"


def test_text_disabled_appropriately_subtle_passes():
    sys = _good_system()
    sys["colors"]["roles"]["text.disabled"] = {"hex": "#B8C2CC"}  # ~1.8:1 on white
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.text-disabled-not-aa"]
    assert rec == []


def test_border_focused_low_contrast_fires_p0():
    sys = _good_system()
    sys["colors"]["roles"]["border.focused"]["hex"] = "#EEEEEE"  # almost invisible
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.border-focused-aa"]
    assert rec and rec[0]["severity"] == "P0"


def test_border_focused_inverse_required_when_dark_surface_exists():
    """When the system defines a dark surface (`surface.bold` at L=0.21),
    the `border.focused-inverse` role MUST be defined."""
    sys = _good_system()
    sys["colors"]["roles"].pop("border.focused-inverse")
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.border-focused-inverse-aa"]
    assert rec and rec[0]["severity"] == "P0"
    assert "no `border.focused-inverse`" in rec[0]["message"]
    assert "surface.bold" in rec[0]["message"]


def test_border_focused_inverse_low_contrast_against_dark_surface_fires_p0():
    """A border.focused-inverse that fails 3:1 against a dark surface fires
    a P0 finding."""
    sys = _good_system()
    # #222222 vs #0A1929 is ~1.4:1 — both are dark, no contrast.
    sys["colors"]["roles"]["border.focused-inverse"]["hex"] = "#222222"
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.border-focused-inverse-aa"]
    assert rec and rec[0]["severity"] == "P0"
    assert "surface.bold" in rec[0]["message"]


def test_border_focused_inverse_not_required_when_no_dark_surface():
    """If the system has no dark surface, the inverse role is unneeded and
    its absence is not flagged."""
    sys = _good_system()
    # Drop all dark surfaces (replace surface.bold with another light shade)
    # and drop the inverse role.
    sys["colors"]["roles"]["surface.bold"]["hex"] = "#EEEEEE"
    sys["colors"]["roles"].pop("border.focused-inverse")
    # text.inverse on surface.bold is now low-contrast (#FFFFFF on #EEEEEE),
    # which would fire a different predicate; we only assert OUR predicate
    # is silent here.
    findings = validate_system(sys)
    rec = [f for f in findings if f["predicate_id"] == "system.roles.border-focused-inverse-aa"]
    assert rec == []


# -- Task 14: type scale ------------------------------------------------


def test_type_scale_non_monotonic_fires_p0():
    sys = _good_system()
    sys["type"]["sizes"] = [
        {"role": "body", "size_px": 16, "weight": 400, "line_height": 24},
        {"role": "h2", "size_px": 14, "weight": 600, "line_height": 20},  # smaller — wrong
    ]
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.type.scale-monotonic" in ids


def test_type_ratio_inconsistent_fires_p1():
    sys = _good_system()
    # Mix two different ratios: 1.2 then 1.5 (not within +/- 5%).
    sys["type"]["sizes"] = [
        {"role": "body", "size_px": 16, "weight": 400, "line_height": 24},
        {"role": "h2", "size_px": int(16 * 1.2), "weight": 600, "line_height": 28},
        {"role": "h1", "size_px": int(16 * 1.2 * 1.5), "weight": 700, "line_height": 40},
    ]
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.type.ratio-consistent" in ids


def test_editorial_archetype_requires_body_line_height_15():
    sys = _good_system()
    sys["archetype"] = "editorial"
    sys["type"]["sizes"] = [
        {"role": "body", "size_px": 16, "weight": 400, "line_height": 22},  # 22/16 = 1.375 < 1.5
    ]
    findings = validate_system(sys, archetype="editorial")
    ids = [f["predicate_id"] for f in findings]
    assert "system.type.line-height-prose" in ids


# -- Task 15: spacing / radii / semantic / archetype-primary ------------


def test_spacing_off_grid_fires_p0():
    sys = _good_system()
    sys["spacing"]["scale"] = [4, 8, 13, 16, 24]  # 13 not a multiple of 4
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.spacing.single-grid" in ids


def test_spacing_non_monotonic_fires_p0():
    sys = _good_system()
    sys["spacing"]["scale"] = [4, 8, 12, 8, 16]
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.spacing.scale-monotonic" in ids


def test_radii_non_monotonic_fires_p0():
    sys = _good_system()
    sys["radii"]["scale"] = [
        {"role": "none", "px": 0},
        {"role": "md", "px": 16},
        {"role": "sm", "px": 4},  # smaller after larger
    ]
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.radii.scale-monotonic" in ids


def test_semantic_success_red_fires_p1():
    sys = _good_system()
    sys["colors"]["semantic"] = {"success": {"hex": "#D62828"}}  # red
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.semantic.success-not-red" in ids


def test_semantic_error_green_fires_p1():
    sys = _good_system()
    sys["colors"]["semantic"] = {"error": {"hex": "#65A30D"}}  # OKLCH hue ~132 deg
    findings = validate_system(sys)
    ids = [f["predicate_id"] for f in findings]
    assert "system.semantic.error-not-green" in ids


def test_brand_forward_without_primary_fires_p0():
    sys = _good_system()
    sys["archetype"] = "brand-forward"
    # No primary role.
    sys["colors"]["roles"].pop("primary", None)
    findings = validate_system(sys, archetype="brand-forward")
    ids = [f["predicate_id"] for f in findings]
    assert "system.archetype.primary-required" in ids


# -- Task 16: contrast matrix -------------------------------------------


def test_contrast_matrix_emitted():
    from harness.validate_system import emit_contrast_matrix

    matrix = emit_contrast_matrix(_good_system())
    assert "text.default" in matrix
    # Every cell is a float ratio.
    for text_role, surfaces in matrix.items():
        for surface_role, ratio in surfaces.items():
            assert isinstance(ratio, float)
            assert 1.0 <= ratio <= 21.0, f"{text_role} x {surface_role} = {ratio}"


# -- Task 17: CLI integration -------------------------------------------


def test_cli_validate_system_exits_clean_on_good_system(tmp_path: Path):
    sys_json = tmp_path / "sys.json"
    sys_json.write_text(json.dumps(_good_system()))
    result = subprocess.run(
        [sys.executable, "-m", "harness", "validate-system", str(sys_json)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_validate_system_exits_nonzero_on_p0(tmp_path: Path):
    bad = _good_system()
    bad["colors"]["roles"]["text.default"]["hex"] = "#CCCCCC"  # ~1.6:1
    sys_json = tmp_path / "bad.json"
    sys_json.write_text(json.dumps(bad))
    result = subprocess.run(
        [sys.executable, "-m", "harness", "validate-system", str(sys_json)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "text-default-aa" in (result.stdout + result.stderr)


def test_cli_validate_system_writes_out_artifacts(tmp_path: Path):
    """--out writes both validation.json and contrast-matrix.json with
    parseable JSON content."""
    sys_json = tmp_path / "sys.json"
    sys_json.write_text(json.dumps(_good_system()))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "validate-system",
            str(sys_json),
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    validation_path = out_dir / "validation.json"
    matrix_path = out_dir / "contrast-matrix.json"
    assert validation_path.exists()
    assert matrix_path.exists()
    validation = json.loads(validation_path.read_text())
    assert "findings" in validation
    assert isinstance(validation["findings"], list)
    matrix = json.loads(matrix_path.read_text())
    assert "text.default" in matrix
    assert isinstance(matrix["text.default"]["surface.default"], (int, float))
