"""Regression tests for the generated-artifact output boundary."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import _safeio, cli


def test_atomic_text_and_binary_writes_replace_regular_files(tmp_path: Path) -> None:
    text_path = tmp_path / "nested" / "report.json"
    binary_path = tmp_path / "screens" / "capture.png"
    _safeio.atomic_write_text(tmp_path, text_path, "first")
    _safeio.atomic_write_text(tmp_path, text_path, "second")
    _safeio.atomic_write_bytes(tmp_path, binary_path, b"\x89PNG\r\n")
    assert text_path.read_text() == "second"
    assert binary_path.read_bytes() == b"\x89PNG\r\n"


def test_remove_output_file_removes_regular_file_and_refuses_symlink(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated.json"
    _safeio.atomic_write_text(tmp_path, generated, "generated")
    _safeio.remove_output_file(tmp_path, generated)
    assert not generated.exists()

    victim = tmp_path / "victim.txt"
    victim.write_text("sentinel")
    generated.symlink_to(victim)
    with pytest.raises(_safeio.UnsafeOutputError):
        _safeio.remove_output_file(tmp_path, generated)
    assert victim.read_text() == "sentinel"


@pytest.mark.parametrize("link_parent", [False, True])
def test_atomic_write_never_follows_leaf_or_parent_symlink(
    tmp_path: Path,
    link_parent: bool,
) -> None:
    victim = tmp_path / "outside.txt"
    victim.write_text("sentinel")
    root = tmp_path / "run"
    root.mkdir()
    if link_parent:
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (root / "components").symlink_to(outside_dir, target_is_directory=True)
        destination = root / "components" / "current.json"
    else:
        destination = root / "report.json"
        destination.symlink_to(victim)

    with pytest.raises(_safeio.UnsafeOutputError):
        _safeio.atomic_write_text(root, destination, "overwrite")
    assert victim.read_text() == "sentinel"


def test_repo_local_symlink_ancestor_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / ".keen").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(repo)

    with pytest.raises(_safeio.UnsafeOutputError):
        _safeio.atomic_write_text(
            Path(".keen/review"),
            Path(".keen/review/report.json"),
            "unsafe",
        )
    assert not (outside / "review" / "report.json").exists()


def test_absolute_output_path_rejects_symlink_ancestor_outside_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "cwd"
    project = tmp_path / "project"
    victim = tmp_path / "victim"
    cwd.mkdir()
    project.mkdir()
    victim.mkdir()
    (project / ".keen").symlink_to(victim, target_is_directory=True)
    monkeypatch.chdir(cwd)

    with pytest.raises(_safeio.UnsafeOutputError, match="ancestor"):
        _safeio.atomic_write_text(
            project / ".keen" / "review",
            project / ".keen" / "review" / "report.json",
            "unsafe",
        )
    assert not (victim / "review" / "report.json").exists()


def test_output_traversal_and_escape_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    with pytest.raises(_safeio.UnsafeOutputError):
        _safeio.atomic_write_text(root, root / ".." / "outside.txt", "unsafe")
    with pytest.raises(_safeio.UnsafeOutputError):
        _safeio.atomic_write_text(root, tmp_path / "outside.txt", "unsafe")


def test_temp_directory_paths_and_missing_fchmod_are_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "fchmod", None, raising=False)
    destination = tmp_path / "run" / "report.json"
    _safeio.atomic_write_text(tmp_path / "run", destination, "ok")
    assert destination.read_text() == "ok"


def test_failed_replace_cleans_private_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    destination = root / "report.json"

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        _safeio.atomic_write_text(root, destination, "data")
    assert list(root.glob(".*.tmp")) == []


def test_windows_reparse_attribute_is_treated_as_linklike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)

    class FakePath:
        def is_symlink(self) -> bool:
            return False

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(st_file_attributes=0x400)

    assert _safeio.is_linklike(FakePath()) is True  # type: ignore[arg-type]


def _write_reviewable_report(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "review_status": {"status": "ready", "reviewable": True},
                "coverage": {"status": "complete", "reviewable": True},
                "score": {"grade": "A", "score": 4},
                "components": [],
                "captures": [],
                "annotated_overviews": {},
                "top_findings": [],
            }
        )
    )


def test_diff_output_symlink_cannot_overwrite_outside_file(tmp_path: Path) -> None:
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    _write_reviewable_report(run_a / "report.json")
    _write_reviewable_report(run_b / "report.json")
    victim = tmp_path / "victim.md"
    victim.write_text("sentinel")
    (run_b / "diff.md").symlink_to(victim)

    assert cli.main(["diff", str(run_a), str(run_b)]) == 1
    assert victim.read_text() == "sentinel"


def test_taste_output_symlink_cannot_overwrite_outside_file(tmp_path: Path) -> None:
    run = tmp_path / "run"
    components = run / "components"
    components.mkdir(parents=True)
    (components / "legacy.json").write_text(
        json.dumps(
            [
                {
                    "index": 0,
                    "component_kind": "button",
                    "role": "button",
                    "tag": "button",
                    "name": "Save",
                    "text": "Save",
                    "box": {"x": 0, "y": 0, "w": 80, "h": 40},
                    "styles": {},
                    "findings": [],
                }
            ]
        )
    )
    victim = tmp_path / "victim.json"
    victim.write_text("sentinel")
    (run / "taste.json").symlink_to(victim)

    assert cli.main(["taste", str(run), "--json"]) == 1
    assert victim.read_text() == "sentinel"
