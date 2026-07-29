"""Report stage: assemble the final JSON report, human-readable summary,
per-component crops, and annotated overview screenshots.

The annotated overviews are one-image-per-viewport with red boxes around P0
findings and yellow boxes around P1s — far more useful than scanning per-
component crops when triaging a screen.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from harness import __version__, _html_report
from harness import _artifacts as artifacts_mod
from harness import _safeio as safeio_mod
from harness import design_context as context_mod
from harness import rubric as rubric_mod
from harness import tokens as tokens_mod
from harness._sanitize import (
    sanitize_capture_meta,
    sanitize_component,
    sanitize_finding,
    sanitize_untrusted_text,
)

logger = logging.getLogger("keen.report")


SEVERITY_COLORS = {
    "P0": (220, 38, 38),  # red-600
    "P1": (245, 158, 11),  # amber-500
    "P2": (148, 163, 184),  # slate-400
}


def _load_capture_manifest(captures_dir: Path) -> dict[str, Any] | None:
    try:
        return artifacts_mod.load_capture_manifest(captures_dir)
    except artifacts_mod.ArtifactIntegrityError as exc:
        return {
            "requested": [],
            "succeeded": [],
            "failures": [
                {
                    "viewport": "unknown",
                    "state": "unknown",
                    "reason_code": exc.reason_code,
                    "reason": "capture evidence failed integrity validation",
                }
            ],
            "complete": False,
            "reviewable": False,
            "status": "failed",
            "_manifest_present": True,
            "integrity_error": sanitize_untrusted_text(str(exc), max_len=300),
        }


def _capture_failure_status(manifest: dict[str, Any]) -> dict[str, Any]:
    failures = manifest.get("failures")
    if not isinstance(failures, list):
        failures = []
    blocked = manifest.get("status") == "blocked" or any(
        isinstance(failure, dict)
        and (
            failure.get("diagnostic_only") is True
            or failure.get("reason_code")
            in {
                "unexpected-url",
                "missing-selector",
                "blocked-legacy-capture",
            }
        )
        for failure in failures
    )
    status = "blocked" if blocked else "failed"
    message = (
        "The requested product surface was not captured, so no design analysis or grade was issued."
        if blocked
        else "Browser capture failed before a reviewable surface was available, so no design analysis or grade was issued."
    )
    return {
        "status": status,
        "reviewable": False,
        "message": message,
        "next_actions": [
            "Inspect the diagnostic screenshot and capture-manifest.json.",
            (
                "Authenticate with --auth-steps or --interactive-auth, or declare the "
                "intended redirect with --expect-url."
            ),
            "Run Keen again against the intended product surface.",
        ],
    }


def _manifest_is_reviewable(manifest: dict[str, Any]) -> bool:
    return manifest.get("reviewable") is True


def build_capture_failure_brief(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build an agent-first stop artifact without exposing browser credentials."""
    status = _capture_failure_status(manifest)
    failures = manifest.get("failures")
    if not isinstance(failures, list):
        failures = []
    diagnostics: list[dict[str, Any]] = []
    for failure in failures[:12]:
        if not isinstance(failure, dict):
            continue
        reason_code = sanitize_untrusted_text(
            failure.get("reason_code") or "capture-failed",
            max_len=64,
        )
        reason = {
            "unexpected-url": "The browser reached a different URL than the intended review surface.",
            "missing-selector": "The expected product-surface selector was not present.",
        }.get(reason_code, "Browser capture failed before review could begin.")
        diagnostics.append(
            {
                "viewport": sanitize_untrusted_text(failure.get("viewport"), max_len=64),
                "state": sanitize_untrusted_text(failure.get("state"), max_len=64),
                "reason_code": reason_code,
                "reason": reason,
                "expected_url": sanitize_untrusted_text(
                    failure.get("expected_url"),
                    max_len=300,
                ),
                "observed_url": sanitize_untrusted_text(
                    failure.get("observed_url"),
                    max_len=300,
                ),
                "expected_selector": sanitize_untrusted_text(
                    failure.get("expected_selector"),
                    max_len=512,
                ),
                "screen_path": sanitize_untrusted_text(failure.get("screen"), max_len=300),
                "dom_path": sanitize_untrusted_text(failure.get("dom"), max_len=300),
                "diagnostic_only": bool(failure.get("diagnostic_only")),
            }
        )
    requested = manifest.get("requested")
    succeeded = manifest.get("succeeded")
    requested_count = len(requested) if isinstance(requested, list) else 0
    succeeded_count = len(succeeded) if isinstance(succeeded, list) else 0
    return {
        "version": __version__,
        "review_status": status,
        "coverage": {
            "complete": False,
            "provisional": True,
            "reviewable": False,
            "status": status["status"],
            "captures_requested": requested_count,
            "captures_complete": succeeded_count,
            "captures_succeeded": succeeded_count,
            "capture_failures": len(failures),
            "manifest_present": bool(manifest.get("_manifest_present", True)),
        },
        "grade": None,
        "damage": None,
        "grade_summary": status["message"],
        "counts": {},
        "captures": [],
        "diagnostics": diagnostics,
        "top_findings": [],
        "automated_signal_summary": {
            "band": None,
            "weighted_index": None,
            "counts": {},
            "meaning": "No automated design analysis ran because capture integrity was not established.",
        },
        "decision_contract": {
            "code_provides": "a capture-integrity stop and bounded diagnostic evidence",
            "model_decides": "how to restore legitimate access without weakening product authentication",
            "requirements": [
                "Do not critique or score the diagnostic page.",
                "Do not weaken shipped authentication solely to satisfy Keen.",
                "Prefer declarative authentication or explicit user-driven interactive authentication.",
            ],
        },
        "artifacts": {
            "capture_manifest": "capture-manifest.json",
            "diagnostic_screens": "screens/",
            "diagnostic_dom": "dom/",
        },
    }


def write_capture_failure_brief(captures_dir: Path) -> Path | None:
    """Write agent-brief.json when capture ended before review could begin."""
    manifest = _load_capture_manifest(captures_dir)
    if manifest is None or _manifest_is_reviewable(manifest):
        return None
    path = captures_dir / "agent-brief.json"
    safeio_mod.atomic_write_text(
        captures_dir,
        path,
        json.dumps(build_capture_failure_brief(manifest), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _crop_components(captures_dir: Path, components: list[dict]) -> None:
    """Crop each component's bounding box out of its source screenshot."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return  # crops are optional

    crops_dir = captures_dir / "components"
    safeio_mod.ensure_output_dir(captures_dir, crops_dir)
    captures_root = captures_dir.resolve()

    cache: dict[str, Any] = {}
    for c in components:
        rel = c.get("capture_path")
        if not rel:
            continue
        screen_path = (captures_dir / rel).resolve()
        try:
            screen_path.relative_to(captures_root)
        except ValueError:
            logger.warning(
                "blocked path traversal: %s outside %s",
                screen_path,
                captures_root,
            )
            continue
        if not screen_path.exists():
            continue
        if str(screen_path) not in cache:
            try:
                with Image.open(screen_path) as im:
                    cache[str(screen_path)] = im.convert("RGB")
            except (OSError, UnidentifiedImageError):
                logger.warning(
                    "failed to load screenshot for cropping: %s",
                    screen_path,
                    exc_info=True,
                )
                continue
        img = cache[str(screen_path)]
        scale = float(c.get("device_pixel_ratio") or 1.0)
        b = c["box"]
        x, y, w, h = b["x"] * scale, b["y"] * scale, b["w"] * scale, b["h"] * scale
        pad = 8
        x0, y0 = max(0, int(x - pad)), max(0, int(y - pad))
        x1 = min(img.width, int(x + w + pad))
        y1 = min(img.height, int(y + h + pad))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = img.crop((x0, y0, x1, y1))
        crop_name = f"{c['viewport']}-{c['state']}-{c['component_kind']}-{c['index']}.png"
        crop_path = crops_dir / crop_name
        try:
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            safeio_mod.atomic_write_bytes(captures_dir, crop_path, buffer.getvalue())
            c["crop_path"] = crop_path.relative_to(captures_dir).as_posix()
        except (OSError, ValueError):
            logger.warning(
                "failed to save crop %s",
                crop_path,
                exc_info=True,
            )
            continue


def _select_annotations(
    components: list[dict],
    top_findings: list[dict],
    *,
    per_capture_limit: int = 8,
    per_predicate_limit: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Build a bounded evidence index from the report's prioritized findings.

    Only P0/P1 findings that resolve to an exact captured component are
    eligible. Multiple findings on the same component share one marker, and a
    noisy predicate may contribute at most two markers per screen. The
    resulting structure is used by both the PNG renderer and report.html so a
    marker always has a matching, readable explanation.
    """
    components_by_key = {
        (str(component.get("capture_path") or ""), component.get("index")): component
        for component in components
        if component.get("capture_path") and component.get("index") is not None
    }
    selected_by_key: dict[tuple[str, Any], dict[str, Any]] = {}
    by_capture: dict[str, list[dict[str, Any]]] = {}
    predicate_counts: dict[tuple[str, str], int] = {}
    capture_counts: dict[str, int] = {}
    next_marker = 1

    for rank, finding in enumerate(top_findings, start=1):
        finding_id = f"finding-{rank:02d}"
        finding["finding_id"] = finding_id
        severity = str(finding.get("severity") or "P2")
        if severity not in {"P0", "P1"}:
            continue
        capture_path = str(finding.get("capture_path") or "")
        component_index = finding.get("component_index")
        key = (capture_path, component_index)
        component = components_by_key.get(key)
        if component is None:
            continue
        box = component.get("box") or {}
        viewport_width = float(
            component.get("capture_width") or component.get("viewport_width") or 0
        )
        capture_height = float(component.get("capture_height") or 0)
        left = float(box.get("x") or 0)
        right = left + float(box.get("w") or 0)
        top = float(box.get("y") or 0)
        bottom = top + float(box.get("h") or 0)
        if (
            right <= 0
            or bottom <= 0
            or (viewport_width > 0 and left >= viewport_width)
            or (capture_height > 0 and top >= capture_height)
        ):
            # A map marker must point to pixels the screenshot actually shows.
            # Keep the finding in the table, but do not create an orphan label
            # for wholly off-screen geometry.
            continue
        predicate_id = str(finding.get("predicate_id") or "finding")
        marker = selected_by_key.get(key)
        if marker is None:
            predicate_key = (capture_path, predicate_id)
            if capture_counts.get(capture_path, 0) >= per_capture_limit:
                continue
            if predicate_counts.get(predicate_key, 0) >= per_predicate_limit:
                continue
            annotation_id = f"A{next_marker:02d}"
            next_marker += 1
            marker = {
                "id": annotation_id,
                "severity": severity,
                "predicate_ids": [],
                "finding_ids": [],
                "message": str(finding.get("message") or ""),
                "component_kind": str(component.get("component_kind") or "component"),
                "component_index": component_index,
                "viewport": str(component.get("viewport") or ""),
                "state": str(component.get("state") or ""),
                "capture_path": capture_path,
                "crop_path": component.get("crop_path"),
                "box": dict(component.get("box") or {}),
                "device_pixel_ratio": float(component.get("device_pixel_ratio") or 1.0),
            }
            selected_by_key[key] = marker
            stem = Path(capture_path).stem
            by_capture.setdefault(stem, []).append(marker)
            capture_counts[capture_path] = capture_counts.get(capture_path, 0) + 1
            predicate_counts[predicate_key] = predicate_counts.get(predicate_key, 0) + 1
        elif severity == "P0":
            marker["severity"] = "P0"

        if predicate_id not in marker["predicate_ids"]:
            marker["predicate_ids"].append(predicate_id)
        marker["finding_ids"].append(finding_id)
        finding["annotation_id"] = marker["id"]

    return by_capture


def _annotate_overviews(
    captures_dir: Path,
    annotations: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    """Render self-explaining evidence maps for prioritized components.

    Returns a dict mapping viewport-state -> relative annotated path.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
    except ImportError:
        return {}

    out_dir = captures_dir / "screens"
    safeio_mod.ensure_output_dir(captures_dir, out_dir)
    captures_root = captures_dir.resolve()

    annotated_paths: dict[str, str] = {}
    for stem, markers in annotations.items():
        if not markers:
            continue
        rel = str(markers[0].get("capture_path") or "")
        if not rel:
            continue
        src = (captures_dir / rel).resolve()
        try:
            src.relative_to(captures_root)
        except ValueError:
            logger.warning("blocked path traversal: %s outside %s", src, captures_root)
            continue
        if not src.exists():
            continue
        try:
            with Image.open(src) as im:
                screenshot = im.convert("RGB").copy()
        except (OSError, UnidentifiedImageError):
            logger.warning(
                "failed to load screenshot for annotation: %s",
                src,
                exc_info=True,
            )
            continue

        scale = max(float(markers[0].get("device_pixel_ratio") or 1.0), 1.0)
        title_size = max(16, int(17 * scale))
        body_size = max(13, int(13 * scale))
        try:
            title_font = ImageFont.load_default(size=title_size)
            body_font = ImageFont.load_default(size=body_size)
        except TypeError:  # Pillow < 10 compatibility for downstream users.
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()
        top_pad = max(14, int(14 * scale))
        title_height = max(24, int(28 * scale))
        row_height = max(28, int(30 * scale))
        header_height = top_pad * 2 + title_height + row_height * len(markers)
        img = Image.new(
            "RGB", (screenshot.width, screenshot.height + header_height), (248, 246, 240)
        )
        img.paste(screenshot, (0, header_height))
        draw = ImageDraw.Draw(img)
        ink = (31, 31, 28)
        muted = (96, 91, 82)
        rule = (208, 202, 190)
        draw.text(
            (top_pad, top_pad),
            f"Keen evidence map - {len(markers)} prioritized area{'s' if len(markers) != 1 else ''}",
            fill=ink,
            font=title_font,
        )

        def fit_text(
            value: str,
            max_width: int,
            *,
            _draw: Any = draw,
            _font: Any = body_font,
        ) -> str:
            if _draw.textlength(value, font=_font) <= max_width:
                return value
            suffix = "…"
            lo, hi = 0, len(value)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _draw.textlength(value[:mid] + suffix, font=_font) <= max_width:
                    lo = mid
                else:
                    hi = mid - 1
            return value[:lo].rstrip() + suffix

        for row, marker in enumerate(markers):
            row_y = top_pad + title_height + row * row_height
            severity = str(marker.get("severity") or "P1")
            color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["P2"])
            marker_text = str(marker["id"])
            marker_w = max(
                int(44 * scale), int(draw.textlength(marker_text, font=body_font) + 18 * scale)
            )
            marker_h = max(int(21 * scale), body_size + int(8 * scale))
            draw.rounded_rectangle(
                [top_pad, row_y, top_pad + marker_w, row_y + marker_h],
                radius=max(2, int(3 * scale)),
                fill=color,
            )
            marker_ink = (255, 255, 255) if severity == "P0" else (31, 31, 28)
            draw.text(
                (top_pad + int(8 * scale), row_y + int(3 * scale)),
                marker_text,
                fill=marker_ink,
                font=body_font,
            )
            predicates = ", ".join(str(item) for item in marker.get("predicate_ids") or [])
            label = f"{severity} - {predicates} - {marker.get('message') or ''}"
            label = (
                label.replace("≥", ">=")
                .replace("≤", "<=")
                .replace("\u00d7", "x")
                .encode("ascii", errors="replace")
                .decode("ascii")
            )
            text_x = top_pad + marker_w + int(12 * scale)
            draw.text(
                (text_x, row_y + int(3 * scale)),
                fit_text(label, screenshot.width - text_x - top_pad),
                fill=muted,
                font=body_font,
            )
            draw.line(
                [
                    (top_pad, row_y + marker_h + int(4 * scale)),
                    (screenshot.width - top_pad, row_y + marker_h + int(4 * scale)),
                ],
                fill=rule,
                width=max(1, int(scale)),
            )

        for marker in markers:
            scale = float(marker.get("device_pixel_ratio") or 1.0)
            box = marker.get("box") or {}
            x0 = int(float(box.get("x", 0)) * scale)
            y0 = int(float(box.get("y", 0)) * scale) + header_height
            x1 = int((float(box.get("x", 0)) + float(box.get("w", 0))) * scale)
            y1 = int((float(box.get("y", 0)) + float(box.get("h", 0))) * scale) + header_height
            severity = str(marker.get("severity") or "P1")
            color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["P2"])
            line_width = max(2, int(2 * scale))
            for offset in range(line_width):
                draw.rectangle(
                    [x0 - offset, y0 - offset, x1 + offset, y1 + offset],
                    outline=color,
                )
            marker_text = str(marker["id"])
            marker_w = max(
                int(44 * scale), int(draw.textlength(marker_text, font=body_font) + 18 * scale)
            )
            marker_h = max(int(21 * scale), body_size + int(8 * scale))
            pill_x = min(max(0, x0), max(0, screenshot.width - marker_w))
            pill_y = max(header_height, y0 - marker_h)
            draw.rounded_rectangle(
                [pill_x, pill_y, pill_x + marker_w, pill_y + marker_h],
                radius=max(2, int(3 * scale)),
                fill=color,
            )
            marker_ink = (255, 255, 255) if severity == "P0" else (31, 31, 28)
            draw.text(
                (pill_x + int(8 * scale), pill_y + int(3 * scale)),
                marker_text,
                fill=marker_ink,
                font=body_font,
            )
        # Output path
        out_name = Path(rel).stem + "-annotated.png"
        out_path = out_dir / out_name
        try:
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            safeio_mod.atomic_write_bytes(captures_dir, out_path, buffer.getvalue())
            annotated_paths[stem] = out_path.relative_to(captures_dir).as_posix()
        except (OSError, ValueError):
            logger.warning(
                "failed to save annotated overview %s",
                out_path,
                exc_info=True,
            )
            continue
    return annotated_paths


def _analysis_integrity_report(
    captures_dir: Path,
    target_system: str | None,
    evidence: artifacts_mod.CaptureEvidence,
    exc: artifacts_mod.ArtifactIntegrityError,
) -> dict[str, Any]:
    requested = len(evidence.manifest["requested"]) if evidence.manifest is not None else 0
    succeeded = len(evidence.dom_paths)
    message = "Analysis artifacts failed integrity validation; no quality grade was issued."
    result: dict[str, Any] = {
        "version": __version__,
        "target_system": target_system,
        "review_status": {
            "status": "incomplete",
            "reviewable": False,
            "message": message,
        },
        "coverage": {
            "complete": False,
            "provisional": True,
            "reviewable": False,
            "status": "incomplete",
            "captures_requested": requested,
            "captures_complete": succeeded,
            "captures_succeeded": succeeded,
            "capture_failures": 0,
            "analysis_complete": False,
            "manifest_present": evidence.manifest is not None,
        },
        "captures": [],
        "annotated_overviews": {},
        "annotations": {},
        "grade": None,
        "damage": None,
        "score": {
            "grade": "INCOMPLETE",
            "grade_summary": message,
            "score": None,
            "counts": {},
            "provisional": True,
        },
        "top_findings": [],
        "global_findings": [],
        "tokens": {},
        "components": [],
        "design_context": None,
        "integrity_error": sanitize_untrusted_text(str(exc), max_len=300),
    }
    safeio_mod.atomic_write_text(
        captures_dir,
        captures_dir / "agent-brief.json",
        json.dumps(build_agent_brief(result), indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def compose(captures_dir: Path, target_system: str | None = None) -> dict[str, Any]:
    """Assemble report.json from analysis files in captures_dir/analysis/."""
    captures_dir = Path(captures_dir)
    manifest_at_start = _load_capture_manifest(captures_dir)
    try:
        evidence = artifacts_mod.capture_evidence(captures_dir)
    except artifacts_mod.ArtifactIntegrityError as exc:
        manifest_at_start = {
            "requested": [],
            "succeeded": [],
            "failures": [
                {
                    "viewport": "unknown",
                    "state": "unknown",
                    "reason_code": exc.reason_code,
                    "reason": "capture evidence failed integrity validation",
                }
            ],
            "complete": False,
            "reviewable": False,
            "status": "failed",
            "_manifest_present": artifacts_mod.capture_manifest_present(captures_dir),
            "integrity_error": sanitize_untrusted_text(str(exc), max_len=300),
        }
        evidence = None
    if (manifest_at_start is not None and not _manifest_is_reviewable(manifest_at_start)) or (
        evidence is not None and not evidence.reviewable and not evidence.legacy
    ):
        if manifest_at_start is None and evidence is not None:
            manifest_at_start = {
                "requested": [],
                "succeeded": [],
                "failures": [],
                "complete": False,
                "reviewable": False,
                "status": evidence.status,
                "_manifest_present": False,
            }
        if manifest_at_start is None:  # Defensive invariant.
            manifest_at_start = {
                "requested": [],
                "succeeded": [],
                "failures": [],
                "complete": False,
                "reviewable": False,
                "status": "failed",
                "_manifest_present": False,
            }
        brief = build_capture_failure_brief(manifest_at_start)
        safeio_mod.atomic_write_text(
            captures_dir,
            captures_dir / "agent-brief.json",
            json.dumps(brief, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "version": __version__,
            "target_system": target_system,
            "review_status": brief["review_status"],
            "coverage": brief["coverage"],
            "captures": [],
            "annotated_overviews": {},
            "annotations": {},
            "grade": None,
            "damage": None,
            "score": {
                "grade": "INCOMPLETE",
                "grade_summary": brief["grade_summary"],
                "score": None,
                "counts": {},
                "provisional": True,
            },
            "top_findings": [],
            "global_findings": [],
            "tokens": {},
            "components": [],
            "design_context": None,
        }
    components: list[dict] = []
    global_findings: list[dict] = []
    summaries: list[dict] = []
    captures_meta: list[dict] = []
    if evidence is None:  # Defensive invariant; invalid evidence returned above.
        raise RuntimeError("capture evidence state was lost during report composition")
    analysis_paths = artifacts_mod.stage_paths(captures_dir, "analysis", evidence.dom_paths)
    if evidence.legacy and not evidence.dom_paths:
        analysis_paths = tuple(sorted((captures_dir / "analysis").glob("*.json")))
    analysis_artifacts_complete = (
        bool(evidence.dom_paths) and len(analysis_paths) == len(evidence.dom_paths)
    ) or (evidence.legacy and not evidence.dom_paths)

    dom_documents: dict[str, dict[str, Any]] = {}
    for dom_path in evidence.dom_paths:
        try:
            dom_documents[dom_path.name] = artifacts_mod.read_dom_document(dom_path)
        except artifacts_mod.ArtifactIntegrityError as exc:
            return _analysis_integrity_report(captures_dir, target_system, evidence, exc)

    for analysis_path in analysis_paths:
        try:
            data = artifacts_mod.read_analysis_document(analysis_path)
            matching_dom = dom_documents.get(analysis_path.name)
            if matching_dom is not None:
                artifacts_mod.validate_analysis_capture_binding(
                    data,
                    matching_dom,
                    where=analysis_path.relative_to(captures_dir).as_posix(),
                )
        except artifacts_mod.ArtifactIntegrityError as exc:
            return _analysis_integrity_report(captures_dir, target_system, evidence, exc)
        for comp in data.get("components", []):
            # Defense-in-depth: even though decompose/analyze already
            # sanitize, the on-disk analysis file could have been authored
            # by hand or stale from before sanitization shipped.
            sanitize_component(comp)
            for f in comp.get("findings", []) or []:
                sanitize_finding(f)
            components.append(comp)
        for finding in data.get("global_findings", []) or []:
            sanitize_finding(finding)
            global_findings.append(finding)
        summaries.append(
            {
                "file": analysis_path.relative_to(captures_dir).as_posix(),
                **data.get("summary", {}),
            }
        )

    for dom_path in evidence.dom_paths:
        dom = dom_documents[dom_path.name]
        meta = dom.get("meta", {})
        # Page title and URL are the most attacker-controlled fields in the
        # entire pipeline — a hostile page picks them freely. Sanitize before
        # they land in report.json, where the agent will load them as
        # context.
        captures_meta.append(
            sanitize_capture_meta(
                {
                    "url": dom.get("url"),
                    "title": dom.get("title"),
                    "viewport": meta.get("viewport"),
                    "state": meta.get("state"),
                    "screen_path": meta.get("screen_path"),
                    "document_size": dom.get("documentSize"),
                    "manual_review_needed": meta.get("manual_review_needed", False),
                    "focus_coverage": dom.get("focus_coverage", {}),
                    "banner_dismissal": meta.get("banner_dismissal")
                    or {
                        "requested": False,
                        "dismissed": False,
                        "accepted_selector": None,
                        "followup_selector": None,
                    },
                    "coverage": meta.get("coverage")
                    or dom.get("coverage")
                    or {
                        "complete": not bool(meta.get("truncated") or dom.get("truncated")),
                        "reason": "legacy-truncated"
                        if bool(meta.get("truncated") or dom.get("truncated"))
                        else None,
                    },
                }
            )
        )

    flagged = [c for c in components if c.get("findings")]
    _crop_components(captures_dir, flagged)
    cfg = rubric_mod.load_config()
    analysis = {"components": components, "global_findings": global_findings}
    scoring = rubric_mod.score(analysis, config=cfg)
    top = rubric_mod.top_findings(analysis, limit=15)
    # top_findings carries finding messages which may quote component text.
    # Sanitize once more here so the report's most-prominent surface is safe.
    for f in top:
        sanitize_finding(f)
    annotations = _select_annotations(components, top)
    annotated = _annotate_overviews(captures_dir, annotations)

    if evidence.manifest is not None:
        # Recompute from the exact manifest-selected DOM. A cached token file
        # may belong to an older run and is not evidence for this report.
        tokens = tokens_mod.extract_from_captures(captures_dir) if evidence.dom_paths else {}
    else:
        # Legacy report-only directories have no manifest authority. Preserve
        # their explicit token artifact for backwards-compatible inspection.
        tokens_path = captures_dir / "tokens" / "extracted.json"
        if not tokens_path.exists():
            tokens_path = captures_dir / "tokens.json"
        tokens = (
            artifacts_mod.read_json_object(tokens_path)
            if tokens_path.exists()
            else tokens_mod.extract_from_captures(captures_dir)
        )

    dom_coverage_complete = bool(captures_meta) and all(
        bool((capture.get("coverage") or {}).get("complete", True)) for capture in captures_meta
    )
    requested_count = len(captures_meta)
    succeeded_count = len(captures_meta)
    manifest_complete: bool | None = None
    manifest_failures = 0
    manifest_reviewable: bool | None = None
    manifest_status: str | None = None
    manifest = evidence.manifest
    if manifest is not None:
        requested_count = len(manifest["requested"])
        succeeded_count = len(manifest["succeeded"])
        manifest_failures = len(manifest["failures"])
        manifest_complete = bool(manifest["complete"])
        manifest_reviewable = bool(manifest["reviewable"])
        manifest_status = str(manifest["status"])

    empty_surface = any(
        (capture.get("coverage") or {}).get("reason") == "empty-surface"
        for capture in captures_meta
    )
    no_analyzable_content = (
        bool(captures_meta)
        and analysis_artifacts_complete
        and not components
        and not global_findings
    )
    unscorable_surface = empty_surface or no_analyzable_content
    if no_analyzable_content and not empty_surface:
        for capture in captures_meta:
            capture["manual_review_needed"] = True
    coverage_complete = (
        dom_coverage_complete and analysis_artifacts_complete and not unscorable_surface
    )
    if manifest_complete is not None:
        coverage_complete = (
            coverage_complete
            and manifest_complete
            and manifest_failures == 0
            and requested_count > 0
            and succeeded_count == requested_count
            and len(captures_meta) == succeeded_count
        )
    coverage = {
        "complete": coverage_complete,
        "captures_requested": requested_count,
        "captures_complete": sum(
            1
            for capture in captures_meta
            if bool((capture.get("coverage") or {}).get("complete", True))
        ),
        "captures_succeeded": succeeded_count,
        "capture_failures": manifest_failures,
        "analysis_complete": analysis_artifacts_complete,
        "manifest_present": manifest_complete is not None,
        "provisional": not coverage_complete,
        "reviewable": (
            bool(captures_meta) and manifest_reviewable is not False and not empty_surface
        ),
        "reason": (
            "empty-surface"
            if empty_surface
            else "no-analyzable-content"
            if no_analyzable_content
            else None
        ),
        "status": (
            "complete"
            if coverage_complete
            else "failed"
            if not captures_meta
            else "incomplete"
            if empty_surface
            else "provisional"
            if no_analyzable_content
            else manifest_status
            if manifest_status in {"blocked", "failed"}
            else "provisional"
        ),
    }
    if not captures_meta or not analysis_artifacts_complete:
        scoring = {
            **scoring,
            "grade": "INCOMPLETE",
            "grade_summary": (
                "No captures were available; no quality grade was issued."
                if not captures_meta
                else "Analysis artifacts are incomplete; no quality grade was issued."
            ),
            "score": None,
            "provisional": True,
        }
    elif unscorable_surface:
        scoring = {
            **scoring,
            "grade": "INCOMPLETE",
            "grade_summary": (
                "The captured page exposed no visible review surface; no quality grade was issued."
                if empty_surface
                else "Analysis found no reviewable interface content; no quality grade was issued."
            ),
            "score": None,
            "provisional": True,
        }
    elif not coverage_complete:
        scoring = {
            **scoring,
            "grade_summary": (
                f"{scoring.get('grade_summary', '')} Partial DOM coverage; grade is provisional."
            ).strip(),
            "provisional": True,
        }

    # Convenience top-level mirrors of the most-asked-for score fields. The
    # canonical values still live under `score.*`; these are just the values
    # downstream agents look for at the top level when grading at a glance.
    grade = scoring.get("grade")
    damage = scoring.get("score")

    project_context: dict[str, Any] | None = None
    project_context_path = context_mod.find_context(captures_dir)
    if project_context_path is not None:
        try:
            payload, loaded_path = context_mod.load_context(project_context_path)
            project_context = context_mod.compact_context(payload, path=loaded_path)
        except ValueError as exc:
            project_context = {
                "artifact": str(project_context_path),
                "valid": False,
                "errors": [sanitize_untrusted_text(str(exc), max_len=500)],
            }

    if not captures_meta:
        review_status = {
            "status": "failed",
            "reviewable": False,
            "message": "No reviewable surface was captured, so no design conclusion can be issued.",
        }
    elif not analysis_artifacts_complete:
        review_status = {
            "status": "incomplete",
            "reviewable": False,
            "message": "The surface was captured, but its analysis artifacts are incomplete.",
        }
    elif unscorable_surface:
        if empty_surface:
            review_status = {
                "status": "incomplete",
                "reviewable": False,
                "message": (
                    "The page exposed no visible review surface; "
                    "verify that the app rendered before review."
                ),
            }
        else:
            review_status = {
                "status": "provisional",
                "reviewable": True,
                "message": (
                    "The screenshot is available for manual visual review, "
                    "but the DOM produced no analyzable components."
                ),
            }
    else:
        review_status = {
            "status": "ready" if coverage_complete else "provisional",
            "reviewable": True,
            "message": (
                "The requested surface was captured and is ready for evidence-led review."
                if coverage_complete
                else "The surface was captured with incomplete coverage; conclusions must remain provisional."
            ),
        }
    result = {
        "version": __version__,
        "target_system": target_system,
        "review_status": review_status,
        "coverage": coverage,
        "captures": captures_meta,
        "annotated_overviews": annotated,
        "annotations": annotations,
        "grade": grade,
        "damage": damage,
        "score": scoring,
        "top_findings": top,
        "global_findings": global_findings,
        "tokens": tokens,
        "components": components,
        "design_context": project_context,
    }

    # Emit a compact, agent-first index. The full report remains available for
    # lazy evidence lookup, but agents should not ingest every computed style.
    brief = build_agent_brief(result)
    safeio_mod.atomic_write_text(
        captures_dir,
        captures_dir / "agent-brief.json",
        json.dumps(brief, indent=2) + "\n",
        encoding="utf-8",
    )

    # Emit an HTML report alongside the JSON/markdown output. Link local image
    # assets by default to avoid duplicating full screenshots as base64.
    # Best-effort: failure to render the HTML must not block JSON emission.
    try:
        summary_md = render_summary(result)
        _html_report.write_report(result, captures_dir, summary_md, link_images=True)
    except Exception:
        logger.exception("html report emission failed")

    return result


def build_agent_brief(report: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded artifact agents should read before the full report."""
    score = report.get("score") or {}
    component_lookup = {
        (str(component.get("capture_path") or ""), component.get("index")): component
        for component in report.get("components", [])
        if isinstance(component, dict)
    }
    captures = []
    for capture in report.get("captures", []):
        captures.append(
            {
                key: capture.get(key)
                for key in (
                    "viewport",
                    "state",
                    "url",
                    "title",
                    "screen_path",
                    "document_size",
                    "manual_review_needed",
                    "banner_dismissal",
                    "coverage",
                )
            }
        )
    grouped_findings: list[dict[str, Any]] = []
    grouped_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    finding_fields = (
        "severity",
        "predicate_id",
        "component_id",
        "component_index",
        "component_kind",
        "message",
        "rule",
        "measured",
        "expected",
        "crop_path",
        "finding_id",
        "annotation_id",
        "capture_path",
        "box",
        "visible_in_capture",
    )
    for finding in report.get("top_findings", []):
        key = (
            str(finding.get("predicate_id") or ""),
            str(finding.get("component_kind") or ""),
            str(finding.get("message") or ""),
        )
        scope = {
            "viewport": finding.get("viewport"),
            "state": finding.get("state"),
        }
        existing = grouped_by_key.get(key)
        if existing is not None:
            existing["occurrences"] += 1
            if (
                scope not in existing["responsive_scopes"]
                and len(existing["responsive_scopes"]) < 6
            ):
                existing["responsive_scopes"].append(scope)
            continue
        if len(grouped_findings) >= 8:
            continue
        item = {
            field: finding.get(field) for field in finding_fields if finding.get(field) is not None
        }
        component = component_lookup.get(
            (str(finding.get("capture_path") or ""), finding.get("component_index"))
        )
        if component is not None:
            element = {
                key: component.get(key)
                for key in (
                    "index",
                    "tag",
                    "role",
                    "name",
                    "name_source",
                    "text",
                    "href",
                    "box",
                    "crop_path",
                )
                if component.get(key) is not None
            }
            if element:
                item["element"] = element
        item["judgment_required"] = True
        item["occurrences"] = 1
        item["responsive_scopes"] = [scope]
        grouped_by_key[key] = item
        grouped_findings.append(item)

    return {
        "version": report.get("version"),
        "target_system": report.get("target_system"),
        "design_context": report.get("design_context"),
        "review_status": report.get("review_status")
        or {
            "status": "ready"
            if bool((report.get("coverage") or {}).get("complete"))
            else "provisional",
            "reviewable": True,
        },
        "coverage": report.get("coverage"),
        "grade": score.get("grade"),
        "damage": score.get("score"),
        "grade_summary": score.get("grade_summary"),
        "counts": score.get("counts", {}),
        "by_component_kind": score.get("by_component_kind", {}),
        "captures": captures,
        "annotated_overviews": report.get("annotated_overviews", {}),
        "annotations": report.get("annotations", {}),
        "top_findings": grouped_findings,
        "token_diagnostics": (report.get("tokens") or {}).get("diagnostics", {}),
        "automated_signal_summary": {
            "band": score.get("grade"),
            "weighted_index": score.get("score"),
            "counts": score.get("counts", {}),
            "meaning": (
                "Deterministic candidate-signal density, not an overall visual-quality verdict."
            ),
        },
        "decision_contract": {
            "code_provides": "rendered facts, candidate rules, coverage, and stable evidence",
            "model_decides": "product relevance, user impact, final priority, and design direction",
            "requirements": [
                "Use the project design context when present.",
                "Cite finding IDs or named elements for consequential judgments.",
                "Treat automated severity and scores as risk hints, not final verdicts.",
                "State uncertainty or missing product intent instead of inventing it.",
            ],
        },
        "artifacts": {
            "summary": "summary.md",
            "full_report": "report.json",
            "html_report": "report.html",
            "components": "components/",
            "analysis": "analysis/",
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Keen review")
    lines.append("")
    lines.append("## Automated signal summary")
    lines.append("")
    sc = report.get("score", {})
    grade = sc.get("grade", "?")
    lines.append(f"**Signal band:** {grade}")
    damage = sc.get("score")
    damage_display = "not scored" if damage is None else str(damage)
    lines.append(f"**Weighted candidate index:** {damage_display} (lower is better)")
    counts = sc.get("counts", {})
    lines.append(
        f"**Findings:** P0={counts.get('P0', 0)}  "
        f"P1={counts.get('P1', 0)}  P2={counts.get('P2', 0)}"
    )
    if report.get("target_system"):
        lines.append(f"**Target system:** {report['target_system']}")
    lines.append("")
    lines.append(
        "> These are deterministic candidate signals, not an overall verdict on beauty, "
        "product quality, or release readiness. The model must judge relevance and priority."
    )
    lines.append("")

    coverage = report.get("coverage") or {}
    if coverage.get("provisional"):
        lines.append(
            "> **Partial audit:** capture coverage is incomplete. "
            "The signal summary is provisional; resolve capture limits before deciding."
        )
        lines.append("")

    lines.append("## Capture matrix")
    lines.append("")
    lines.append(f"**Captures:** {len(report.get('captures', []))}")
    for cap in report.get("captures", []):
        manual = " ⚠ manual-review-needed" if cap.get("manual_review_needed") else ""
        lines.append(
            f"- {cap.get('viewport')}/{cap.get('state')} → `{cap.get('screen_path')}`{manual}"
        )
    annotated = report.get("annotated_overviews", {})
    if annotated:
        lines.append("")
        lines.append("**Annotated overviews** (red=P0, yellow=P1, slate=P2):")
        for _stem, path in annotated.items():
            lines.append(f"- `{path}`")
    lines.append("")

    lines.append("## Candidate findings")
    lines.append("")
    top_findings = report.get("top_findings", [])
    for f in top_findings:
        crop = f" `{f['crop_path']}`" if f.get("crop_path") else ""
        lines.append(
            f"- **[{f['severity']}]** `{f['predicate_id']}` "
            f"({f['component_kind']}, {f.get('viewport', '')}/{f.get('state', '')}){crop}  "
            f"— {f['message']}"
        )
    if not top_findings:
        lines.append("_No prioritized findings were emitted._")
    lines.append("")

    by_kind = sc.get("by_component_kind", {})
    if by_kind:
        lines.append("## Issue density by component kind")
        lines.append("")
        lines.append("| Kind | Total | With findings | P0 | P1 | P2 | Density |")
        lines.append("|------|------:|--------------:|---:|---:|---:|--------:|")
        for kind, row in sorted(by_kind.items(), key=lambda x: -x[1]["density_pct"]):
            lines.append(
                f"| {kind} | {row['total']} | {row['with_findings']} | "
                f"{row['P0']} | {row['P1']} | {row['P2']} | {row['density_pct']}% |"
            )
        lines.append("")

    diag = report.get("tokens", {}).get("diagnostics", {})
    if diag:
        lines.append("## Token diagnostics")
        lines.append("")
        for k, v in diag.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_This is the human-facing fallback. The agent should read `agent-brief.json` "
        "as its sole first artifact, inspect annotated overviews for selected findings, "
        "and query `report.json` only for chosen evidence. Apply the canonical skill's "
        "screen-intent guide when intent analysis is relevant._"
    )
    return "\n".join(lines)
