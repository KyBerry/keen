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


def test_capture_parser_exposes_integrity_and_interactive_auth_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "capture",
            "https://example.com/poc",
            "--expect-url",
            "https://example.com/poc",
            "--expect-selector",
            "[data-poc-ready]",
            "--interactive-auth",
        ]
    )

    assert args.expect_url == "https://example.com/poc"
    assert args.wait_selector == "[data-poc-ready]"
    assert args.interactive_auth is True


def test_capture_parser_keeps_wait_selector_as_compatibility_alias() -> None:
    args = cli.build_parser().parse_args(
        ["capture", "https://example.com", "--wait-selector", "main"]
    )
    assert args.wait_selector == "main"


def test_capture_parser_auth_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "capture",
                "https://example.com",
                "--interactive-auth",
                "--auth-steps",
                "auth.json",
            ]
        )


def test_interactive_auth_refuses_unattended_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.capture_mod, "validate_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.capture_mod, "_interactive_terminal_available", lambda: False)

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "capture",
                "https://example.com/account",
                "--interactive-auth",
                "--viewports",
                "desktop",
                "--out",
                str(tmp_path / "capture"),
            ]
        )

    assert exc.value.code == 1


def test_invalid_overwrite_preflight_preserves_previous_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "review"
    out.mkdir()
    old_report = out / "report.json"
    old_report.write_text('{"previous": true}')
    monkeypatch.setattr(
        cli.capture_mod,
        "validate_target",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(SystemExit):
        cli.main(
            [
                "capture",
                "https://example.com",
                "--expect-url",
                "/not-absolute",
                "--out",
                str(out),
                "--overwrite",
            ]
        )

    assert old_report.read_text() == '{"previous": true}'


def test_context_lifecycle_commands_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    project = tmp_path / "northstar"
    project.mkdir()
    assert cli.main(["context", "init", str(project), "--name", "Northstar"]) == 0
    context = project / ".keen" / "design-context.json"
    direction = project / ".keen" / "direction.md"
    assert context.exists()
    assert direction.exists()

    assert cli.main(["context", "validate", str(project)]) == 0
    assert "[PASS]" in capsys.readouterr().out
    assert cli.main(["context", "show", str(project)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["project"]["name"] == "Northstar"

    payload = json.loads(context.read_text())
    payload["direction"]["qualities"] = ["precise", "warm"]
    context.write_text(json.dumps(payload))
    assert cli.main(["context", "render", str(project)]) == 0
    assert "precise" in direction.read_text()


def test_context_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    assert cli.main(["context", "init", str(tmp_path), "--name", "One"]) == 0
    with pytest.raises(SystemExit) as exc:
        cli.main(["context", "init", str(tmp_path), "--name", "Two"])
    assert exc.value.code == 2
    assert cli.main(["context", "init", str(tmp_path), "--name", "Two", "--force"]) == 0
    payload = json.loads((tmp_path / ".keen" / "design-context.json").read_text())
    assert payload["project"]["name"] == "Two"


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
        "components": [],
        "score": {"grade": "A", "score": 0},
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


def test_cmd_intent_accepts_reviewable_manual_visual_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    report = {
        "review_status": {"status": "provisional", "reviewable": True},
        "coverage": {
            "status": "provisional",
            "reviewable": True,
            "reason": "no-analyzable-content",
        },
        "captures": [
            {
                "viewport": "desktop",
                "state": "default",
                "screen_path": "screens/canvas.png",
                "manual_review_needed": True,
            }
        ],
        "annotated_overviews": {},
        "top_findings": [],
        "components": [],
        "score": {"grade": "INCOMPLETE", "score": None},
    }
    run = tmp_path / "canvas-run"
    run.mkdir()
    (run / "report.json").write_text(json.dumps(report))

    assert cli.main(["intent", str(run)]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["grade"] == "INCOMPLETE"
    assert parsed["captures"][0]["manual_review_needed"] is True
    assert parsed["captures"][0]["screen_path"] == "screens/canvas.png"


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


def test_cmd_audit_refuses_diagnostic_only_capture(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "screens").mkdir()
    (captures / "dom").mkdir()
    (captures / "screens" / "poc-desktop-default.png").write_bytes(b"diagnostic")
    (captures / "dom" / "poc-desktop-default.json").write_text(
        json.dumps(
            {
                "meta": {
                    "screen_path": "screens/poc-desktop-default.png",
                    "viewport": "desktop",
                    "state": "default",
                },
                "coverage": {
                    "expectation": {
                        "status": "blocked",
                        "reason_code": "unexpected-url",
                    }
                },
            }
        )
    )
    (captures / "capture-manifest.json").write_text(
        json.dumps(
            {
                "target": "https://example.com/poc",
                "requested": [{"viewport": "desktop", "state": "default"}],
                "succeeded": [],
                "failures": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "reason_code": "unexpected-url",
                        "reason": "redirected to sign-in",
                        "screen": "screens/poc-desktop-default.png",
                        "dom": "dom/poc-desktop-default.json",
                        "diagnostic_only": True,
                    }
                ],
                "complete": False,
                "reviewable": False,
                "status": "blocked",
            }
        )
    )

    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", str(captures)])

    assert exc.value.code == 1
    assert not (captures / "report.json").exists()
    brief = json.loads((captures / "agent-brief.json").read_text())
    assert brief["review_status"]["status"] == "blocked"
    assert brief["grade"] is None


@pytest.mark.parametrize("manifest_text", ["{broken", "[]"])
def test_cmd_audit_fails_closed_on_invalid_capture_manifest(
    tmp_path: Path,
    manifest_text: str,
) -> None:
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "capture-manifest.json").write_text(manifest_text)

    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", str(captures)])

    assert exc.value.code == 1
    brief = json.loads((captures / "agent-brief.json").read_text())
    assert brief["review_status"]["status"] == "failed"
    assert brief["review_status"]["reviewable"] is False


@pytest.mark.parametrize(
    "command",
    [
        ["tokens", "{run}"],
        ["systemize", "{run}"],
        ["intent", "{run}"],
        ["taste", "{run}"],
        ["slop", "{run}"],
        ["diff", "{run}", "{run}"],
    ],
)
def test_downstream_commands_refuse_diagnostic_only_capture(
    tmp_path: Path,
    command: list[str],
) -> None:
    captures = tmp_path / "blocked"
    captures.mkdir()
    (captures / "capture-manifest.json").write_text(
        json.dumps(
            {
                "requested": [{"viewport": "desktop", "state": "default"}],
                "succeeded": [],
                "failures": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "reason_code": "unexpected-url",
                        "reason": "redirected",
                        "diagnostic_only": True,
                    }
                ],
                "complete": False,
                "reviewable": False,
                "status": "blocked",
            }
        )
    )
    argv = [part.format(run=str(captures)) for part in command]

    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    assert exc.value.code == 1


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
