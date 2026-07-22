"""Gap tests for harness/cli.py.

CLI module had 0% coverage. These exercise the pure helpers, the argument
parser, and a few subcommands that operate purely on the filesystem
(no playwright / browser involvement): audit, diff, tokens (image mode),
intent, doctor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from harness import cli

# --- Pure helpers --------------------------------------------------------


def test_csv_helper_splits_and_strips() -> None:
    assert cli._csv("a, b,c") == ["a", "b", "c"]
    assert cli._csv("") == []
    assert cli._csv(" , ,") == []


def test_default_outdir_includes_prefix() -> None:
    out = cli._default_outdir("review")
    assert out.parts[:2] == (".keen", "review")


def test_normalize_capture_target_converts_existing_local_file(tmp_path: Path) -> None:
    target = tmp_path / "screen.html"
    target.write_text("<!doctype html><title>Fixture</title>")

    assert cli._normalize_capture_target(str(target)) == target.resolve().as_uri()


def test_normalize_capture_target_preserves_urls_and_unknown_paths(tmp_path: Path) -> None:
    assert cli._normalize_capture_target("https://example.com/app") == "https://example.com/app"
    missing = tmp_path / "missing.html"
    assert cli._normalize_capture_target(str(missing)) == str(missing)


def test_validate_system_none_returns_none() -> None:
    assert cli.validate_system(None) is None


def test_validate_system_unknown_raises() -> None:
    with pytest.raises(SystemExit):
        cli.validate_system("definitely-not-a-system")


def test_list_available_systems_returns_strings() -> None:
    out = cli.list_available_systems()
    assert isinstance(out, list)
    # Each name corresponds to a *.md file stem.
    for n in out:
        assert isinstance(n, str)


def test_load_custom_system_tokens_missing_returns_none() -> None:
    assert cli.load_custom_system_tokens("definitely-not-a-system") is None


# --- build_parser --------------------------------------------------------


def test_build_parser_constructs_top_level() -> None:
    p = cli.build_parser()
    assert isinstance(p, argparse.ArgumentParser)


def test_parser_recognizes_subcommands() -> None:
    p = cli.build_parser()
    # diff is the cheapest subcommand to exercise.
    args = p.parse_args(["diff", "/tmp/a", "/tmp/b"])
    assert args.cmd == "diff"
    assert args.run_a == "/tmp/a"


# --- cmd_diff end-to-end -------------------------------------------------


def _write_run(base: Path, name: str, report: dict) -> Path:
    rd = base / name
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.json").write_text(json.dumps(report))
    return rd


def test_cmd_diff_writes_markdown(tmp_path: Path) -> None:
    rep = {"components": [], "score": {"score": 0, "grade": "A"}}
    a = _write_run(tmp_path, "a", rep)
    b = _write_run(tmp_path, "b", rep)
    out = tmp_path / "diff.md"
    rc = cli.main(["diff", str(a), str(b), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert "# Keen diff" in out.read_text()


def test_cmd_diff_missing_report_exits_2(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    with pytest.raises(SystemExit) as exc:
        cli.main(["diff", str(a), str(b)])
    assert exc.value.code == 2


# --- cmd_intent end-to-end -----------------------------------------------


def test_cmd_intent_run_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rep = {
        "captures": [],
        "annotated_overviews": {},
        "top_findings": [],
        "score": {"grade": "A"},
    }
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "report.json").write_text(json.dumps(rep))
    rc = cli.main(["intent", str(rd)])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["mode"] == "run-dir"
    assert "instruction" not in parsed
    assert parsed["evidence_contract"]["finding_key"] == "predicate_id"


def test_cmd_intent_missing_report_exits_2(tmp_path: Path) -> None:
    rd = tmp_path / "no-report"
    rd.mkdir()
    with pytest.raises(SystemExit) as exc:
        cli.main(["intent", str(rd)])
    assert exc.value.code == 2


def test_cmd_doctor_runs(capsys: pytest.CaptureFixture) -> None:
    """doctor exits with 0 or 1 depending on env — just ensure it runs."""
    try:
        rc = cli.main(["doctor"])
    except SystemExit as e:
        rc = e.code
    assert rc in (0, 1)


# --- cmd_audit / cmd_tokens / cmd_intent file-only end-to-end ------------


def test_cmd_audit_runs_pipeline_on_existing_captures(tmp_path: Path) -> None:
    """audit decomposes + analyzes + reports a pre-built captures dir."""
    captures = tmp_path / "captures"
    captures.mkdir()
    dom_dir = captures / "dom"
    dom_dir.mkdir()
    (dom_dir / "desktop-default.json").write_text(
        json.dumps(
            {
                "title": "T",
                "url": "https://example.com/",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/desktop-default.png",
                    "viewport_size": {"width": 1280, "deviceScaleFactor": 1},
                },
                "elements": [
                    {
                        "index": 0,
                        "tag": "button",
                        "role": "button",
                        "name": "Submit",
                        "text": "Submit",
                        "box": {"x": 100, "y": 100, "w": 60, "h": 60},
                        "styles": {
                            "color": "rgb(0,0,0)",
                            "backgroundColor": "rgb(255,255,255)",
                            "fontSize": "16px",
                            "fontWeight": "400",
                        },
                    }
                ],
                "focus_coverage": {"focus_visible_rules": 1, "total_focus_rules": 1},
            }
        )
    )
    rc = cli.main(["audit", str(captures)])
    assert rc == 0
    assert (captures / "report.json").exists()
    assert (captures / "summary.md").exists()


def test_cmd_audit_missing_target_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", str(tmp_path / "nope")])
    assert exc.value.code == 2


def test_cmd_intent_single_image_mode(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A .png target triggers single-image intent mode."""
    img = tmp_path / "shot.png"
    # Just an empty file with .png suffix — the CLI dispatch only checks the
    # path, not the magic bytes.
    img.write_bytes(b"")
    rc = cli.main(["intent", str(img)])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["mode"] == "single-image"
    assert parsed["evidence_scope"] == "visual-only"
    assert parsed["stable_ids"] is False
    assert "instruction" not in parsed
