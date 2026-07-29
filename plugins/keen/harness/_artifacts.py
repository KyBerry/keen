"""Capture-artifact integrity helpers.

The capture manifest is the authority for a current run.  Downstream stages
must never discover current evidence by globbing a directory that may contain
stale files from an older run.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from harness._safeio import UnsafeOutputError, ensure_safe_input_path, is_linklike

MAX_MANIFEST_BYTES = 1_048_576
MAX_DOM_BYTES = 64 * 1_048_576
MAX_STAGE_BYTES = 64 * 1_048_576
CAPTURE_IN_PROGRESS_FILENAME = ".capture-in-progress"
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ARTIFACT_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}$")
_SEVERITIES = frozenset({"P0", "P1", "P2"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class ArtifactIntegrityError(ValueError):
    """Raised when capture evidence is missing, contradictory, or unsafe."""

    def __init__(self, message: str, *, reason_code: str = "invalid-artifact") -> None:
        super().__init__(message)
        self.reason_code = reason_code


def is_windows_reserved_artifact_name(value: str) -> bool:
    """Return whether Windows treats a basename as a device path."""
    device_name = value.rstrip(" .").split(".", 1)[0].upper()
    return device_name in _WINDOWS_RESERVED_NAMES


def _normalize_artifact_reference(
    value: Any,
    *,
    prefix: str,
    suffix: str,
    field: str,
) -> str:
    """Validate and canonicalize an artifact reference without touching disk."""
    if not isinstance(value, str) or not value:
        raise ArtifactIntegrityError(f"{field} is missing")
    relative = PurePosixPath(value.replace("\\", "/"))
    if (
        relative.is_absolute()
        or relative.parts[:1] != (prefix,)
        or len(relative.parts) != 2
        or ".." in relative.parts
        or "." in relative.parts
        or relative.suffix.lower() != suffix
        or ":" in relative.parts[0]
        or _ARTIFACT_BASENAME_RE.fullmatch(relative.stem) is None
        or is_windows_reserved_artifact_name(relative.name)
    ):
        raise ArtifactIntegrityError(f"{field} is unsafe: {value!r}")
    return relative.as_posix()


@dataclass(frozen=True)
class CaptureEvidence:
    manifest: dict[str, Any] | None
    dom_paths: tuple[Path, ...]
    status: str
    reviewable: bool
    complete: bool
    legacy: bool = False


def _validate_box(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"{where} box must be an object")
    for key in ("x", "y", "w", "h"):
        coordinate = value.get(key)
        if (
            not isinstance(coordinate, (int, float))
            or isinstance(coordinate, bool)
            or not math.isfinite(float(coordinate))
            or (key in {"w", "h"} and float(coordinate) < 0)
        ):
            raise ArtifactIntegrityError(
                f"{where} box.{key} must be a finite"
                f"{' non-negative' if key in {'w', 'h'} else ''} number"
            )
    return value


def _validate_styles(value: Any, *, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or (item is not None and not isinstance(item, str))
        for key, item in value.items()
    ):
        raise ArtifactIntegrityError(
            f"{where} styles must contain only string or legacy null values"
        )
    # Keen 0.8.0 emitted null for unsupported computed-style properties.
    # Normalize at the typed boundary so downstream consumers stay string-only.
    for key, item in value.items():
        if item is None:
            value[key] = ""
    return value


def _validate_finite_fields(
    value: Any,
    *,
    fields: tuple[str, ...],
    where: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"{where} must be an object")
    for field in fields:
        number = value.get(field)
        if number is not None and (
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or not math.isfinite(float(number))
        ):
            raise ArtifactIntegrityError(f"{where}.{field} must be a finite number")
    return value


def _validate_dom_coverage(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"{where} must be an object")
    complete = value.get("complete")
    if complete is not None and not isinstance(complete, bool):
        raise ArtifactIntegrityError(f"{where}.complete must be a boolean")
    for key in ("expectation", "dom", "screenshot", "accessibility_names"):
        nested = value.get(key)
        if nested is not None and not isinstance(nested, dict):
            raise ArtifactIntegrityError(f"{where}.{key} must be an object")
    screenshot = value.get("screenshot")
    if isinstance(screenshot, dict) and screenshot.get("captured_css_px") is not None:
        _validate_finite_fields(
            screenshot["captured_css_px"],
            fields=("width", "height"),
            where=f"{where}.screenshot.captured_css_px",
        )
    return value


def _validate_component_identity(
    component: dict[str, Any],
    *,
    where: str,
) -> int:
    index = component.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ArtifactIntegrityError(f"{where} index must be an integer")
    for field in ("viewport", "state"):
        if not isinstance(component.get(field), str):
            raise ArtifactIntegrityError(f"{where} {field} must be a string")
    component["capture_path"] = _normalize_artifact_reference(
        component.get("capture_path"),
        prefix="screens",
        suffix=".png",
        field=f"{where} capture_path",
    )
    return index


def _validate_component_geometry(
    component: dict[str, Any],
    *,
    where: str,
) -> None:
    dpr = component.get("device_pixel_ratio")
    if dpr is not None and (
        not isinstance(dpr, (int, float))
        or isinstance(dpr, bool)
        or not math.isfinite(float(dpr))
        or float(dpr) <= 0
    ):
        raise ArtifactIntegrityError(f"{where} device_pixel_ratio must be a positive number")
    for field in ("viewport_width", "capture_width", "capture_height"):
        value = component.get(field)
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ArtifactIntegrityError(f"{where} {field} must be a non-negative number")
    parent_index = component.get("parent_index")
    if parent_index is not None and (
        not isinstance(parent_index, int) or isinstance(parent_index, bool)
    ):
        raise ArtifactIntegrityError(f"{where} parent_index must be an integer")


def _validate_findings(value: Any, *, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ArtifactIntegrityError(f"{where} findings must be a list of objects")
    for index, finding in enumerate(value):
        if finding.get("severity") not in _SEVERITIES:
            raise ArtifactIntegrityError(f"{where} finding {index} severity is invalid")
        predicate_id = finding.get("predicate_id")
        if not isinstance(predicate_id, str) or not predicate_id:
            raise ArtifactIntegrityError(f"{where} finding {index} predicate_id is invalid")
    return value


def _bounded_json(path: Path, *, max_bytes: int) -> Any:
    """Read bounded UTF-8 JSON while rejecting symlinked inputs."""
    try:
        path = ensure_safe_input_path(path, directory=False)
    except UnsafeOutputError as exc:
        raise ArtifactIntegrityError(str(exc)) from None
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ArtifactIntegrityError(
            f"artifact is unavailable: {path.name} ({type(exc).__name__})"
        ) from None
    if size > max_bytes:
        raise ArtifactIntegrityError(f"artifact exceeds the {max_bytes}-byte limit: {path.name}")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ArtifactIntegrityError(
            f"artifact is not readable UTF-8: {path.name} ({type(exc).__name__})"
        ) from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactIntegrityError(
            f"artifact is not valid JSON: {path.name} (line {exc.lineno})"
        ) from None


def read_dom_document(path: Path) -> dict[str, Any]:
    payload = _bounded_json(path, max_bytes=MAX_DOM_BYTES)
    if not isinstance(payload, dict):
        raise ArtifactIntegrityError(f"DOM artifact must contain a JSON object: {path.name}")
    for key in (
        "meta",
        "coverage",
        "documentSize",
        "focus_coverage",
        "viewport",
        "surface",
    ):
        value = payload.get(key)
        if value is not None and not isinstance(value, dict):
            raise ArtifactIntegrityError(f"DOM {key} must be an object: {path.name}")
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("viewport", "state", "screen_path"):
            value = meta.get(key)
            if value is not None and not isinstance(value, str):
                raise ArtifactIntegrityError(f"DOM meta.{key} must be a string: {path.name}")
        if meta.get("screen_path") is not None:
            meta["screen_path"] = _normalize_artifact_reference(
                meta["screen_path"],
                prefix="screens",
                suffix=".png",
                field=f"DOM meta.screen_path in {path.name}",
            )
        manual_review = meta.get("manual_review_needed")
        if manual_review is not None and not isinstance(manual_review, bool):
            raise ArtifactIntegrityError(
                f"DOM meta.manual_review_needed must be a boolean: {path.name}"
            )
        if meta.get("viewport_size") is not None:
            _validate_finite_fields(
                meta["viewport_size"],
                fields=("width", "height", "deviceScaleFactor"),
                where=f"DOM meta.viewport_size in {path.name}",
            )
        if meta.get("coverage") is not None:
            _validate_dom_coverage(
                meta["coverage"],
                where=f"DOM meta.coverage in {path.name}",
            )
        if meta.get("banner_dismissal") is not None and not isinstance(
            meta["banner_dismissal"], dict
        ):
            raise ArtifactIntegrityError(
                f"DOM meta.banner_dismissal must be an object: {path.name}"
            )
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        _validate_dom_coverage(coverage, where=f"DOM coverage in {path.name}")
    for key in ("documentSize", "viewport"):
        value = payload.get(key)
        if isinstance(value, dict):
            _validate_finite_fields(
                value,
                fields=("width", "height"),
                where=f"DOM {key} in {path.name}",
            )
    device_pixel_ratio = payload.get("devicePixelRatio")
    if device_pixel_ratio is not None and (
        not isinstance(device_pixel_ratio, (int, float))
        or isinstance(device_pixel_ratio, bool)
        or not math.isfinite(float(device_pixel_ratio))
    ):
        raise ArtifactIntegrityError(f"DOM devicePixelRatio must be finite: {path.name}")
    focused_index = payload.get("focused_index")
    if focused_index is not None and (
        not isinstance(focused_index, int) or isinstance(focused_index, bool)
    ):
        raise ArtifactIntegrityError(f"DOM focused_index must be an integer: {path.name}")
    surface = payload.get("surface")
    if isinstance(surface, dict):
        body_text_chars = surface.get("body_text_chars")
        if body_text_chars is not None and (
            not isinstance(body_text_chars, (int, float))
            or isinstance(body_text_chars, bool)
            or not math.isfinite(float(body_text_chars))
            or float(body_text_chars) < 0
        ):
            raise ArtifactIntegrityError(
                f"DOM surface.body_text_chars must be a non-negative finite number: {path.name}"
            )
        for key in ("painted_background", "pseudo_content"):
            value = surface.get(key)
            if value is not None and not isinstance(value, bool):
                raise ArtifactIntegrityError(f"DOM surface.{key} must be a boolean: {path.name}")
    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        raise ArtifactIntegrityError(f"DOM elements must be a list: {path.name}")
    seen_indices: set[int] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ArtifactIntegrityError(f"DOM elements[{index}] must be an object: {path.name}")
        element_index = element.get("index")
        if (
            not isinstance(element_index, int)
            or isinstance(element_index, bool)
            or element_index in seen_indices
        ):
            raise ArtifactIntegrityError(
                f"DOM elements[{index}].index must be a unique integer: {path.name}"
            )
        seen_indices.add(element_index)
        tag = element.get("tag")
        if not isinstance(tag, str) or not tag:
            raise ArtifactIntegrityError(f"DOM elements[{index}].tag is invalid: {path.name}")
        _validate_styles(
            element.get("styles"),
            where=f"DOM elements[{index}] in {path.name}",
        )
        _validate_box(element.get("box"), where=f"DOM elements[{index}]")
        parent_index = element.get("parentIndex")
        if parent_index is not None and (
            not isinstance(parent_index, int) or isinstance(parent_index, bool)
        ):
            raise ArtifactIntegrityError(
                f"DOM elements[{index}].parentIndex must be an integer: {path.name}"
            )
        if "effectiveOpacity" in element:
            opacity = element["effectiveOpacity"]
            if (
                not isinstance(opacity, (int, float))
                or isinstance(opacity, bool)
                or not math.isfinite(float(opacity))
                or not 0 <= float(opacity) <= 1
            ):
                raise ArtifactIntegrityError(
                    f"DOM elements[{index}].effectiveOpacity is invalid: {path.name}"
                )
    return payload


def read_json_object(path: Path) -> dict[str, Any]:
    payload = _bounded_json(path, max_bytes=MAX_STAGE_BYTES)
    if not isinstance(payload, dict):
        raise ArtifactIntegrityError(f"artifact must contain a JSON object: {path.name}")
    return payload


def read_json_list(path: Path) -> list[Any]:
    payload = _bounded_json(path, max_bytes=MAX_STAGE_BYTES)
    if not isinstance(payload, list):
        raise ArtifactIntegrityError(f"artifact must contain a JSON list: {path.name}")
    return payload


def read_component_list(path: Path) -> list[dict[str, Any]]:
    payload = read_json_list(path)
    components: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for index, component in enumerate(payload):
        if not isinstance(component, dict):
            raise ArtifactIntegrityError(
                f"component artifact item {index} must be an object: {path.name}"
            )
        kind = component.get("component_kind")
        if not isinstance(kind, str) or not kind:
            raise ArtifactIntegrityError(
                f"component artifact component_kind is invalid: {path.name}"
            )
        component_index = _validate_component_identity(
            component,
            where=f"component artifact item {index} in {path.name}",
        )
        if component_index in seen_indices:
            raise ArtifactIntegrityError(f"component artifact index must be unique: {path.name}")
        seen_indices.add(component_index)
        _validate_styles(
            component.get("styles"),
            where=f"component artifact item {index} in {path.name}",
        )
        _validate_box(component.get("box"), where=f"component artifact item {index}")
        _validate_component_geometry(
            component,
            where=f"component artifact item {index} in {path.name}",
        )
        _validate_findings(
            component.get("findings", []),
            where=f"component artifact item {index}",
        )
        components.append(component)
    return components


def read_analysis_document(path: Path) -> dict[str, Any]:
    payload = read_json_object(path)
    components = payload.get("components", [])
    global_findings = payload.get("global_findings", [])
    summary = payload.get("summary", {})
    if not isinstance(components, list) or not all(
        isinstance(component, dict) for component in components
    ):
        raise ArtifactIntegrityError(f"analysis components must be a list of objects: {path.name}")
    _validate_findings(global_findings, where="analysis global")
    if not isinstance(summary, dict):
        raise ArtifactIntegrityError(f"analysis summary must be an object: {path.name}")
    seen_indices: set[int] = set()
    for component_index, component in enumerate(components):
        kind = component.get("component_kind")
        if not isinstance(kind, str) or not kind:
            raise ArtifactIntegrityError(f"analysis component_kind is invalid: {path.name}")
        identity_index = _validate_component_identity(
            component,
            where=f"analysis component {component_index} in {path.name}",
        )
        if identity_index in seen_indices:
            raise ArtifactIntegrityError(f"analysis component index must be unique: {path.name}")
        seen_indices.add(identity_index)
        _validate_styles(
            component.get("styles"),
            where=f"analysis component in {path.name}",
        )
        _validate_box(component.get("box"), where="analysis component")
        _validate_component_geometry(
            component,
            where=f"analysis component {component_index} in {path.name}",
        )
        _validate_findings(component.get("findings", []), where="analysis component")
    return payload


def validate_analysis_capture_binding(
    analysis: dict[str, Any],
    dom: dict[str, Any],
    *,
    where: str,
) -> None:
    """Bind analyzed component identities to their same-capture DOM metadata."""
    meta = dom.get("meta")
    if not isinstance(meta, dict):
        raise ArtifactIntegrityError(f"{where} matching DOM is missing capture metadata")
    expected = {
        "capture_path": meta.get("screen_path"),
        "viewport": meta.get("viewport"),
        "state": meta.get("state"),
    }
    for field, value in expected.items():
        if not isinstance(value, str):
            raise ArtifactIntegrityError(f"{where} matching DOM {field} identity is invalid")
    for index, component in enumerate(analysis.get("components", [])):
        for field, expected_value in expected.items():
            if component.get(field) != expected_value:
                raise ArtifactIntegrityError(
                    f"{where} component {index} {field} contradicts its DOM capture"
                )


def read_report_document(
    path: Path,
    *,
    require_reviewable: bool = True,
    require_scored: bool = True,
) -> dict[str, Any]:
    """Read a bounded report and enforce the caller's evidence requirements."""
    payload = read_json_object(path)
    score = payload.get("score")
    components = payload.get("components", [])
    captures = payload.get("captures", [])
    top_findings = payload.get("top_findings", [])
    annotated = payload.get("annotated_overviews", {})
    if not isinstance(score, dict):
        raise ArtifactIntegrityError(f"report score must be an object: {path.name}")
    grade = score.get("grade")
    if not isinstance(grade, str) or not grade.strip():
        raise ArtifactIntegrityError(f"report score.grade must be a non-empty string: {path.name}")
    numeric_score = score.get("score")
    if numeric_score is not None and (
        not isinstance(numeric_score, (int, float))
        or isinstance(numeric_score, bool)
        or not math.isfinite(float(numeric_score))
    ):
        raise ArtifactIntegrityError(f"report score.score must be a finite number: {path.name}")
    counts = score.get("counts")
    if counts is not None:
        if not isinstance(counts, dict):
            raise ArtifactIntegrityError(f"report score.counts must be an object: {path.name}")
        for severity in _SEVERITIES:
            count = counts.get(severity)
            if count is not None and (
                not isinstance(count, int) or isinstance(count, bool) or count < 0
            ):
                raise ArtifactIntegrityError(
                    f"report score.counts.{severity} must be a non-negative integer: {path.name}"
                )
    if not isinstance(components, list) or not all(
        isinstance(component, dict) for component in components
    ):
        raise ArtifactIntegrityError(f"report components must be a list of objects: {path.name}")
    if not isinstance(captures, list) or not all(isinstance(item, dict) for item in captures):
        raise ArtifactIntegrityError(f"report captures must be a list of objects: {path.name}")
    if not isinstance(annotated, dict):
        raise ArtifactIntegrityError(f"report annotated_overviews must be an object: {path.name}")
    _validate_findings(top_findings, where="report top")
    for index, component in enumerate(components):
        kind = component.get("component_kind")
        if not isinstance(kind, str) or not kind:
            raise ArtifactIntegrityError(
                f"report component {index} component_kind is invalid: {path.name}"
            )
        for field in ("viewport", "state"):
            value = component.get(field)
            if not isinstance(value, str):
                raise ArtifactIntegrityError(
                    f"report component {index} {field} must be a string: {path.name}"
                )
        capture_path = component.get("capture_path")
        if capture_path is not None and (not isinstance(capture_path, str) or not capture_path):
            raise ArtifactIntegrityError(
                f"report component {index} capture_path is invalid: {path.name}"
            )
        if capture_path is not None:
            component["capture_path"] = _normalize_artifact_reference(
                capture_path,
                prefix="screens",
                suffix=".png",
                field=f"report component {index} capture_path in {path.name}",
            )
        component_index = component.get("index", component.get("component_index"))
        if not isinstance(component_index, int) or isinstance(component_index, bool):
            raise ArtifactIntegrityError(
                f"report component {index} index must be an integer: {path.name}"
            )
        _validate_findings(
            component.get("findings", []),
            where=f"report component {index}",
        )

    review_status = payload.get("review_status")
    coverage = payload.get("coverage")
    if review_status is not None and not isinstance(review_status, dict):
        raise ArtifactIntegrityError(f"report review_status must be an object: {path.name}")
    if coverage is not None and not isinstance(coverage, dict):
        raise ArtifactIntegrityError(f"report coverage must be an object: {path.name}")
    if require_reviewable:
        if isinstance(review_status, dict) and (
            review_status.get("reviewable") is False
            or review_status.get("status") in {"blocked", "failed", "incomplete"}
        ):
            raise ArtifactIntegrityError("report is not reviewable")
        if isinstance(coverage, dict) and (
            coverage.get("reviewable") is False
            or coverage.get("status") in {"blocked", "failed", "incomplete"}
        ):
            raise ArtifactIntegrityError("report coverage is not reviewable")
        if require_scored and (grade == "INCOMPLETE" or numeric_score is None):
            raise ArtifactIntegrityError("report does not contain a reviewable quality result")
    return payload


def _safe_relative_artifact(
    root: Path,
    value: Any,
    *,
    prefix: str,
    suffix: str,
    must_exist: bool = True,
) -> Path:
    normalized = _normalize_artifact_reference(
        value,
        prefix=prefix,
        suffix=suffix,
        field=f"manifest artifact path for {prefix}",
    )
    relative = PurePosixPath(normalized)

    if is_linklike(root):
        raise ArtifactIntegrityError("capture directory must not be a symlink")
    root_resolved = root.resolve()
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if is_linklike(current):
            raise ArtifactIntegrityError(f"manifest artifact path uses a symlink: {value!r}")
    try:
        candidate.resolve(strict=must_exist).relative_to(root_resolved)
    except (OSError, ValueError):
        raise ArtifactIntegrityError(
            f"manifest artifact escapes the capture directory or is missing: {value!r}"
        ) from None
    if must_exist and not candidate.is_file():
        raise ArtifactIntegrityError(f"manifest artifact is missing: {value!r}")
    return candidate


def _matrix_item(value: Any, *, where: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"{where} entry must be an object")
    viewport = value.get("viewport")
    state = value.get("state")
    if not isinstance(viewport, str) or _ARTIFACT_ID_RE.fullmatch(viewport) is None:
        raise ArtifactIntegrityError(f"{where} viewport is invalid")
    if not isinstance(state, str) or _ARTIFACT_ID_RE.fullmatch(state) is None:
        raise ArtifactIntegrityError(f"{where} state is invalid")
    return viewport, state


def _capture_expectation(document: dict[str, Any], *, where: str) -> dict[str, Any]:
    coverage = document.get("coverage")
    if coverage is None:
        return {}
    if not isinstance(coverage, dict):
        raise ArtifactIntegrityError(f"{where} DOM coverage must be an object")
    expectation = coverage.get("expectation")
    if expectation is None:
        return {}
    if not isinstance(expectation, dict):
        raise ArtifactIntegrityError(f"{where} DOM expectation must be an object")
    return expectation


def _derive_status(
    *,
    requested_count: int,
    succeeded_count: int,
    failures: list[Any],
    successful_coverage_complete: bool,
) -> tuple[str, bool, bool]:
    blocked = any(
        isinstance(item, dict)
        and (
            item.get("diagnostic_only") is True
            or item.get("reason_code") in {"unexpected-url", "missing-selector"}
        )
        for item in failures
    )
    if blocked:
        return "blocked", False, False
    if (
        requested_count > 0
        and succeeded_count == requested_count
        and not failures
        and successful_coverage_complete
    ):
        return "complete", True, True
    if succeeded_count > 0:
        return "partial", True, False
    return "failed", False, False


def validate_capture_manifest(root: Path, payload: Any) -> dict[str, Any]:
    """Validate, normalize, and bind a manifest to artifacts under ``root``."""
    if not isinstance(payload, dict):
        raise ArtifactIntegrityError("capture manifest must contain a JSON object")
    requested = payload.get("requested")
    succeeded = payload.get("succeeded")
    failures = payload.get("failures")
    if not isinstance(requested, list) or not requested:
        raise ArtifactIntegrityError("capture manifest requested must be a non-empty list")
    if not isinstance(succeeded, list):
        raise ArtifactIntegrityError("capture manifest succeeded must be a list")
    if not isinstance(failures, list):
        raise ArtifactIntegrityError("capture manifest failures must be a list")

    requested_pairs = [_matrix_item(item, where="requested") for item in requested]
    if len(requested_pairs) != len(set(requested_pairs)):
        raise ArtifactIntegrityError("capture manifest requested matrix contains duplicates")
    requested_set = set(requested_pairs)

    seen_results: set[tuple[str, str]] = set()
    seen_screens: set[str] = set()
    seen_doms: set[str] = set()
    successful_coverage_complete = True
    normalized_succeeded: list[dict[str, Any]] = []
    for item in succeeded:
        pair = _matrix_item(item, where="succeeded")
        if pair not in requested_set or pair in seen_results:
            raise ArtifactIntegrityError(
                "capture manifest succeeded contains an unknown or duplicate matrix entry"
            )
        screen = _safe_relative_artifact(root, item.get("screen"), prefix="screens", suffix=".png")
        dom = _safe_relative_artifact(root, item.get("dom"), prefix="dom", suffix=".json")
        screen_relative = screen.relative_to(root).as_posix()
        dom_relative = dom.relative_to(root).as_posix()
        if screen_relative in seen_screens or dom_relative in seen_doms:
            raise ArtifactIntegrityError(
                "capture manifest reuses a screen or DOM artifact across matrix entries"
            )
        document = read_dom_document(dom)
        coverage = document.get("coverage")
        if isinstance(coverage, dict) and coverage.get("complete") is False:
            successful_coverage_complete = False
        expectation = _capture_expectation(document, where="successful")
        if expectation.get("status") == "blocked":
            raise ArtifactIntegrityError("manifest declares a diagnostic-only DOM as successful")
        meta = document.get("meta")
        if not isinstance(meta, dict):
            raise ArtifactIntegrityError("successful DOM is missing capture metadata")
        if meta.get("screen_path") != screen_relative:
            raise ArtifactIntegrityError(
                "successful DOM screen_path contradicts the capture manifest"
            )
        if meta.get("viewport") != pair[0]:
            raise ArtifactIntegrityError("successful DOM viewport contradicts the capture manifest")
        if meta.get("state") != pair[1]:
            raise ArtifactIntegrityError("successful DOM state contradicts the capture manifest")
        seen_results.add(pair)
        seen_screens.add(screen_relative)
        seen_doms.add(dom_relative)
        normalized_succeeded.append(
            {
                **item,
                "screen": screen_relative,
                "dom": dom_relative,
            }
        )

    normalized_failures: list[dict[str, Any]] = []
    for item in failures:
        pair = _matrix_item(item, where="failures")
        if pair not in requested_set or pair in seen_results:
            raise ArtifactIntegrityError(
                "capture manifest failures contains an unknown or duplicate matrix entry"
            )
        seen_results.add(pair)
        normalized = dict(item)
        if item.get("diagnostic_only") is True:
            screen = _safe_relative_artifact(
                root, item.get("screen"), prefix="screens", suffix=".png"
            )
            dom = _safe_relative_artifact(root, item.get("dom"), prefix="dom", suffix=".json")
            screen_relative = screen.relative_to(root).as_posix()
            dom_relative = dom.relative_to(root).as_posix()
            if screen_relative in seen_screens or dom_relative in seen_doms:
                raise ArtifactIntegrityError(
                    "capture manifest reuses a screen or DOM artifact across matrix entries"
                )
            diagnostic = read_dom_document(dom)
            expectation = _capture_expectation(diagnostic, where="diagnostic-only")
            if expectation.get("status") != "blocked":
                raise ArtifactIntegrityError("diagnostic-only DOM is missing a blocked expectation")
            meta = diagnostic.get("meta")
            if not isinstance(meta, dict):
                raise ArtifactIntegrityError("diagnostic-only DOM is missing capture metadata")
            if meta.get("screen_path") != screen_relative:
                raise ArtifactIntegrityError(
                    "diagnostic-only DOM screen_path contradicts the capture manifest"
                )
            if meta.get("viewport") != pair[0]:
                raise ArtifactIntegrityError(
                    "diagnostic-only DOM viewport contradicts the capture manifest"
                )
            if meta.get("state") != pair[1]:
                raise ArtifactIntegrityError(
                    "diagnostic-only DOM state contradicts the capture manifest"
                )
            reason_code = item.get("reason_code")
            if (
                not isinstance(reason_code, str)
                or not reason_code
                or expectation.get("reason_code") != reason_code
            ):
                raise ArtifactIntegrityError(
                    "diagnostic-only DOM reason_code contradicts the capture manifest"
                )
            seen_screens.add(screen_relative)
            seen_doms.add(dom_relative)
            normalized["screen"] = screen_relative
            normalized["dom"] = dom_relative
        normalized_failures.append(normalized)

    if seen_results != requested_set:
        raise ArtifactIntegrityError(
            "capture manifest does not account for every requested matrix entry"
        )

    status, reviewable, complete = _derive_status(
        requested_count=len(requested_pairs),
        succeeded_count=len(normalized_succeeded),
        failures=normalized_failures,
        successful_coverage_complete=successful_coverage_complete,
    )
    declared_status = payload.get("status")
    declared_reviewable = payload.get("reviewable")
    declared_complete = payload.get("complete")
    legacy_manifest_contract = declared_status is None and declared_reviewable is None
    # Legacy manifests omitted status/reviewable.  Accept those only when the
    # rest of the evidence matrix is complete and internally consistent.
    # Keen 0.8.0 used "failed" for a reviewable partial run.
    legacy_partial = declared_status == "failed" and status == "partial" and reviewable
    if declared_status is not None and declared_status != status and not legacy_partial:
        raise ArtifactIntegrityError(
            f"capture manifest status contradicts its evidence matrix ({declared_status!r})"
        )
    if declared_reviewable is not None and (
        not isinstance(declared_reviewable, bool) or declared_reviewable != reviewable
    ):
        raise ArtifactIntegrityError("capture manifest reviewable flag is contradictory")
    if (
        not legacy_manifest_contract
        and declared_complete is not None
        and (not isinstance(declared_complete, bool) or declared_complete != complete)
    ):
        raise ArtifactIntegrityError("capture manifest complete flag is contradictory")

    return {
        **payload,
        "requested": requested,
        "succeeded": normalized_succeeded,
        "failures": normalized_failures,
        "status": status,
        "reviewable": reviewable,
        "complete": complete,
    }


def load_capture_manifest(root: Path) -> dict[str, Any] | None:
    try:
        root = ensure_safe_input_path(root, directory=True)
    except UnsafeOutputError as exc:
        raise ArtifactIntegrityError(str(exc)) from None
    path = root / "capture-manifest.json"
    if not path.exists() and not path.is_symlink():
        return None
    payload = _bounded_json(path, max_bytes=MAX_MANIFEST_BYTES)
    return validate_capture_manifest(root, payload)


def capture_manifest_present(root: Path) -> bool:
    """Return true for regular or dangling/symlinked manifest entries."""
    path = Path(root) / "capture-manifest.json"
    return path.exists() or path.is_symlink()


def capture_evidence(root: Path, *, allow_legacy: bool = True) -> CaptureEvidence:
    """Resolve exactly the DOM artifacts that are valid evidence for a run."""
    try:
        root = ensure_safe_input_path(Path(root), directory=True)
    except UnsafeOutputError as exc:
        raise ArtifactIntegrityError(str(exc)) from None
    in_progress = root / CAPTURE_IN_PROGRESS_FILENAME
    if in_progress.exists() or in_progress.is_symlink():
        raise ArtifactIntegrityError(
            "capture evidence is unavailable while a capture is in progress",
            reason_code="capture-in-progress",
        )
    manifest = load_capture_manifest(root)
    if manifest is not None:
        dom_paths = tuple(root / item["dom"] for item in manifest["succeeded"])
        return CaptureEvidence(
            manifest=manifest,
            dom_paths=dom_paths,
            status=str(manifest["status"]),
            reviewable=bool(manifest["reviewable"]),
            complete=bool(manifest["complete"]),
        )
    if not allow_legacy:
        raise ArtifactIntegrityError(
            "capture manifest is required for this operation",
            reason_code="missing-manifest",
        )

    dom_dir = root / "dom"
    paths = tuple(sorted(dom_dir.glob("*.json"))) if dom_dir.exists() else ()
    safe_paths: list[Path] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        safe = _safe_relative_artifact(root, relative, prefix="dom", suffix=".json")
        document = read_dom_document(safe)
        expectation = _capture_expectation(document, where="legacy")
        if expectation.get("status") == "blocked":
            raise ArtifactIntegrityError(
                "legacy capture contains diagnostic-only DOM but no manifest",
                reason_code="blocked-legacy-capture",
            )
        safe_paths.append(safe)
    return CaptureEvidence(
        manifest=None,
        dom_paths=tuple(safe_paths),
        status="legacy" if safe_paths else "failed",
        reviewable=bool(safe_paths),
        complete=False,
        legacy=True,
    )


def stage_paths(root: Path, stage: str, dom_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Map selected DOM evidence to the matching generated stage files."""
    if stage not in {"components", "analysis"}:
        raise ValueError(f"unsupported artifact stage: {stage}")
    paths: list[Path] = []
    for dom_path in dom_paths:
        candidate = root / stage / dom_path.name
        if candidate.exists():
            paths.append(
                _safe_relative_artifact(
                    root,
                    candidate.relative_to(root).as_posix(),
                    prefix=stage,
                    suffix=".json",
                )
            )
    return tuple(paths)
