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
from unittest import mock

import pytest

from harness.capture import (
    DEFAULT_VIEWPORT_PRESETS,
    MAX_CAPTURE_CONCURRENCY,
    CaptureBlocked,
    CaptureConfig,
    _apply_setup_step,
    _browser_launch_failure_message,
    _capture_one,
    _concurrency,
    _enrich_accessibility_names,
    _goto_with_retries,
    _parse_aria_snapshot_root,
    _screenshot_options,
    _slug,
    _url_expectation_failure,
    _url_matches_expectation,
    _validate_capture_expectations,
    _validate_requested_matrix,
    load_states,
    load_viewport_presets,
    preflight,
)


def test_capture_config_preserves_0_8_positional_order() -> None:
    cfg = CaptureConfig(
        "https://example.com",
        ["desktop"],
        ["default"],
        False,
        "#ready",
        Path("auth-steps.json"),
        Path("auth.py"),
        Path("captures"),
        Path("viewports.json"),
        Path("states.json"),
        True,
        True,
        12_345,
        678,
        True,
    )

    assert cfg.wait_selector == "#ready"
    assert cfg.auth_steps_path == Path("auth-steps.json")
    assert cfg.auth_script == Path("auth.py")
    assert cfg.outdir == Path("captures")
    assert cfg.allow_internal is True
    assert cfg.allow_file is True
    assert cfg.goto_timeout_ms == 12_345
    assert cfg.settle_ms == 678
    assert cfg.dismiss_banners is True
    assert cfg.expect_url is None
    assert cfg.interactive_auth is False
    assert cfg.allow_origins == []


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


def test_accessibility_name_enrichment_marks_snapshot_miss_incomplete() -> None:
    class Locator:
        async def aria_snapshot(self, *, timeout: int) -> str:
            raise RuntimeError("snapshot unavailable")

    class Page:
        async def evaluate(self, _script: str, _arg: object) -> None:
            return None

        def locator(self, _selector: str) -> Locator:
            return Locator()

    dom = {
        "elements": [
            {"index": 0, "tag": "button", "name": "Fallback", "role": "button"},
        ]
    }
    coverage = asyncio.run(_enrich_accessibility_names(Page(), dom))

    assert coverage["attempted"] == 1
    assert coverage["enriched"] == 0
    assert coverage["failed"] == 1
    assert coverage["complete"] is False
    assert coverage["reason"] == "accessibility-name-miss"


def test_browser_launch_failure_message_explains_missing_chromium() -> None:
    message = _browser_launch_failure_message(RuntimeError("Executable doesn't exist at /cache"))

    assert "Chromium is not installed" in message
    assert "python -m playwright install chromium" in message


def test_browser_launch_failure_message_preserves_other_errors() -> None:
    assert _browser_launch_failure_message(RuntimeError("sandbox denied")) == (
        "could not launch Chromium: sandbox denied"
    )


def test_url_expectation_allows_canonical_changes_only() -> None:
    target = "http://example.com/account/?mode=poc"

    assert _url_matches_expectation(target, "https://example.com/account?mode=poc", None)
    assert not _url_matches_expectation(target, "https://example.com/sign-in", None)
    assert not _url_matches_expectation(target, "https://auth.example.com/account", None)
    assert not _url_matches_expectation(
        "https://example.com/app?mode=poc",
        "https://example.com/app?mode=login",
        None,
    )
    assert not _url_matches_expectation(
        "https://example.com/#/poc",
        "https://example.com/#/login",
        None,
    )


def test_explicit_url_glob_accepts_an_intended_redirect() -> None:
    assert _url_matches_expectation(
        "https://example.com/",
        "https://example.com/en/dashboard?tab=one",
        "https://example.com/*/dashboard",
    )


def test_url_expectation_canonicalizes_unicode_host_and_path() -> None:
    assert _url_matches_expectation(
        "https://éxample.com/café",
        "https://xn--xample-9ua.com/caf%C3%A9",
        None,
    )


def test_url_expectation_rejects_authority_wildcard() -> None:
    with pytest.raises(ValueError, match="only in the path"):
        _validate_capture_expectations(
            CaptureConfig(
                target="https://example.com/dashboard",
                expect_url="https://*/dashboard",
            )
        )


def test_url_expectation_failure_redacts_query_values() -> None:
    cfg = CaptureConfig(target="https://example.com/poc?preview_token=secret")

    failure = _url_expectation_failure(
        cfg,
        "https://example.com/sign-in?callback_token=also-secret",
    )

    assert failure is not None
    assert failure["reason_code"] == "unexpected-url"
    assert failure["target"] == "https://example.com/poc"
    assert failure["observed_url"] == "https://example.com/sign-in"
    assert "secret" not in json.dumps(failure)


def test_capture_expectations_reject_unsafe_or_ambiguous_inputs() -> None:
    with pytest.raises(ValueError, match="absolute"):
        _validate_capture_expectations(
            CaptureConfig(target="https://example.com", expect_url="/dashboard")
        )
    with pytest.raises(ValueError, match="absolute"):
        _validate_capture_expectations(CaptureConfig(target="https://example.com", expect_url="*"))
    with pytest.raises(ValueError, match="control"):
        _validate_capture_expectations(
            CaptureConfig(target="https://example.com", expect_url="https://example.com/\nlogin")
        )
    with pytest.raises(ValueError, match="selector"):
        _validate_capture_expectations(
            CaptureConfig(target="https://example.com", wait_selector="${injected}")
        )


@pytest.mark.asyncio
async def test_unexpected_redirect_writes_diagnostic_artifacts_before_blocking(
    tmp_path: Path,
) -> None:
    class Page:
        url = "https://example.com/sign-in?callback_token=secret"

        async def goto(self, *_args, **_kwargs) -> None:
            return None

        async def wait_for_timeout(self, _timeout: int) -> None:
            return None

        async def evaluate(self, script: str, *_args):
            if "document.fonts" in script:
                return None
            return {
                "url": self.url,
                "title": "Sign in",
                "documentSize": {"width": 1280, "height": 720},
                "elements": [],
                "coverage": {"complete": True, "reason": None},
            }

        async def screenshot(self, **_options) -> bytes:
            return b"diagnostic"

    class Context:
        def __init__(self) -> None:
            self.page = Page()

        async def clear_cookies(self) -> None:
            return None

        async def clear_permissions(self) -> None:
            return None

        async def route(self, *_args) -> None:
            return None

        async def route_web_socket(self, *_args) -> None:
            return None

        async def new_page(self) -> Page:
            return self.page

        async def close(self) -> None:
            return None

    class Browser:
        async def new_context(self, **_options) -> Context:
            return Context()

    cfg = CaptureConfig(
        target="https://example.com/poc?preview_token=secret",
        viewports=["desktop"],
        states=["default"],
        outdir=tmp_path,
    )

    with pytest.raises(CaptureBlocked, match="different URL") as exc:
        await _capture_one(
            Browser(),
            cfg,
            {"desktop": {"width": 1280, "height": 720, "deviceScaleFactor": 1}},
            {"default": {"setup_steps": []}},
            "desktop",
            "default",
        )

    assert exc.value.reason_code == "unexpected-url"
    assert exc.value.screen_path.exists()
    dom = json.loads(exc.value.dom_path.read_text())
    expectation = dom["coverage"]["expectation"]
    assert expectation["status"] == "blocked"
    assert expectation["observed_url"] == "https://example.com/sign-in"
    assert "secret" not in exc.value.dom_path.read_text()


@pytest.mark.asyncio
async def test_delayed_redirect_after_dom_capture_is_still_blocked(tmp_path: Path) -> None:
    class Page:
        url = "https://example.com/product"

        async def goto(self, *_args, **_kwargs) -> None:
            return None

        async def wait_for_timeout(self, _timeout: int) -> None:
            return None

        async def evaluate(self, script: str, *_args):
            if "document.fonts" in script:
                return None
            return {
                "url": self.url,
                "title": "Product",
                "documentSize": {"width": 800, "height": 600},
                "elements": [],
                "coverage": {"complete": True, "reason": None},
            }

        async def screenshot(self, **_options) -> bytes:
            self.url = "https://example.com/sign-in?token=late-secret"
            return b"diagnostic"

    class Context:
        def __init__(self) -> None:
            self.page = Page()

        async def clear_cookies(self) -> None:
            return None

        async def clear_permissions(self) -> None:
            return None

        async def route(self, *_args) -> None:
            return None

        async def route_web_socket(self, *_args) -> None:
            return None

        async def new_page(self) -> Page:
            return self.page

        async def close(self) -> None:
            return None

    class Browser:
        async def new_context(self, **_options) -> Context:
            return Context()

    cfg = CaptureConfig(
        target="https://example.com/product",
        viewports=["desktop"],
        states=["default"],
        outdir=tmp_path,
    )
    with pytest.raises(CaptureBlocked) as exc:
        await _capture_one(
            Browser(),
            cfg,
            {"desktop": {"width": 800, "height": 600, "deviceScaleFactor": 1}},
            {"default": {"setup_steps": []}},
            "desktop",
            "default",
        )
    assert exc.value.reason_code == "unexpected-url"
    assert "late-secret" not in exc.value.dom_path.read_text()


@pytest.mark.asyncio
async def test_navigation_failure_logs_never_expose_query_secret(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        async def goto(self, *_args, **_kwargs) -> None:
            raise RuntimeError(
                "net::ERR_CONNECTION_REFUSED at https://example.com/review?token=sentinel-secret"
            )

    monkeypatch.setattr(asyncio, "sleep", mock.AsyncMock())
    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        await _goto_with_retries(
            Page(),
            "https://example.com/review?token=sentinel-secret",
            1_000,
        )
    assert "sentinel-secret" not in caplog.text


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
    assert cfg.allow_origins == []
    assert cfg.allow_file is False
    assert cfg.file_request_roots == ()
    assert cfg.file_document_paths == ()
    assert cfg.internal_request_origins == ()
    assert cfg.goto_timeout_ms == 30_000
    assert cfg.expect_url is None
    assert cfg.interactive_auth is False


def test_preflight_scopes_file_requests_to_explicit_target_and_auth_roots(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "surface"
    auth_dir = tmp_path / "auth"
    target_dir.mkdir()
    auth_dir.mkdir()
    target = target_dir / "index.html"
    auth_page = auth_dir / "login.html"
    target.write_text("surface", encoding="utf-8")
    auth_page.write_text("login", encoding="utf-8")
    steps_path = tmp_path / "auth.json"
    steps_path.write_text(
        json.dumps({"steps": [{"action": "goto", "url": auth_page.as_uri()}]}),
        encoding="utf-8",
    )
    cfg = CaptureConfig(
        target=target.as_uri(),
        allow_file=True,
        auth_steps_path=steps_path,
    )

    preflight(cfg)

    assert set(cfg.file_request_roots) == {
        target_dir.resolve(),
        auth_dir.resolve(),
    }
    assert set(cfg.file_document_paths) == {
        target.resolve(),
        auth_page.resolve(),
    }


def test_preflight_scopes_internal_requests_and_requires_explicit_extra_origins() -> None:
    cfg = CaptureConfig(
        target="http://127.0.0.1:3000/app",
        allow_internal=True,
        allow_origins=["http://127.0.0.1:8787"],
    )

    preflight(cfg)

    assert set(cfg.internal_request_origins) == {
        ("http", "127.0.0.1", 3000),
        ("http", "127.0.0.1", 8787),
    }

    with pytest.raises(ValueError, match="requires --allow-internal"):
        preflight(
            CaptureConfig(
                target="https://example.com",
                allow_origins=["http://127.0.0.1:8787"],
            )
        )
    with pytest.raises(ValueError, match="must not include a path"):
        preflight(
            CaptureConfig(
                target="http://127.0.0.1:3000",
                allow_internal=True,
                allow_origins=["http://127.0.0.1:8787/api"],
            )
        )
