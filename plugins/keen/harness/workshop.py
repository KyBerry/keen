"""Temporary local decision workshops for Keen's model-led design workflow.

The workshop intentionally contains no model client.  An agent writes a small,
product-specific JSON spec, this module renders it on loopback, and the user's
answers return as bounded evidence.  The agent remains responsible for
interpreting those answers, prototyping, critiquing the render, and proposing
the durable context update.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from harness import design_context as context_mod
from harness._assets import asset_path
from harness._sanitize import sanitize_untrusted_text

SCHEMA_VERSION = 1
STAGES = ("product-truth", "direction", "critique")
QUESTION_KINDS = ("single", "multiple", "text")
SPECIMEN_KINDS = ("page", "type", "action", "density", "color", "interaction")
STYLE_VALUES: dict[str, tuple[str, ...]] = {
    "display_font": ("serif", "grotesk", "humanist", "mono"),
    "body_font": ("serif", "grotesk", "humanist", "mono"),
    "scale": ("quiet", "balanced", "dramatic"),
    "density": ("compact", "comfortable", "airy"),
    "palette": ("ink", "blueprint", "warm", "signal", "forest"),
    "corners": ("square", "soft", "round"),
    "alignment": ("left", "split", "center"),
    "depth": ("flat", "lined", "raised"),
    "motion": ("still", "measured", "expressive"),
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_MAX_BODY_BYTES = 96 * 1024


class WorkshopError(ValueError):
    """Raised for invalid workshop artifacts or unsafe output targets."""


def _is_string(value: Any, *, maximum: int, allow_empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= maximum and (allow_empty or bool(value.strip()))


def _validate_string_list(
    value: Any,
    path: str,
    errors: list[str],
    *,
    limit: int,
    item_maximum: int,
) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return
    if len(value) > limit:
        errors.append(f"{path} must contain at most {limit} items")
    for index, item in enumerate(value):
        if not _is_string(item, maximum=item_maximum):
            errors.append(
                f"{path}[{index}] must be a non-empty string up to {item_maximum} characters"
            )


def _reject_unknown(value: dict[str, Any], allowed: set[str], path: str, errors: list[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{path} has unknown keys: {', '.join(unknown)}")


def validate_spec(payload: Any) -> list[str]:
    """Validate the closed, render-safe workshop spec contract."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    _reject_unknown(payload, {"schema_version", "session", "context", "questions"}, "root", errors)
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")

    session = payload.get("session")
    if not isinstance(session, dict):
        errors.append("session must be an object")
    else:
        _reject_unknown(
            session,
            {"id", "stage", "title", "intro", "submit_label", "eyebrow"},
            "session",
            errors,
        )
        session_id = session.get("id")
        if not isinstance(session_id, str) or _ID_RE.fullmatch(session_id) is None:
            errors.append(
                "session.id must use 1-80 lowercase letters, digits, underscores, or hyphens"
            )
        if session.get("stage") not in STAGES:
            errors.append(f"session.stage must be one of: {', '.join(STAGES)}")
        for key, maximum in (("title", 120), ("intro", 500), ("submit_label", 80)):
            if not _is_string(session.get(key), maximum=maximum):
                errors.append(
                    f"session.{key} must be a non-empty string up to {maximum} characters"
                )
        eyebrow = session.get("eyebrow", "")
        if not _is_string(eyebrow, maximum=80, allow_empty=True):
            errors.append("session.eyebrow must be a string up to 80 characters")

    context = payload.get("context")
    if not isinstance(context, dict):
        errors.append("context must be an object")
    else:
        _reject_unknown(
            context,
            {"product", "product_job", "audience", "primary_action", "constraints"},
            "context",
            errors,
        )
        for key, maximum in (
            ("product", 120),
            ("product_job", 500),
            ("audience", 400),
            ("primary_action", 240),
        ):
            if not _is_string(context.get(key), maximum=maximum):
                errors.append(
                    f"context.{key} must be a non-empty string up to {maximum} characters"
                )
        _validate_string_list(
            context.get("constraints"), "context.constraints", errors, limit=10, item_maximum=240
        )

    questions = payload.get("questions")
    if not isinstance(questions, list):
        errors.append("questions must be an array")
        return errors
    if not 1 <= len(questions) <= 4:
        errors.append("questions must contain between 1 and 4 items")

    seen_questions: set[str] = set()
    for index, question in enumerate(questions):
        prefix = f"questions[{index}]"
        if not isinstance(question, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _reject_unknown(
            question,
            {"id", "kind", "prompt", "help", "required", "allow_custom", "options"},
            prefix,
            errors,
        )
        question_id = question.get("id")
        if not isinstance(question_id, str) or _ID_RE.fullmatch(question_id) is None:
            errors.append(f"{prefix}.id must be a safe lowercase identifier")
        elif question_id in seen_questions:
            errors.append(f"{prefix}.id is duplicated")
        else:
            seen_questions.add(question_id)
        kind = question.get("kind")
        if kind not in QUESTION_KINDS:
            errors.append(f"{prefix}.kind must be one of: {', '.join(QUESTION_KINDS)}")
        for key, maximum in (("prompt", 240), ("help", 500)):
            if not _is_string(question.get(key), maximum=maximum):
                errors.append(
                    f"{prefix}.{key} must be a non-empty string up to {maximum} characters"
                )
        if not isinstance(question.get("required"), bool):
            errors.append(f"{prefix}.required must be a boolean")
        if not isinstance(question.get("allow_custom"), bool):
            errors.append(f"{prefix}.allow_custom must be a boolean")

        options = question.get("options")
        if kind == "text":
            if options not in (None, []):
                errors.append(f"{prefix}.options must be empty for a text question")
            continue
        if not isinstance(options, list) or not 2 <= len(options) <= 3:
            errors.append(f"{prefix}.options must contain 2 or 3 meaningful alternatives")
            continue
        seen_options: set[str] = set()
        for option_index, option in enumerate(options):
            option_prefix = f"{prefix}.options[{option_index}]"
            if not isinstance(option, dict):
                errors.append(f"{option_prefix} must be an object")
                continue
            _reject_unknown(
                option,
                {"id", "label", "thesis", "fit", "change", "risk", "cost", "specimen"},
                option_prefix,
                errors,
            )
            option_id = option.get("id")
            if not isinstance(option_id, str) or _ID_RE.fullmatch(option_id) is None:
                errors.append(f"{option_prefix}.id must be a safe lowercase identifier")
            elif option_id in seen_options:
                errors.append(f"{option_prefix}.id is duplicated within the question")
            else:
                seen_options.add(option_id)
            for key, maximum in (
                ("label", 80),
                ("thesis", 220),
                ("fit", 320),
                ("change", 320),
                ("risk", 260),
                ("cost", 160),
            ):
                if not _is_string(option.get(key), maximum=maximum):
                    errors.append(
                        f"{option_prefix}.{key} must be a non-empty string up to {maximum} characters"
                    )
            specimen = option.get("specimen")
            if not isinstance(specimen, dict):
                errors.append(f"{option_prefix}.specimen must be an object")
                continue
            _reject_unknown(
                specimen,
                {
                    "kind",
                    "kicker",
                    "title",
                    "body",
                    "primary_action",
                    "secondary_action",
                    "details",
                    "annotation",
                    "style",
                },
                f"{option_prefix}.specimen",
                errors,
            )
            if specimen.get("kind") not in SPECIMEN_KINDS:
                errors.append(
                    f"{option_prefix}.specimen.kind must be one of: {', '.join(SPECIMEN_KINDS)}"
                )
            for key, maximum in (
                ("kicker", 80),
                ("title", 160),
                ("body", 360),
                ("primary_action", 80),
                ("secondary_action", 80),
                ("annotation", 160),
            ):
                if not _is_string(specimen.get(key, ""), maximum=maximum, allow_empty=True):
                    errors.append(
                        f"{option_prefix}.specimen.{key} must be a string up to {maximum} characters"
                    )
            _validate_string_list(
                specimen.get("details", []),
                f"{option_prefix}.specimen.details",
                errors,
                limit=4,
                item_maximum=100,
            )
            style = specimen.get("style")
            if not isinstance(style, dict):
                errors.append(f"{option_prefix}.specimen.style must be an object")
            else:
                unknown_style = sorted(set(style) - set(STYLE_VALUES))
                if unknown_style:
                    errors.append(
                        f"{option_prefix}.specimen.style has unknown keys: {', '.join(unknown_style)}"
                    )
                for key, allowed in STYLE_VALUES.items():
                    if style.get(key) not in allowed:
                        errors.append(
                            f"{option_prefix}.specimen.style.{key} must be one of: {', '.join(allowed)}"
                        )
    return errors


def load_spec(path: str | Path) -> tuple[dict[str, Any], Path]:
    resolved = Path(path).expanduser()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkshopError(f"workshop spec not found: {resolved}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkshopError(f"cannot read workshop spec {resolved}: {exc}") from exc
    errors = validate_spec(payload)
    if errors:
        raise WorkshopError("invalid workshop spec: " + "; ".join(errors))
    return payload, resolved


def validate_response(payload: Any, spec: dict[str, Any]) -> list[str]:
    """Validate a submitted response against the exact spec that rendered it."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    _reject_unknown(
        payload,
        {"schema_version", "session_id", "submitted_at", "answers"},
        "root",
        errors,
    )
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")
    session_id = spec["session"]["id"]
    if payload.get("session_id") != session_id:
        errors.append("session_id does not match the active workshop")
    answers = payload.get("answers")
    if not isinstance(answers, list):
        return [*errors, "answers must be an array"]
    if len(answers) > len(spec["questions"]):
        errors.append("answers contains more entries than the workshop")

    questions = {question["id"]: question for question in spec["questions"]}
    seen: set[str] = set()
    for index, answer in enumerate(answers):
        prefix = f"answers[{index}]"
        if not isinstance(answer, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _reject_unknown(
            answer,
            {"question_id", "selected", "custom", "notes"},
            prefix,
            errors,
        )
        question_id = answer.get("question_id")
        if not isinstance(question_id, str) or question_id not in questions:
            errors.append(f"{prefix}.question_id is not part of this workshop")
            continue
        if question_id in seen:
            errors.append(f"{prefix}.question_id is duplicated")
            continue
        seen.add(question_id)
        question = questions[question_id]
        selected = answer.get("selected")
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            errors.append(f"{prefix}.selected must be an array of option ids")
            selected = []
        valid_options = {option["id"] for option in question.get("options", [])}
        invalid = sorted(set(selected) - valid_options)
        if invalid:
            errors.append(f"{prefix}.selected contains unknown options: {', '.join(invalid)}")
        if len(selected) != len(set(selected)):
            errors.append(f"{prefix}.selected must not contain duplicates")
        if question["kind"] == "single" and len(selected) > 1:
            errors.append(f"{prefix}.selected must contain at most one option")
        if question["kind"] == "text" and selected:
            errors.append(f"{prefix}.selected must be empty for a text question")
        custom = answer.get("custom", "")
        notes = answer.get("notes", "")
        if not _is_string(custom, maximum=1_200, allow_empty=True):
            errors.append(f"{prefix}.custom must be a string up to 1200 characters")
            custom = ""
        if not _is_string(notes, maximum=800, allow_empty=True):
            errors.append(f"{prefix}.notes must be a string up to 800 characters")
        if custom.strip() and not question["allow_custom"] and question["kind"] != "text":
            errors.append(f"{prefix}.custom is not allowed for this question")
        if question["required"] and not selected and not custom.strip():
            errors.append(f"{prefix} requires a selection or custom response")

    missing = [
        question["id"]
        for question in spec["questions"]
        if question["required"] and question["id"] not in seen
    ]
    if missing:
        errors.append("missing required answers: " + ", ".join(missing))
    return errors


def load_response(path: str | Path, spec: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    resolved = Path(path).expanduser()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkshopError(f"workshop response not found: {resolved}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkshopError(f"cannot read workshop response {resolved}: {exc}") from exc
    errors = validate_response(payload, spec)
    if errors:
        raise WorkshopError("invalid workshop response: " + "; ".join(errors))
    return payload, resolved


def _clean(value: Any, maximum: int) -> str:
    return sanitize_untrusted_text(value, max_len=maximum)


def summarize_response(spec: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Expand selected ids into a bounded, model-facing decision handoff."""
    answers_by_id = {answer["question_id"]: answer for answer in response["answers"]}
    summarized: list[dict[str, Any]] = []
    for question in spec["questions"]:
        answer = answers_by_id.get(question["id"], {"selected": [], "custom": "", "notes": ""})
        selected_ids = set(answer.get("selected", []))
        selections = []
        for option in question.get("options", []):
            if option["id"] not in selected_ids:
                continue
            selections.append(
                {
                    "id": option["id"],
                    "label": _clean(option["label"], 80),
                    "thesis": _clean(option["thesis"], 220),
                    "fit": _clean(option["fit"], 320),
                    "change": _clean(option["change"], 320),
                    "risk": _clean(option["risk"], 260),
                    "cost": _clean(option["cost"], 160),
                }
            )
        summarized.append(
            {
                "question_id": question["id"],
                "prompt": _clean(question["prompt"], 240),
                "selections": selections,
                "custom": _clean(answer.get("custom", ""), 1_200),
                "notes": _clean(answer.get("notes", ""), 800),
            }
        )
    context = spec["context"]
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": "keen-direction-workshop-result",
        "session_id": spec["session"]["id"],
        "stage": spec["session"]["stage"],
        "submitted_at": response.get("submitted_at"),
        "product_truth": {
            "product": _clean(context["product"], 120),
            "product_job": _clean(context["product_job"], 500),
            "audience": _clean(context["audience"], 400),
            "primary_action": _clean(context["primary_action"], 240),
            "constraints": [_clean(item, 240) for item in context["constraints"][:10]],
        },
        "answers": summarized,
        "agent_contract": {
            "treat_as": "user evidence, not an automatic design decision",
            "next": (
                "Interpret the choices, make the smallest useful prototype, capture relevant "
                "viewports and states, critique the render, then either run the next workshop "
                "round or propose a durable promotion."
            ),
            "do_not": (
                "Promote preferences that were not tested, invent brand evidence, or retain "
                "the temporary workshop spec and response as project memory."
            ),
        },
    }


def validate_promotion(payload: Any) -> list[str]:
    """Validate the narrow artifact an agent may promote after rendered critique."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    _reject_unknown(
        payload,
        {
            "schema_version",
            "source_session_id",
            "project",
            "direction",
            "constraints",
            "quality_bar",
            "avoid",
            "baselines",
            "references",
            "decision",
        },
        "root",
        errors,
    )
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")
    source_session_id = payload.get("source_session_id")
    if not isinstance(source_session_id, str) or _ID_RE.fullmatch(source_session_id) is None:
        errors.append("source_session_id must be a safe lowercase identifier")
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        errors.append("decision must be an object")
    else:
        _reject_unknown(decision, {"id", "summary", "rationale", "status"}, "decision", errors)
        for key, maximum in (("summary", 300), ("rationale", 500)):
            if not _is_string(decision.get(key), maximum=maximum):
                errors.append(
                    f"decision.{key} must be a non-empty string up to {maximum} characters"
                )
        if decision.get("status") not in ("active", "trial", "superseded"):
            errors.append("decision.status must be active, trial, or superseded")
        decision_id = decision.get("id", "")
        if decision_id and (
            not isinstance(decision_id, str) or _ID_RE.fullmatch(decision_id) is None
        ):
            errors.append("decision.id must be empty or a safe lowercase identifier")

    project = payload.get("project", {})
    if not isinstance(project, dict):
        errors.append("project must be an object when present")
    else:
        unknown = sorted(set(project) - {"summary", "audiences", "jobs", "primary_action", "stage"})
        if unknown:
            errors.append("project has unknown keys: " + ", ".join(unknown))
        if "stage" in project and project["stage"] not in context_mod.STAGES:
            errors.append(f"project.stage must be one of: {', '.join(context_mod.STAGES)}")
        for key, maximum in (("summary", 500), ("primary_action", 240)):
            if key in project and not _is_string(project[key], maximum=maximum):
                errors.append(
                    f"project.{key} must be a non-empty string up to {maximum} characters"
                )
        for key in ("audiences", "jobs"):
            if key in project:
                _validate_string_list(
                    project[key], f"project.{key}", errors, limit=8, item_maximum=240
                )

    direction = payload.get("direction", {})
    if not isinstance(direction, dict):
        errors.append("direction must be an object when present")
    else:
        allowed_direction = {
            "qualities",
            "anti_qualities",
            "principles",
            "typography",
            "color",
            "density",
            "shape",
            "motion",
            "imagery",
            "icons",
        }
        unknown = sorted(set(direction) - allowed_direction)
        if unknown:
            errors.append("direction has unknown keys: " + ", ".join(unknown))
        for key in ("qualities", "anti_qualities", "principles"):
            if key in direction:
                _validate_string_list(
                    direction[key], f"direction.{key}", errors, limit=8, item_maximum=240
                )
        for key, fields in (
            ("typography", {"display": 120, "body": 120, "rationale": 300}),
            ("color", {"rationale": 300}),
        ):
            if key not in direction:
                continue
            value = direction[key]
            if not isinstance(value, dict):
                errors.append(f"direction.{key} must be an object")
                continue
            unknown_nested = sorted(set(value) - set(fields))
            if unknown_nested:
                errors.append(f"direction.{key} has unknown keys: {', '.join(unknown_nested)}")
            for field, maximum in fields.items():
                if field in value and not _is_string(value[field], maximum=maximum):
                    errors.append(
                        f"direction.{key}.{field} must be a non-empty string up to {maximum} characters"
                    )
        for key in ("density", "shape", "motion", "imagery", "icons"):
            if key in direction and not _is_string(direction[key], maximum=300):
                errors.append(f"direction.{key} must be a non-empty string up to 300 characters")

    for key in ("constraints", "quality_bar", "avoid", "baselines"):
        if key in payload:
            _validate_string_list(payload[key], key, errors, limit=12, item_maximum=300)
    references = payload.get("references", [])
    if not isinstance(references, list):
        errors.append("references must be an array")
    elif len(references) > 6:
        errors.append("references must contain at most 6 items")
    else:
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                errors.append(f"references[{index}] must be an object")
                continue
            if sorted(set(reference) - {"source", "take", "avoid", "notes"}):
                errors.append(f"references[{index}] has unknown keys")
            if not _is_string(reference.get("source"), maximum=300):
                errors.append(f"references[{index}].source must be a non-empty string")
            for key in ("take", "avoid"):
                _validate_string_list(
                    reference.get(key, []),
                    f"references[{index}].{key}",
                    errors,
                    limit=5,
                    item_maximum=240,
                )
            if "notes" in reference and not _is_string(
                reference["notes"], maximum=300, allow_empty=True
            ):
                errors.append(f"references[{index}].notes must be a string up to 300 characters")
    return errors


def _atomic_write(path: Path, text: str) -> None:
    if path.is_symlink():
        raise WorkshopError(f"refusing symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
        # Workshop readiness URLs contain the bearer token, and responses can
        # contain private product decisions. Keep both owner-only even when a
        # permissive process umask is in effect.
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def promote_context(target: str | Path, promotion: dict[str, Any]) -> tuple[Path, Path]:
    """Apply an agent-authored, rendered-and-reviewed decision to project memory."""
    errors = validate_promotion(promotion)
    if errors:
        raise WorkshopError("invalid workshop promotion: " + "; ".join(errors))
    payload, path = context_mod.load_context(target)
    if path.is_symlink():
        raise WorkshopError(f"refusing symlink design context: {path}")

    project_update = promotion.get("project", {})
    for key, value in project_update.items():
        payload["project"][key] = value
    direction_update = promotion.get("direction", {})
    for key, value in direction_update.items():
        if key in {"typography", "color"}:
            payload["direction"].setdefault(key, {}).update(value)
        else:
            payload["direction"][key] = value

    for key in ("constraints", "quality_bar", "avoid", "baselines"):
        existing = payload.setdefault(key, [])
        for value in promotion.get(key, []):
            if value not in existing:
                existing.append(value)
    references = payload.setdefault("references", [])
    for reference in promotion.get("references", []):
        if reference not in references:
            references.append(reference)

    decision = dict(promotion["decision"])
    decision["source_session_id"] = promotion["source_session_id"]
    decisions = payload.setdefault("decisions", [])
    decision_id = decision.get("id")
    if decision_id:
        decisions[:] = [
            item
            for item in decisions
            if not isinstance(item, dict) or item.get("id") != decision_id
        ]
    decisions.append(decision)
    context_errors = context_mod.validate_context(payload)
    if context_errors:
        raise WorkshopError(
            "promotion produced invalid design context: " + "; ".join(context_errors)
        )

    direction_path = path.parent / context_mod.DIRECTION_FILENAME
    if direction_path.is_symlink():
        raise WorkshopError(f"refusing symlink direction output: {direction_path}")
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    _atomic_write(direction_path, context_mod.render_direction(payload))
    return path, direction_path


def _safe_output(path: str | Path, *, overwrite: bool) -> Path:
    resolved = Path(path).expanduser()
    if resolved.is_symlink():
        raise WorkshopError(f"refusing symlink output: {resolved}")
    if resolved.exists() and not overwrite:
        raise WorkshopError(f"output already exists: {resolved} (pass --overwrite to replace it)")
    if resolved.exists() and not resolved.is_file():
        raise WorkshopError(f"output is not a file: {resolved}")
    return resolved


def _loopback_host(host_header: str) -> bool:
    host = host_header.strip().lower()
    hostname = host.split("]", 1)[0] + "]" if host.startswith("[") else host.split(":", 1)[0]
    return hostname in {"127.0.0.1", "localhost", "[::1]"}


def _handler_factory(
    *,
    spec: dict[str, Any],
    token: str,
    response_path: Path,
    submitted: threading.Event,
) -> type[BaseHTTPRequestHandler]:
    assets = {
        "/": (asset_path("workshop", "index.html"), "text/html; charset=utf-8"),
        "/assets/workshop.css": (
            asset_path("workshop", "workshop.css"),
            "text/css; charset=utf-8",
        ),
        "/assets/workshop.js": (
            asset_path("workshop", "workshop.js"),
            "text/javascript; charset=utf-8",
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "KeenWorkshop/1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _headers(self, status: HTTPStatus, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
            )

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, parsed_query: dict[str, list[str]] | None = None) -> bool:
            supplied = self.headers.get("X-Keen-Workshop-Token", "")
            if parsed_query is not None and not supplied:
                supplied = parsed_query.get("token", [""])[0]
            return secrets.compare_digest(supplied, token)

        def _check_host(self) -> bool:
            if _loopback_host(self.headers.get("Host", "")):
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "loopback host required"})
            return False

        def do_GET(self) -> None:
            if not self._check_host():
                return
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/spec":
                if not self._authorized(query):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "invalid session token"})
                    return
                self._json(HTTPStatus.OK, spec)
                return
            if parsed.path == "/api/status":
                if not self._authorized(query):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "invalid session token"})
                    return
                self._json(HTTPStatus.OK, {"submitted": submitted.is_set()})
                return
            if parsed.path == "/favicon.ico":
                self._headers(HTTPStatus.NO_CONTENT, "image/x-icon")
                self.end_headers()
                return
            asset = assets.get(parsed.path)
            if asset is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if parsed.path == "/" and not self._authorized(query):
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid session token"})
                return
            path, content_type = asset
            try:
                body = path.read_bytes()
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "workshop asset missing"})
                return
            self._headers(HTTPStatus.OK, content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not self._check_host():
                return
            parsed = urlsplit(self.path)
            if parsed.path != "/api/submit":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid session token"})
                return
            if submitted.is_set():
                self._json(HTTPStatus.CONFLICT, {"error": "workshop already submitted"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json required"}
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > _MAX_BODY_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid request size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            errors = validate_response(payload, spec)
            if errors:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "invalid response", "details": errors},
                )
                return
            payload["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                _atomic_write(response_path, json.dumps(payload, indent=2) + "\n")
            except (OSError, WorkshopError):
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "could not write response"})
                return
            self._json(HTTPStatus.CREATED, {"ok": True})
            submitted.set()

    return Handler


def serve(
    spec: dict[str, Any],
    *,
    response_path: str | Path,
    port: int = 0,
    timeout_seconds: float = 1_800,
    open_browser: bool = True,
    ready_path: str | Path | None = None,
    overwrite: bool = False,
    on_ready: Callable[[str], None] | None = None,
) -> Path:
    """Serve one temporary workshop round and block until submission or timeout."""
    errors = validate_spec(spec)
    if errors:
        raise WorkshopError("invalid workshop spec: " + "; ".join(errors))
    if not 0 <= port <= 65_535:
        raise WorkshopError("port must be between 0 and 65535")
    if timeout_seconds <= 0:
        raise WorkshopError("timeout must be greater than zero")
    destination = _safe_output(response_path, overwrite=overwrite)
    ready_destination = _safe_output(ready_path, overwrite=overwrite) if ready_path else None
    token = secrets.token_urlsafe(32)
    submitted = threading.Event()
    handler = _handler_factory(
        spec=spec,
        token=token,
        response_path=destination,
        submitted=submitted,
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.timeout = 0.25
    actual_port = int(server.server_address[1])
    url = f"http://127.0.0.1:{actual_port}/?token={token}"
    if ready_destination is not None:
        _atomic_write(
            ready_destination,
            json.dumps(
                {"url": url, "port": actual_port, "session_id": spec["session"]["id"]}, indent=2
            )
            + "\n",
        )
    if on_ready is not None:
        on_ready(url)
    if open_browser:
        webbrowser.open(url, new=2)
    deadline = time.monotonic() + timeout_seconds
    try:
        while not submitted.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not submitted.is_set():
        raise WorkshopError(f"workshop timed out after {timeout_seconds:g} seconds")
    return destination
