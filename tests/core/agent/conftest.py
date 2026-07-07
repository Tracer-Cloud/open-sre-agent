"""Pytest fixtures for co-located turn tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.config import (
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings,
)
from config.grafana_cloud import load_env
from config.llm_auth.credentials import status as credential_status
from config.platform_bootstrap import ensure_project_platform_package
from tests.core.agent._ci_gates import (
    running_in_github_actions,
)

ensure_project_platform_package()


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


_PROJECT_ROOT = _repo_root()
_ENV_PATH = _PROJECT_ROOT / ".env"
_TURN_TEST_DEFAULT_ENV = {
    "OPENSRE_SENTRY_DISABLED": "1",
    "OPENSRE_NO_TELEMETRY": "1",
    "OPENSRE_INVESTIGATION_SOURCE": "test",
}


def _skip_or_fail_live_llm(message: str) -> None:
    if running_in_github_actions():
        pytest.fail(message)
    pytest.skip(message)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register selection flags for the live turn scenario suite.

    The suite is downsampled by default (everywhere, including CI) to a small
    representative subset, then sharded. Use these to change the subset or run
    the full suite on demand. ``TURN_MAX_RUNS`` separately caps each scenario's
    majority-vote ``runs`` (default 1; set ``0``/``all`` to honour fixtures).
    """
    group = parser.getgroup("turn scenarios")
    group.addoption(
        "--turn-select",
        action="store",
        default=None,
        help=(
            "Choose which live turn scenarios run. Use 'all' to run the FULL "
            "suite (the default is a small representative downsample). Otherwise "
            "'<mode>:<n>' where mode is 'complex' (most complex) or 'sample' "
            "(random), and n is a count, a fraction, or a percentage (e.g. "
            "'complex:5', 'sample:0.1', 'sample:10%'). Also settable via the "
            "TURN_SELECT env var."
        ),
    )
    group.addoption(
        "--turn-select-seed",
        action="store",
        default=None,
        help="Random seed for '--turn-select=sample:*' (default: TURN_SELECT_SEED or 1337).",
    )


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Load project settings for co-located turn tests."""
    load_env(_ENV_PATH, override=False)


@pytest.fixture(autouse=True)
def _turn_test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror test-suite defaults while keeping env mutations isolated per test."""
    for key, value in _TURN_TEST_DEFAULT_ENV.items():
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
def _resolve_live_llm_configuration(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Let live LLM turn tests run with Anthropic or OpenAI credentials."""
    if request.node.get_closest_marker("live_llm") is None:
        yield
        return

    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        _skip_or_fail_live_llm(
            f"Live LLM turn tests require usable LLM configuration:{hint}. {msg}"
        )

    auth = credential_status(settings.provider)
    if not auth.configured or auth.stale:
        _skip_or_fail_live_llm(
            "Live LLM turn tests require usable LLM credentials:"
            f" configured provider={settings.provider!r}, auth={auth.source}, detail={auth.detail}"
        )

    from core.llm.factory import reset_llm_clients

    monkeypatch.setenv("LLM_PROVIDER", settings.provider)
    reset_llm_clients()
    yield
    reset_llm_clients()


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.execution_confirm.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


_LIVE_LLM_SKIPS_IN_CI: list[str] = []


def _is_xdist_worker() -> bool:
    """True on pytest-xdist worker processes (not the controller)."""
    return os.getenv("PYTEST_XDIST_WORKER") is not None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Fail the run if any live_llm test skips in CI (controller-only under xdist)."""
    if _is_xdist_worker() or not running_in_github_actions():
        return
    if report.when != "call" or not report.skipped:
        return
    if "live_llm" not in report.keywords:
        return
    _LIVE_LLM_SKIPS_IN_CI.append(f"{report.nodeid}: {report.longrepr}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _is_xdist_worker() or not _LIVE_LLM_SKIPS_IN_CI:
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line("live_llm tests must not skip in CI (fix credentials or shard config):")
        for line in _LIVE_LLM_SKIPS_IN_CI:
            terminal.write_line(f"  - {line}")
    session.exitstatus = 1
