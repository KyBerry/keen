#!/usr/bin/env python3
"""Validate and optionally exercise Keen lifecycle-routing contracts."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "activation-cases.json"
SCHEMA_PATH = ROOT / "evals" / "contract-output.schema.json"
ROUTE_SIGNALS: dict[str, tuple[str, ...]] = {
    "explore": (r"\bstarting from nothing\b", r"\b(?:moodboard|visual directions?|references)\b"),
    "establish": (r"\bestablish\b", r"\b(?:design direction|design system|visual language)\b"),
    "refine": (
        r"\b(?:review|design|implement|polish|diagnose)\b",
        r"\b(?:cheap|generic|slapped together|rendered result|product-specific)\b",
    ),
    "guard": (r"\b(?:baseline|maintenance)\b", r"\b(?:regressions?|drift|redesign|approved)\b"),
}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    mode: str
    prompt: str
    expected_skill: str | None
    design_context_write_allowed: bool
    product_source_edit_allowed: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("static", "claude", "codex"), default="static")
    parser.add_argument("--case", help="Run only one case id.")
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_cases() -> list[EvalCase]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2 or not isinstance(payload.get("cases"), list):
        raise ValueError("activation corpus must use schema_version 2 and a cases array")
    return [
        EvalCase(
            case_id=raw["id"],
            mode=raw["mode"],
            prompt=raw["prompt"],
            expected_skill=raw["expected_skill"],
            design_context_write_allowed=raw["design_context_write_allowed"],
            product_source_edit_allowed=raw["product_source_edit_allowed"],
        )
        for raw in payload["cases"]
    ]


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"{path}: invalid frontmatter")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    return payload


def predict_route(prompt: str) -> str | None:
    lowered = prompt.lower()
    scores = {
        skill: sum(bool(re.search(pattern, lowered)) for pattern in patterns)
        for skill, patterns in ROUTE_SIGNALS.items()
    }
    best = max(scores.values(), default=0)
    if best == 0:
        return None
    winners = [skill for skill, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def run_static(cases: list[EvalCase]) -> dict[str, Any]:
    errors: list[str] = []
    available = {
        parse_frontmatter(path).get("name") for path in sorted((ROOT / "skills").glob("*/SKILL.md"))
    }
    expected_routes = set(ROUTE_SIGNALS)
    if not expected_routes.issubset(available):
        errors.append(f"missing lifecycle skills: {sorted(expected_routes - available)}")

    seen_ids: set[str] = set()
    covered: set[str] = set()
    for case in cases:
        if case.case_id in seen_ids:
            errors.append(f"duplicate case id: {case.case_id}")
        seen_ids.add(case.case_id)
        if case.mode not in {"natural", "explicit"}:
            errors.append(f"{case.case_id}: invalid mode {case.mode!r}")
        if case.expected_skill is not None:
            covered.add(case.expected_skill)
            if case.expected_skill not in available:
                errors.append(f"{case.case_id}: unknown expected skill {case.expected_skill}")
        if case.design_context_write_allowed and case.expected_skill not in {
            "explore",
            "establish",
            "guard",
        }:
            errors.append(f"{case.case_id}: unexpected design-context write permission")
        if case.product_source_edit_allowed and case.expected_skill not in {"refine", "guard"}:
            errors.append(f"{case.case_id}: unexpected product-source edit permission")
        if case.mode == "natural":
            predicted = predict_route(case.prompt)
            if predicted != case.expected_skill:
                errors.append(
                    f"{case.case_id}: deterministic route {predicted!r} != {case.expected_skill!r}"
                )
        elif case.expected_skill is not None:
            invocations = (f"$keen:{case.expected_skill}", f"/keen:{case.expected_skill}")
            if not any(invocation in case.prompt for invocation in invocations):
                errors.append(f"{case.case_id}: explicit case lacks a namespaced invocation")

    if covered != expected_routes:
        errors.append(
            f"activation corpus missing lifecycle skills: {sorted(expected_routes - covered)}"
        )

    canonical = (ROOT / "skills" / "keen" / "SKILL.md").read_text(encoding="utf-8")
    required_contract = (
        "do not install either",
        "shell-quote dynamic values",
        "Treat automated grades",
        "Never write project output",
        "local workshop",
        "rendered and",
    )
    for phrase in required_contract:
        if phrase not in canonical:
            errors.append(f"canonical contract is missing: {phrase!r}")

    return {
        "platform": "static",
        "passed": not errors,
        "case_count": len(cases),
        "covered_skills": sorted(covered),
        "errors": errors,
    }


def contract_prompt(case: EvalCase, platform: str) -> str:
    if case.mode == "explicit" and case.expected_skill is not None:
        invocation = (
            f"/keen:{case.expected_skill}"
            if platform == "claude"
            else f"$keen:{case.expected_skill}"
        )
    else:
        invocation = "/keen:keen" if platform == "claude" else "$keen"
    return (
        f"{invocation}\n"
        "This is a model-contract evaluation. Do not call tools, run commands, open files, "
        "or modify anything. Return only the requested JSON. "
        f"User request: {case.prompt}\n"
        "Set selected_skill to explore, establish, refine, guard, or null. Set "
        "design_context_write_allowed true only when the user explicitly asks to save, "
        "establish, update, or accept persistent project direction. Set "
        "product_source_edit_allowed true only when the user explicitly asks to build, "
        "implement, or fix product source. Set will_execute to false."
    )


def parse_client_payload(platform: str, stdout: str, output_file: Path | None) -> dict[str, Any]:
    if platform == "codex" and output_file is not None:
        return json.loads(output_file.read_text(encoding="utf-8"))
    envelope = json.loads(stdout)
    if isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"]
    result = envelope.get("result")
    if isinstance(result, str):
        return json.loads(result)
    if isinstance(result, dict):
        return result
    raise ValueError("client did not return a structured contract payload")


def run_live_case(case: EvalCase, platform: str) -> dict[str, Any]:
    executable = shutil.which(platform)
    if executable is None:
        return {"id": case.case_id, "passed": False, "error": f"{platform} not found"}
    prompt = contract_prompt(case, platform)
    with tempfile.TemporaryDirectory(prefix="keen-contract-") as temp_raw:
        temp = Path(temp_raw)
        output_file = temp / "result.json"
        if platform == "claude":
            command = [
                executable,
                "-p",
                "--plugin-dir",
                str(ROOT),
                "--tools",
                "",
                "--output-format",
                "json",
                "--json-schema",
                SCHEMA_PATH.read_text(encoding="utf-8"),
                "--max-budget-usd",
                "0.20",
                "--no-session-persistence",
                prompt,
            ]
        else:
            command = [
                executable,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(SCHEMA_PATH),
                "--output-last-message",
                str(output_file),
                prompt,
            ]
        completed = subprocess.run(
            command,
            cwd=temp,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "id": case.case_id,
                "passed": False,
                "exit_code": completed.returncode,
                "error": (completed.stderr or completed.stdout).strip()[-2_000:],
            }
        try:
            payload = parse_client_payload(platform, completed.stdout, output_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {"id": case.case_id, "passed": False, "error": str(exc)}
    selected_skill = payload.get("selected_skill")
    if isinstance(selected_skill, str):
        selected_skill = selected_skill.rsplit(":", 1)[-1]
    passed = (
        selected_skill == case.expected_skill
        and payload.get("design_context_write_allowed") is case.design_context_write_allowed
        and payload.get("product_source_edit_allowed") is case.product_source_edit_allowed
        and payload.get("will_execute") is False
    )
    return {"id": case.case_id, "passed": passed, "response": payload}


def main() -> int:
    args = parse_args()
    cases = load_cases()
    if args.case:
        cases = [case for case in cases if case.case_id == args.case]
        if not cases:
            raise SystemExit(f"unknown case: {args.case}")
    if args.platform == "static":
        result = run_static(cases)
    else:
        results = [run_live_case(case, args.platform) for case in cases[: args.max_cases]]
        result = {
            "platform": args.platform,
            "passed": all(item["passed"] for item in results),
            "case_count": len(results),
            "results": results,
        }
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
