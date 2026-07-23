"""Persistent project design direction for Keen workflows.

The capture engine can describe rendered facts, but it cannot know why a
product should look or behave a particular way.  ``design-context.json`` is
the small, project-owned bridge between those facts and the model's judgment.
It records the product job, chosen visual direction, reference rationale, and
decisions that future agents must preserve.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness._sanitize import sanitize_untrusted_text

SCHEMA_VERSION = 1
STAGES = ("explore", "establish", "refine", "guard")
CONTEXT_FILENAME = "design-context.json"
DIRECTION_FILENAME = "direction.md"


def context_path(target: str | Path) -> Path:
    """Resolve a project directory, ``.keen`` directory, or JSON file."""
    path = Path(target).expanduser()
    if path.suffix.lower() == ".json":
        return path
    if path.name == ".keen":
        return path / CONTEXT_FILENAME
    return path / ".keen" / CONTEXT_FILENAME


def new_context(name: str, *, stage: str = "explore") -> dict[str, Any]:
    """Return a deliberately sparse context the model and user can shape."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("project name must not be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "name": clean_name,
            "stage": stage,
            "summary": "",
            "audiences": [],
            "jobs": [],
            "primary_action": "",
        },
        "direction": {
            "qualities": [],
            "anti_qualities": [],
            "principles": [],
            "typography": {
                "display": "",
                "body": "",
                "rationale": "",
            },
            "color": {"rationale": ""},
            "density": "",
            "shape": "",
            "motion": "",
            "imagery": "",
            "icons": "",
        },
        "references": [],
        "constraints": [],
        "quality_bar": [],
        "avoid": [],
        "decisions": [],
        "baselines": [],
    }


def validate_context(payload: Any) -> list[str]:
    """Validate the stable envelope while leaving creative fields extensible."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")

    project = payload.get("project")
    if not isinstance(project, dict):
        errors.append("project must be an object")
    else:
        name = project.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("project.name must be a non-empty string")
        stage = project.get("stage")
        if stage not in STAGES:
            errors.append(f"project.stage must be one of: {', '.join(STAGES)}")
        for key in ("audiences", "jobs"):
            if not isinstance(project.get(key, []), list):
                errors.append(f"project.{key} must be an array")

    direction = payload.get("direction")
    if not isinstance(direction, dict):
        errors.append("direction must be an object")
    else:
        for key in ("qualities", "anti_qualities", "principles"):
            if not isinstance(direction.get(key, []), list):
                errors.append(f"direction.{key} must be an array")
        if not isinstance(direction.get("typography", {}), dict):
            errors.append("direction.typography must be an object")
        if not isinstance(direction.get("color", {}), dict):
            errors.append("direction.color must be an object")

    for key in ("references", "constraints", "quality_bar", "avoid", "decisions", "baselines"):
        if not isinstance(payload.get(key, []), list):
            errors.append(f"{key} must be an array")

    for index, reference in enumerate(payload.get("references", [])):
        if not isinstance(reference, dict):
            errors.append(f"references[{index}] must be an object")
            continue
        if not isinstance(reference.get("source"), str) or not reference.get("source", "").strip():
            errors.append(f"references[{index}].source must be a non-empty string")
        for key in ("take", "avoid"):
            if key in reference and not isinstance(reference[key], list):
                errors.append(f"references[{index}].{key} must be an array")

    for index, decision in enumerate(payload.get("decisions", [])):
        if not isinstance(decision, dict):
            errors.append(f"decisions[{index}] must be an object")
            continue
        if not isinstance(decision.get("summary"), str) or not decision.get("summary", "").strip():
            errors.append(f"decisions[{index}].summary must be a non-empty string")
    return errors


def load_context(target: str | Path) -> tuple[dict[str, Any], Path]:
    path = context_path(target)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"design context not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read design context {path}: {exc}") from exc
    errors = validate_context(payload)
    if errors:
        raise ValueError("invalid design context: " + "; ".join(errors))
    return payload, path


def find_context(start: str | Path) -> Path | None:
    """Find the nearest project context without walking beyond filesystem root."""
    current = Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidates = [directory / ".keen" / CONTEXT_FILENAME]
        if directory.name == ".keen":
            candidates.insert(0, directory / CONTEXT_FILENAME)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def _bounded_strings(values: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values[:limit]:
        if isinstance(value, str) and value.strip():
            result.append(sanitize_untrusted_text(value, max_len=240))
    return result


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def compact_context(payload: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    """Return the bounded project memory safe to place in an agent brief."""
    project = _object(payload.get("project"))
    direction = _object(payload.get("direction"))
    typography = _object(direction.get("typography"))
    color = _object(direction.get("color"))

    references: list[dict[str, Any]] = []
    for raw in payload.get("references", [])[:6]:
        if not isinstance(raw, dict):
            continue
        references.append(
            {
                "source": sanitize_untrusted_text(raw.get("source"), max_len=300),
                "take": _bounded_strings(raw.get("take"), limit=5),
                "avoid": _bounded_strings(raw.get("avoid"), limit=5),
                "notes": sanitize_untrusted_text(raw.get("notes"), max_len=300),
            }
        )

    decisions: list[dict[str, Any]] = []
    for raw in payload.get("decisions", [])[-8:]:
        if not isinstance(raw, dict):
            continue
        decisions.append(
            {
                key: sanitize_untrusted_text(raw.get(key), max_len=300)
                for key in ("id", "summary", "rationale", "status")
                if raw.get(key) is not None
            }
        )

    compact = {
        "schema_version": payload.get("schema_version"),
        "artifact": str(path) if path is not None else None,
        "project": {
            "name": sanitize_untrusted_text(project.get("name"), max_len=120),
            "stage": project.get("stage"),
            "summary": sanitize_untrusted_text(project.get("summary"), max_len=500),
            "audiences": _bounded_strings(project.get("audiences")),
            "jobs": _bounded_strings(project.get("jobs")),
            "primary_action": sanitize_untrusted_text(project.get("primary_action"), max_len=240),
        },
        "direction": {
            "qualities": _bounded_strings(direction.get("qualities")),
            "anti_qualities": _bounded_strings(direction.get("anti_qualities")),
            "principles": _bounded_strings(direction.get("principles")),
            "typography": {
                "display": sanitize_untrusted_text(typography.get("display"), max_len=120),
                "body": sanitize_untrusted_text(typography.get("body"), max_len=120),
                "rationale": sanitize_untrusted_text(typography.get("rationale"), max_len=300),
            },
            "color_rationale": sanitize_untrusted_text(color.get("rationale"), max_len=300),
            **{
                key: sanitize_untrusted_text(direction.get(key), max_len=300)
                for key in ("density", "shape", "motion", "imagery", "icons")
            },
        },
        "references": references,
        "constraints": _bounded_strings(payload.get("constraints"), limit=10),
        "quality_bar": _bounded_strings(payload.get("quality_bar"), limit=10),
        "avoid": _bounded_strings(payload.get("avoid"), limit=10),
        "decisions": decisions,
        "baselines": _bounded_strings(payload.get("baselines"), limit=10),
    }
    return compact


def render_direction(payload: dict[str, Any]) -> str:
    """Render the project memory as a concise, human-reviewable direction."""
    compact = compact_context(payload)
    project = compact["project"]
    direction = compact["direction"]

    def bullets(values: list[str], empty: str) -> list[str]:
        return [f"- {value}" for value in values] or [f"- _{empty}_"]

    lines = [
        f"# {project['name']} design direction",
        "",
        f"**Stage:** {project['stage']}",
        "",
        project["summary"] or "_Describe the product and why it matters._",
        "",
        "## Product job",
        "",
        "### Audiences",
        "",
        *bullets(project["audiences"], "Name the people this product serves."),
        "",
        "### Jobs",
        "",
        *bullets(project["jobs"], "Name the jobs the interface must make easier."),
        "",
        "### Primary action",
        "",
        project["primary_action"] or "_Name the action or decision the hierarchy should support._",
        "",
        "## Authored direction",
        "",
        "### Desired qualities",
        "",
        *bullets(direction["qualities"], "Choose concrete visual or emotional qualities."),
        "",
        "### Avoid",
        "",
        *bullets(
            compact["avoid"] + direction["anti_qualities"], "Record generic or off-brand defaults."
        ),
        "",
        "### Principles",
        "",
        *bullets(direction["principles"], "Record the rules future agents should preserve."),
        "",
        "## Visual language",
        "",
        f"- **Typography:** {direction['typography']['display'] or 'TBD'} / "
        f"{direction['typography']['body'] or 'TBD'}",
        f"- **Type rationale:** {direction['typography']['rationale'] or 'TBD'}",
        f"- **Color:** {direction['color_rationale'] or 'TBD'}",
        f"- **Density:** {direction['density'] or 'TBD'}",
        f"- **Shape:** {direction['shape'] or 'TBD'}",
        f"- **Motion:** {direction['motion'] or 'TBD'}",
        f"- **Imagery:** {direction['imagery'] or 'TBD'}",
        f"- **Icons:** {direction['icons'] or 'TBD'}",
        "",
        "## Quality bar",
        "",
        *bullets(compact["quality_bar"], "Define what finished and intentional means here."),
        "",
        "## References",
        "",
    ]
    if compact["references"]:
        for reference in compact["references"]:
            lines.append(f"### {reference['source']}")
            lines.append("")
            lines.append("**Take**")
            lines.append("")
            lines.extend(bullets(reference["take"], "Nothing chosen yet."))
            lines.append("")
            lines.append("**Do not copy**")
            lines.append("")
            lines.extend(bullets(reference["avoid"], "No exclusions recorded yet."))
            if reference["notes"]:
                lines.extend(["", reference["notes"]])
            lines.append("")
    else:
        lines.extend(["- _Add references with what to borrow and what not to copy._", ""])

    lines.extend(["## Decisions", ""])
    if compact["decisions"]:
        for decision in compact["decisions"]:
            summary = decision.get("summary", "Decision")
            rationale = decision.get("rationale", "No rationale recorded.")
            status = decision.get("status", "active")
            lines.append(f"- **{summary}** ({status}) — {rationale}")
    else:
        lines.append(
            "- _Record important choices and rejected alternatives as the product evolves._"
        )
    lines.append("")
    return "\n".join(lines)
