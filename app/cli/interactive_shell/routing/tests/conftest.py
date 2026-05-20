"""Pytest fixtures for co-located routing tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.utils.config import load_env

_PROJECT_ROOT = Path(__file__).resolve().parents[6]
_ENV_PATH = _PROJECT_ROOT / ".env"
_ROUTING_TEST_DEFAULT_ENV = {
    "OPENSRE_SENTRY_DISABLED": "1",
    "OPENSRE_NO_TELEMETRY": "1",
    "OPENSRE_INVESTIGATION_SOURCE": "test",
}


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Load project settings for co-located routing tests."""
    load_env(_ENV_PATH, override=False)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Expose CLI toggles for routing test suites."""
    parser.addoption(
        "--run-full-oracle",
        action="store_true",
        default=False,
        help="Include tier=full live action oracle cases.",
    )


@pytest.fixture()
def run_full_oracle(pytestconfig: pytest.Config) -> bool:
    """Whether full-tier oracle cases are enabled for this run."""
    return bool(pytestconfig.getoption("--run-full-oracle"))


@pytest.fixture(autouse=True)
def _routing_test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror test-suite defaults while keeping env mutations isolated per test."""
    for key, value in _ROUTING_TEST_DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _disable_system_keyring(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    if request.node.get_closest_marker("live_llm") is not None:
        return
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
