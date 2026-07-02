"""Shared fixtures for cross-surface orchestration tests.

The parity harness records probe runs and resolved integrations in module-level
lists. They are reset inside ``wire_tool_registry``, but a test that drives a
surface without going through that path would otherwise inherit stale entries.
This autouse fixture resets them before every test, so the reset no longer
depends on which configure helper a test happens to call.
"""

from __future__ import annotations

import pytest

from tests.core.agent.orchestration.cross_surface_parity_harness import (
    reset_integrations_seen,
    reset_probe_runs,
)


@pytest.fixture(autouse=True)
def _reset_parity_harness_state() -> None:
    reset_probe_runs()
    reset_integrations_seen()
