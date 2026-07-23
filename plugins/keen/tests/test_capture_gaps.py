"""Gap tests for harness/capture.py top-level helpers.

The full capture pipeline needs Playwright + a browser; these tests
target the pure-Python helpers that run BEFORE the browser opens:
viewport / states config loading, slug helper, and concurrency.

The auth-steps DSL is covered separately in tests/test_auth_steps.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness.capture import (
    DEFAULT_VIEWPORT_PRESETS,
    MAX_CAPTURE_CONCURRENCY,
    CaptureConfig,
    _apply_setup_step,
    _browser_launch_failure_message,
    _concurrency,
    _enrich_accessibility_names,
    _parse_aria_snapshot_root,
    _screenshot_options,
    _slug,
    _validate_requested_matrix,
    load_states,
    load_viewport_presets,
)


def test_parse_aria_snapshot_root_reads_computed_name_and_state() -> None:
    assert _parse_aria_snapshot_root('- link "CI":') == ("link", "CI")
    assert _parse_aria_snapshot_root('- heading "Keen" [level=1]') == (
        "heading",
        "Keen",
    )
    assert _parse_aria_snapshot_root('- button "Say \\"hello\\""') == (
        "button",
        'Say "hello"',
    )


def test_parse_aria_snapshot_root_preserves_empty_name() -> None:
    assert _parse_aria_snapshot_root("- link:") == ("link", "")
    assert _parse_aria_snapshot_root("") is None


def test_accessibility_name_enrichment_uses_browser_tree_and_cleans_markers() -> None:
    class Locator:
        def __init__(self, snapshot: str) -> None:
            self.snapshot = snapshot

        async def aria_snapshot(self, *, timeout: int) -> str:
            assert timeout == 1_500
            return self.snapshot

    class Page:
        def __init__(self) -> None:
            self.evaluations: list[object] = []

        async def evaluate(self, _script: str, arg: object) -> None:
            self.evaluations.append(arg)

        def locator(self, selector: str) -> Locator:
            snapshots = {
                '[data-keen-ax-index="0"]': '- link "CI":',
                '[data-keen-ax-index="1"]': '- button "Open menu"',
            }
            return Locator(snapshots[selector])

    page = Page()
    dom = {
        "elements": [
            {"index": 0, "tag": "a", "name": "", "role": ""},
            {"index": 1, "tag": "button", "name": "Menu", "role": "button"},
            {"index": 2, "tag": "div", "name": "Decorative", "role": ""},
        ]
    }

    asyncio.run(_enrich_accessibility_names(page, dom))

    assert dom["elements"][0]["name"] == "CI"
    assert dom["elements"][1]["name"] == "Open menu"
    assert dom["elements"][0]["nameSource"] == "browser-accessibility-tree"
    assert "nameSource" not in dom["elements"][2]
    assert page.evaluations[0][0] == [0, 1]
    assert page.evaluations[-1] == "data-keen-ax-index"


def test_browser_launch_failure_message_explains_missing_chromium() -> None:
    message = _browser_launch_failure_message(RuntimeError("Executable doesn't exist at /cache"))

    assert "Chromium is not installed" in message
    assert "python -m playwright install chromium" in message


def test_browser_launch_failure_message_preserves_other_errors() -> None:
    assert _browser_launch_failure_message(RuntimeError("sandbox denied")) == (
        "could not launch Chromium: sandbox denied"
    )


def test_media_setup_steps_apply_to_page_not_context() -> None:
    class Page:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def emulate_media(self, **kwargs: str) -> None:
            self.calls.append(kwargs)

    page = Page()
    context = object()
    asyncio.run(
        _apply_setup_step(context, page, {"action": "emulate_color_scheme", "value": "dark"})
    )
    asyncio.run(
        _apply_setup_step(context, page, {"action": "emulate_forced_colors", "value": "active"})
    )
    asyncio.run(
        _apply_setup_step(context, page, {"action": "emulate_reduced_motion", "value": "reduce"})
    )

    assert page.calls == [
        {"color_scheme": "dark"},
        {"forced_colors": "active"},
        {"reduced_motion": "reduce"},
    ]


def test_zoom_setup_reduces_layout_viewport_to_trigger_reflow() -> None:
    class Page:
        def __init__(self) -> None:
            self.viewport_size = {"width": 1440, "height": 900}
            self.resized: dict[str, int] | None = None

        async def set_viewport_size(self, size: dict[str, int]) -> None:
            self.resized = size

    page = Page()
    asyncio.run(_apply_setup_step(object(), page, {"action": "set_zoom", "value": 2.0}))

    assert page.resized == {"width": 720, "height": 450}


# --- load_viewport_presets -----------------------------------------------


def test_viewport_presets_default_when_missing(tmp_path: Path) -> None:
    out = load_viewport_presets(tmp_path / "nope.json")
    assert out == DEFAULT_VIEWPORT_PRESETS


def test_viewport_presets_skips_underscore_keys(tmp_path: Path) -> None:
    f = tmp_path / "vp.json"
    f.write_text(
        json.dumps(
            {
                "presets": {
                    "_comment": {"width": 1, "height": 1},
                    "mobile": {"width": 390, "height": 844, "deviceScaleFactor": 2},
                }
            }
        )
    )
    out = load_viewport_presets(f)
    assert "_comment" not in out
    assert out["mobile"]["width"] == 390


def test_viewport_presets_malformed_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "vp.json"
    f.write_text("{ not valid json")
    out = load_viewport_presets(f)
    # JSON decode failure -> defaults
    assert out == DEFAULT_VIEWPORT_PRESETS


def test_viewport_presets_wrong_shape_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "vp.json"
    f.write_text(json.dumps({"presets": "not a dict"}))
    out = load_viewport_presets(f)
    assert out == DEFAULT_VIEWPORT_PRESETS


def test_viewport_presets_invalid_entry_skipped(tmp_path: Path) -> None:
    f = tmp_path / "vp.json"
    # 'broken' is missing required 'height' key -> skipped with warning;
    # 'good' is valid -> included.
    f.write_text(
        json.dumps(
            {
                "presets": {
                    "broken": {"width": "not-an-int"},
                    "good": {"width": 800, "height": 600},
                }
            }
        )
    )
    out = load_viewport_presets(f)
    assert "good" in out
    assert "broken" not in out


# --- load_states ---------------------------------------------------------


def test_load_states_default_when_missing(tmp_path: Path) -> None:
    out = load_states(tmp_path / "nope.json")
    assert "default" in out


def test_load_states_drops_underscore_prefixed(tmp_path: Path) -> None:
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"states": {"_comment": {}, "hover": {"setup_steps": []}}}))
    out = load_states(f)
    assert "_comment" not in out
    assert "hover" in out


def test_load_states_wrong_shape_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"states": "not a dict"}))
    out = load_states(f)
    # Default-only fallback.
    assert "default" in out


# --- _slug ---------------------------------------------------------------


def test_slug_from_url_path() -> None:
    assert _slug("https://example.com/about/team") == "about-team"


def test_slug_no_path_uses_netloc() -> None:
    assert _slug("https://example.com/") == "example.com"


def test_slug_special_chars_normalized() -> None:
    assert _slug("https://example.com/foo%20bar?x=1") == "foo-20bar"


# --- _concurrency --------------------------------------------------------


def test_concurrency_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEEN_CONCURRENCY", raising=False)
    assert _concurrency() == 4


def test_concurrency_garbage_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEN_CONCURRENCY", "not-a-number")
    assert _concurrency() == 4


def test_concurrency_negative_clamps_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEN_CONCURRENCY", "-3")
    assert _concurrency() == 1


def test_concurrency_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEN_CONCURRENCY", "10000")
    assert _concurrency() == MAX_CAPTURE_CONCURRENCY


def test_requested_matrix_rejects_unknown_or_unsafe_ids() -> None:
    presets = {"desktop": {"width": 100, "height": 100, "deviceScaleFactor": 1}}
    states = {"default": {"setup_steps": []}}
    with pytest.raises(ValueError, match="unknown viewport"):
        _validate_requested_matrix(
            CaptureConfig(target="https://example.com", viewports=["typo"]),
            presets,
            states,
        )
    with pytest.raises(ValueError, match="invalid state"):
        _validate_requested_matrix(
            CaptureConfig(
                target="https://example.com",
                viewports=["desktop"],
                states=["../../escape"],
            ),
            presets,
            states,
        )


def test_requested_matrix_rejects_empty_and_duplicate_lists() -> None:
    presets = {"desktop": {"width": 100, "height": 100, "deviceScaleFactor": 1}}
    states = {"default": {"setup_steps": []}}
    with pytest.raises(ValueError, match="at least one viewport"):
        _validate_requested_matrix(
            CaptureConfig(target="https://example.com", viewports=[]),
            presets,
            states,
        )
    with pytest.raises(ValueError, match="duplicate states"):
        _validate_requested_matrix(
            CaptureConfig(
                target="https://example.com",
                viewports=["desktop"],
                states=["default", "default"],
            ),
            presets,
            states,
        )


def test_screenshot_options_cap_hostile_full_page_geometry() -> None:
    cfg = CaptureConfig(target="https://example.com", full_page=True)
    preset = {"width": 1440, "height": 900, "deviceScaleFactor": 2}
    options, coverage = _screenshot_options(
        cfg,
        preset,
        {"documentSize": {"width": 100_000, "height": 1_000_000}},
    )
    assert options["full_page"] is False
    assert options["clip"]["width"] <= 8_000
    assert options["clip"]["height"] <= 20_000
    assert options["clip"]["width"] <= preset["width"]
    assert options["clip"]["height"] <= preset["height"]
    assert options["clip"]["width"] * options["clip"]["height"] * 4 <= 16_000_000
    assert "capture_beyond_viewport" not in options
    assert coverage["complete"] is False
    assert coverage["captured_css_px"] == {"width": 1440, "height": 900}


def test_screenshot_options_keep_normal_page_full_size() -> None:
    cfg = CaptureConfig(target="https://example.com", full_page=True)
    preset = {"width": 1440, "height": 900, "deviceScaleFactor": 1}
    options, coverage = _screenshot_options(
        cfg,
        preset,
        {"documentSize": {"width": 1440, "height": 5000}},
    )
    assert options == {"full_page": True}
    assert coverage["complete"] is True


# --- CaptureConfig dataclass ---------------------------------------------


def test_capture_config_defaults() -> None:
    cfg = CaptureConfig(target="https://example.com/")
    assert cfg.viewports == ["mobile", "tablet", "desktop"]
    assert cfg.states == ["default"]
    assert cfg.allow_internal is False
    assert cfg.goto_timeout_ms == 30_000
