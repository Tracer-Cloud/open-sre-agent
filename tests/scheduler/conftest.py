"""Shared fixtures for :mod:`platform.scheduler` tests.

Automatically registers the investigation-runner bootstrap so scheduler tests
can execute tasks without every test having to bind a runner explicitly. The
bootstrap resolves ``tools.investigation.capability.run_investigation`` at call
time, so tests that monkeypatch that attribute still take effect.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def install_scheduler_investigation_runner() -> Iterator[None]:
    """Bind + tear down the scheduler's investigation runner for every test.

    The layered contract forbids ``platform.scheduler`` from importing from
    ``tools`` directly (T-4 layering audit, issue #3352). The runner is
    supplied by :mod:`tools.investigation.scheduler_bootstrap`; installing it
    per test keeps the module-level registry isolated across the suite.
    """
    from platform.scheduler.investigation_runner import register_investigation_runner
    from tools.investigation.scheduler_bootstrap import install

    install()
    try:
        yield
    finally:
        register_investigation_runner(None)


@pytest.fixture(autouse=True)
def install_scheduler_agent_runner() -> Iterator[None]:
    """Bind + tear down the scheduler's agent runner for every test."""
    from integrations.sentry.scheduler_bootstrap import install
    from platform.scheduler.agent_runner import register_agent_runner

    install()
    try:
        yield
    finally:
        register_agent_runner(None)
