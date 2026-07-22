"""Tests for the lightweight per-stage timing helper in harness/_timing.py.

The module exposes:
    - stage(name)            context manager that logs and (optionally) captures
    - enable_profiling()     turn on summary capture + force INFO logs
    - disable_profiling()    turn off summary capture
    - drain_summary()        return + clear the summary buffer
    - format_summary(...)    render the timing table

These tests verify each behavior in isolation. They reset profiling between
tests via the autouse fixture so order does not matter.
"""

from __future__ import annotations

import logging
import re

import pytest

from harness import _timing


@pytest.fixture(autouse=True)
def _reset_timing_state() -> None:
    """Make every test start from a clean module state.

    The module-level `_PROFILING` flag and `_SUMMARY` list are designed to be
    process-global, so reset them so test ordering is irrelevant.
    """
    _timing.disable_profiling()
    _timing.drain_summary()
    yield
    _timing.disable_profiling()
    _timing.drain_summary()


def test_stage_logs_name_and_ms(caplog: pytest.LogCaptureFixture) -> None:
    """`stage("x")` must emit an INFO log naming the stage and an ms count."""
    with caplog.at_level(logging.INFO, logger="keen.timing"), _timing.stage("x"):
        pass

    matching = [r for r in caplog.records if r.name == "keen.timing"]
    assert len(matching) == 1, "expected exactly one timing log line"
    msg = matching[0].getMessage()
    # We don't assert on the exact ms value (it varies); we assert the shape.
    assert re.match(r"stage x: \d+ ms$", msg), msg


def test_stage_logs_even_on_exception(caplog: pytest.LogCaptureFixture) -> None:
    """Exceptions in the stage body must still produce a timing log."""
    with (
        caplog.at_level(logging.INFO, logger="keen.timing"),
        pytest.raises(RuntimeError, match="boom"),
        _timing.stage("failing"),
    ):
        raise RuntimeError("boom")

    msgs = [r.getMessage() for r in caplog.records if r.name == "keen.timing"]
    assert any("stage failing:" in m for m in msgs)


def test_nested_stages_log_independently(caplog: pytest.LogCaptureFixture) -> None:
    """Two nested stages each produce their own timing log line.

    The inner stage exits first (so logs in reverse-source order).
    """
    with (
        caplog.at_level(logging.INFO, logger="keen.timing"),
        _timing.stage("outer"),
        _timing.stage("inner"),
    ):
        pass

    msgs = [r.getMessage() for r in caplog.records if r.name == "keen.timing"]
    # We expect two messages, inner first, outer second.
    assert len(msgs) == 2
    assert "stage inner:" in msgs[0]
    assert "stage outer:" in msgs[1]


def test_summary_captures_each_stage_when_profiling() -> None:
    """When profiling is on, the summary buffer should accumulate one entry per stage."""
    _timing.enable_profiling()

    with _timing.stage("alpha"):
        pass
    with _timing.stage("beta"):
        pass
    with _timing.stage("gamma"):
        pass

    summary = _timing.drain_summary()
    names = [name for name, _ms in summary]
    assert names == ["alpha", "beta", "gamma"]
    assert all(isinstance(ms, int) and ms >= 0 for _name, ms in summary)


def test_summary_empty_when_profiling_off() -> None:
    """With profiling disabled, the summary buffer must stay empty."""
    # default state from the fixture is off
    with _timing.stage("noop"):
        pass
    assert _timing.drain_summary() == []


def test_format_summary_renders_total_and_alignment() -> None:
    """`format_summary` should produce a header, one line per stage, and a total row."""
    out = _timing.format_summary(
        [("capture", 12345), ("decompose", 45), ("report", 67)],
        total_ms=12457,
    )
    assert out.startswith("=== timing summary ===\n")
    assert "capture" in out
    assert "decompose" in out
    assert "report" in out
    assert re.search(r"total\s+12457 ms", out)


def test_format_summary_empty_safe() -> None:
    """Calling format_summary with no timings should still return a valid string."""
    out = _timing.format_summary([], total_ms=None)
    assert "=== timing summary ===" in out
