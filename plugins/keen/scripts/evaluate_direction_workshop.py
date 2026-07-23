#!/usr/bin/env python3
"""Evaluate whether Keen's workshop improves a fresh agent's design handoff."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "direction-workshop-cases.json"
DIRECTION_SCHEMA = ROOT / "evals" / "direction-output.schema.json"
HANDOFF_SCHEMA = ROOT / "evals" / "fresh-handoff.schema.json"
ARMS = ("brief-only", "static-questionnaire", "adaptive-workshop", "rendered-loop")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("static", "codex", "claude"), default="static")
    parser.add_argument("--arm", choices=("all", *ARMS), default="all")
    parser.add_argument("--case")
    parser.add_argument("--max-cases", type=int, default=2)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_corpus() -> dict[str, Any]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("direction workshop corpus must use schema_version 1")
    if tuple(payload.get("arms", [])) != ARMS:
        raise ValueError("direction workshop corpus arms do not match the evaluator")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("direction workshop corpus must contain cases")
    seen: set[str] = set()
    for case in cases:
        required = {
            "id",
            "brief",
            "static_questionnaire",
            "adaptive_workshop",
            "rendered_evidence",
            "fresh_task",
            "expected_terms",
            "forbidden_terms",
        }
        if not isinstance(case, dict) or not required.issubset(case):
            raise ValueError("every direction workshop case must contain the complete contract")
        if case["id"] in seen:
            raise ValueError(f"duplicate direction workshop case: {case['id']}")
        seen.add(case["id"])
        if not case["expected_terms"] or not case["forbidden_terms"]:
            raise ValueError(f"{case['id']}: expected and forbidden terms must be non-empty")
    return payload


def arm_evidence(case: dict[str, Any], arm: str) -> str:
    if arm == "brief-only":
        return "No preference questionnaire or rendered evidence is available."
    if arm == "static-questionnaire":
        return "Static questionnaire response:\n" + case["static_questionnaire"]
    evidence = "Adaptive workshop result:\n" + case["adaptive_workshop"]
    if arm == "rendered-loop":
        evidence += "\n\nRendered critique and revision:\n" + case["rendered_evidence"]
    return evidence


def direction_prompt(case: dict[str, Any], arm: str) -> str:
    return (
        "Produce a bounded design-direction artifact for a later implementation agent. "
        "Do not call tools or invent research, content, product features, screenshots, or code facts. "
        "Name coverage gaps. Use the product brief as truth and the supplied evidence only for the "
        "experimental arm. Return only JSON matching the schema.\n\n"
        f"Product brief:\n{case['brief']}\n\n"
        f"Arm evidence:\n{arm_evidence(case, arm)}"
    )


def handoff_prompt(case: dict[str, Any], direction: dict[str, Any]) -> str:
    return (
        "You are a fresh implementation agent. You receive only the project design direction and "
        "a task. Return a concrete handoff plan as JSON. Preserve supported decisions, reject generic "
        "defaults that conflict with them, and state remaining questions. Do not call tools or claim "
        "you inspected code or renders.\n\n"
        f"Direction:\n{json.dumps(direction, indent=2)}\n\nTask:\n{case['fresh_task']}"
    )


def _parse_payload(platform: str, stdout: str, output_file: Path | None) -> dict[str, Any]:
    if platform == "codex" and output_file is not None:
        payload = json.loads(output_file.read_text(encoding="utf-8"))
    else:
        envelope = json.loads(stdout)
        payload = envelope.get("structured_output", envelope.get("result"))
        if isinstance(payload, str):
            payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("model did not return a JSON object")
    return payload


def run_model(platform: str, prompt: str, schema: Path) -> dict[str, Any]:
    executable = shutil.which(platform)
    if executable is None:
        raise RuntimeError(f"{platform} not found")
    with tempfile.TemporaryDirectory(prefix="keen-direction-eval-") as raw:
        temp = Path(raw)
        output = temp / "result.json"
        if platform == "codex":
            command = [
                executable,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                prompt,
            ]
        else:
            command = [
                executable,
                "-p",
                "--tools",
                "",
                "--output-format",
                "json",
                "--json-schema",
                schema.read_text(encoding="utf-8"),
                "--max-budget-usd",
                "0.40",
                "--no-session-persistence",
                prompt,
            ]
        completed = subprocess.run(
            command, cwd=temp, text=True, capture_output=True, timeout=240, check=False
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout).strip()[-2_000:])
        return _parse_payload(platform, completed.stdout, output if platform == "codex" else None)


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(_nonempty(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(_nonempty(item) for item in value.values())
    return value is not None


def score(
    case: dict[str, Any], direction: dict[str, Any], handoff: dict[str, Any]
) -> dict[str, Any]:
    direction_text = json.dumps(direction, ensure_ascii=False).lower()
    handoff_text = json.dumps(handoff, ensure_ascii=False).lower()
    combined = direction_text + "\n" + handoff_text
    direction_usage = dict(direction)
    direction_usage["chosen_direction"] = dict(direction.get("chosen_direction", {}))
    direction_usage["chosen_direction"].pop("avoid", None)
    handoff_usage = dict(handoff)
    handoff_usage.pop("rejected_defaults", None)
    usage_text = (
        json.dumps(direction_usage, ensure_ascii=False).lower()
        + "\n"
        + json.dumps(handoff_usage, ensure_ascii=False).lower()
    )
    expected = [term for term in case["expected_terms"] if term.lower() in combined]
    preserved = [term for term in case["expected_terms"] if term.lower() in handoff_text]
    forbidden = [term for term in case["forbidden_terms"] if term.lower() in usage_text]
    structure_points = sum(
        1
        for key in (
            "product_truth",
            "chosen_direction",
            "prototype",
            "critique",
            "durable_decisions",
        )
        if _nonempty(direction.get(key))
    )
    handoff_points = sum(
        1
        for key in (
            "understanding",
            "implementation_choices",
            "preserved_decisions",
            "rejected_defaults",
        )
        if _nonempty(handoff.get(key))
    )
    total = (
        structure_points * 4
        + handoff_points * 5
        + round(30 * len(expected) / len(case["expected_terms"]))
        + round(30 * len(preserved) / len(case["expected_terms"]))
        - 10 * len(forbidden)
    )
    return {
        "score_100": max(0, min(100, total)),
        "expected_terms_found": expected,
        "expected_terms_preserved_by_fresh_agent": preserved,
        "forbidden_terms_found": forbidden,
        "automated_score_is": "a structural smoke test; use blinded review for the outcome verdict",
    }


def run_static(corpus: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    protocol = (ROOT / "evals" / "direction-workshop-protocol.md").read_text(encoding="utf-8")
    for phrase in ("fresh agent", "blinded", "same model/version", "rendered-loop"):
        if phrase.lower() not in protocol.lower():
            errors.append(f"evaluation protocol is missing {phrase!r}")
    for schema in (DIRECTION_SCHEMA, HANDOFF_SCHEMA):
        try:
            payload = json.loads(schema.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid schema {schema.name}: {exc}")
            continue
        if payload.get("type") != "object" or not payload.get("required"):
            errors.append(f"schema lacks a closed object contract: {schema.name}")
    return {
        "platform": "static",
        "passed": not errors,
        "case_count": len(corpus["cases"]),
        "arms": list(ARMS),
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    try:
        corpus = load_corpus()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    cases = corpus["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            raise SystemExit(f"unknown case: {args.case}")
    cases = cases[: args.max_cases]
    if args.platform == "static":
        result = run_static(corpus)
    else:
        arms = ARMS if args.arm == "all" else (args.arm,)
        results = []
        for case in cases:
            for arm in arms:
                try:
                    direction = run_model(
                        args.platform, direction_prompt(case, arm), DIRECTION_SCHEMA
                    )
                    handoff = run_model(
                        args.platform, handoff_prompt(case, direction), HANDOFF_SCHEMA
                    )
                    results.append(
                        {
                            "case": case["id"],
                            "arm": arm,
                            "passed": True,
                            "score": score(case, direction, handoff),
                            "direction": direction,
                            "fresh_handoff": handoff,
                        }
                    )
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    results.append(
                        {"case": case["id"], "arm": arm, "passed": False, "error": str(exc)}
                    )
        result = {
            "platform": args.platform,
            "passed": all(item["passed"] for item in results),
            "results": results,
        }
    rendered = json.dumps(result, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
