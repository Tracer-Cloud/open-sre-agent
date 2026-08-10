"""``LLM_MAX_TOKENS`` resolution across every provider, model role, and transport.

The defect this pins: each builder used to carry its own frozen token budget, so
the documented override could be honoured on one transport and silently dropped
on another. Every construction path now routes through
:func:`core.llm.shared.max_tokens.resolve_max_output_tokens`.

The settings layer is covered separately in ``tests/config/test_config.py``
(``LLM_MAX_TOKENS`` unset / blank -> ``LLMSettings.max_tokens is None``). These
tests take the resolved ``max_tokens`` value as their input and assert on the
budget the constructed client actually carries.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.llm.client_builders import build_agent_client, build_reasoning_client
from core.llm.shared.max_tokens import resolve_max_output_tokens
from core.llm.transports.litellm.routing import (
    build_litellm_agent_client,
    build_litellm_llm_client,
)
from core.llm.types import LLMRoute, ModelType

#: Every provider that reaches a ``max_tokens`` call site in either dispatcher.
_ALL_PROVIDERS = (
    "anthropic",
    "azure-openai",
    "bedrock",
    "deepseek",
    "gemini",
    "groq",
    "minimax",
    "nvidia",
    "ollama",
    "openai",
    "openrouter",
    "vertex-ai",
)

#: The budgets shipped today, as literals. Deliberately **not** derived from
#: ``FIRST_PARTY_PROVIDERS`` / ``OPENAI_COMPATIBLE_PROVIDERS``: asserting a built
#: client against the table that produced it is tautological and cannot notice a
#: table change moving a live token budget.
_DEFAULT_BUDGET = 4096
_OLLAMA_AGENT_BUDGET = 1024

_OVERRIDE = 32000

_UNSET_BUDGET_CASES = [
    (
        provider,
        role,
        transport,
        _OLLAMA_AGENT_BUDGET if (provider, role) == ("ollama", "agent") else _DEFAULT_BUDGET,
    )
    for provider in _ALL_PROVIDERS
    for role in ("agent", "reasoning")
    for transport in ("sdk", "litellm")
]

#: One case per dispatcher branch that holds a ``resolve_max_output_tokens``
#: call site, minus the two Ollama-agent branches (covered by the Ollama test)
#: and the CLI branch (covered by the CLI test).
_OVERRIDE_BRANCH_CASES = [
    ("anthropic", "agent", "sdk"),  # client_builders: first-party agent
    ("anthropic", "reasoning", "sdk"),  # client_builders: first-party reasoning
    ("ollama", "reasoning", "sdk"),  # client_builders: openai-compat reasoning
    ("anthropic", "agent", "litellm"),  # routing: first-party agent
    ("anthropic", "reasoning", "litellm"),  # routing: first-party reasoning
    ("azure-openai", "agent", "litellm"),  # routing: azure agent
    ("azure-openai", "reasoning", "litellm"),  # routing: azure reasoning
    ("vertex-ai", "agent", "litellm"),  # routing: vertex agent
    ("vertex-ai", "reasoning", "litellm"),  # routing: vertex reasoning
    ("ollama", "reasoning", "litellm"),  # routing: openai-compat reasoning
]

_MODEL_TIERS = ("reasoning", "classification", "toolcall")
_SETTINGS_PREFIXES = (
    "anthropic",
    "azure_openai",
    "bedrock",
    "deepseek",
    "gemini",
    "groq",
    "minimax",
    "nvidia",
    "openai",
    "openrouter",
    "vertex_ai",
)
_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MINIMAX_API_KEY",
    "NVIDIA_API_KEY",
    "OLLAMA_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)


def _stub_api_key(_env_var: str) -> str:
    return "test-key"


@pytest.fixture(autouse=True)
def _provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK clients validate credentials/region at construction time."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    for env_var in _CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", _stub_api_key
    )


def _settings(max_tokens: int | None) -> SimpleNamespace:
    """Build a settings fake carrying every attribute the dispatchers read.

    ``max_tokens=None`` is what ``LLMSettings`` holds when ``LLM_MAX_TOKENS`` is
    unset or blank.
    """
    settings = SimpleNamespace(max_tokens=max_tokens)
    for prefix in _SETTINGS_PREFIXES:
        for tier in _MODEL_TIERS:
            setattr(settings, f"{prefix}_{tier}_model", f"{prefix}-{tier}")
    settings.ollama_model = "llama3.1:8b"
    settings.ollama_host = "http://localhost:11434"
    settings.azure_openai_base_url = "https://example.openai.azure.invalid"
    settings.azure_openai_api_version = "2024-06-01"
    settings.vertex_ai_project = "test-project"
    settings.vertex_ai_location = "us-central1"
    return settings


def _build_client(provider: str, role: str, transport: str, settings: Any) -> Any:
    """Construct the client for one provider/role/transport combination."""
    if transport == "litellm":
        if role == "agent":
            return build_litellm_agent_client(settings, provider)
        return build_litellm_llm_client(settings, provider, ModelType.REASONING)
    route = LLMRoute(
        settings=settings,
        provider=provider,
        cli_provider_registration=None,
        use_litellm=False,
    )
    if role == "agent":
        return build_agent_client(route)
    return build_reasoning_client(route, ModelType.REASONING)


@pytest.mark.parametrize(("provider", "role", "transport", "expected"), _UNSET_BUDGET_CASES)
def test_unset_env_keeps_every_provider_at_its_current_budget(
    provider: str, role: str, transport: str, expected: int
) -> None:
    """No override must leave every live token budget exactly where it is today."""
    client = _build_client(provider, role, transport, _settings(None))

    assert client._max_tokens == expected


@pytest.mark.parametrize(("provider", "role", "transport"), _OVERRIDE_BRANCH_CASES)
def test_explicit_override_reaches_every_dispatcher_branch(
    provider: str, role: str, transport: str
) -> None:
    """Every construction branch must carry the override, not its frozen constant."""
    client = _build_client(provider, role, transport, _settings(_OVERRIDE))

    assert client._max_tokens == _OVERRIDE


def _stub_adapter() -> SimpleNamespace:
    return SimpleNamespace()


def test_explicit_override_reaches_the_cli_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI builder must hand the override to ``build_cli_client``.

    Wiring only: ``integrations/llm_cli/runner.py`` discards ``max_tokens``
    because CLI adapters expose no scriptable token limit, so no test can assert
    a real limit on that transport.
    """
    captured: dict[str, Any] = {}

    def _capture_build_cli_client(_adapter: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr("platform.harness_ports.build_cli_client", _capture_build_cli_client)
    registration = SimpleNamespace(model_env_key="TEST_CLI_MODEL", adapter_factory=_stub_adapter)
    route = LLMRoute(
        settings=_settings(_OVERRIDE),
        provider="codex",
        cli_provider_registration=registration,
        use_litellm=False,
    )

    build_reasoning_client(route, ModelType.REASONING)

    assert captured["max_tokens"] == _OVERRIDE


@pytest.mark.parametrize("transport", ["litellm", "sdk"])
def test_ollama_agent_override_wins_over_the_1024_default(transport: str) -> None:
    """The one provider with a non-default budget must not swallow the override."""
    client = _build_client("ollama", "agent", transport, _settings(_OVERRIDE))

    assert client._max_tokens == _OVERRIDE


def test_resolver_tolerates_settings_without_the_attribute() -> None:
    """``getattr`` tolerance is intentional: settings fakes omit ``max_tokens``."""
    assert (
        resolve_max_output_tokens(SimpleNamespace(max_tokens=None), provider_default=4096) == 4096
    )
    assert (
        resolve_max_output_tokens(SimpleNamespace(max_tokens=32000), provider_default=4096) == 32000
    )
    assert resolve_max_output_tokens(SimpleNamespace(), provider_default=4096) == 4096
