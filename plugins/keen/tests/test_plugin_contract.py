"""Agent-facing plugin contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_static_skill_contract_evaluation_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/evaluate_skill_contracts.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["passed"] is True
    assert payload["case_count"] >= 14
    assert len(payload["covered_skills"]) == 11


def test_codex_specialists_require_explicit_invocation() -> None:
    for skill_dir in sorted((ROOT / "skills").glob("ui-*")):
        payload = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert payload["policy"]["allow_implicit_invocation"] is False


def test_shared_router_does_not_preapprove_write() -> None:
    text = (ROOT / "skills" / "keen" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "allowed-tools" not in frontmatter
    assert "Write" not in frontmatter
