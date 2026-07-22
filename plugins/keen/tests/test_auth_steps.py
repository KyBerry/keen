"""Tests for the declarative auth-steps DSL in harness/capture.py.

Covers validate_auth_steps() and the eval_safe expression parser. These
tests are pure-Python and do not require Playwright — they exercise the
schema gate and selector hardening that runs BEFORE any browser is opened.

There is also one happy-path test that loads a JSON file from a tmp_path
and validates its structure end-to-end (no Playwright execution).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from harness.capture import (
    _ALLOWED_AUTH_ACTIONS,
    _AUTH_STEPS_MAX_BYTES,
    CaptureConfig,
    _env_required,
    _parse_eval_safe,
    _prepare_auth_storage_state,
    _validate_env_name,
    _validate_selector,
    apply_auth_steps,
    load_and_validate_auth_steps,
    validate_auth_steps,
)

# -- Top-level shape ------------------------------------------------------


def test_missing_steps_key_rejected() -> None:
    with pytest.raises(ValueError, match="missing required key 'steps'"):
        validate_auth_steps({})


def test_non_dict_top_level_rejected() -> None:
    with pytest.raises(ValueError, match="top-level must be an object"):
        validate_auth_steps([{"action": "goto", "url": "https://x"}])


def test_steps_not_list_rejected() -> None:
    with pytest.raises(ValueError, match="'steps' must be a list"):
        validate_auth_steps({"steps": "not-a-list"})


def test_empty_steps_list_ok() -> None:
    # An empty list is structurally valid — caller may choose to noop.
    assert validate_auth_steps({"steps": []}) == []


def test_step_must_be_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        validate_auth_steps({"steps": ["not-a-dict"]})


def test_step_missing_action_rejected() -> None:
    with pytest.raises(ValueError, match="missing required key 'action'"):
        validate_auth_steps({"steps": [{"url": "https://x"}]})


def test_unknown_action_rejected_with_supported_list() -> None:
    with pytest.raises(ValueError, match="unknown action 'spoof_request'"):
        validate_auth_steps({"steps": [{"action": "spoof_request"}]})


def test_unknown_action_message_lists_allowed_actions() -> None:
    with pytest.raises(ValueError) as ei:
        validate_auth_steps({"steps": [{"action": "xxx"}]})
    msg = str(ei.value)
    for name in ("goto", "fill", "click", "wait_for_selector", "eval_safe"):
        assert name in msg


# -- goto -----------------------------------------------------------------


def test_goto_requires_url() -> None:
    with pytest.raises(ValueError, match="'goto' requires"):
        validate_auth_steps({"steps": [{"action": "goto"}]})


def test_goto_happy_path() -> None:
    steps = validate_auth_steps({"steps": [{"action": "goto", "url": "https://example.com/login"}]})
    assert steps[0]["action"] == "goto"


# -- fill -----------------------------------------------------------------


def test_fill_requires_value_or_value_env() -> None:
    with pytest.raises(ValueError, match="requires 'value' or 'value_env'"):
        validate_auth_steps({"steps": [{"action": "fill", "selector": "#email"}]})


def test_fill_rejects_both_value_and_value_env() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#email", "value": "a", "value_env": "B"}]}
        )


def test_fill_value_env_must_be_non_empty_string() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        validate_auth_steps({"steps": [{"action": "fill", "selector": "#email", "value_env": ""}]})


def test_fill_value_must_be_string() -> None:
    with pytest.raises(ValueError, match=r"'fill\.value' must be a string"):
        validate_auth_steps({"steps": [{"action": "fill", "selector": "#email", "value": 12345}]})


def test_structured_secret_actions_validate_env_and_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEEN_AUTH_TOKEN", "secret")
    steps = validate_auth_steps(
        {
            "steps": [
                {
                    "action": "set_storage",
                    "key": "auth.token",
                    "value_env": "KEEN_AUTH_TOKEN",
                },
                {
                    "action": "set_cookie",
                    "name": "session_id",
                    "value_env": "KEEN_AUTH_TOKEN",
                },
            ]
        }
    )
    assert [step["action"] for step in steps] == ["set_storage", "set_cookie"]


def test_structured_secret_actions_reject_literal_or_unsafe_keys() -> None:
    with pytest.raises(ValueError, match="value_env"):
        validate_auth_steps(
            {"steps": [{"action": "set_storage", "key": "token", "value": "secret"}]}
        )
    with pytest.raises(ValueError, match=r"set_cookie\.name"):
        validate_auth_steps(
            {
                "steps": [
                    {
                        "action": "set_cookie",
                        "name": "bad;name",
                        "value_env": "KEEN_TOKEN",
                    }
                ]
            }
        )


# -- selector hardening ---------------------------------------------------


def test_selector_too_long_rejected() -> None:
    big = "a" * 600
    with pytest.raises(ValueError, match=r"length must be 1\.\.512"):
        _validate_selector(big, where="step 0")


def test_selector_in_step_too_long_rejected() -> None:
    big = "a" * 600
    with pytest.raises(ValueError, match=r"length must be 1\.\.512"):
        validate_auth_steps({"steps": [{"action": "fill", "selector": big, "value": "x"}]})


def test_selector_backtick_rejected() -> None:
    with pytest.raises(ValueError, match="backtick"):
        validate_auth_steps({"steps": [{"action": "click", "selector": "button`bad`"}]})


def test_selector_template_interpolation_rejected() -> None:
    with pytest.raises(ValueError, match=r"\$\{"):
        validate_auth_steps({"steps": [{"action": "click", "selector": "button${alert(1)}"}]})


def test_selector_must_be_string() -> None:
    with pytest.raises(ValueError, match="selector must be a string"):
        validate_auth_steps({"steps": [{"action": "click", "selector": 42}]})


def test_selector_empty_rejected() -> None:
    with pytest.raises(ValueError, match=r"length must be 1\.\.512"):
        validate_auth_steps({"steps": [{"action": "click", "selector": ""}]})


# -- wait_for_url / wait_for_selector -------------------------------------


def test_wait_for_url_requires_pattern() -> None:
    with pytest.raises(ValueError, match="requires non-empty 'pattern'"):
        validate_auth_steps({"steps": [{"action": "wait_for_url"}]})


def test_wait_for_selector_state_validated() -> None:
    with pytest.raises(ValueError, match="'state' must be one of"):
        validate_auth_steps(
            {"steps": [{"action": "wait_for_selector", "selector": ".x", "state": "BAD"}]}
        )


def test_wait_for_selector_valid_states() -> None:
    for state in ("visible", "attached", "hidden", "detached"):
        validate_auth_steps(
            {"steps": [{"action": "wait_for_selector", "selector": ".x", "state": state}]}
        )


# -- click ----------------------------------------------------------------


def test_click_button_value_validated() -> None:
    with pytest.raises(ValueError, match="'button' must be one of"):
        validate_auth_steps(
            {"steps": [{"action": "click", "selector": ".x", "button": "double-tap"}]}
        )


def test_click_valid_buttons() -> None:
    for btn in ("left", "right", "middle"):
        validate_auth_steps({"steps": [{"action": "click", "selector": ".x", "button": btn}]})


# -- press ----------------------------------------------------------------


def test_press_requires_key() -> None:
    with pytest.raises(ValueError, match="'press' requires"):
        validate_auth_steps({"steps": [{"action": "press", "selector": ".x"}]})


# -- select_option --------------------------------------------------------


def test_select_option_requires_value_or_values() -> None:
    with pytest.raises(ValueError, match="exactly one of 'value' or 'values'"):
        validate_auth_steps({"steps": [{"action": "select_option", "selector": "select"}]})


def test_select_option_values_must_be_list_of_strings() -> None:
    with pytest.raises(ValueError, match="must be a list of strings"):
        validate_auth_steps(
            {"steps": [{"action": "select_option", "selector": "select", "values": [1, 2]}]}
        )


# -- sleep_ms -------------------------------------------------------------


def test_sleep_ms_cap_rejected() -> None:
    with pytest.raises(ValueError, match=r"exceeds cap of 5000ms"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": 6000}]})


def test_sleep_ms_negative_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": -1}]})


def test_sleep_ms_int_required() -> None:
    with pytest.raises(ValueError, match="must be an int"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": "1000"}]})


def test_sleep_ms_at_cap_ok() -> None:
    validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": 5000}]})


# -- eval_safe ------------------------------------------------------------


def test_eval_safe_disallowed_expression() -> None:
    with pytest.raises(ValueError, match="not in whitelist"):
        _parse_eval_safe("window.location = 'http://evil/'")


def test_eval_safe_arbitrary_function_call_rejected() -> None:
    with pytest.raises(ValueError, match="not in whitelist"):
        _parse_eval_safe("fetch('/secret')")


def test_eval_safe_cookie_allowed() -> None:
    kind, args = _parse_eval_safe("document.cookie = 'session=abc'")
    assert kind == "cookie"
    assert args == ("session=abc",)


def test_eval_safe_localstorage_set_allowed() -> None:
    kind, args = _parse_eval_safe("localStorage.setItem('token', 'xyz')")
    assert kind == "localStorage.set"
    assert args == ("token", "xyz")


def test_eval_safe_localstorage_remove_allowed() -> None:
    kind, args = _parse_eval_safe("localStorage.removeItem('token')")
    assert kind == "localStorage.remove"
    assert args == ("token",)


def test_eval_safe_sessionstorage_set_allowed() -> None:
    kind, args = _parse_eval_safe("sessionStorage.setItem('flag', '1')")
    assert kind == "sessionStorage.set"
    assert args == ("flag", "1")


def test_eval_safe_sessionstorage_remove_allowed() -> None:
    kind, args = _parse_eval_safe("sessionStorage.removeItem('flag')")
    assert kind == "sessionStorage.remove"
    assert args == ("flag",)


def test_eval_safe_in_step_validated() -> None:
    with pytest.raises(ValueError, match="not in whitelist"):
        validate_auth_steps({"steps": [{"action": "eval_safe", "expr": "alert(1)"}]})


def test_eval_safe_too_long_rejected() -> None:
    big = "document.cookie = '" + "a" * 5000 + "'"
    with pytest.raises(ValueError, match="too long"):
        _parse_eval_safe(big)


# -- timeout_ms -----------------------------------------------------------


def test_timeout_ms_must_be_int() -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        validate_auth_steps(
            {"steps": [{"action": "click", "selector": ".x", "timeout_ms": "1000"}]}
        )


def test_timeout_ms_negative_rejected() -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        validate_auth_steps({"steps": [{"action": "click", "selector": ".x", "timeout_ms": -1}]})


# -- env var resolution at run time ---------------------------------------


def test_fill_value_env_present_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Schema validation alone does NOT touch env; this is just shape ok.
    monkeypatch.setenv("KEEN_TEST_PWD", "supersecret")
    steps = validate_auth_steps(
        {"steps": [{"action": "fill", "selector": "#p", "value_env": "KEEN_TEST_PWD"}]}
    )
    assert steps[0]["value_env"] == "KEEN_TEST_PWD"


@pytest.mark.asyncio
async def test_apply_auth_steps_env_missing_raises(tmp_path: Path) -> None:
    """The full happy-path test for env-missing — uses a mock Page so no browser is needed."""
    steps_file = tmp_path / "auth.json"
    steps_file.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "fill",
                        "selector": "#email",
                        "value_env": "KEEN_DOES_NOT_EXIST_KE7Y6",
                    },
                ],
            }
        )
    )
    # Ensure not in env.
    os.environ.pop("KEEN_DOES_NOT_EXIST_KE7Y6", None)

    page = mock.AsyncMock()
    with pytest.raises(ValueError, match=r"env var .+ not set"):
        await apply_auth_steps(page, steps_file)


def test_apply_auth_steps_missing_file(tmp_path: Path) -> None:
    """Path that doesn't exist raises FileNotFoundError."""
    import asyncio

    missing = tmp_path / "nope.json"
    page = mock.AsyncMock()
    with pytest.raises(FileNotFoundError, match="auth-steps file not found"):
        asyncio.run(apply_auth_steps(page, missing))


@pytest.mark.asyncio
async def test_authentication_storage_state_is_prepared_once_in_memory(tmp_path: Path) -> None:
    steps_file = tmp_path / "auth.json"
    steps_file.write_text(json.dumps({"steps": []}))
    browser = mock.AsyncMock()
    context = mock.AsyncMock()
    browser.new_context.return_value = context
    context.storage_state.return_value = {"cookies": [], "origins": []}
    cfg = CaptureConfig(
        target="https://example.com",
        viewports=["desktop"],
        auth_steps_path=steps_file,
    )

    state = await _prepare_auth_storage_state(
        browser,
        cfg,
        {"desktop": {"width": 1440, "height": 900, "deviceScaleFactor": 1}},
    )

    assert state == {"cookies": [], "origins": []}
    context.storage_state.assert_awaited_once()
    context.close.assert_awaited_once()


def test_apply_auth_steps_bad_json(tmp_path: Path) -> None:
    """Malformed JSON raises ValueError."""
    import asyncio

    f = tmp_path / "bad.json"
    f.write_text("{ not valid json")
    page = mock.AsyncMock()
    with pytest.raises(ValueError, match="JSON parse error"):
        asyncio.run(apply_auth_steps(page, f))


# -- happy-path integration: load a realistic JSON file -------------------


def test_realistic_login_steps_validate_clean(tmp_path: Path) -> None:
    """End-to-end: load a typical login flow from a JSON file and validate.

    Env var names use the KEEN_ prefix per the hardened env-name
    rule. Unprefixed names like ``EMAIL`` would be rejected (see
    ``test_fill_value_env_unprefixed_rejected``).
    """
    f = tmp_path / "auth.json"
    f.write_text(
        json.dumps(
            {
                "steps": [
                    {"action": "goto", "url": "https://example.com/login"},
                    {"action": "wait_for_selector", "selector": "#email"},
                    {"action": "fill", "selector": "#email", "value_env": "KEEN_EMAIL"},
                    {"action": "fill", "selector": "#password", "value_env": "KEEN_PWD"},
                    {"action": "click", "selector": "button[type=submit]"},
                    {"action": "wait_for_selector", "selector": ".dashboard", "timeout_ms": 15000},
                    {"action": "wait_for_url", "pattern": "**/dashboard"},
                ]
            }
        )
    )
    data = json.loads(f.read_text())
    steps = validate_auth_steps(data)
    assert len(steps) == 7
    assert [s["action"] for s in steps] == [
        "goto",
        "wait_for_selector",
        "fill",
        "fill",
        "click",
        "wait_for_selector",
        "wait_for_url",
    ]


def test_realistic_oauth_token_validates_clean(tmp_path: Path) -> None:
    """Token-style auth via localStorage uses eval_safe; verify it validates."""
    f = tmp_path / "oauth.json"
    f.write_text(
        json.dumps(
            {
                "steps": [
                    {"action": "goto", "url": "https://example.com"},
                    {"action": "eval_safe", "expr": "localStorage.setItem('auth_token', 'abc123')"},
                    {"action": "goto", "url": "https://example.com/app"},
                ]
            }
        )
    )
    data = json.loads(f.read_text())
    steps = validate_auth_steps(data)
    assert len(steps) == 3
    assert steps[1]["action"] == "eval_safe"


# -- Hardening: goto SSRF (gap A) ----------------------------------------
#
# Before this fix, validate_auth_steps only checked that 'url' was a
# non-empty string — a hostile JSON could drive page.goto() to internal
# cloud-metadata IPs (169.254.169.254, GCP metadata.google.internal) or
# javascript: URLs. validate_target is the same SSRF guard we use for
# the top-level target; reuse it here.
#
# Cite: OWASP "Server Side Request Forgery Prevention Cheat Sheet" —
# https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html


def test_goto_url_metadata_ip_rejected() -> None:
    """goto must reject http://169.254.169.254/ (cloud metadata endpoint)."""
    with pytest.raises(ValueError, match=r"169\.254\.169\.254"):
        validate_auth_steps(
            {"steps": [{"action": "goto", "url": "http://169.254.169.254/latest/meta-data/"}]}
        )


def test_goto_url_javascript_scheme_rejected() -> None:
    """goto must reject javascript: URLs (XSS vector via page.goto)."""
    with pytest.raises(ValueError, match=r"(?i)javascript|unsupported scheme"):
        validate_auth_steps({"steps": [{"action": "goto", "url": "javascript:alert(1)"}]})


def test_goto_url_file_scheme_rejected() -> None:
    """goto must reject file:// — local file read via the headless browser."""
    with pytest.raises(ValueError, match=r"(?i)file://|--allow-file"):
        validate_auth_steps({"steps": [{"action": "goto", "url": "file:///etc/passwd"}]})


def test_goto_url_loopback_rejected() -> None:
    """goto must reject http://localhost/ — protects against localhost admin
    panels (Redis, internal HTTP services on the runner)."""
    with pytest.raises(ValueError, match=r"(?i)loopback|private"):
        validate_auth_steps({"steps": [{"action": "goto", "url": "http://127.0.0.1:6379/"}]})


# -- Hardening: eval_safe regex anchor robustness (gap B) -----------------
#
# The regex patterns are anchored to ^…$, but the threat model demands
# adversarial tests that walk through the obvious bypass attempts.


def test_eval_safe_trailing_content_after_close_quote_rejected() -> None:
    """document.cookie = 'a'; alert(1); //' — naive matchers see the OK
    prefix and miss the trailing JS. ^…$ anchor blocks it."""
    with pytest.raises(ValueError, match="not in whitelist"):
        _parse_eval_safe("document.cookie = 'a'; alert(1); //'")


def test_eval_safe_string_concatenation_rejected() -> None:
    """localStorage.setItem('a', 'b' + document.cookie) — concatenation
    isn't in the whitelisted grammar."""
    with pytest.raises(ValueError, match="not in whitelist"):
        _parse_eval_safe("localStorage.setItem('a', 'b' + document.cookie)")


def test_eval_safe_chained_call_rejected() -> None:
    """localStorage.setItem('a','b'); fetch('/secret') — second statement
    blocked by the anchored regex."""
    with pytest.raises(ValueError, match="not in whitelist"):
        _parse_eval_safe("localStorage.setItem('a','b'); fetch('/secret')")


def test_eval_safe_total_length_cap_enforced() -> None:
    """eval_safe rejects expressions over the conservative length cap."""
    big = "document.cookie = '" + "a" * 1100 + "'"
    with pytest.raises(ValueError, match="too long"):
        _parse_eval_safe(big)


# -- Hardening: press key whitelist (gap C) -------------------------------
#
# Playwright's page.press(selector, key) accepts arbitrary key chords. A
# hostile JSON could deliver Meta+Q (quit Chromium), Control+W (close
# tab), or vendor-specific shortcuts. Restrict to a closed regex.
#
# Cite: Playwright Keyboard reference —
# https://playwright.dev/python/docs/input#keys-and-shortcuts


def test_press_key_tab_accepted() -> None:
    """Tab is a common form-navigation key — must validate cleanly."""
    steps = validate_auth_steps(
        {"steps": [{"action": "press", "selector": "#email", "key": "Tab"}]}
    )
    assert steps[0]["key"] == "Tab"


def test_press_key_enter_accepted() -> None:
    """Enter is the canonical submit key — must validate."""
    validate_auth_steps({"steps": [{"action": "press", "selector": "#email", "key": "Enter"}]})


def test_press_key_modifier_chord_accepted() -> None:
    """Control+Shift+I is a legitimate keyboard shortcut for dev-tools-style
    UI in some apps — must pass the regex."""
    validate_auth_steps(
        {"steps": [{"action": "press", "selector": "body", "key": "Control+Shift+I"}]}
    )


def test_press_key_xss_string_rejected() -> None:
    """An arbitrary string like 'XSS' has no business being passed as a key."""
    with pytest.raises(ValueError, match="not in allowed key set"):
        validate_auth_steps({"steps": [{"action": "press", "selector": "#x", "key": "XSS"}]})


def test_press_key_backtick_rejected() -> None:
    """Backtick injection in the key arg is blocked (template-literal vector)."""
    with pytest.raises(ValueError, match="not in allowed key set"):
        validate_auth_steps({"steps": [{"action": "press", "selector": "#x", "key": "`a`"}]})


def test_press_key_unicode_rejected() -> None:
    """Unicode tricks (zero-width joiners, RTL marks) blocked by ASCII regex."""
    with pytest.raises(ValueError, match="not in allowed key set"):
        validate_auth_steps({"steps": [{"action": "press", "selector": "#x", "key": "A‍B"}]})


def test_press_key_f13_rejected() -> None:
    """F1..F12 are allowed; F13+ are not in the regex."""
    with pytest.raises(ValueError, match="not in allowed key set"):
        validate_auth_steps({"steps": [{"action": "press", "selector": "#x", "key": "F13"}]})


# -- Hardening: sleep_ms type coercion (gap D) ----------------------------


def test_sleep_ms_negative_one_rejected() -> None:
    """Negative sleep is meaningless; reject -1 specifically (boundary)."""
    with pytest.raises(ValueError, match=">= 0"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": -1}]})


def test_sleep_ms_over_cap_rejected() -> None:
    """9999 ms exceeds the 5000 ms cap — rejected with explicit cap message."""
    with pytest.raises(ValueError, match="exceeds cap of 5000ms"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": 9999}]})


def test_sleep_ms_float_rejected() -> None:
    """Floats are not allowed even if they could be coerced to int."""
    with pytest.raises(ValueError, match="must be an int"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": 100.5}]})


def test_sleep_ms_string_rejected() -> None:
    """Strings that look like numbers ('1000') are still rejected."""
    with pytest.raises(ValueError, match="must be an int"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": "1000"}]})


def test_sleep_ms_bool_rejected() -> None:
    """bool subclasses int in Python — explicitly reject True/False."""
    with pytest.raises(ValueError, match="must be an int"):
        validate_auth_steps({"steps": [{"action": "sleep_ms", "ms": True}]})


# -- Hardening: JSON file size cap (gap E) --------------------------------
#
# json.loads of a multi-gigabyte file would consume runner memory and
# either OOM or stall. Cap to 256 KiB which is more than enough for any
# realistic login flow (the test fixtures here are < 1 KiB each).


def test_oversized_auth_steps_file_rejected(tmp_path: Path) -> None:
    """A 300 KiB file should be rejected by the file-size guard."""
    f = tmp_path / "huge.json"
    # 300 KiB worth of padding inside a structurally-valid JSON.
    payload = '{"steps":[], "_pad":"' + ("a" * 300_000) + '"}'
    f.write_text(payload)
    assert f.stat().st_size > _AUTH_STEPS_MAX_BYTES
    with pytest.raises(ValueError, match=r"too large|256 KiB"):
        load_and_validate_auth_steps(f)


def test_under_cap_auth_steps_file_accepted(tmp_path: Path) -> None:
    """File well under the cap still works end-to-end."""
    f = tmp_path / "small.json"
    f.write_text(json.dumps({"steps": [{"action": "goto", "url": "https://example.com"}]}))
    assert f.stat().st_size < _AUTH_STEPS_MAX_BYTES
    steps = load_and_validate_auth_steps(f)
    assert len(steps) == 1


# -- Hardening: env var name validation (gap F) ---------------------------
#
# `fill.value_env` controls which environment variable's value is typed
# into a form field. Without validation, a hostile JSON could read PATH
# or LD_PRELOAD and submit them to an attacker-controlled URL — a
# controlled exfiltration channel. Restrict to KEEN_-prefixed
# names + an explicit blocklist of ambient/dangerous names.


def test_fill_value_env_path_rejected() -> None:
    """value_env: 'PATH' must be rejected (blocklist + unprefixed)."""
    with pytest.raises(ValueError, match=r"PATH"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#email", "value_env": "PATH"}]}
        )


def test_fill_value_env_ld_preload_rejected() -> None:
    """LD_PRELOAD is the classic library-injection env var — blocklisted."""
    with pytest.raises(ValueError, match=r"LD_PRELOAD|blocked-env"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#x", "value_env": "LD_PRELOAD"}]}
        )


def test_fill_value_env_dyld_rejected() -> None:
    """DYLD_INSERT_LIBRARIES is the macOS equivalent of LD_PRELOAD."""
    with pytest.raises(ValueError, match=r"DYLD|blocked-env"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#x", "value_env": "DYLD_INSERT_LIBRARIES"}]}
        )


def test_fill_value_env_home_rejected() -> None:
    """HOME would let an attacker fingerprint the user/runner — blocklisted."""
    with pytest.raises(ValueError, match=r"HOME|blocked-env"):
        validate_auth_steps({"steps": [{"action": "fill", "selector": "#x", "value_env": "HOME"}]})


def test_fill_value_env_unprefixed_rejected() -> None:
    """FOO_BAR (no KEEN_ prefix) rejected — keeps blast radius small."""
    with pytest.raises(ValueError, match=r"must start with 'KEEN_'"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#x", "value_env": "FOO_BAR"}]}
        )


def test_fill_value_env_lowercase_rejected() -> None:
    """value_env names must be uppercase ([A-Z][A-Z0-9_]*)."""
    with pytest.raises(ValueError, match=r"\[A-Z\]\[A-Z0-9_\]"):
        validate_auth_steps(
            {"steps": [{"action": "fill", "selector": "#x", "value_env": "keen_token"}]}
        )


def test_fill_value_env_prefixed_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """KEEN_TOKEN (correctly prefixed) validates cleanly."""
    monkeypatch.setenv("KEEN_TOKEN", "secret-xyz")
    steps = validate_auth_steps(
        {"steps": [{"action": "fill", "selector": "#x", "value_env": "KEEN_TOKEN"}]}
    )
    assert steps[0]["value_env"] == "KEEN_TOKEN"


def test_env_required_blocked_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """_env_required also re-validates the name — defense in depth."""
    monkeypatch.setenv("PATH", "/usr/bin")  # noop, but proves it's not the env miss path
    with pytest.raises(ValueError, match=r"PATH|blocked-env"):
        _env_required("PATH", where="test")


def test_validate_env_name_helper_allows_opt_out() -> None:
    """allow_unprefixed=True lets internal callers skip the prefix gate."""
    # Even with allow_unprefixed, blocklist still applies.
    assert _validate_env_name("MY_VAR", where="t", allow_unprefixed=True) == "MY_VAR"
    with pytest.raises(ValueError, match="blocked-env"):
        _validate_env_name("PATH", where="t", allow_unprefixed=True)


# -- Hardening: wait_for_url timeout cap (gap G) --------------------------


def test_wait_for_url_excessive_timeout_rejected() -> None:
    """A hostile JSON setting timeout_ms = 24h would block the run forever."""
    with pytest.raises(ValueError, match="exceeds cap"):
        validate_auth_steps(
            {
                "steps": [
                    {
                        "action": "wait_for_url",
                        "pattern": "**/dash",
                        "timeout_ms": 24 * 60 * 60 * 1000,
                    }
                ]
            }
        )


def test_wait_for_url_at_cap_accepted() -> None:
    """60s (the cap itself) is allowed; exceeds-cap fires only above it."""
    validate_auth_steps(
        {"steps": [{"action": "wait_for_url", "pattern": "**/x", "timeout_ms": 60_000}]}
    )


# -- Hardening: closed dispatch table (gap I) -----------------------------


def test_allowed_actions_is_frozen() -> None:
    """_ALLOWED_AUTH_ACTIONS is a frozenset — runtime mutation impossible.

    This catches the class of bug where dispatch is keyed off a mutable
    dict and a hostile import could insert a new action at runtime.
    """
    assert isinstance(_ALLOWED_AUTH_ACTIONS, frozenset)
    with pytest.raises(AttributeError):
        _ALLOWED_AUTH_ACTIONS.add("evil")  # type: ignore[attr-defined]


def test_unknown_action_at_apply_time_fails_closed(tmp_path: Path) -> None:
    """Even if the validator somehow let an unknown action through,
    apply_auth_steps fails closed in its dispatch loop (belt + braces)."""
    import asyncio

    # Build a "validated" steps list out-of-band that bypasses
    # validate_auth_steps — simulates a future bug where the validator
    # widens but dispatch doesn't.
    f = tmp_path / "ok.json"
    f.write_text(json.dumps({"steps": [{"action": "goto", "url": "https://example.com"}]}))

    page = mock.AsyncMock()
    # Patch validate_auth_steps to return a bad action so we can probe
    # the dispatch-side guard in isolation.
    with (
        mock.patch(
            "harness.capture.validate_auth_steps",
            return_value=[{"action": "exfiltrate", "url": "x"}],
        ),
        pytest.raises(ValueError, match="not in closed dispatch set"),
    ):
        asyncio.run(apply_auth_steps(page, f))


# -- Hardening: pre-launch fail-fast (gap H) ------------------------------


def test_load_and_validate_helper_exposed() -> None:
    """load_and_validate_auth_steps is the public pre-launch entry point."""
    # Quick smoke: it's importable, callable, and bound to the same checks.
    assert callable(load_and_validate_auth_steps)


def test_load_and_validate_size_gate_runs_before_parse(tmp_path: Path) -> None:
    """The size cap fires before json.loads — confirms cap is the first
    line of defense, not after a successful parse."""
    f = tmp_path / "big.json"
    # Deliberately invalid JSON, but oversized. If size check ran AFTER
    # parse, we'd get a JSON parse error first.
    f.write_text("not valid json " * 25_000)
    assert f.stat().st_size > _AUTH_STEPS_MAX_BYTES
    with pytest.raises(ValueError, match=r"too large|256 KiB"):
        load_and_validate_auth_steps(f)


# -- Hardening: regression — env var values never leak in error messages --


def test_fill_value_env_error_does_not_log_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hostile JSON tries to bait the runner into logging a secret.

    _env_required must never log the env var VALUE. The exception
    message references the NAME only, never the resolved value.
    """
    monkeypatch.setenv("KEEN_SECRET_QWZ", "super-secret-xyz-9000")
    # Resolve happy-path — must succeed and not leak.
    with caplog.at_level("DEBUG"):
        v = _env_required("KEEN_SECRET_QWZ", where="t")
    assert v == "super-secret-xyz-9000"
    for rec in caplog.records:
        assert "super-secret-xyz-9000" not in rec.getMessage()
