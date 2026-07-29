"""Tests designed to kill mutmut survivors in harness/diff.py.

The existing test_diff_props.py covers set-arithmetic invariants (an
identical run produces no added/removed; etc.). test_diff_gaps.py covers
some markdown rendering corner cases. Mutmut still surfaces a long tail
of literal/boundary/key-name mutations that these don't address —
specifically: the joiner character in the dedup key, the truncation
boundaries on the [:50]/[:25] list slices, the emoji prefixes, and the
exact format-string interpolation order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness._artifacts import ArtifactIntegrityError
from harness.diff import diff_runs, render_markdown


def _write_run(base: Path, name: str, report: dict) -> Path:
    rd = base / name
    rd.mkdir(parents=True, exist_ok=True)
    normalized = dict(report)
    score = dict(normalized.get("score") or {})
    score.setdefault("score", 0)
    score.setdefault("grade", "A")
    normalized["score"] = score
    (rd / "report.json").write_text(json.dumps(normalized))
    return rd


# ---------------------------------------------------------------------------
# diff_runs — key-construction (predicate_id, kind, viewport, state, index)
# ---------------------------------------------------------------------------


def test_diff_runs_key_uses_predicate_id(tmp_path: Path) -> None:
    # Two findings with the same component context but different
    # predicate_ids must produce TWO entries in the same run (deduped
    # by key) and remain distinct across runs.
    report_a = {
        "components": [
            {
                "component_kind": "btn",
                "viewport": "desktop",
                "state": "default",
                "component_index": 0,
                "findings": [{"predicate_id": "a", "severity": "P0"}],
            }
        ],
        "score": {"score": 1, "grade": "A"},
    }
    report_b = {
        "components": [
            {
                "component_kind": "btn",
                "viewport": "desktop",
                "state": "default",
                "component_index": 0,
                "findings": [{"predicate_id": "b", "severity": "P0"}],
            }
        ],
        "score": {"score": 1, "grade": "A"},
    }
    a_dir = _write_run(tmp_path, "a", report_a)
    b_dir = _write_run(tmp_path, "b", report_b)
    result = diff_runs(a_dir, b_dir)
    # If the key didn't include predicate_id, the two findings would
    # dedup to one and result["added"] / ["removed"] would be empty.
    assert len(result["added"]) == 1
    assert len(result["removed"]) == 1


def test_diff_runs_key_uses_component_kind(tmp_path: Path) -> None:
    # Same predicate_id but different component_kind → distinct entries.
    report_a = {
        "components": [
            {
                "component_kind": "btn",
                "viewport": "v",
                "state": "s",
                "component_index": 0,
                "findings": [{"predicate_id": "p", "severity": "P0"}],
            }
        ],
        "score": {"score": 1},
    }
    report_b = {
        "components": [
            {
                "component_kind": "link",
                "viewport": "v",
                "state": "s",
                "component_index": 0,
                "findings": [{"predicate_id": "p", "severity": "P0"}],
            }
        ],
        "score": {"score": 1},
    }
    a_dir = _write_run(tmp_path, "a", report_a)
    b_dir = _write_run(tmp_path, "b", report_b)
    result = diff_runs(a_dir, b_dir)
    assert len(result["added"]) == 1
    assert len(result["removed"]) == 1


def test_diff_runs_key_uses_viewport(tmp_path: Path) -> None:
    report_a = {
        "components": [
            {
                "component_kind": "btn",
                "viewport": "mobile",
                "state": "default",
                "component_index": 0,
                "findings": [{"predicate_id": "p", "severity": "P0"}],
            }
        ],
        "score": {},
    }
    report_b = {
        "components": [
            {
                "component_kind": "btn",
                "viewport": "desktop",
                "state": "default",
                "component_index": 0,
                "findings": [{"predicate_id": "p", "severity": "P0"}],
            }
        ],
        "score": {},
    }
    a_dir = _write_run(tmp_path, "a", report_a)
    b_dir = _write_run(tmp_path, "b", report_b)
    result = diff_runs(a_dir, b_dir)
    assert len(result["added"]) == 1
    assert len(result["removed"]) == 1


def test_diff_runs_key_uses_state(tmp_path: Path) -> None:
    base = {
        "component_kind": "btn",
        "viewport": "v",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [{**base, "state": "default"}], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [{**base, "state": "hover"}], "score": {}})
    result = diff_runs(a_dir, b_dir)
    assert len(result["added"]) == 1
    assert len(result["removed"]) == 1


def test_diff_runs_key_uses_component_index(tmp_path: Path) -> None:
    base = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [{**base, "component_index": 0}], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [{**base, "component_index": 1}], "score": {}})
    result = diff_runs(a_dir, b_dir)
    assert len(result["added"]) == 1
    assert len(result["removed"]) == 1


def test_diff_runs_finding_record_includes_viewport_key(tmp_path: Path) -> None:
    # mutmut: kill mutations 342, 343 — was: "viewport": c.get("viewport")
    # -> "XXviewportXX": ... or -> "viewport": c.get("XXviewportXX").
    # In either form the resulting dict either has the wrong key
    # "XXviewportXX" or has "viewport" = None (because c.get("XXviewportXX")
    # returns None even when "viewport" is present).
    component = {
        "component_kind": "btn",
        "viewport": "mobile",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [component], "score": {}})
    result = diff_runs(a_dir, b_dir)
    entry = result["unchanged"][0]
    assert "viewport" in entry
    assert "XXviewportXX" not in entry
    assert entry["viewport"] == "mobile"


def test_diff_runs_finding_record_includes_state_key(tmp_path: Path) -> None:
    # mutmut: kill mutation 344 — was: "state": ... -> "XXstateXX": ...
    component = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "hover",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [component], "score": {}})
    result = diff_runs(a_dir, b_dir)
    entry = result["unchanged"][0]
    assert "state" in entry
    assert "XXstateXX" not in entry
    assert entry["state"] == "hover"


def test_diff_runs_finding_record_includes_component_kind_key(tmp_path: Path) -> None:
    # mutmut: kill mutations 482, 483 — was: "component_kind" key/value
    # garbled.
    component = {
        "component_kind": "link",
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [component], "score": {}})
    result = diff_runs(a_dir, b_dir)
    entry = result["unchanged"][0]
    assert "component_kind" in entry
    assert "XXcomponent_kindXX" not in entry
    assert entry["component_kind"] == "link"


def test_diff_runs_includes_message_in_unchanged(tmp_path: Path) -> None:
    # The internal record includes a "message" field copied from the
    # finding. A key-name mutation like f.get("message") -> f.get("XXmessageXX")
    # would null out the message.
    component = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0", "message": "exact"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [component], "score": {}})
    result = diff_runs(a_dir, b_dir)
    assert result["unchanged"][0]["message"] == "exact"


def test_diff_runs_includes_crop_path_from_component(tmp_path: Path) -> None:
    # crop_path comes from the COMPONENT, not the finding.
    component = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "crop_path": "crops/0.png",
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [{**component, "findings": []}], "score": {}})
    result = diff_runs(a_dir, b_dir)
    # The finding was removed in b -> ends up in `removed`.
    assert result["removed"][0]["crop_path"] == "crops/0.png"


def test_diff_runs_setdefault_dedupes_same_key(tmp_path: Path) -> None:
    # Two findings with IDENTICAL keys collapse to one — first one wins.
    # If the setdefault were swapped with assignment, the *last* would win.
    component = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "findings": [
            {"predicate_id": "p", "severity": "P0", "message": "first"},
            {"predicate_id": "p", "severity": "P1", "message": "second"},
        ],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [component], "score": {}})
    result = diff_runs(a_dir, b_dir)
    # Only one entry — setdefault keeps the FIRST.
    assert len(result["unchanged"]) == 1
    assert result["unchanged"][0]["message"] == "first"
    assert result["unchanged"][0]["severity"] == "P0"


def test_diff_runs_uses_pipe_joiner_for_key(tmp_path: Path) -> None:
    # The pipe character "|" was chosen because it doesn't appear in
    # component_kind/viewport/state/predicate_id under normal use. Pin
    # this by using fields that *would* collide if a different separator
    # like "" or "-" were chosen.
    # With original joiner "|", these two findings have distinct keys.
    # With joiner "" they would collide because their concatenations
    # match: "ab" + "" + "cd" vs "abc" + "" + "d".
    component_a = {
        "component_kind": "ab",
        "viewport": "cd",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    component_b = {
        "component_kind": "abc",
        "viewport": "d",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(
        tmp_path,
        "a",
        {"components": [component_a, component_b], "score": {}},
    )
    # Same in B — confirms both keys survive as distinct (count = 2).
    b_dir = _write_run(
        tmp_path,
        "b",
        {"components": [component_a, component_b], "score": {}},
    )
    result = diff_runs(a_dir, b_dir)
    assert len(result["unchanged"]) == 2


def test_diff_runs_rejects_missing_predicate_id(tmp_path: Path) -> None:
    component = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "findings": [{"severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [component], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    with pytest.raises(ArtifactIntegrityError, match="predicate_id"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_rejects_missing_component_kind(tmp_path: Path) -> None:
    no_kind = {
        "viewport": "v",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [no_kind], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    with pytest.raises(ArtifactIntegrityError, match="component_kind"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_rejects_missing_viewport(tmp_path: Path) -> None:
    no_vp = {
        "component_kind": "btn",
        "state": "s",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [no_vp], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    with pytest.raises(ArtifactIntegrityError, match="viewport"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_rejects_missing_state(tmp_path: Path) -> None:
    no_state = {
        "component_kind": "btn",
        "viewport": "v",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [no_state], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    with pytest.raises(ArtifactIntegrityError, match="state"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_rejects_missing_component_index(tmp_path: Path) -> None:
    no_idx = {
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(tmp_path, "a", {"components": [no_idx], "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    with pytest.raises(ArtifactIntegrityError, match="index"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_key_joiner_is_literal_pipe_not_double_X(tmp_path: Path) -> None:
    # mutmut: kill mutation 325 — was: "|".join(...) -> "XX|XX".join(...).
    # The joiner doesn't normally surface to the user. Construct two
    # components whose key-tokens DIFFER but produce a colliding final
    # key string under the mutated joiner.
    # Under the original joiner "|":
    #   A: kind="a", vp="bXX|XXc", state="", idx=0 → "p|a|bXX|XXc||0"
    #   B: kind="aXX|XXb", vp="c", state="", idx=0 → "p|aXX|XXb|c||0"
    #   These are DIFFERENT strings.
    # Under the mutated joiner "XX|XX":
    #   A: "pXX|XXaXX|XXbXX|XXcXX|XXXX|XX0"
    #   B: "pXX|XXaXX|XXbXX|XXcXX|XXXX|XX0"
    #   They COLLIDE.
    # So under original we get 2 unchanged entries; under mutant, 1.
    component_a = {
        "component_kind": "a",
        "viewport": "bXX|XXc",
        "state": "",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    component_b = {
        "component_kind": "aXX|XXb",
        "viewport": "c",
        "state": "",
        "component_index": 0,
        "findings": [{"predicate_id": "p", "severity": "P0"}],
    }
    a_dir = _write_run(
        tmp_path,
        "a",
        {"components": [component_a, component_b], "score": {}},
    )
    b_dir = _write_run(
        tmp_path,
        "b",
        {"components": [component_a, component_b], "score": {}},
    )
    result = diff_runs(a_dir, b_dir)
    # Under "|" joiner: both keys distinct → 2 unchanged entries.
    # Under "XX|XX" joiner: both keys collide → 1 unchanged entry.
    assert len(result["unchanged"]) == 2


def test_diff_runs_sorted_output_is_stable(tmp_path: Path) -> None:
    # Both sorted() calls ensure deterministic output; pin a small
    # ordering to detect a mutation that drops the sort or reverses it.
    comps = []
    for pid in ["c", "a", "b"]:
        comps.append(
            {
                "component_kind": "btn",
                "viewport": "v",
                "state": "s",
                "component_index": 0,
                "findings": [{"predicate_id": pid, "severity": "P0"}],
            }
        )
    a_dir = _write_run(tmp_path, "a", {"components": comps, "score": {}})
    b_dir = _write_run(tmp_path, "b", {"components": [], "score": {}})
    result = diff_runs(a_dir, b_dir)
    pids = [f["predicate_id"] for f in result["removed"]]
    # The key is "<pid>|btn|v|s|0", so sort is by pid. Expect a,b,c.
    assert pids == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# render_markdown — emoji and exact format strings
# ---------------------------------------------------------------------------


def test_render_markdown_fixed_in_b_heading_no_xx_padding() -> None:
    # mutmut: kill mutation 412 — was: f"## Fixed in B ({...})" -> with
    # "XX...XX" padding wrapping the entire heading. `"## Fixed in B" in md`
    # still holds under the mutation. To kill, assert one line of the
    # output is EXACTLY "## Fixed in B (N)" for some N.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [_mk_finding("p")],
        "unchanged": [],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    assert any(line == "## Fixed in B (1)" for line in lines), lines


def test_render_markdown_new_in_b_heading_no_xx_padding() -> None:
    # Same approach for the "New in B" heading.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding("p")],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    assert any(line == "## New in B (1)" for line in lines), lines


def test_render_markdown_still_present_heading_no_xx_padding() -> None:
    # Same approach for the "Still present" heading.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [_mk_finding("p")],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    assert any(line == "## Still present (1)" for line in lines), lines


def test_render_markdown_blank_line_after_heading() -> None:
    # mutmut: kill mutation 376 — was: lines.append("") -> lines.append("XXXX").
    # The output should have a blank line immediately after the title.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    assert lines[0] == "# Keen diff"
    assert lines[1] == ""  # empty blank line, not "XXXX"
    assert "XXXX" not in md


def test_render_markdown_score_a_score_key_is_score_not_xxscorex() -> None:
    # mutmut: kill mutation 394 — was: sa.get('score', 0) ->
    # sa.get('XXscoreXX', 0). The mutant ignores the actual score key
    # and always reads the default 0. With score_a = {"score": 7}, the
    # original prints "7" on the left of the arrow; mutant prints "0".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 7, "grade": "B"},
        "score_b": {"score": 7, "grade": "B"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The damage line must display "7 → 7", not "0 → 7".
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    assert "7 " in damage  # left side is 7
    # Pin: must not show 0 on the left.
    assert " 0 → " not in damage and " 0  " not in damage


def test_render_markdown_score_b_score_key_is_score_not_xxscorex() -> None:
    # mutmut: kill mutation 396 — same as above for sb.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 9, "grade": "B"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # Right side after the arrow must be 9, not 0.
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    # The right-side value is "9".
    assert "→ 9" in damage or "↗ 9" in damage or "↘ 9" in damage
    assert "→ 0" not in damage and "↗ 0" not in damage and "↘ 0" not in damage


def test_render_markdown_damage_format_string_intact() -> None:
    # mutmut: kill mutation 398 — was: f"**Damage:** ..." -> garbled.
    # Pin the exact "**Damage:**" prefix.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "**Damage:**" in md
    # Pin that the literal text isn't padded with "XX...XX".
    assert "XX**Damage:**" not in md
    assert "XX(" not in md


def test_render_markdown_decrease_uses_down_right_arrow() -> None:
    # mutmut: kill mutation 485 — was: "↘" -> "XX↘XX".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 10, "grade": "C"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    assert "↘" in damage
    assert "XX↘XX" not in md


def test_render_markdown_increase_uses_up_right_arrow() -> None:
    # mutmut: kill mutation 488 — was: "↗" -> "XX↗XX".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 10, "grade": "C"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    assert "↗" in damage
    assert "XX↗XX" not in md


def test_render_markdown_arrow_uses_strict_greater_than_zero() -> None:
    # mutmut: kill mutation 490 — was: `delta > 0` -> `delta > 1`. With
    # damage going up by exactly 1, original picks ↗; mutant picks → (the
    # "no change" arrow).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 6, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    # delta = 6 - 5 = 1; original: "↗" (because 1 > 0); mutant: "→"
    # (because 1 is not > 1).
    assert "↗" in damage
    assert "→" not in damage


def test_render_markdown_missing_score_a_defaults_to_zero() -> None:
    # mutmut: kill mutation 381 — was: sa.get("score", 0) ->
    # sa.get("score", 1). When score_a has no "score" key, the damage
    # computation must treat the missing value as 0, not 1.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"grade": "A"},  # no "score" key
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # delta = 0 - 0 = 0 -> arrow "→" and damage delta "+0.0".
    assert "+0.0" in md
    # The leading damage number must be 0 (the default), not 1.
    damage = md.split("**Damage:**", 1)[1].strip()
    assert damage.startswith("0 ")


def test_render_markdown_missing_score_b_defaults_to_zero() -> None:
    # mutmut: kill mutation 384 — was: sb.get("score", 0) ->
    # sb.get("score", 1).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"grade": "A"},  # no "score" key
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # Both default to 0 -> delta=0 -> "+0.0".
    assert "+0.0" in md
    # Format the damage line and pin: "0 → 0" with the integer 0 on
    # the right-hand side of the arrow.
    damage = md.split("**Damage:**", 1)[1].split("(", 1)[0]
    # Should be "0 → 0" — under the mutant default of 1, would be "0 → 1".
    assert "→ 0" in damage
    assert "→ 1" not in damage


def test_render_markdown_includes_keen_diff_heading() -> None:
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert md.startswith("# Keen diff\n")


def test_render_markdown_uses_check_emoji_for_removed() -> None:
    # The "Fixed in B" section uses U+2705 (✅).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 3, "grade": "A"},
        "added": [],
        "removed": [
            {
                "severity": "P0",
                "predicate_id": "p",
                "component_kind": "btn",
                "viewport": "v",
                "state": "s",
                "message": "msg",
            }
        ],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "✅" in md


def test_render_markdown_uses_red_circle_emoji_for_added() -> None:
    # The "New in B" section uses U+1F534 (🔴).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 3, "grade": "A"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [
            {
                "severity": "P0",
                "predicate_id": "p",
                "component_kind": "btn",
                "viewport": "v",
                "state": "s",
                "message": "msg",
            }
        ],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "🔴" in md


def test_render_markdown_uses_pause_emoji_for_unchanged() -> None:
    # The "Still present" section uses U+23F8 (⏸).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [
            {
                "severity": "P0",
                "predicate_id": "p",
                "component_kind": "btn",
                "viewport": "v",
                "state": "s",
                "message": "msg",
            }
        ],
    }
    md = render_markdown(result)
    assert "⏸" in md


# ---------------------------------------------------------------------------
# render_markdown — truncation boundaries
# ---------------------------------------------------------------------------


def _mk_finding(pid: str) -> dict:
    return {
        "severity": "P0",
        "predicate_id": pid,
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "message": "x",
    }


def test_render_markdown_removed_slice_exactly_50() -> None:
    # mutmut: kill mutation 415 — was: result["removed"][:50] ->
    # result["removed"][:51]. With 51 findings, original shows 50; mutant
    # shows 51 (and would NOT show the "1 more" line because the slice
    # captured all).
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [_mk_finding(f"p{i}") for i in range(51)],
        "unchanged": [],
    }
    md = render_markdown(result)
    # Under original ([:50]): output has 50 emoji lines + 1 "_… and 1
    # more_" line.
    # Under mutant ([:51]): output has 51 emoji lines + still 1 "more"
    # line (because the post-loop count check is independent).
    # The distinguishing test: count ✅ occurrences in output.
    assert md.count("✅") == 50


def test_render_markdown_added_slice_exactly_50() -> None:
    # mutmut: kill mutation 433 — was: result["added"][:50] ->
    # result["added"][:51].
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(51)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert md.count("🔴") == 50


def test_render_markdown_removed_line_starts_with_dash_check() -> None:
    # mutmut: kill mutation 418 — was: "- ✅ ..." -> "XX- ✅ ...XX".
    # Each removed-list line must start with literal "- ✅".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [_mk_finding("p")],
        "unchanged": [],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    # Find the line with ✅; it should start with "-".
    check_lines = [line for line in lines if "✅" in line]
    assert check_lines
    for line in check_lines:
        assert line.startswith("- ✅"), f"Line should start with '- ✅': {line!r}"


def test_render_markdown_removed_line_format_no_padding() -> None:
    # mutmut: kill mutation 423 — was: "({kind}, {vp}/{state}) — {msg}"
    # -> "XX(...)XX". The continuation string must NOT have leading XX.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [_mk_finding("p")],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The full removed-line should have "✅" then a backtick predicate
    # then "(btn, v/s) — x". The mutant adds XX prefix/suffix.
    check_lines = [line for line in md.split("\n") if "✅" in line]
    assert check_lines
    for line in check_lines:
        assert "XX" not in line, f"Line should not contain XX: {line!r}"
        # Format pin: must end with the message text "x"
        assert line.endswith("x"), f"Line should end with 'x': {line!r}"


def test_render_markdown_added_line_starts_with_dash_redcircle() -> None:
    # mutmut: kill mutation 441 — was: "- 🔴 ..." -> "XX- 🔴 ...XX".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding("p")],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    lines = md.split("\n")
    check_lines = [line for line in lines if "🔴" in line]
    assert check_lines
    for line in check_lines:
        assert line.startswith("- 🔴"), f"Line should start with '- 🔴': {line!r}"


def test_render_markdown_added_line_continuation_no_padding() -> None:
    # mutmut: kill mutation 446 — was: continuation string padded with XX.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding("p")],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    check_lines = [line for line in md.split("\n") if "🔴" in line]
    assert check_lines
    for line in check_lines:
        assert "XX" not in line, line
        assert line.endswith("x"), line  # message text


def test_render_markdown_crop_path_format_when_present() -> None:
    # mutmut: kill mutation 435 — was: f" `{crop_path}`" -> f"XX `{crop_path}`XX".
    f_with_crop = {**_mk_finding("p"), "crop_path": "crops/x.png"}
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [f_with_crop],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The crop should appear as a single space then backtick path then backtick.
    assert " `crops/x.png`" in md
    assert "XX `crops/x.png`XX" not in md


def test_render_markdown_crop_path_empty_string_when_missing() -> None:
    # mutmut: kill mutation 437 — was: else "" -> else "XXXX". With no
    # crop_path, the f-string interpolation yields "" exactly, so the
    # resulting line has NO crop chunk between the bold predicate and the
    # subsequent paren-group.
    f_without_crop = _mk_finding("p")  # no crop_path key
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [f_without_crop],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The line should NOT contain "XXXX" in the crop position.
    check_lines = [line for line in md.split("\n") if "🔴" in line]
    assert check_lines
    for line in check_lines:
        # The crop is empty, so the line's structure is
        # "- 🔴 **[P0]** `p` (btn, v/s) — x"  (no extra backtick path).
        assert "XXXX" not in line


def test_render_markdown_added_truncation_boundary_at_50() -> None:
    # mutmut: kill mutation 510 — was: `len(result["added"]) > 50` ->
    # `>= 50`. With exactly 50 entries, original does NOT add the "more"
    # line; mutant does.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(50)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "_… and" not in md  # original: no "more" line at exactly 50


def test_render_markdown_added_truncation_strict_greater_than_50() -> None:
    # mutmut: kill mutation 511 — was: `> 50` -> `> 51`. With 51 entries,
    # original adds "more" line; mutant does not.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(51)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "and 1 more" in md  # original: 51 - 50 = 1 more


def test_render_markdown_added_more_count_uses_minus_not_plus() -> None:
    # mutmut: kill mutation 451 — was: `len(result['added']) - 50` ->
    # `len(result['added']) + 50`. With 51 entries, original prints "1
    # more"; mutant prints "101 more".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(60)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # Original: 60 - 50 = 10 more.
    # Mutant: 60 + 50 = 110 more.
    assert "and 10 more" in md
    assert "and 110 more" not in md


def test_render_markdown_added_more_line_format_no_padding() -> None:
    # mutmut: kill mutation 453 — was: "- _… and N more_" -> "XX...XX".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(75)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The more-line should appear as a list bullet "- _..._" with no XX.
    more_lines = [line for line in md.split("\n") if "more" in line]
    assert more_lines
    for line in more_lines:
        assert "XX" not in line
        assert line.startswith("- _… and")


def test_render_markdown_clean_run_message_no_padding() -> None:
    # mutmut: kill mutation 460 — was: "_(none — clean run)_" -> "XX...XX".
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "_(none — clean run)_" in md
    assert "XX_(none — clean run)_XX" not in md


def test_render_markdown_blank_line_separators_between_sections() -> None:
    # mutmut: kill mutations 500, 427, 431 — was: `lines.append("")` ->
    # `lines.append("XXXX")`. Each section should be separated by exactly
    # one blank line; the mutant would put garbage there.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding("a")],
        "removed": [_mk_finding("r")],
        "unchanged": [_mk_finding("u")],
    }
    md = render_markdown(result)
    # Pin no "XXXX" sentinel anywhere.
    assert "XXXX" not in md


def test_render_markdown_truncates_removed_at_50() -> None:
    # 50 findings → all shown, no "… and N more". 51 findings → 50 shown,
    # ellipsis line appears with "1 more".
    result_50 = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [_mk_finding(f"p{i}") for i in range(50)],
        "unchanged": [],
    }
    md_50 = render_markdown(result_50)
    assert "_… and" not in md_50  # at 50 there is no overflow

    result_51 = {**result_50, "removed": [_mk_finding(f"p{i}") for i in range(51)]}
    md_51 = render_markdown(result_51)
    assert "1 more" in md_51


def test_render_markdown_truncates_added_at_50() -> None:
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [_mk_finding(f"p{i}") for i in range(75)],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "25 more" in md


def test_render_markdown_truncates_unchanged_at_25() -> None:
    # Unchanged list cap is 25, not 50.
    result_25 = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [_mk_finding(f"p{i}") for i in range(25)],
    }
    md_25 = render_markdown(result_25)
    assert "_… and" not in md_25

    result_30 = {**result_25, "unchanged": [_mk_finding(f"p{i}") for i in range(30)]}
    md_30 = render_markdown(result_30)
    assert "5 more" in md_30


def test_render_markdown_delta_format_has_sign_and_one_decimal() -> None:
    # The damage delta is formatted with `:+.1f` — always carries a sign,
    # always shows exactly one decimal. Pin both.
    result_neg = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 20, "grade": "C"},
        "score_b": {"score": 5, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md_neg = render_markdown(result_neg)
    assert "-15.0" in md_neg

    result_pos = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 5, "grade": "A"},
        "score_b": {"score": 20, "grade": "C"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md_pos = render_markdown(result_pos)
    assert "+15.0" in md_pos


def test_render_markdown_crop_path_appears_in_added_only_when_present() -> None:
    # The "New in B" item conditionally renders crop_path; verify both
    # branches.
    with_crop = {
        "severity": "P0",
        "predicate_id": "p",
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "message": "m",
        "crop_path": "crops/x.png",
    }
    without_crop = {
        "severity": "P0",
        "predicate_id": "p",
        "component_kind": "btn",
        "viewport": "v",
        "state": "s",
        "message": "m",
    }
    result_with = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [with_crop],
        "removed": [],
        "unchanged": [],
    }
    md_with = render_markdown(result_with)
    assert "crops/x.png" in md_with

    result_without = {**result_with, "added": [without_crop]}
    md_without = render_markdown(result_without)
    assert "crops/" not in md_without


# ---------------------------------------------------------------------------
# render_markdown — empty-state messages
# ---------------------------------------------------------------------------


def test_render_markdown_empty_unchanged_shows_clean_run_message() -> None:
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "_(none — clean run)_" in md
    # The "Still present" heading still appears with count 0.
    assert "## Still present (0)" in md


def test_render_markdown_missing_grade_defaults_to_question_mark() -> None:
    # `sa.get('grade', '?')` default; pin by omitting grade.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0},  # no grade
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert "Grade ? → A" in md


def test_render_markdown_runs_a_and_b_appear_in_output() -> None:
    # mutmut: kill mutations 406, 408 — was: f"**A:** `{result['run_a']}`"
    # -> "XX...XX" padding. Pin that the line starts exactly with the
    # "**A:**" markdown bold.
    result = {
        "run_a": "/path/to/a",
        "run_b": "/path/to/b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # Each run-name line must appear on its OWN line, prefixed with
    # the bold marker — no "XX" padding.
    assert "**A:** `/path/to/a`" in md
    assert "**B:** `/path/to/b`" in md
    # Pin the line position: search splittable lines for the exact text.
    lines = md.split("\n")
    assert any(line == "**A:** `/path/to/a`" for line in lines), (
        f"no line exactly matches '**A:** `/path/to/a`'; got {lines}"
    )
    assert any(line == "**B:** `/path/to/b`" for line in lines), (
        f"no line exactly matches '**B:** `/path/to/b`'; got {lines}"
    )


def test_render_markdown_score_b_missing_grade_defaults_to_question_mark() -> None:
    # mutmut: kill mutation 496 — was: sb.get('grade', '?') ->
    # sb.get('grade', 'XX?XX').
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0},  # no grade
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    # The grade line should read "Grade A → ?", NOT "Grade A → XX?XX".
    assert "Grade A → ?" in md
    assert "XX?XX" not in md


def test_render_markdown_arrow_only_one_of_three() -> None:
    # Confirm the score arrow position uses the right glyph for the
    # damage-delta sign. Note the grade-transition uses a literal "→"
    # in all branches, so we only check the SCORE side here by looking
    # at the chars immediately around the damage-number.
    cases = [
        ({"score": 5}, {"score": 5}, "→"),
        ({"score": 10}, {"score": 5}, "↘"),
        ({"score": 5}, {"score": 10}, "↗"),
    ]
    for sa, sb, expected in cases:
        result = {
            "run_a": "a",
            "run_b": "b",
            "score_a": {**sa, "grade": "A"},
            "score_b": {**sb, "grade": "A"},
            "added": [],
            "removed": [],
            "unchanged": [],
        }
        md = render_markdown(result)
        # The damage header always reads "Damage: <sa> <ARROW> <sb>" —
        # extract by string slicing for clarity.
        # Find the substring after "Damage:** " and before "(".
        damage_part = md.split("**Damage:**", 1)[1].split("(", 1)[0]
        assert expected in damage_part
        # The other two arrow glyphs must NOT appear in the damage_part.
        other_arrows = {"↘", "↗", "→"} - {expected}
        for ar in other_arrows:
            assert ar not in damage_part, f"unexpected arrow {ar} in damage part {damage_part!r}"


def test_render_markdown_trailing_newline() -> None:
    # The function returns "\n".join(lines) + "\n" — pin the trailing
    # newline so an editor or render mutmut mutation doesn't drop it.
    result = {
        "run_a": "a",
        "run_b": "b",
        "score_a": {"score": 0, "grade": "A"},
        "score_b": {"score": 0, "grade": "A"},
        "added": [],
        "removed": [],
        "unchanged": [],
    }
    md = render_markdown(result)
    assert md.endswith("\n")


# ---------------------------------------------------------------------------
# diff_runs — score fields
# ---------------------------------------------------------------------------


def test_diff_runs_loads_score_a_and_score_b_separately(tmp_path: Path) -> None:
    rep_a = {"components": [], "score": {"score": 10, "grade": "C"}}
    rep_b = {"components": [], "score": {"score": 3, "grade": "A"}}
    a_dir = _write_run(tmp_path, "a", rep_a)
    b_dir = _write_run(tmp_path, "b", rep_b)
    result = diff_runs(a_dir, b_dir)
    assert result["score_a"] == {"score": 10, "grade": "C"}
    assert result["score_b"] == {"score": 3, "grade": "A"}


def test_diff_runs_rejects_missing_score(tmp_path: Path) -> None:
    rep = {"components": []}
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "report.json").write_text(json.dumps(rep))
    (b_dir / "report.json").write_text(json.dumps(rep))
    with pytest.raises(ArtifactIntegrityError, match="score"):
        diff_runs(a_dir, b_dir)


def test_diff_runs_run_a_run_b_strings_are_paths(tmp_path: Path) -> None:
    rep = {"components": [], "score": {}}
    a_dir = _write_run(tmp_path, "left", rep)
    b_dir = _write_run(tmp_path, "right", rep)
    result = diff_runs(a_dir, b_dir)
    assert result["run_a"] == str(a_dir)
    assert result["run_b"] == str(b_dir)
