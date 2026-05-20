"""Pytest fixtures for co-located routing tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings,
)
from app.utils.config import load_env

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_ENV_PATH = _PROJECT_ROOT / ".env"
_ROUTING_TEST_DEFAULT_ENV = {
    "OPENSRE_SENTRY_DISABLED": "1",
    "OPENSRE_NO_TELEMETRY": "1",
    "OPENSRE_INVESTIGATION_SOURCE": "test",
}


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Load project settings for co-located routing tests."""
    load_env(_ENV_PATH, override=False)


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
def _resolve_live_llm_configuration(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Let live LLM routing tests run with Anthropic or OpenAI credentials."""
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
        hint += f", fallback providers={DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS!r}"
        pytest.fail(f"Live LLM routing tests require usable LLM configuration:{hint}. {msg}")

    from app.services.llm_client import reset_llm_singletons

    monkeypatch.setenv("LLM_PROVIDER", settings.provider)
    reset_llm_singletons()
    yield
    reset_llm_singletons()


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
