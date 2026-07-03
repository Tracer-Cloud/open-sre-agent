"""Shared fixtures for cross-surface orchestration tests."""

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
