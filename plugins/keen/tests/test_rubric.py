"""Tests for harness/rubric.py: config loading, scoring, grading."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from harness.rubric import (
    DEFAULT_COMPONENT_FILTER,
    DEFAULT_GRADE_THRESHOLDS,
    DEFAULT_REPORT_OPTS,
    DEFAULT_WEIGHTS,
    grade_for,
    load_config,
    score,
    top_findings,
)

# --- load_config ----------------------------------------------------------


def test_load_config_returns_defaults_when_file_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "absent.yaml"
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(missing)
    assert cfg["severity_weights"] == DEFAULT_WEIGHTS
    assert cfg["grade_thresholds"] == DEFAULT_GRADE_THRESHOLDS
    assert cfg["component_filter"] == DEFAULT_COMPONENT_FILTER
    assert cfg["report"] == DEFAULT_REPORT_OPTS
    # Loud, not silent: a warning must be logged.
    assert any("not found" in r.getMessage() for r in caplog.records)


def test_load_config_default_path_loads_real_rubric_yaml() -> None:
    # No arg => loads config/rubric.yaml shipped with the plugin.
    cfg = load_config()
    # The shipped file mirrors defaults closely.
    assert cfg["severity_weights"]["P0"] == 10.0
    assert cfg["severity_weights"]["P1"] == 3.0
    assert cfg["severity_weights"]["P2"] == 1.0
    assert isinstance(cfg["grade_thresholds"], list)
    assert any(t.get("grade") == "A" for t in cfg["grade_thresholds"])


def test_load_config_malformed_yaml_warns_and_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad.yaml"
    # Unbalanced quote -> yaml.YAMLError
    bad.write_text("severity_weights: {P0: 'oops\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    # Full-file fallback when YAML can't be parsed at all.
    assert cfg["severity_weights"] == DEFAULT_WEIGHTS
    assert any("falling back to defaults" in r.getMessage() for r in caplog.records)


def test_load_config_non_dict_top_level_warns_and_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    assert cfg["severity_weights"] == DEFAULT_WEIGHTS
    assert any("expected a mapping" in r.getMessage() for r in caplog.records)


def test_load_config_invalid_severity_weights_warns_and_keeps_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad_weights.yaml"
    # severity_weights set to a non-numeric value -> ValueError on float()
    bad.write_text("severity_weights:\n  P0: not_a_number\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    assert cfg["severity_weights"] == DEFAULT_WEIGHTS
    assert any("severity_weights" in r.getMessage() for r in caplog.records)


def test_load_config_grade_thresholds_wrong_type_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad_thresholds.yaml"
    bad.write_text("grade_thresholds:\n  not_a_list: true\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    assert cfg["grade_thresholds"] == DEFAULT_GRADE_THRESHOLDS
    assert any("grade_thresholds" in r.getMessage() for r in caplog.records)


def test_load_config_component_filter_wrong_type_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad_filter.yaml"
    bad.write_text("component_filter: not_a_dict\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    assert cfg["component_filter"] == DEFAULT_COMPONENT_FILTER
    assert any("component_filter" in r.getMessage() for r in caplog.records)


def test_load_config_report_wrong_type_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad_report.yaml"
    bad.write_text("report: 'string-instead-of-mapping'\n")
    with caplog.at_level(logging.WARNING, logger="keen"):
        cfg = load_config(bad)
    assert cfg["report"] == DEFAULT_REPORT_OPTS
    assert any("report" in r.getMessage() for r in caplog.records)


def test_load_config_merges_partial_overrides(tmp_path: Path) -> None:
    f = tmp_path / "partial.yaml"
    f.write_text(
        "severity_weights:\n"
        "  P0: 20.0\n"
        "  P1: 5.0\n"
        "  P2: 2.0\n"
        "component_filter:\n"
        "  exclude_kinds:\n"
        "    - banner\n"
    )
    cfg = load_config(f)
    assert cfg["severity_weights"] == {"P0": 20.0, "P1": 5.0, "P2": 2.0}
    # component_filter update merges over defaults
    assert cfg["component_filter"]["exclude_kinds"] == ["banner"]
    # include_kinds default preserved
    assert cfg["component_filter"]["include_kinds"] is None
    # untouched sections fall back to defaults
    assert cfg["report"] == DEFAULT_REPORT_OPTS


def test_load_config_empty_yaml_returns_defaults(tmp_path: Path) -> None:
    f = tmp_path / "empty.yaml"
    f.write_text("")
    cfg = load_config(f)
    assert cfg["severity_weights"] == DEFAULT_WEIGHTS
    assert cfg["grade_thresholds"] == DEFAULT_GRADE_THRESHOLDS


# --- grade_for ------------------------------------------------------------


def test_grade_for_damage_zero_is_A() -> None:
    g = grade_for(0, DEFAULT_GRADE_THRESHOLDS)
    assert g["grade"] == "A"


def test_grade_for_damage_5_still_A_inclusive_boundary() -> None:
    # max_damage 5 means <= 5 maps to A.
    assert grade_for(5, DEFAULT_GRADE_THRESHOLDS)["grade"] == "A"


def test_grade_for_damage_6_drops_to_B() -> None:
    assert grade_for(6, DEFAULT_GRADE_THRESHOLDS)["grade"] == "B"


def test_grade_for_damage_15_is_B_boundary() -> None:
    assert grade_for(15, DEFAULT_GRADE_THRESHOLDS)["grade"] == "B"


def test_grade_for_damage_35_is_C_boundary() -> None:
    assert grade_for(35, DEFAULT_GRADE_THRESHOLDS)["grade"] == "C"


def test_grade_for_damage_36_is_D() -> None:
    assert grade_for(36, DEFAULT_GRADE_THRESHOLDS)["grade"] == "D"


def test_grade_for_huge_damage_is_F() -> None:
    assert grade_for(100, DEFAULT_GRADE_THRESHOLDS)["grade"] == "F"


def test_grade_for_returns_summary() -> None:
    g = grade_for(0, DEFAULT_GRADE_THRESHOLDS)
    assert "Production-quality" in g["summary"]


def test_grade_for_empty_thresholds_defaults_to_F() -> None:
    g = grade_for(0, [])
    assert g["grade"] == "F"


# --- score ----------------------------------------------------------------


def _component(kind: str, findings: list[dict] | None = None) -> dict:
    return {
        "component_kind": kind,
        "findings": findings or [],
        "index": 0,
    }


def test_score_no_findings_yields_zero_damage_and_A() -> None:
    analysis = {"components": [_component("button"), _component("link")]}
    out = score(analysis)
    assert out["score"] == 0
    assert out["grade"] == "A"
    assert out["counts"] == {}


def test_score_single_p0_yields_10_damage() -> None:
    analysis = {
        "components": [_component("button", [{"severity": "P0", "predicate_id": "hit_target"}])]
    }
    out = score(analysis)
    assert out["score"] == 10.0
    # 10 damage > 5, < 15 => B grade
    assert out["grade"] == "B"


def test_score_mixed_severities_aggregates_correctly() -> None:
    analysis = {
        "components": [
            _component(
                "button",
                [
                    {"severity": "P0", "predicate_id": "a"},
                    {"severity": "P1", "predicate_id": "b"},
                    {"severity": "P2", "predicate_id": "c"},
                ],
            )
        ]
    }
    out = score(analysis)
    # 10 + 3 + 1 = 14 damage; <=15 => B
    assert out["score"] == 14.0
    assert out["counts"] == {"P0": 1, "P1": 1, "P2": 1}
    assert out["grade"] == "B"


def test_score_custom_weights_override() -> None:
    analysis = {"components": [_component("button", [{"severity": "P0", "predicate_id": "x"}])]}
    out = score(analysis, weights={"P0": 1.0, "P1": 1.0, "P2": 1.0})
    assert out["score"] == 1.0


def test_score_by_component_kind_density() -> None:
    analysis = {
        "components": [
            _component("button", [{"severity": "P0", "predicate_id": "x"}]),
            _component("button", []),
            _component("link", [{"severity": "P1", "predicate_id": "y"}]),
        ]
    }
    out = score(analysis)
    by_kind = out["by_component_kind"]
    assert by_kind["button"]["total"] == 2
    assert by_kind["button"]["with_findings"] == 1
    assert by_kind["button"]["P0"] == 1
    assert by_kind["button"]["density_pct"] == 50.0
    assert by_kind["link"]["total"] == 1
    assert by_kind["link"]["with_findings"] == 1
    assert by_kind["link"]["density_pct"] == 100.0


def test_score_empty_components_returns_zero() -> None:
    analysis = {"components": []}
    out = score(analysis)
    assert out["score"] == 0
    assert out["grade"] == "A"
    assert out["by_component_kind"] == {}


def test_score_uses_config_thresholds() -> None:
    # Force a stricter rubric where 1 damage = F
    cfg = {
        "severity_weights": {"P0": 100.0, "P1": 100.0, "P2": 100.0},
        "grade_thresholds": [{"max_damage": 0, "grade": "A", "summary": ""}],
    }
    analysis = {"components": [_component("button", [{"severity": "P2", "predicate_id": "x"}])]}
    out = score(analysis, config=cfg)
    # damage = 100 > 0 => falls past all thresholds => last entry
    assert out["grade"] == "A"  # last threshold has grade "A" in this trivial config


def test_score_caps_repeated_predicate_but_preserves_raw_score() -> None:
    components = [
        _component(
            "button",
            [{"severity": "P2", "predicate_id": "spacing.grid"}],
        )
        for _ in range(20)
    ]

    out = score({"components": components})

    assert out["raw_score"] == 20.0
    assert out["score"] == 3.0
    assert out["scoring"]["repeat_cap_per_predicate"] == 3
    assert out["scoring"]["version"] == "density-v2"


def test_score_same_defect_density_is_stable_across_page_sizes() -> None:
    def surface(component_count: int, finding_count: int) -> dict:
        return {
            "components": [
                _component(
                    "button",
                    (
                        [{"severity": "P1", "predicate_id": "focus.visible"}]
                        if index < finding_count
                        else []
                    ),
                )
                for index in range(component_count)
            ]
        }

    reference = score(surface(50, 5))
    large = score(surface(400, 40))

    assert reference["score"] == 15.0
    assert large["score"] == reference["score"]
    assert large["raw_score"] == 120.0
    assert large["scoring"]["normalization_factor"] == 8.0


def test_score_single_p0_keeps_non_a_floor_on_large_surface() -> None:
    components = [_component("button") for _ in range(500)]
    components[0]["findings"] = [{"severity": "P0", "predicate_id": "contrast.text"}]

    out = score({"components": components})

    assert out["raw_score"] == 10.0
    assert out["score"] == 6.0
    assert out["grade"] == "B"


def test_score_retains_legacy_top_level_keys() -> None:
    out = score({"components": [_component("button")]})

    assert {
        "score",
        "counts",
        "weights",
        "by_component_kind",
        "grade",
        "grade_summary",
    } <= out.keys()


def _captured_surface(
    viewport: str,
    finding: dict | None = None,
    component_count: int = 10,
) -> list[dict]:
    components: list[dict] = []
    for index in range(component_count):
        component = _component("button", [finding] if finding and index == 0 else [])
        component.update(
            {
                "index": index,
                "viewport": viewport,
                "state": "default",
                "capture_path": f"screens/page-{viewport}-default.png",
            }
        )
        components.append(component)
    return components


def test_score_is_invariant_when_small_surface_repeats_across_captures() -> None:
    finding = {"severity": "P1", "predicate_id": "target.size-aa"}
    single = score({"components": _captured_surface("desktop", finding)})
    repeated = score(
        {
            "components": [
                *_captured_surface("desktop", finding),
                *_captured_surface("tablet", finding),
                *_captured_surface("mobile", finding),
            ]
        }
    )

    assert single["score"] == 3.0
    assert repeated["score"] == single["score"]
    assert repeated["raw_score"] == 9.0
    assert repeated["scoring"]["capture_count"] == 3
    assert repeated["scoring"]["graded_component_count"] == 10
    assert repeated["scoring"]["aggregation"] == "worst-capture"


def test_score_includes_defect_found_only_in_one_responsive_capture() -> None:
    finding = {"severity": "P1", "predicate_id": "layout.off-canvas"}
    clean = score(
        {
            "components": [
                *_captured_surface("desktop"),
                *_captured_surface("tablet"),
                *_captured_surface("mobile"),
            ]
        }
    )
    responsive_defect = score(
        {
            "components": [
                *_captured_surface("desktop"),
                *_captured_surface("tablet"),
                *_captured_surface("mobile", finding),
            ]
        }
    )

    assert clean["score"] == 0
    assert responsive_defect["score"] == 3.0
    assert responsive_defect["scoring"]["worst_capture"].endswith("page-mobile-default.png")


def test_score_dedupes_unscoped_page_finding_repeated_by_each_capture() -> None:
    page_finding = {"severity": "P1", "predicate_id": "landmark.one-main"}
    components = [
        *_captured_surface("desktop"),
        *_captured_surface("tablet"),
        *_captured_surface("mobile"),
    ]
    single = score({"components": components, "global_findings": [page_finding]})
    repeated = score(
        {
            "components": components,
            "global_findings": [page_finding, page_finding, page_finding],
        }
    )

    assert repeated["score"] == single["score"] == 3.0
    assert repeated["raw_score"] == 9.0
    assert repeated["global_findings"] == 3


def test_score_maps_scoped_global_finding_to_its_responsive_capture() -> None:
    components = [
        *_captured_surface("desktop"),
        *_captured_surface("mobile"),
    ]
    mobile_page_finding = {
        "severity": "P1",
        "predicate_id": "landmark.one-main",
        "viewport": "mobile",
        "state": "default",
    }

    out = score({"components": components, "global_findings": [mobile_page_finding]})

    assert out["score"] == 3.0
    assert out["scoring"]["worst_capture"].endswith("page-mobile-default.png")


def test_score_combines_unscoped_page_and_component_damage_per_capture() -> None:
    component_finding = {"severity": "P1", "predicate_id": "layout.off-canvas"}
    page_finding = {"severity": "P1", "predicate_id": "landmark.one-main"}
    components = [
        *_captured_surface("desktop", component_finding),
        *_captured_surface("mobile"),
    ]

    out = score({"components": components, "global_findings": [page_finding]})

    assert out["score"] == 6.0
    assert out["grade"] == "B"


# --- top_findings ---------------------------------------------------------


def test_top_findings_sorted_by_severity() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": 0,
                "findings": [
                    {"severity": "P2", "predicate_id": "a"},
                    {"severity": "P0", "predicate_id": "b"},
                    {"severity": "P1", "predicate_id": "c"},
                ],
            }
        ]
    }
    out = top_findings(analysis, limit=10)
    severities = [f["severity"] for f in out]
    assert severities == ["P0", "P1", "P2"]


def test_top_findings_respects_limit() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": 0,
                "findings": [{"severity": "P1", "predicate_id": f"p{i}"} for i in range(20)],
            }
        ]
    }
    out = top_findings(analysis, limit=5)
    assert len(out) == 5


def test_top_findings_diversifies_with_per_predicate_cap() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": 0,
                "findings": [
                    # 5 of the same predicate, then 2 different ones
                    *[{"severity": "P1", "predicate_id": "noisy"} for _ in range(5)],
                    {"severity": "P1", "predicate_id": "rare_a"},
                    {"severity": "P1", "predicate_id": "rare_b"},
                ],
            }
        ]
    }
    out = top_findings(analysis, limit=10, per_predicate_cap=2)
    pids = [f["predicate_id"] for f in out]
    # At most 2 of "noisy" before the cap kicks in
    assert pids.count("noisy") == 2 + 3  # 2 within cap, 3 deferred backfill since limit not yet hit
    # Both rare ones come through
    assert "rare_a" in pids
    assert "rare_b" in pids


def test_top_findings_attaches_component_metadata() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "button",
                "index": 7,
                "viewport": "mobile",
                "state": "default",
                "crop_path": "crops/0.png",
                "findings": [
                    {"severity": "P0", "predicate_id": "x"},
                ],
            }
        ]
    }
    out = top_findings(analysis)
    assert len(out) == 1
    f = out[0]
    assert f["component_kind"] == "button"
    assert f["component_index"] == 7
    assert f["viewport"] == "mobile"
    assert f["state"] == "default"
    assert f["crop_path"] == "crops/0.png"


def test_top_findings_empty_returns_empty() -> None:
    assert top_findings({"components": []}) == []
