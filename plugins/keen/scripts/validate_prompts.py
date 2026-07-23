#!/usr/bin/env python3
"""Static checks for Keen's Claude and Codex agent surface."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXPECTED_SKILLS = {
    "establish",
    "explore",
    "guard",
    "keen",
    "refine",
}
EXPECTED_REFS = {"design-direction.md", "explore.md", "guard.md", "refine.md", "workshop.md"}
VERSION = "0.8.0"


def words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def check_markdown(path: Path, errors: list[str]) -> tuple[str, int]:
    text = path.read_text(encoding="utf-8")
    if text.count("```") % 2:
        errors.append(f"{path}: unbalanced fenced code block")
    return text, words(text)


def frontmatter(text: str, path: Path) -> dict[str, Any]:
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"{path}: missing or invalid YAML frontmatter")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    return payload


def load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path}: root must be an object")
        return {}
    return payload


def check_skill(path: Path, errors: list[str]) -> tuple[str, int]:
    text, count = check_markdown(path, errors)
    skill_name = path.parent.name
    try:
        meta = frontmatter(text, path)
    except ValueError as exc:
        errors.append(str(exc))
        return text, count

    if meta.get("name") != skill_name:
        errors.append(f"{path}: name must be {skill_name!r}")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{path}: missing description")
    elif "Use when" not in description:
        errors.append(f"{path}: description must state when the skill applies")
    if set(meta) != {"name", "description"}:
        errors.append(f"{path}: cross-platform frontmatter must contain only name and description")
    if "allowed-tools" in meta or re.search(r"(?im)^allowed-tools:", text):
        errors.append(f"{path}: must not preapprove host tools")
    if count > (1_100 if skill_name == "keen" else 500):
        errors.append(f"{path}: {count} words exceeds the skill budget")

    openai_path = path.parent / "agents" / "openai.yaml"
    if not openai_path.is_file():
        errors.append(f"{openai_path}: missing Codex metadata")
        return text, count
    payload = yaml.safe_load(openai_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        errors.append(f"{openai_path}: root must be an object")
        return text, count
    interface = payload.get("interface")
    policy = payload.get("policy")
    if not isinstance(interface, dict):
        errors.append(f"{openai_path}: missing interface object")
        interface = {}
    if not isinstance(policy, dict):
        errors.append(f"{openai_path}: missing policy object")
        policy = {}
    default_prompt = interface.get("default_prompt", "")
    if f"${skill_name}" not in default_prompt:
        errors.append(f"{openai_path}: default prompt must mention ${skill_name}")
    expected_implicit = skill_name == "keen"
    if policy.get("allow_implicit_invocation") is not expected_implicit:
        errors.append(f"{openai_path}: allow_implicit_invocation must be {expected_implicit}")

    if skill_name != "keen" and "../keen/SKILL.md" not in text:
        errors.append(f"{path}: must link the shared safety contract directly")
    return text, count


def main() -> int:
    errors: list[str] = []
    command_files = sorted((ROOT / "commands").glob("*.md"))
    if command_files:
        errors.append(
            "commands/: obsolete Claude-only wrappers remain: "
            + ", ".join(path.name for path in command_files)
        )

    skill_paths = sorted(SKILLS.glob("*/SKILL.md"))
    skill_names = {path.parent.name for path in skill_paths}
    if skill_names != EXPECTED_SKILLS:
        errors.append(
            "skills/: inventory mismatch; "
            f"missing={sorted(EXPECTED_SKILLS - skill_names)}, "
            f"extra={sorted(skill_names - EXPECTED_SKILLS)}"
        )

    skill_words = 0
    runtime_text: list[str] = []
    descriptions: set[str] = set()
    for path in skill_paths:
        text, count = check_skill(path, errors)
        runtime_text.append(text)
        skill_words += count
        try:
            description = frontmatter(text, path).get("description")
        except ValueError:
            description = None
        if isinstance(description, str):
            if description in descriptions:
                errors.append(f"{path}: duplicates another skill description")
            descriptions.add(description)

    reference_paths = sorted((SKILLS / "keen" / "references").glob("*.md"))
    actual_refs = {path.name for path in reference_paths}
    if actual_refs != EXPECTED_REFS:
        errors.append(
            "skill references: inventory mismatch; "
            f"missing={sorted(EXPECTED_REFS - actual_refs)}, "
            f"extra={sorted(actual_refs - EXPECTED_REFS)}"
        )
    reference_words = 0
    for path in reference_paths:
        _text, count = check_markdown(path, errors)
        reference_words += count
        if count > 1_000:
            errors.append(f"{path}: {count} words exceeds the reference budget")

    claude_manifest = load_json(ROOT / ".claude-plugin" / "plugin.json", errors)
    codex_manifest = load_json(ROOT / ".codex-plugin" / "plugin.json", errors)
    claude_marketplace = load_json(
        ROOT.parent.parent / ".claude-plugin" / "marketplace.json", errors
    )
    codex_marketplace = load_json(
        ROOT.parent.parent / ".agents" / "plugins" / "marketplace.json", errors
    )
    claude_entry = next(
        (
            item
            for item in claude_marketplace.get("plugins", [])
            if isinstance(item, dict) and item.get("name") == "keen"
        ),
        {},
    )
    codex_base_version = str(codex_manifest.get("version", "")).split("+", 1)[0]
    versions = {
        claude_manifest.get("version"),
        codex_base_version,
        claude_entry.get("version"),
    }
    if versions != {VERSION}:
        errors.append(f"plugin versions must all equal {VERSION}; found {versions}")
    if claude_manifest.get("skills") != "./skills/":
        errors.append("Claude manifest must declare ./skills/")
    if codex_manifest.get("skills") != "./skills/":
        errors.append("Codex manifest must declare ./skills/")
    codex_entry = next(
        (
            item
            for item in codex_marketplace.get("plugins", [])
            if isinstance(item, dict) and item.get("name") == "keen"
        ),
        {},
    )
    if codex_entry.get("policy") != {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }:
        errors.append("Codex marketplace entry has the wrong install/auth policy")
    if codex_entry.get("category") != "Developer Tools":
        errors.append("Codex marketplace entry must use Developer Tools category")

    docs = [ROOT / "README.md", ROOT / "AGENTS.md"]
    docs_text = "\n".join(path.read_text(encoding="utf-8") for path in docs)
    docs_words = sum(words(path.read_text(encoding="utf-8")) for path in docs)
    combined = "\n".join(runtime_text) + "\n" + docs_text
    forbidden_patterns = {
        "nested Claude skill handoff": r"keen:keen",
        "legacy Codex skill path": r"CODEX_HOME[^\n]*skills|\.codex/skills/keen",
        "wrong marketplace id": r"keen@keen-marketplace",
        "wrong HIG alias": r"--against\s+<?hig>?(?:\s|`|$)",
        "wrong Fluent alias": r"--against\s+<?fluent>?(?:\s|`|$)",
        "raw command arguments": r"\$ARGUMENTS",
    }
    for label, pattern in forbidden_patterns.items():
        if re.search(pattern, combined, flags=re.IGNORECASE):
            errors.append(f"agent surface contains {label}")

    print("Keen agent surface")
    print(f"  cross-platform skills: {len(skill_paths):>2} files, {skill_words:>5} words")
    print(f"  on-demand references: {len(reference_paths):>2} files, {reference_words:>5} words")
    print(f"  README + AGENTS:       2 files, {docs_words:>5} words")
    print(f"  obsolete commands:     {len(command_files):>2}")

    if errors:
        print("\nPrompt validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Prompt validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
