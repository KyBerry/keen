from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness.design_context import new_context
from harness.workshop import (
    WorkshopError,
    load_spec,
    promote_context,
    serve,
    summarize_response,
    validate_promotion,
    validate_response,
    validate_spec,
)

FIXTURE = Path(__file__).parent.parent / "evals" / "fixtures" / "relay-direction-workshop.json"


def _spec() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _response() -> dict:
    return {
        "schema_version": 1,
        "session_id": "relay-direction-01",
        "submitted_at": "2026-07-22T20:00:00Z",
        "answers": [
            {
                "question_id": "hierarchy",
                "selected": ["command-brief"],
                "custom": "",
                "notes": "Keep the operational facts above the fold.",
            },
            {
                "question_id": "transfer",
                "selected": ["continuous-relay"],
                "custom": "",
                "notes": "",
            },
        ],
    }


def test_fixture_is_a_valid_product_specific_workshop() -> None:
    payload, path = load_spec(FIXTURE)
    assert path == FIXTURE
    assert validate_spec(payload) == []
    assert len(payload["questions"][0]["options"]) == 3


def test_spec_rejects_duplicate_ids_and_open_ended_style_values() -> None:
    payload = _spec()
    payload["arbitrary_html"] = "<script>alert(1)</script>"
    payload["questions"][0]["options"][1]["id"] = "command-brief"
    payload["questions"][0]["options"][0]["specimen"]["style"]["palette"] = "url-red"
    errors = validate_spec(payload)
    assert any("root has unknown keys" in error for error in errors)
    assert any("duplicated" in error for error in errors)
    assert any("style.palette" in error for error in errors)


def test_response_is_bound_to_spec_options_and_required_questions() -> None:
    payload = _response()
    assert validate_response(payload, _spec()) == []
    payload["answers"][0]["selected"] = ["unknown"]
    payload["answers"][0]["callback"] = "https://example.com"
    payload["answers"].pop()
    errors = validate_response(payload, _spec())
    assert any("unknown options" in error for error in errors)
    assert any("unknown keys" in error for error in errors)
    assert any("missing required answers: transfer" in error for error in errors)


def test_summary_expands_choices_and_sets_agent_boundary() -> None:
    summary = summarize_response(_spec(), _response())
    hierarchy = summary["answers"][0]
    assert hierarchy["selections"][0]["label"] == "Command brief"
    assert "prototype" in summary["agent_contract"]["next"]
    assert (
        summary["agent_contract"]["treat_as"] == "user evidence, not an automatic design decision"
    )


def test_promotion_updates_only_durable_context_after_agent_decision(tmp_path: Path) -> None:
    context_path = tmp_path / ".keen" / "design-context.json"
    context_path.parent.mkdir()
    context_path.write_text(json.dumps(new_context("Relay")), encoding="utf-8")
    promotion = {
        "schema_version": 1,
        "source_session_id": "relay-direction-01",
        "project": {
            "summary": "Incident handoff for interrupted on-call engineers.",
            "audiences": ["On-call engineers"],
            "jobs": ["Understand and accept incident ownership"],
            "primary_action": "Take ownership",
        },
        "direction": {
            "qualities": ["decisive without alarmism"],
            "typography": {
                "display": "Georgia",
                "body": "Arial",
                "rationale": "Editorial incident sentence with restrained operational detail.",
            },
        },
        "avoid": ["severity color flooding the whole screen"],
        "decision": {
            "id": "incident-hierarchy",
            "summary": "Use the command-brief hierarchy for P1 incident handoff.",
            "rationale": "The mobile and desktop prototype preserved the facts while improving orientation.",
            "status": "active",
        },
    }
    assert validate_promotion(promotion) == []
    written_context, written_direction = promote_context(tmp_path, promotion)
    payload = json.loads(written_context.read_text(encoding="utf-8"))
    assert payload["project"]["primary_action"] == "Take ownership"
    assert payload["decisions"][-1]["source_session_id"] == "relay-direction-01"
    assert "Georgia / Arial" in written_direction.read_text(encoding="utf-8")


def test_promotion_rejects_unknown_context_keys() -> None:
    promotion = {
        "schema_version": 1,
        "source_session_id": "relay-direction-01",
        "direction": {"make_it_pop": True},
        "decision": {"summary": "A", "rationale": "B", "status": "active"},
    }
    assert any("unknown keys" in error for error in validate_promotion(promotion))


def test_loopback_server_requires_token_and_writes_valid_response(tmp_path: Path) -> None:
    ready = tmp_path / "ready.json"
    response_path = tmp_path / "response.json"
    errors: list[BaseException] = []
    ready_url: list[str] = []
    ready_signal = threading.Event()

    def mark_ready(url: str) -> None:
        ready_url.append(url)
        ready_signal.set()

    def run() -> None:
        try:
            serve(
                _spec(),
                response_path=response_path,
                timeout_seconds=5,
                open_browser=False,
                ready_path=ready,
                on_ready=mark_ready,
            )
        except BaseException as exc:  # pragma: no cover - asserted after join
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert ready_signal.wait(timeout=10)
    assert ready.exists()
    url = ready_url[0]
    assert json.loads(ready.read_text(encoding="utf-8"))["url"] == url
    origin, query = url.split("/?", 1)
    token = query.removeprefix("token=")

    with pytest.raises(HTTPError) as denied:
        urlopen(f"{origin}/api/spec", timeout=2)
    assert denied.value.code == 403

    spec_request = Request(
        f"{origin}/api/spec",
        headers={"X-Keen-Workshop-Token": token},
    )
    with urlopen(spec_request, timeout=2) as loaded:
        assert json.load(loaded)["session"]["id"] == "relay-direction-01"

    submitted = _response()
    submitted.pop("submitted_at")
    request = Request(
        f"{origin}/api/submit",
        data=json.dumps(submitted).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Keen-Workshop-Token": token,
        },
    )
    with urlopen(request, timeout=2) as result:
        assert result.status == 201
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors == []
    written = json.loads(response_path.read_text(encoding="utf-8"))
    assert written["submitted_at"].endswith("Z")
    if os.name != "nt":
        assert stat.S_IMODE(ready.stat().st_mode) == 0o600
        assert stat.S_IMODE(response_path.stat().st_mode) == 0o600


def test_serve_refuses_existing_or_symlink_response(tmp_path: Path) -> None:
    response = tmp_path / "response.json"
    response.write_text("{}")
    with pytest.raises(WorkshopError, match="already exists"):
        serve(_spec(), response_path=response, timeout_seconds=0.1, open_browser=False)

    link = tmp_path / "response-link.json"
    link.symlink_to(response)
    with pytest.raises(WorkshopError, match="symlink"):
        serve(_spec(), response_path=link, timeout_seconds=0.1, open_browser=False)
