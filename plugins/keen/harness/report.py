"""Report stage: assemble the final JSON report, human-readable summary,
per-component crops, and annotated overview screenshots.

The annotated overviews are one-image-per-viewport with red boxes around P0
findings and yellow boxes around P1s — far more useful than scanning per-
component crops when triaging a screen.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from harness import __version__, _html_report
from harness import design_context as context_mod
from harness import rubric as rubric_mod
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


def _crop_components(captures_dir: Path, components: list[dict]) -> None:
    """Crop each component's bounding box out of its source screenshot."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return  # crops are optional

    crops_dir = captures_dir / "components"
    crops_dir.mkdir(parents=True, exist_ok=True)
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
            crop.save(crop_path)
            c["crop_path"] = str(crop_path.relative_to(captures_dir))
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
    out_dir.mkdir(parents=True, exist_ok=True)
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
            img.save(out_path)
            annotated_paths[stem] = str(out_path.relative_to(captures_dir))
        except (OSError, ValueError):
            logger.warning(
                "failed to save annotated overview %s",
                out_path,
                exc_info=True,
            )
            continue
    return annotated_paths


def compose(captures_dir: Path, target_system: str | None = None) -> dict[str, Any]:
    """Assemble report.json from analysis files in captures_dir/analysis/."""
    captures_dir = Path(captures_dir)
    components: list[dict] = []
    global_findings: list[dict] = []
    summaries: list[dict] = []
    captures_meta: list[dict] = []

    for analysis_path in sorted((captures_dir / "analysis").glob("*.json")):
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
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
                "file": str(analysis_path.relative_to(captures_dir)),
                **data.get("summary", {}),
            }
        )

    for dom_path in sorted((captures_dir / "dom").glob("*.json")):
        dom = json.loads(dom_path.read_text(encoding="utf-8"))
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

    tokens_path = captures_dir / "tokens" / "extracted.json"
    if not tokens_path.exists():
        tokens_path = captures_dir / "tokens.json"  # legacy fallback
        if tokens_path.exists():
            logger.warning("using legacy tokens.json path; expected tokens dir")
    tokens = json.loads(tokens_path.read_text(encoding="utf-8")) if tokens_path.exists() else {}

    dom_coverage_complete = bool(captures_meta) and all(
        bool((capture.get("coverage") or {}).get("complete", True)) for capture in captures_meta
    )
    requested_count = len(captures_meta)
    succeeded_count = len(captures_meta)
    manifest_complete: bool | None = None
    manifest_failures = 0
    manifest_path = captures_dir / "capture-manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            requested = manifest.get("requested") if isinstance(manifest, dict) else None
            succeeded = manifest.get("succeeded") if isinstance(manifest, dict) else None
            failures = manifest.get("failures") if isinstance(manifest, dict) else None
            if not isinstance(requested, list) or not isinstance(succeeded, list):
                raise ValueError("capture manifest requested/succeeded must be lists")
            if not isinstance(failures, list):
                failures = []
            requested_count = len(requested)
            succeeded_count = len(succeeded)
            manifest_failures = len(failures)
            manifest_complete = bool(manifest.get("complete"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("capture manifest is unreadable; coverage is provisional", exc_info=True)
            manifest_complete = False

    coverage_complete = dom_coverage_complete
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
        "manifest_present": manifest_complete is not None,
        "provisional": not coverage_complete,
    }
    if not captures_meta:
        scoring = {
            **scoring,
            "grade": "INCOMPLETE",
            "grade_summary": "No captures were available; no quality grade was issued.",
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

    result = {
        "version": __version__,
        "target_system": target_system,
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
    (captures_dir / "agent-brief.json").write_text(
        json.dumps(brief, indent=2) + "\n", encoding="utf-8"
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
