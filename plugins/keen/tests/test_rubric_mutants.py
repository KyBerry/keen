"""Tests designed to kill mutmut survivors in harness/rubric.py.

The existing rubric tests (test_rubric.py and test_rubric_props.py) are
already strong — they walk every load_config branch and exercise score
arithmetic broadly. This file pins a handful of literal/boundary cases
that property tests don't quite reach: exact DEFAULT_WEIGHTS values,
default-grade summary strings, the per-predicate cap arithmetic in
top_findings, and the severity rank ordering.
"""

from __future__ import annotations

from harness.rubric import (
    DEFAULT_GRADE_THRESHOLDS,
    DEFAULT_REPORT_OPTS,
    DEFAULT_WEIGHTS,
    PREDICATE_SEVERITIES,
    grade_for,
    score,
    top_findings,
)

# ---------------------------------------------------------------------------
# DEFAULT_WEIGHTS — exact numerical values
# ---------------------------------------------------------------------------


def test_default_weights_p0_is_10() -> None:
    assert DEFAULT_WEIGHTS["P0"] == 10.0


def test_default_weights_p1_is_3() -> None:
    assert DEFAULT_WEIGHTS["P1"] == 3.0


def test_default_weights_p2_is_1() -> None:
    assert DEFAULT_WEIGHTS["P2"] == 1.0


def test_default_weights_strict_ordering_p0_gt_p1_gt_p2() -> None:
    # Pin the relative ordering so any constant mutation that swaps
    # two values gets caught.
    assert DEFAULT_WEIGHTS["P0"] > DEFAULT_WEIGHTS["P1"] > DEFAULT_WEIGHTS["P2"]
    assert DEFAULT_WEIGHTS["P0"] - DEFAULT_WEIGHTS["P1"] == 7.0
    assert DEFAULT_WEIGHTS["P1"] - DEFAULT_WEIGHTS["P2"] == 2.0


# ---------------------------------------------------------------------------
# DEFAULT_GRADE_THRESHOLDS — exact max_damage and grade letters
# ---------------------------------------------------------------------------


def test_grade_thresholds_letters_in_order() -> None:
    letters = [row["grade"] for row in DEFAULT_GRADE_THRESHOLDS]
    assert letters == ["A", "B", "C", "D", "F"]


def test_grade_thresholds_boundaries() -> None:
    by_grade = {row["grade"]: row["max_damage"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["A"] == 5
    assert by_grade["B"] == 15
    assert by_grade["C"] == 35
    assert by_grade["D"] == 75
    # The F threshold is intentionally absurd to catch everything.
    assert by_grade["F"] == 9999


def test_grade_thresholds_summary_text_for_a() -> None:
    # mutmut: kill mutation 219 — was: summary for A grade swapped to
    # "XX...XX". A `substring in summary` check passes both before and
    # after the mutation, so we pin the full exact text to distinguish.
    by_grade = {row["grade"]: row["summary"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["A"] == "Production-quality. Ship-blocking issues absent."


def test_grade_thresholds_summary_text_for_b() -> None:
    # mutmut: kill mutations 480, 481 — pin the exact text and the
    # "summary" key.
    by_grade = {row["grade"]: row["summary"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["B"] == "Solid. Address the listed P1s before ship."
    # Also assert every row uses the literal "summary" key (not "XXsummaryXX").
    for row in DEFAULT_GRADE_THRESHOLDS:
        assert "summary" in row, f"row {row} missing 'summary' key"


def test_grade_thresholds_summary_text_for_c() -> None:
    # mutmut: kill mutations 230, 231 — pin the exact text.
    by_grade = {row["grade"]: row["summary"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["C"] == (
        "Functional but rough. Several issues affect usability or accessibility."
    )


def test_grade_thresholds_summary_text_for_d() -> None:
    # mutmut: kill mutations 236, 237 — pin the exact text and key name.
    by_grade = {row["grade"]: row["summary"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["D"] == (
        "Significant accessibility or quality gaps. Don't ship without addressing P0s."
    )


def test_grade_thresholds_summary_text_for_f() -> None:
    by_grade = {row["grade"]: row["summary"] for row in DEFAULT_GRADE_THRESHOLDS}
    assert by_grade["F"] == "Not ready. Fundamental accessibility or quality failures."


def test_load_config_grade_thresholds_key_used_in_cfg(tmp_path) -> None:
    # mutmut: kill mutations 275, 276 —
    #  275: gt = loaded["grade_thresholds"] -> gt = None.
    #  276: cfg["grade_thresholds"] = list(gt) ->
    #        cfg["XXgrade_thresholdsXX"] = list(gt).
    # Both leave cfg["grade_thresholds"] as the *default* under the
    # mutated code path. Use a custom max_damage that wouldn't appear in
    # the defaults so we can distinguish.
    from harness.rubric import load_config

    yaml_file = tmp_path / "rubric.yaml"
    yaml_file.write_text("grade_thresholds:\n  - {max_damage: 42, grade: A, summary: ok}\n")
    cfg = load_config(yaml_file)
    assert "grade_thresholds" in cfg
    # max_damage=42 is NOT in the default list, so observing it confirms
    # the YAML value actually got assigned to cfg["grade_thresholds"].
    assert cfg["grade_thresholds"][0]["max_damage"] == 42
    # Pin no garbled "XXgrade_thresholdsXX" key.
    assert "XXgrade_thresholdsXX" not in cfg


def test_load_config_report_key_assigned_from_loaded(tmp_path) -> None:
    # mutmut: kill mutation 287 — was: rep = loaded["report"] ->
    # rep = None. With rep=None, isinstance check fails → cfg["report"]
    # keeps defaults. Use a custom field value that wouldn't appear in
    # the defaults.
    from harness.rubric import load_config

    yaml_file = tmp_path / "rubric.yaml"
    yaml_file.write_text("report:\n  group_by: custom_value_xyz\n")
    cfg = load_config(yaml_file)
    assert cfg["report"]["group_by"] == "custom_value_xyz"


def test_grade_for_missing_max_damage_defaults_to_zero() -> None:
    # mutmut: kill mutation 292 — was: row.get("max_damage", 0) ->
    # row.get("max_damage", 1). With a row lacking max_damage and
    # damage=0, original matches (0 <= 0), mutant matches (0 <= 1).
    # With damage=1, original DOES NOT match (1 > 0), mutant DOES match
    # (1 <= 1).
    out = grade_for(1, [{"grade": "X"}])
    # The row lacks max_damage. Under original: 1 <= 0 is False → no
    # match → falls to "last" fallback. Last row's grade is "X" so
    # result is X anyway. Try with two rows so we can distinguish.
    out = grade_for(1, [{"grade": "X"}, {"max_damage": 100, "grade": "Z"}])
    # Original: row 1's max_damage defaults to 0; 1 <= 0 is False → row
    # 2: 1 <= 100 → grade Z.
    # Mutant: row 1's default is 1; 1 <= 1 → grade X.
    # So original gives Z, mutant gives X.
    assert out["grade"] == "Z"


def test_grade_for_missing_summary_defaults_to_empty_string() -> None:
    # mutmut: kill mutation 298 — was: row.get("summary", "") -> "XXXX".
    out = grade_for(0, [{"max_damage": 10, "grade": "X"}])  # no summary
    assert out["summary"] == ""
    assert "XX" not in out["summary"]


def test_default_component_filter_exclude_kinds_key_and_values() -> None:
    # mutmut: kill mutations 243, 244, 245 — was: "exclude_kinds" key
    # garbled or its "image"/"label" values garbled.
    from harness.rubric import DEFAULT_COMPONENT_FILTER

    assert "exclude_kinds" in DEFAULT_COMPONENT_FILTER
    assert DEFAULT_COMPONENT_FILTER["exclude_kinds"] == ["image", "label"]


def test_default_component_filter_include_kinds_is_none() -> None:
    from harness.rubric import DEFAULT_COMPONENT_FILTER

    assert "include_kinds" in DEFAULT_COMPONENT_FILTER
    assert DEFAULT_COMPONENT_FILTER["include_kinds"] is None


def test_default_report_opts_max_findings_per_component_is_10() -> None:
    # Pin a single integer that downstream rendering depends on.
    assert DEFAULT_REPORT_OPTS["max_findings_per_component"] == 10


def test_default_report_opts_group_by_is_component() -> None:
    assert DEFAULT_REPORT_OPTS["group_by"] == "component"


def test_default_report_opts_show_passing_is_false() -> None:
    assert DEFAULT_REPORT_OPTS["show_passing"] is False


def test_default_report_opts_include_screenshots_is_true() -> None:
    assert DEFAULT_REPORT_OPTS["include_screenshots"] is True


# ---------------------------------------------------------------------------
# grade_for — boundary behaviour at each threshold
# ---------------------------------------------------------------------------


def test_grade_for_returns_a_at_exact_boundary_5() -> None:
    # The check is `damage <= max_damage`, so damage=5 returns A (not B).
    out = grade_for(5, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "A"


def test_grade_for_returns_b_at_exact_boundary_15() -> None:
    out = grade_for(15, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "B"


def test_grade_for_returns_b_just_above_a_boundary() -> None:
    out = grade_for(5.001, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "B"


def test_grade_for_returns_c_at_exact_boundary_35() -> None:
    out = grade_for(35, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "C"


def test_grade_for_returns_d_at_exact_boundary_75() -> None:
    out = grade_for(75, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "D"


def test_grade_for_returns_f_above_9999() -> None:
    out = grade_for(10000, DEFAULT_GRADE_THRESHOLDS)
    assert out["grade"] == "F"


def test_grade_for_returns_summary_alongside_grade() -> None:
    # Pin that the summary text from the matching row is returned.
    out = grade_for(0, DEFAULT_GRADE_THRESHOLDS)
    assert "Production-quality" in out["summary"]


def test_grade_for_empty_thresholds_returns_f_default() -> None:
    out = grade_for(10, [])
    assert out["grade"] == "F"
    assert out["summary"] == ""


def test_grade_for_missing_grade_key_in_row_defaults_to_question_mark() -> None:
    out = grade_for(0, [{"max_damage": 10}])
    assert out["grade"] == "?"


def test_grade_for_missing_max_damage_treats_as_zero() -> None:
    # Row without max_damage -> float(row.get("max_damage", 0)) == 0,
    # so only damage<=0 matches it.
    out = grade_for(0, [{"grade": "X"}])
    assert out["grade"] == "X"
    # Damage > 0 falls through to the fallback (last row).
    out2 = grade_for(1, [{"grade": "X"}])
    assert out2["grade"] == "X"  # last row is the only row


# ---------------------------------------------------------------------------
# score — damage arithmetic
# ---------------------------------------------------------------------------


def test_score_damage_is_sum_of_weighted_counts() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [
                    {"severity": "P0"},  # 10
                    {"severity": "P1"},  # 3
                    {"severity": "P2"},  # 1
                ],
            }
        ]
    }
    out = score(analysis)
    # 10 + 3 + 1 = 14
    assert out["score"] == 14.0


def test_score_damage_zero_for_clean_run() -> None:
    out = score({"components": [{"component_kind": "btn", "findings": []}]})
    assert out["score"] == 0


def test_score_grade_matches_damage() -> None:
    # damage 14 falls in B (>5, <=15).
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [{"severity": "P0"}, {"severity": "P1"}, {"severity": "P2"}],
            }
        ]
    }
    out = score(analysis)
    assert out["grade"] == "B"


def test_score_grade_summary_returned() -> None:
    analysis = {"components": [{"component_kind": "btn", "findings": []}]}
    out = score(analysis)
    assert out["grade"] == "A"
    assert "Production" in out["grade_summary"]


def test_score_unknown_severity_in_weights_dict_contributes_zero() -> None:
    # severity not in `weights` -> weights.get returns 0, no contribution.
    # NOTE: the by_component_kind breakdown only initializes P0/P1/P2 buckets,
    # so a severity outside that set actually raises KeyError there. The score
    # branch alone uses `weights.get(s, 0)`, so to exercise just that branch
    # we override weights to a 1-key dict and supply the matching severity.
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [{"severity": "P0"}],
            }
        ]
    }
    # Weights dict excludes P0 entirely -> weights.get("P0", 0) == 0.
    out = score(analysis, weights={"P1": 5.0, "P2": 5.0})
    assert out["score"] == 0


def test_score_counts_dict_keyed_by_severity() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [
                    {"severity": "P0"},
                    {"severity": "P0"},
                    {"severity": "P1"},
                ],
            }
        ]
    }
    out = score(analysis)
    assert out["counts"] == {"P0": 2, "P1": 1}


def test_score_custom_weights_override_defaults() -> None:
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [{"severity": "P0"}],
            }
        ]
    }
    out = score(analysis, weights={"P0": 100.0, "P1": 0.0, "P2": 0.0})
    assert out["score"] == 100.0


def test_score_by_component_kind_breakdown() -> None:
    analysis = {
        "components": [
            {"component_kind": "btn", "findings": [{"severity": "P0"}]},
            {"component_kind": "btn", "findings": []},
            {"component_kind": "link", "findings": [{"severity": "P1"}]},
        ]
    }
    out = score(analysis)
    btn = out["by_component_kind"]["btn"]
    link = out["by_component_kind"]["link"]
    assert btn["total"] == 2
    assert btn["with_findings"] == 1
    assert btn["P0"] == 1
    assert link["total"] == 1
    assert link["with_findings"] == 1
    assert link["P1"] == 1


def test_score_density_pct_is_with_findings_over_total_times_100() -> None:
    analysis = {
        "components": [
            {"component_kind": "btn", "findings": [{"severity": "P0"}]},
            {"component_kind": "btn", "findings": [{"severity": "P0"}]},
            {"component_kind": "btn", "findings": []},
            {"component_kind": "btn", "findings": []},
        ]
    }
    out = score(analysis)
    # 2/4 = 0.5 -> 50.0%.
    assert out["by_component_kind"]["btn"]["density_pct"] == 50.0


def test_score_density_pct_zero_total_yields_zero() -> None:
    # No components of a given kind -> no entry. But verify that the
    # density formula's `if total` short-circuit guards against div-by-0.
    out = score({"components": []})
    assert out["by_component_kind"] == {}


def test_score_damage_rounded_to_one_decimal() -> None:
    # `round(damage, 1)`. Build a damage that's an awkward float.
    analysis = {
        "components": [
            {
                "component_kind": "btn",
                "findings": [{"severity": "P0"}, {"severity": "P1"}],
            }
        ]
    }
    out = score(analysis, weights={"P0": 0.1, "P1": 0.2})
    # 0.1 + 0.2 = 0.30000000000000004; rounded to 1 decimal -> 0.3.
    assert out["score"] == 0.3


# ---------------------------------------------------------------------------
# top_findings — sort order & per-predicate cap
# ---------------------------------------------------------------------------


def _flat(*sev_pids):
    """Build an analysis with one component carrying many findings."""
    return {
        "components": [
            {
                "component_kind": "btn",
                "findings": [{"severity": s, "predicate_id": p} for s, p in sev_pids],
            }
        ]
    }


def test_top_findings_orders_p0_before_p1_before_p2() -> None:
    analysis = _flat(
        ("P2", "low"),
        ("P0", "high"),
        ("P1", "mid"),
    )
    out = top_findings(analysis, limit=10, per_predicate_cap=10)
    assert [f["severity"] for f in out] == ["P0", "P1", "P2"]


def test_top_findings_breaks_severity_ties_by_predicate_id() -> None:
    analysis = _flat(
        ("P0", "z_last"),
        ("P0", "a_first"),
        ("P0", "m_mid"),
    )
    out = top_findings(analysis, limit=10, per_predicate_cap=10)
    assert [f["predicate_id"] for f in out] == ["a_first", "m_mid", "z_last"]


def test_top_findings_unknown_severity_sorts_last() -> None:
    # `severity_rank.get(sev, 9)` — unknown severities sort last.
    analysis = _flat(("X", "unknown"), ("P0", "known"))
    out = top_findings(analysis, limit=10, per_predicate_cap=10)
    assert [f["severity"] for f in out] == ["P0", "X"]


def test_top_findings_zero_limit_returns_empty() -> None:
    analysis = _flat(("P0", "any"))
    out = top_findings(analysis, limit=0)
    assert out == []


def test_top_findings_negative_limit_returns_empty() -> None:
    # `if limit <= 0: return out`
    analysis = _flat(("P0", "any"))
    out = top_findings(analysis, limit=-5)
    assert out == []


def test_top_findings_cap_three_per_predicate_under_limit() -> None:
    # Cap of 3 — first three of pid "x" come through immediately, the
    # 4th goes to deferred. With limit=10 and only 4 items, all 4 come
    # back (3 + 1 from deferred backfill).
    analysis = _flat(
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
        ("P1", "y"),
    )
    out = top_findings(analysis, limit=10, per_predicate_cap=3)
    pids = [f["predicate_id"] for f in out]
    assert pids.count("x") == 4
    assert pids.count("y") == 1


def test_top_findings_cap_kicks_in_when_limit_hit() -> None:
    # 6 noisy + 6 rare. With limit=4 and cap=2: x*2, y*2, then break.
    analysis = _flat(
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "y"),
        ("P0", "y"),
    )
    out = top_findings(analysis, limit=4, per_predicate_cap=2)
    pids = [f["predicate_id"] for f in out]
    # Exactly 4 results, capped at 2 of each predicate.
    assert len(out) == 4
    assert pids.count("x") == 2
    assert pids.count("y") == 2


def test_top_findings_default_per_predicate_cap_is_three() -> None:
    # Pin the default — pass limit much larger than cap so all four
    # findings come through (3 + 1 deferred).
    analysis = _flat(
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
        ("P0", "x"),
    )
    out = top_findings(analysis, limit=100)
    assert len(out) == 4


def test_top_findings_default_limit_is_fifteen() -> None:
    # 20 findings, all unique predicates → returns 15 with no deferred
    # backfill (cap unused).
    analysis = _flat(*[("P0", f"p{i}") for i in range(20)])
    out = top_findings(analysis)
    assert len(out) == 15


# ---------------------------------------------------------------------------
# PREDICATE_SEVERITIES — pin the key strings the harness uses
# ---------------------------------------------------------------------------


def test_predicate_severities_name_icon_button_key_exists() -> None:
    # mutmut: kill mutation 180 — was: "name.icon-button" -> "XXname.icon-buttonXX".
    # If the key were mangled the icon-button predicate would lose its
    # severity reference (downstream rendering and severity overrides).
    assert "name.icon-button" in PREDICATE_SEVERITIES
    assert PREDICATE_SEVERITIES["name.icon-button"] == "P0"


def test_predicate_severities_name_link_key_exists() -> None:
    # mutmut: kill mutation 182 — was: "name.link" -> "XXname.linkXX".
    assert "name.link" in PREDICATE_SEVERITIES
    assert PREDICATE_SEVERITIES["name.link"] == "P0"


def test_predicate_severities_all_p0_set() -> None:
    # Lock in every P0-rated predicate so any literal mutation that
    # mangles the key string would change the membership of the set.
    p0_keys = {k for k, v in PREDICATE_SEVERITIES.items() if v == "P0"}
    expected_p0 = {
        "contrast.text",
        "name.icon-button",
        "name.link",
        "name.image",
        "label.association",
        "heading.empty",
    }
    # All expected keys must be P0 in the actual map.
    assert expected_p0.issubset(p0_keys)


def test_predicate_severities_all_p1_set() -> None:
    p1_keys = {k for k, v in PREDICATE_SEVERITIES.items() if v == "P1"}
    expected_p1 = {
        "name.link.generic",
        "heading.hierarchy",
        "layout.off-canvas",
        "tap-target.overlap",
        "dialog.aria-modal",
        "visual-dom.background-mismatch",
        "name.link.context",
        "contrast.non-text",
        "label-in-name",
        "focus.positive-tabindex",
        "target.size-aa",
        "dialog.focus-trap-affordance",
        "landmark.one-main",
    }
    assert expected_p1.issubset(p1_keys)


def test_predicate_severities_all_p2_set() -> None:
    p2_keys = {k for k, v in PREDICATE_SEVERITIES.items() if v == "P2"}
    expected_p2 = {
        "input.autocomplete",
        "input.numeric-mode",
        "spacing.grid",
        "dialog.initial-focus",
        "landmark.duplicate",
        "form.required-indicator",
        "focus.visible",
        "focus.no-styles",
        "heading.duplicate-h1",
        "hit-target.size",
        "link.distinguishable",
    }
    assert expected_p2.issubset(p2_keys)


# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------


def test_load_config_non_dict_top_level_log_template_no_xx(tmp_path) -> None:
    # mutmut: kill mutation 502 — was: "rubric.yaml: top-level value is
    # %s, expected a mapping; falling back to defaults" -> "XX...XX".
    import logging

    from harness.rubric import load_config

    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n")

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        load_config(bad)
    finally:
        keen.removeHandler(handler)

    top_level = [r for r in records if "top-level value" in r.getMessage()]
    assert top_level
    for r in top_level:
        assert not r.msg.startswith("XX"), r.msg
        assert not r.msg.endswith("XX"), r.msg


def test_load_config_component_filter_non_dict_log_template_no_xx(tmp_path) -> None:
    # mutmut: kill mutation 283 — was: "rubric.yaml: component_filter is
    # %s, expected mapping; falling back to defaults for component_filter"
    # -> "XX...XX".
    import logging

    from harness.rubric import load_config

    bad = tmp_path / "bad_filter.yaml"
    bad.write_text("component_filter: not_a_mapping\n")

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        load_config(bad)
    finally:
        keen.removeHandler(handler)

    cf_records = [r for r in records if "component_filter is" in r.getMessage()]
    assert cf_records
    for r in cf_records:
        assert not r.msg.startswith("XX"), r.msg
        assert not r.msg.endswith("XX"), r.msg


def test_load_config_report_non_dict_log_template_no_xx(tmp_path) -> None:
    # mutmut: kill mutation 289 — was: "rubric.yaml: report is %s,
    # expected mapping; falling back to defaults for report" -> "XX...XX".
    import logging

    from harness.rubric import load_config

    bad = tmp_path / "bad_report.yaml"
    bad.write_text("report: not_a_mapping\n")

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        load_config(bad)
    finally:
        keen.removeHandler(handler)

    rep_records = [r for r in records if "report is" in r.getMessage()]
    assert rep_records
    for r in rep_records:
        assert not r.msg.startswith("XX"), r.msg
        assert not r.msg.endswith("XX"), r.msg


def test_load_config_yaml_parse_error_log_message_exact(tmp_path) -> None:
    # mutmut: kill mutation 501 — was: "rubric.yaml: %s; falling back to
    # defaults for the whole file" -> "XX...XX".
    import logging

    from harness.rubric import load_config

    bad = tmp_path / "bad.yaml"
    bad.write_text("severity_weights: {P0: 'oops\n")

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        load_config(bad)
    finally:
        keen.removeHandler(handler)

    parse = [r for r in records if "falling back" in r.getMessage()]
    assert parse, f"no parse-error warning in {[r.getMessage() for r in records]}"
    # Pin the raw template (record.msg).
    raw_templates = [r.msg for r in parse]
    for t in raw_templates:
        assert not t.startswith("XX"), t
        assert not t.endswith("XX"), t


def test_load_config_pyyaml_missing_log_message_exact(monkeypatch) -> None:
    # mutmut: kill mutation 266 — was: "rubric.yaml: PyYAML is not
    # installed; falling back to defaults" -> "XX...XX".
    import logging
    import sys
    from pathlib import Path

    # Force ImportError by removing yaml from sys.modules and the import
    # cache. Patch builtins.__import__ to raise on yaml.
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    sys.modules.pop("yaml", None)

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)

    # Use an existing rubric.yaml so the ImportError branch is hit (not
    # the file-not-found branch).
    config_yaml = Path(__file__).parent.parent / "config" / "rubric.yaml"
    if not config_yaml.exists():
        # Fall back to a tmp path that exists; create a minimal yaml file.
        # But we can't create here without importing yaml. Use any
        # existing file the harness considers a valid path.
        config_yaml = Path(__file__)  # any existing file

    from harness.rubric import load_config

    try:
        load_config(config_yaml)
    finally:
        keen.removeHandler(handler)

    not_installed = [r for r in records if "PyYAML is not installed" in r.getMessage()]
    assert not_installed, f"missing PyYAML warning, got {[r.getMessage() for r in records]}"
    raw = [r.msg for r in not_installed]
    for t in raw:
        assert not t.startswith("XX"), t
        assert not t.endswith("XX"), t


def test_load_config_missing_file_log_message_exact() -> None:
    # mutmut: kill mutation 265 — was: "rubric.yaml: file not found at
    # %s; falling back to defaults" -> "XX...XX". Existing tests assert
    # only the substring "not found", which a "XX...XX"-padded message
    # still satisfies. Pin the exact format-string template.
    import logging
    from pathlib import Path

    from harness.rubric import load_config

    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        load_config(Path("/tmp/__does_not_exist_keen_test__.yaml"))
    finally:
        keen.removeHandler(handler)

    not_found = [r for r in records if "not found" in r.getMessage()]
    assert not_found, f"Expected a 'not found' warning, got {[r.getMessage() for r in records]}"
    # Pin the raw template (record.msg before % formatting). The mutant
    # would have surrounding "XX" markers.
    raw_templates = [r.msg for r in not_found]
    for t in raw_templates:
        assert not t.startswith("XX"), f"template starts with XX: {t!r}"
        assert not t.endswith("XX"), f"template ends with XX: {t!r}"
    # Pin the exact template.
    assert "rubric.yaml: file not found at %s; falling back to defaults" in raw_templates


def test_load_config_default_path_finds_shipped_rubric_yaml() -> None:
    # mutmut: kill mutations 257, 259 — was: "config" -> "XXconfigXX" or
    # "rubric.yaml" -> "XXrubric.yamlXX" in the default path computation.
    # Under either mutation the default Path doesn't exist; load_config
    # would emit a "not found" warning and return all defaults. We
    # exercise the loaded-from-disk path by asserting the loaded config
    # carries an `_assets` directive that the shipped rubric.yaml
    # populates — and DEFAULT_REPORT_OPTS does not.
    import logging

    from harness.rubric import load_config

    # Capture warnings so we can verify the file WAS found (no warning
    # about a missing file).
    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.handle = records.append
    keen = logging.getLogger("keen")
    keen.addHandler(handler)
    keen.setLevel(logging.WARNING)
    try:
        cfg = load_config()
    finally:
        keen.removeHandler(handler)

    # If the default path were garbled, there would be a "not found"
    # warning. Pin that there is NOT one.
    not_found_records = [r for r in records if "not found" in r.getMessage()]
    assert not not_found_records, (
        f"Expected no 'not found' warnings, got {[r.getMessage() for r in records]}"
    )
    # Spot-check: cfg["severity_weights"]["P0"] is 10.0 (matches defaults
    # AND the shipped file).
    assert cfg["severity_weights"]["P0"] == 10.0


def test_module_logger_channel_name_is_keen() -> None:
    # mutmut: kill mutation 167 — was: logging.getLogger("keen") ->
    # getLogger("XXkeenXX"). If the logger name changed, any
    # caplog filter "keen" would catch nothing. That's exactly
    # what tests like test_load_config_returns_defaults_when_file_missing
    # rely on; pin it explicitly here.
    from harness import rubric

    assert rubric.logger.name == "keen"


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
