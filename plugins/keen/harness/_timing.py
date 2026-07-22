"""Lightweight per-stage timing for the harness CLI.

A single-process CLI doesn't need a real metrics pipeline; what it needs is a
way to answer "why was this run slow?". This module provides:

    stage(name) -> context manager
        Logs INFO with the stage name and elapsed ms on exit (success or fail).
        When profiling is on, also appends the timing into a module-level
        buffer so the CLI can render a summary table at the end.

    enable_profiling() / disable_profiling()
        Toggles capture into the summary buffer. The stage logger is also
        forced to INFO so timings are visible even when verbosity is otherwise
        suppressed.

    drain_summary() -> list[tuple[str, int]]
        Returns the accumulated stage timings in insertion order and clears
        the buffer.

    format_summary(timings, total_ms) -> str
        Renders a fixed-width table for stdout/stderr.

This is intentionally synchronous + module-global. The CLI runs one command
per process and the timing is informational, so thread-safety is not a goal.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger("keen.timing")

# When True, every `stage()` exit also appends (name, elapsed_ms) into
# `_SUMMARY`. The CLI flips this on with `--profile` and drains the buffer
# at the end of the run to render the summary table.
_PROFILING: bool = False
_SUMMARY: list[tuple[str, int]] = []


def enable_profiling() -> None:
    """Turn on summary capture and force INFO-level timing logs."""
    global _PROFILING
    _PROFILING = True
    logger.setLevel(logging.INFO)


def disable_profiling() -> None:
    """Turn off summary capture. Does not reset the buffer."""
    global _PROFILING
    _PROFILING = False


def is_profiling() -> bool:
    return _PROFILING


def drain_summary() -> list[tuple[str, int]]:
    """Return accumulated timings and clear the buffer."""
    out = list(_SUMMARY)
    _SUMMARY.clear()
    return out


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a pipeline stage; log INFO with name and ms on exit.

    The log line is emitted whether the body succeeds or raises. When
    profiling is on, the timing is also appended to `_SUMMARY` so the CLI
    can render a summary table.
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("stage %s: %d ms", name, elapsed_ms)
        if _PROFILING:
            _SUMMARY.append((name, elapsed_ms))


def format_summary(timings: list[tuple[str, int]], total_ms: int | None = None) -> str:
    """Render an aligned table of stage timings.

    Example::

        === timing summary ===
        capture    12345 ms
        decompose     45 ms
        analyze      234 ms
        tokens        12 ms
        report        67 ms
        total      12703 ms
    """
    if not timings and total_ms is None:
        return "=== timing summary ===\n(no stages recorded)\n"
    name_width = max((len(name) for name, _ in timings), default=5)
    name_width = max(name_width, len("total"))
    ms_width = max((len(str(ms)) for _, ms in timings), default=1)
    if total_ms is not None:
        ms_width = max(ms_width, len(str(total_ms)))
    lines = ["=== timing summary ==="]
    for name, ms in timings:
        lines.append(f"{name:<{name_width}}  {ms:>{ms_width}} ms")
    if total_ms is not None:
        lines.append(f"{'total':<{name_width}}  {total_ms:>{ms_width}} ms")
    return "\n".join(lines) + "\n"
