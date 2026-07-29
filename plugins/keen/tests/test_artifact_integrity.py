"""Adversarial tests for the capture-manifest evidence boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import _artifacts
from harness.report import compose
from harness.tokens import extract_from_captures


def _write_success_run(root: Path) -> None:
    (root / "screens").mkdir(parents=True)
    (root / "dom").mkdir()
    (root / "analysis").mkdir()
    (root / "screens" / "current.png").write_bytes(b"current")
    (root / "dom" / "current.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/current",
                "title": "Current",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/current.png",
                },
                "elements": [
                    {
                        "index": 0,
                        "tag": "button",
                        "box": {"x": 0, "y": 0, "w": 100, "h": 40},
                        "styles": {
                            "color": "rgb(255, 0, 0)",
                            "backgroundColor": "rgb(255, 255, 255)",
                        },
                    }
                ],
            }
        )
    )
    (root / "analysis" / "current.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "index": 0,
                        "component_kind": "button",
                        "name": "Current",
                        "viewport": "desktop",
                        "state": "default",
                        "capture_path": "screens/current.png",
                        "box": {"x": 0, "y": 0, "w": 100, "h": 40},
                        "styles": {},
                        "findings": [],
                    }
                ],
                "global_findings": [],
                "summary": {},
            }
        )
    )
    (root / "capture-manifest.json").write_text(
        json.dumps(
            {
                "target": "https://example.com/current",
                "requested": [{"viewport": "desktop", "state": "default"}],
                "succeeded": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "screen": "screens/current.png",
                        "dom": "dom/current.json",
                    }
                ],
                "failures": [],
                "complete": True,
                "reviewable": True,
                "status": "complete",
            }
        )
    )


def _write_diagnostic_run(root: Path) -> None:
    (root / "screens").mkdir(parents=True)
    (root / "dom").mkdir()
    (root / "screens" / "diagnostic.png").write_bytes(b"diagnostic")
    (root / "dom" / "diagnostic.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/login",
                "title": "Login",
                "meta": {
                    "viewport": "desktop",
                    "state": "default",
                    "screen_path": "screens/diagnostic.png",
                },
                "coverage": {
                    "expectation": {
                        "status": "blocked",
                        "reason_code": "unexpected-url",
                    }
                },
                "elements": [],
            }
        )
    )
    (root / "capture-manifest.json").write_text(
        json.dumps(
            {
                "target": "https://example.com/account",
                "requested": [{"viewport": "desktop", "state": "default"}],
                "succeeded": [],
                "failures": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "reason_code": "unexpected-url",
                        "diagnostic_only": True,
                        "screen": "screens/diagnostic.png",
                        "dom": "dom/diagnostic.json",
                    }
                ],
                "complete": False,
                "reviewable": False,
                "status": "blocked",
            }
        )
    )


def test_manifest_declared_files_are_the_only_current_evidence(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    (tmp_path / "screens" / "stale.png").write_bytes(b"stale")
    (tmp_path / "dom" / "stale.json").write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "index": 0,
                        "tag": "button",
                        "styles": {"color": "rgb(0, 0, 255)"},
                    }
                ]
            }
        )
    )
    (tmp_path / "analysis" / "stale.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "component_kind": "button",
                        "name": "Stale",
                        "findings": [
                            {
                                "predicate_id": "stale",
                                "severity": "P0",
                                "message": "must not appear",
                            }
                        ],
                    }
                ],
                "global_findings": [],
            }
        )
    )

    tokens = extract_from_captures(tmp_path)
    report = compose(tmp_path)

    assert [item["value"] for item in tokens["colors"]["foreground"]] == ["rgb(255, 0, 0)"]
    assert [component["name"] for component in report["components"]] == ["Current"]
    assert report["top_findings"] == []


def test_capture_evidence_rejects_in_progress_marker_before_manifest(
    tmp_path: Path,
) -> None:
    _write_success_run(tmp_path)
    (tmp_path / _artifacts.CAPTURE_IN_PROGRESS_FILENAME).write_text("in progress")

    with pytest.raises(
        _artifacts.ArtifactIntegrityError,
        match="capture is in progress",
    ) as exc_info:
        _artifacts.capture_evidence(tmp_path)

    assert exc_info.value.reason_code == "capture-in-progress"


def test_capture_evidence_rejects_dangling_in_progress_marker(
    tmp_path: Path,
) -> None:
    (tmp_path / "dom").mkdir()
    marker = tmp_path / _artifacts.CAPTURE_IN_PROGRESS_FILENAME
    try:
        marker.symlink_to(tmp_path / "missing-marker-target")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(
        _artifacts.ArtifactIntegrityError,
        match="capture is in progress",
    ) as exc_info:
        _artifacts.capture_evidence(tmp_path)

    assert exc_info.value.reason_code == "capture-in-progress"


def test_manifestless_capture_remains_compatible_without_in_progress_marker(
    tmp_path: Path,
) -> None:
    dom_dir = tmp_path / "dom"
    dom_dir.mkdir()
    dom_path = dom_dir / "legacy.json"
    dom_path.write_text(json.dumps({"elements": []}))

    evidence = _artifacts.capture_evidence(tmp_path)

    assert evidence.manifest is None
    assert evidence.dom_paths == (dom_path,)
    assert evidence.status == "legacy"
    assert evidence.reviewable is True
    assert evidence.complete is False
    assert evidence.legacy is True


def test_missing_current_analysis_never_promotes_stale_analysis(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    (tmp_path / "analysis" / "current.json").unlink()
    (tmp_path / "analysis" / "stale.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "component_kind": "button",
                        "name": "Stale",
                        "findings": [
                            {
                                "predicate_id": "stale",
                                "severity": "P0",
                                "message": "must not appear",
                            }
                        ],
                    }
                ],
                "global_findings": [],
            }
        )
    )
    report = compose(tmp_path)
    assert report["components"] == []
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["review_status"]["status"] == "incomplete"


def test_missing_manifest_cannot_restore_blocked_dom_scoring(tmp_path: Path) -> None:
    (tmp_path / "dom").mkdir()
    (tmp_path / "dom" / "diagnostic.json").write_text(
        json.dumps(
            {
                "coverage": {
                    "expectation": {
                        "status": "blocked",
                        "reason_code": "unexpected-url",
                    }
                }
            }
        )
    )
    with pytest.raises(_artifacts.ArtifactIntegrityError, match="diagnostic-only"):
        _artifacts.capture_evidence(tmp_path)
    report = compose(tmp_path)
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["review_status"]["status"] == "blocked"
    assert report["coverage"]["manifest_present"] is False


def test_manifest_cannot_promote_diagnostic_dom_to_success(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    document["coverage"] = {"expectation": {"status": "blocked"}}
    dom_path.write_text(json.dumps(document))
    with pytest.raises(_artifacts.ArtifactIntegrityError, match="diagnostic-only"):
        _artifacts.load_capture_manifest(tmp_path)


def test_diagnostic_dom_accepts_exact_manifest_binding(tmp_path: Path) -> None:
    _write_diagnostic_run(tmp_path)

    manifest = _artifacts.load_capture_manifest(tmp_path)

    assert manifest is not None
    assert manifest["status"] == "blocked"
    assert manifest["reviewable"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("screen_path", "screens/other.png"),
        ("viewport", "mobile"),
        ("state", "hover"),
        ("reason_code", "missing-selector"),
    ],
)
def test_diagnostic_dom_requires_exact_manifest_binding(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _write_diagnostic_run(tmp_path)
    dom_path = tmp_path / "dom" / "diagnostic.json"
    document = json.loads(dom_path.read_text())
    if field == "reason_code":
        document["coverage"]["expectation"][field] = value
    else:
        document["meta"][field] = value
    dom_path.write_text(json.dumps(document))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match=field):
        _artifacts.load_capture_manifest(tmp_path)


def test_manifest_cannot_reuse_artifacts_for_multiple_successes(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    manifest_path = tmp_path / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["requested"].append({"viewport": "mobile", "state": "default"})
    duplicate = dict(manifest["succeeded"][0])
    duplicate["viewport"] = "mobile"
    manifest["succeeded"].append(duplicate)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="reuses"):
        _artifacts.load_capture_manifest(tmp_path)


@pytest.mark.parametrize("missing_key", ["meta", "screen_path", "viewport", "state"])
def test_successful_dom_requires_exact_manifest_binding(
    tmp_path: Path,
    missing_key: str,
) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    if missing_key == "meta":
        document.pop("meta")
    else:
        document["meta"].pop(missing_key)
    dom_path.write_text(json.dumps(document))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match=r"DOM|metadata"):
        _artifacts.load_capture_manifest(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "requested": [{"viewport": "desktop", "state": "default"}],
            "succeeded": [],
            "failures": [{"viewport": "desktop", "state": "default"}],
            "complete": True,
            "reviewable": True,
            "status": "complete",
        },
    ],
)
def test_manifest_shape_and_invariants_fail_closed(
    tmp_path: Path,
    payload: dict,
) -> None:
    (tmp_path / "capture-manifest.json").write_text(json.dumps(payload))
    with pytest.raises(_artifacts.ArtifactIntegrityError):
        _artifacts.load_capture_manifest(tmp_path)


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}")
    (tmp_path / "screens").mkdir()
    (tmp_path / "screens" / "current.png").write_bytes(b"current")
    (tmp_path / "capture-manifest.json").write_text(
        json.dumps(
            {
                "requested": [{"viewport": "desktop", "state": "default"}],
                "succeeded": [
                    {
                        "viewport": "desktop",
                        "state": "default",
                        "screen": "screens/current.png",
                        "dom": "dom/../../outside.json",
                    }
                ],
                "failures": [],
                "complete": True,
                "reviewable": True,
                "status": "complete",
            }
        )
    )
    with pytest.raises(_artifacts.ArtifactIntegrityError, match="unsafe"):
        _artifacts.load_capture_manifest(tmp_path)


def test_manifest_rejects_symlinked_dom(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    real_dom = tmp_path / "dom" / "real.json"
    (tmp_path / "dom" / "current.json").replace(real_dom)
    try:
        (tmp_path / "dom" / "current.json").symlink_to(real_dom)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(_artifacts.ArtifactIntegrityError, match="symlink"):
        _artifacts.load_capture_manifest(tmp_path)


def test_manifest_rejects_invalid_utf8_dom(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    (tmp_path / "dom" / "current.json").write_bytes(b"\xff")
    with pytest.raises(_artifacts.ArtifactIntegrityError, match="UTF-8"):
        _artifacts.load_capture_manifest(tmp_path)


def test_malformed_style_value_fails_closed_without_downstream_traceback(
    tmp_path: Path,
) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    document["elements"][0]["styles"]["fontSize"] = {"not": "css"}
    dom_path.write_text(json.dumps(document))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="string or legacy"):
        extract_from_captures(tmp_path)

    report = compose(tmp_path)
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["score"]["score"] is None
    assert report["review_status"]["reviewable"] is False


def test_legacy_null_style_is_normalized_to_empty_string(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    document["elements"][0]["styles"]["webkitBackdropFilter"] = None
    dom_path.write_text(json.dumps(document))

    parsed = _artifacts.read_dom_document(dom_path)

    assert parsed["elements"][0]["styles"]["webkitBackdropFilter"] == ""
    assert _artifacts.capture_evidence(tmp_path).reviewable is True


def test_legacy_windows_artifact_separators_are_canonicalized(tmp_path: Path) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    document["meta"]["screen_path"] = r"screens\current.png"
    dom_path.write_text(json.dumps(document))
    analysis_path = tmp_path / "analysis" / "current.json"
    analysis = json.loads(analysis_path.read_text())
    analysis["components"][0]["capture_path"] = r"screens\current.png"
    analysis_path.write_text(json.dumps(analysis))
    manifest_path = tmp_path / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["succeeded"][0]["screen"] = r"screens\current.png"
    manifest["succeeded"][0]["dom"] = r"dom\current.json"
    manifest_path.write_text(json.dumps(manifest))

    normalized_manifest = _artifacts.load_capture_manifest(tmp_path)
    normalized_dom = _artifacts.read_dom_document(dom_path)
    normalized_analysis = _artifacts.read_analysis_document(analysis_path)
    _artifacts.validate_analysis_capture_binding(
        normalized_analysis,
        normalized_dom,
        where="legacy Windows analysis",
    )

    assert normalized_manifest is not None
    assert normalized_manifest["succeeded"][0]["screen"] == "screens/current.png"
    assert normalized_manifest["succeeded"][0]["dom"] == "dom/current.json"
    assert normalized_dom["meta"]["screen_path"] == "screens/current.png"
    assert normalized_analysis["components"][0]["capture_path"] == "screens/current.png"


def test_legacy_windows_diagnostic_separators_are_canonicalized(tmp_path: Path) -> None:
    _write_diagnostic_run(tmp_path)
    dom_path = tmp_path / "dom" / "diagnostic.json"
    document = json.loads(dom_path.read_text())
    document["meta"]["screen_path"] = r"screens\diagnostic.png"
    dom_path.write_text(json.dumps(document))
    manifest_path = tmp_path / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["failures"][0]["screen"] = r"screens\diagnostic.png"
    manifest["failures"][0]["dom"] = r"dom\diagnostic.json"
    manifest_path.write_text(json.dumps(manifest))

    normalized = _artifacts.load_capture_manifest(tmp_path)

    assert normalized is not None
    assert normalized["failures"][0]["screen"] == "screens/diagnostic.png"
    assert normalized["failures"][0]["dom"] == "dom/diagnostic.json"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"screens\..\outside.png",
        r"C:\private\outside.png",
    ],
)
def test_analysis_rejects_unsafe_windows_capture_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    _write_success_run(tmp_path)
    analysis_path = tmp_path / "analysis" / "current.json"
    analysis = json.loads(analysis_path.read_text())
    analysis["components"][0]["capture_path"] = unsafe_path
    analysis_path.write_text(json.dumps(analysis))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="unsafe"):
        _artifacts.read_analysis_document(analysis_path)


@pytest.mark.parametrize("reserved", ["con.foo.png", "LPT1.anything.png"])
def test_manifest_rejects_windows_device_names_with_extensions(
    tmp_path: Path,
    reserved: str,
) -> None:
    _write_success_run(tmp_path)
    manifest_path = tmp_path / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["succeeded"][0]["screen"] = f"screens/{reserved}"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="unsafe"):
        _artifacts.load_capture_manifest(tmp_path)


def test_legacy_manifest_complete_flag_uses_matrix_semantics(
    tmp_path: Path,
) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    document["coverage"] = {
        "complete": False,
        "reason": "legacy-partial-coverage",
    }
    dom_path.write_text(json.dumps(document))
    manifest_path = tmp_path / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("status")
    manifest.pop("reviewable")
    manifest["complete"] = True
    manifest_path.write_text(json.dumps(manifest))

    normalized = _artifacts.load_capture_manifest(tmp_path)

    assert normalized is not None
    assert normalized["status"] == "partial"
    assert normalized["reviewable"] is True
    assert normalized["complete"] is False


@pytest.mark.parametrize(
    ("container", "key"),
    [
        ("meta", "viewport_size"),
        ("screenshot", "captured_css_px"),
    ],
)
def test_malformed_nested_capture_metadata_fails_typed_integrity_check(
    tmp_path: Path,
    container: str,
    key: str,
) -> None:
    _write_success_run(tmp_path)
    dom_path = tmp_path / "dom" / "current.json"
    document = json.loads(dom_path.read_text())
    if container == "meta":
        document["meta"][key] = []
    else:
        document["coverage"] = {
            "complete": True,
            "screenshot": {key: []},
        }
    dom_path.write_text(json.dumps(document))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match=key):
        _artifacts.read_dom_document(dom_path)

    report = compose(tmp_path)
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["review_status"]["reviewable"] is False


def test_malformed_analysis_component_identity_returns_incomplete_report(
    tmp_path: Path,
) -> None:
    _write_success_run(tmp_path)
    analysis_path = tmp_path / "analysis" / "current.json"
    analysis = json.loads(analysis_path.read_text())
    analysis["components"][0]["capture_path"] = {"not": "a path"}
    analysis["components"][0]["findings"] = [{"severity": "P0", "predicate_id": "malformed"}]
    analysis_path.write_text(json.dumps(analysis))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="capture_path"):
        _artifacts.read_analysis_document(analysis_path)

    report = compose(tmp_path)
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["score"]["score"] is None
    assert report["review_status"]["reviewable"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_pixel_ratio", "bad"),
        ("viewport_width", -1),
        ("capture_width", float("inf")),
        ("capture_height", {}),
    ],
)
def test_malformed_analysis_geometry_returns_incomplete_report(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _write_success_run(tmp_path)
    analysis_path = tmp_path / "analysis" / "current.json"
    analysis = json.loads(analysis_path.read_text())
    analysis["components"][0][field] = value
    analysis_path.write_text(json.dumps(analysis))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match=field):
        _artifacts.read_analysis_document(analysis_path)

    report = compose(tmp_path)
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["review_status"]["reviewable"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture_path", "screens/stale.png"),
        ("viewport", "mobile"),
        ("state", "hover"),
    ],
)
def test_analysis_identity_must_match_manifest_selected_dom(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _write_success_run(tmp_path)
    analysis_path = tmp_path / "analysis" / "current.json"
    analysis = json.loads(analysis_path.read_text())
    analysis["components"][0][field] = value
    analysis_path.write_text(json.dumps(analysis))

    report = compose(tmp_path)

    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["score"]["score"] is None
    assert report["components"] == []
    assert report["review_status"]["reviewable"] is False


def test_capture_evidence_rejects_repo_local_symlink_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    run = outside / "run"
    repo.mkdir()
    run.mkdir(parents=True)
    _write_success_run(run)
    (repo / ".keen").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(repo)

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="ancestor"):
        _artifacts.capture_evidence(Path(".keen/run"))


def test_artifact_reader_rejects_symlink_ancestor_outside_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "cwd"
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    cwd.mkdir()
    run.mkdir()
    outside.mkdir()
    (outside / "components.json").write_text("[]")
    (run / "components").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(cwd)

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="ancestor"):
        _artifacts.read_component_list(run / "components" / "components.json")


def _write_reviewable_report(path: Path) -> dict:
    report = {
        "review_status": {"status": "ready", "reviewable": True},
        "coverage": {"status": "complete", "reviewable": True},
        "score": {"grade": "A", "score": 0},
        "components": [
            {
                "component_kind": "button",
                "index": 0,
                "viewport": "desktop",
                "state": "default",
                "capture_path": "screens/desktop-default.png",
                "findings": [],
            }
        ],
        "captures": [],
        "annotated_overviews": {},
        "top_findings": [],
    }
    path.write_text(json.dumps(report))
    return report


def test_report_reader_canonicalizes_legacy_windows_capture_path(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = _write_reviewable_report(report_path)
    report["components"][0]["capture_path"] = r"screens\desktop-default.png"
    report_path.write_text(json.dumps(report))

    parsed = _artifacts.read_report_document(report_path)

    assert parsed["components"][0]["capture_path"] == "screens/desktop-default.png"


@pytest.mark.parametrize("score", ["bad", True, float("inf"), float("nan")])
def test_report_reader_rejects_nonnumeric_score(tmp_path: Path, score: object) -> None:
    report_path = tmp_path / "report.json"
    report = _write_reviewable_report(report_path)
    report["score"]["score"] = score
    report_path.write_text(json.dumps(report))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match="finite number"):
        _artifacts.read_report_document(report_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("viewport", 1),
        ("state", {}),
        ("capture_path", 5),
        ("index", "zero"),
    ],
)
def test_report_reader_rejects_invalid_component_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    report_path = tmp_path / "report.json"
    report = _write_reviewable_report(report_path)
    report["components"][0][field] = value
    report_path.write_text(json.dumps(report))

    with pytest.raises(_artifacts.ArtifactIntegrityError, match=field):
        _artifacts.read_report_document(report_path)
