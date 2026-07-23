from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "evaluate_direction_workshop.py"


def _module():
    spec = importlib.util.spec_from_file_location("evaluate_direction_workshop", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direction_eval_corpus_and_protocol_are_complete() -> None:
    module = _module()
    corpus = module.load_corpus()
    result = module.run_static(corpus)
    assert result["passed"] is True
    assert result["arms"][-1] == "rendered-loop"


def test_direction_eval_scores_fresh_handoff_preservation() -> None:
    module = _module()
    case = module.load_corpus()["cases"][0]
    direction = {
        "product_truth": {
            "job": "incident ownership",
            "audience": "engineer",
            "primary_action": "own",
            "constraints": ["mobile"],
        },
        "chosen_direction": {
            "thesis": "operational facts",
            "composition": "incident first",
            "typography": "serif",
            "density": "compact",
            "color": "quiet",
            "interaction": "next check",
            "avoid": ["gradient"],
            "failure_mode": "alarmism",
            "cost": "medium",
        },
        "prototype": {
            "scope": "header",
            "real_content": ["incident"],
            "viewports": ["mobile"],
            "states": ["ownership"],
        },
        "critique": {"observed": "facts", "revision": "mobile", "coverage_gap": "none"},
        "durable_decisions": ["keep operational facts"],
    }
    handoff = {
        "understanding": "incident ownership",
        "implementation_choices": ["keep operational facts and next check visible on mobile"],
        "preserved_decisions": ["ownership remains explicit"],
        "rejected_defaults": ["dashboard cards"],
        "open_questions": [],
    }
    scored = module.score(case, direction, handoff)
    assert scored["score_100"] >= 80
    assert "ownership" in scored["expected_terms_preserved_by_fresh_agent"]
