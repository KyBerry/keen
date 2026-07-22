"""Shared test configuration.

Registers a hypothesis profile with a generous deadline so property tests
don't flake under load. Loads automatically because pytest auto-imports
conftest.py from the tests/ directory.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

# Hypothesis settings docs: https://hypothesis.readthedocs.io/en/latest/settings.html
settings.register_profile(
    "keen",
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.load_profile("keen")
