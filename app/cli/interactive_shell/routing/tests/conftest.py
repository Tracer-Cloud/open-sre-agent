"""Pytest fixtures for co-located routing tests."""

from __future__ import annotations

import sys

import pytest

_ROUTING_TEST_DEFAULT_ENV = {
    "OPENSRE_SENTRY_DISABLED": "1",
    "OPENSRE_NO_TELEMETRY": "1",
    "OPENSRE_INVESTIGATION_SOURCE": "test",
}


@pytest.fixture(autouse=True)
def _routing_test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror test-suite defaults while keeping env mutations isolated per test."""
    for key, value in _ROUTING_TEST_DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _disable_system_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.orchestration.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
