"""Synthetic tests for the real-world-coverage fixes.

These tests don't launch a browser; they exercise:
- CaptureConfig has the new settle_ms / dismiss_banners fields with defaults.
- COMMON_BANNER_DISMISS contains the expected closed selector list.
- _dismiss_common_banners returns the matched selector or None.
- INSTRUMENT_JS contains the shadow-DOM walk function.

The real Playwright-driven shadow-DOM behavior is covered by the live
captures under /tmp/d-rw/ (see docs/real-world-coverage.md). These unit tests
guard against regressions in the dataclass shape and helper wiring.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from harness.capture import (
    COMMON_BANNER_DISMISS,
    INSTRUMENT_JS,
    CaptureConfig,
    _dismiss_common_banners,
)

# --- CaptureConfig fields ----------------------------------------------------


def test_capture_config_has_settle_ms_default_zero() -> None:
    cfg = CaptureConfig(target="https://example.com/")
    assert cfg.settle_ms == 0


def test_capture_config_has_dismiss_banners_default_false() -> None:
    cfg = CaptureConfig(target="https://example.com/")
    assert cfg.dismiss_banners is False


def test_capture_config_settle_ms_is_settable() -> None:
    cfg = CaptureConfig(target="https://example.com/", settle_ms=1500)
    assert cfg.settle_ms == 1500


def test_capture_config_dismiss_banners_is_settable() -> None:
    cfg = CaptureConfig(target="https://example.com/", dismiss_banners=True)
    assert cfg.dismiss_banners is True


# --- Banner-dismiss whitelist ------------------------------------------------


def test_banner_dismiss_list_is_non_empty() -> None:
    assert len(COMMON_BANNER_DISMISS) >= 5


def test_banner_dismiss_list_contains_onetrust() -> None:
    # OneTrust is the single most common consent platform (~30% of EU sites).
    assert any("onetrust" in s.lower() for s in COMMON_BANNER_DISMISS)


def test_banner_dismiss_list_contains_accept_all_aria() -> None:
    assert any("Accept all" in s for s in COMMON_BANNER_DISMISS)


def test_banner_dismiss_list_no_injection_chars() -> None:
    """Selectors are static literals — defense against future contributors
    adding attacker-controlled fragments by mistake."""
    for s in COMMON_BANNER_DISMISS:
        assert "`" not in s
        assert "${" not in s
        assert "\x00" not in s


# --- _dismiss_common_banners helper ------------------------------------------


def test_dismiss_common_banners_returns_first_match() -> None:
    """When `click(selector)` resolves on the first selector, return that selector."""
    page = AsyncMock()
    page.click = AsyncMock(return_value=None)  # resolves immediately
    result = asyncio.run(_dismiss_common_banners(page))
    assert result == COMMON_BANNER_DISMISS[0]
    page.click.assert_called_once()


def test_dismiss_common_banners_returns_none_when_no_match() -> None:
    """When every click() raises, return None and don't crash."""
    page = AsyncMock()
    page.click = AsyncMock(side_effect=Exception("not found"))
    result = asyncio.run(_dismiss_common_banners(page))
    assert result is None
    # Should try every selector in the list.
    assert page.click.call_count == len(COMMON_BANNER_DISMISS)


def test_dismiss_common_banners_returns_middle_match() -> None:
    """If the first 2 raise and the 3rd succeeds, return the 3rd."""
    page = AsyncMock()
    page.click = AsyncMock(side_effect=[Exception("no"), Exception("no"), None, None])
    result = asyncio.run(_dismiss_common_banners(page))
    assert result == COMMON_BANNER_DISMISS[2]
    assert page.click.call_count == 3


def test_dismiss_common_banners_passes_timeout_to_click() -> None:
    """Each click() must specify a short timeout so an unmatched selector
    doesn't block the capture."""
    page = AsyncMock()
    page.click = AsyncMock(return_value=None)
    asyncio.run(_dismiss_common_banners(page))
    _, kwargs = page.click.call_args
    assert kwargs.get("timeout") == 500


def test_dismiss_common_banners_is_async() -> None:
    assert inspect.iscoroutinefunction(_dismiss_common_banners)


# --- Shadow DOM walk in INSTRUMENT_JS ----------------------------------------


def test_instrument_js_contains_shadow_walk_function() -> None:
    """The shadow-DOM walk must be present in the JS source. Without it,
    components inside <custom-element> are invisible to the harness."""
    assert "collectAll" in INSTRUMENT_JS
    assert "shadowRoot" in INSTRUMENT_JS


def test_instrument_js_shadow_walk_is_iterative_not_recursive() -> None:
    """Recursive walks blow the stack on deeply-nested shadow trees. We
    enforce iterative BFS via a queue."""
    assert "queue" in INSTRUMENT_JS
    # Should not use `collectAll(el.shadowRoot)` recursively from inside itself.
    # Look for the iterative pattern: queue.shift() somewhere.
    assert "queue.shift" in INSTRUMENT_JS


def test_instrument_js_shadow_walk_bounded() -> None:
    """The traversal must be bounded by some multiple of MAX_ELEMENTS to
    prevent pathological pages (deep shadow trees) from blowing memory."""
    assert "const MAX_TRAVERSED = 50000" in INSTRUMENT_JS
    assert "out.length >= MAX_TRAVERSED" in INSTRUMENT_JS


def test_instrument_js_still_caps_results_at_max_elements() -> None:
    """The original 5000-element cap on RESULTS (the returned payload) must
    remain regardless of the shadow walk."""
    assert "const MAX_ELEMENTS = 5000" in INSTRUMENT_JS
    assert "visible.length >= MAX_ELEMENTS" in INSTRUMENT_JS
    assert "visibleTruncated = true" in INSTRUMENT_JS


# --- Integration smoke: importability ----------------------------------------


def test_capture_module_imports_clean() -> None:
    """Sanity: after the new helper + dataclass fields, the module still imports."""
    import harness.capture  # noqa: F401


def test_all_exposed_symbols_present() -> None:
    """The new public-ish names should be importable for downstream callers."""
    from harness.capture import (  # noqa: F401
        COMMON_BANNER_DISMISS,
        INSTRUMENT_JS,
        CaptureConfig,
        _dismiss_common_banners,
    )


@pytest.mark.skip(reason="manual live test: see /tmp/d-rw/ captures for evidence")
def test_real_world_live() -> None:
    """Placeholder. The real evidence lives in /tmp/d-rw/<slug>/ from the
    stress-test run documented in docs/real-world-coverage.md."""
