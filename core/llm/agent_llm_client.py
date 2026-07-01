"""Dispatch entrypoint for the investigation agent's tool-calling LLM client."""

from __future__ import annotations

import os
from typing import Any

from core.llm.openai_compat_providers import (
    is_openai_compat_provider,
    resolve_openai_compat_provider,
)
from core.llm.sdk.agent_clients import (
    AnthropicAgentClient,
    BedrockAgentClient,
    BedrockConverseAgentClient,
    CLIBackedAgentClient,
    OpenAIAgentClient,
    _try_parse_tool_call_json,
)
from core.llm.tool_schema_normalize import build_openai_tool_specs
from core.llm.transport_mode import current_llm_transport, use_litellm_transport
from core.llm.types import AgentLLMClient

__all__ = [
    "AnthropicAgentClient",
    "BedrockAgentClient",
    "BedrockConverseAgentClient",
    "CLIBackedAgentClient",
    "OpenAIAgentClient",
    "_try_parse_tool_call_json",
    "build_openai_tool_specs",
    "get_agent_llm",
    "reset_agent_client",
]

# The tool-calling clients (Anthropic / OpenAI / Bedrock-Converse / CLI-backed /
# LiteLLM) all satisfy this structural contract; callers only use the shared
# ``tool_schemas`` / ``invoke`` surface, never provider-specific attributes.
_AgentClientType = AgentLLMClient


class _AgentClientState:
    """Mutable holder for the cached investigation agent LLM client.

    Wrapped in a class so transport/client fields are read/written via attribute
    access on a stable container, avoiding the ``global`` keyword (which CodeQL's
    ``py/unused-global-variable`` rule misreports despite the in-function reads).
    """

    client: _AgentClientType | None = None
    transport: str | None = None


_agent_state = _AgentClientState()


def get_agent_llm() -> _AgentClientType:
    """Return a singleton tool-calling LLM client for the investigation agent."""
    transport = current_llm_transport()
    if _agent_state.client is not None and _agent_state.transport != transport:
        _agent_state.client = None
    if _agent_state.client is not None:
        return _agent_state.client

    from pydantic import ValidationError

    from config.config import resolve_llm_settings

    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        raise RuntimeError(str(exc)) from exc

    from config.llm_auth.auth_method import effective_llm_provider, get_configured_llm_auth_method

    provider = settings.provider
    runtime_provider = effective_llm_provider(provider, get_configured_llm_auth_method(provider))

    if (cli_reg := _get_cli_provider_registration(runtime_provider)) is not None:
        model_name = os.getenv(cli_reg.model_env_key, "").strip() or None
        _agent_state.client = CLIBackedAgentClient(cli_reg.adapter_factory(), model=model_name)
        _agent_state.transport = transport
        return _agent_state.client

    if use_litellm_transport():
        from core.llm.litellm.routing import build_litellm_agent_client

        _agent_state.client = build_litellm_agent_client(settings, runtime_provider)
        _agent_state.transport = transport
        return _agent_state.client

    if runtime_provider == "openai":
        from config.config import OPENAI_LLM_CONFIG

        _agent_state.client = OpenAIAgentClient(
            model=settings.openai_reasoning_model,
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
        )
    elif is_openai_compat_provider(runtime_provider):
        _agent_state.client = _create_sdk_openai_compat_client(settings, runtime_provider)
    elif runtime_provider == "bedrock":
        from config.config import BEDROCK_LLM_CONFIG
        from core.llm.bedrock_model_ids import is_anthropic_bedrock_model

        model = settings.bedrock_reasoning_model
        if is_anthropic_bedrock_model(model):
            _agent_state.client = BedrockAgentClient(
                model=model,
                max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
            )
        else:
            _agent_state.client = BedrockConverseAgentClient(
                model=model,
                max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
            )
    else:
        from config.config import ANTHROPIC_LLM_CONFIG

        _agent_state.client = AnthropicAgentClient(
            model=settings.anthropic_reasoning_model,
            max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
        )

    _agent_state.transport = transport
    return _agent_state.client


def _create_sdk_openai_compat_client(settings: Any, provider: str) -> Any:
    resolved = resolve_openai_compat_provider(settings, provider, "reasoning")
    max_tokens = 1024 if provider == "ollama" else resolved.config.max_tokens
    return OpenAIAgentClient(
        model=resolved.model,
        max_tokens=max_tokens,
        base_url=resolved.base_url,
        api_key_env=resolved.api_key_env,
        api_key_default=resolved.api_key_default,
    )


def _get_cli_provider_registration(provider: str) -> Any:
    """Return the CLI registry entry for *provider*, or None if not CLI-backed."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    return get_cli_provider_registration(provider)


def reset_agent_client() -> None:
    """Reset the singleton (for tests / config changes)."""
    _agent_state.client = None
    _agent_state.transport = None
