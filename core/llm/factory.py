"""Single LLM factory: one provider-routing decision for every model role.

The provider/transport decision (CLI-backed vs LiteLLM vs native SDK, and which
vendor) is resolved once in :func:`resolve_llm_route` and reused by every role, so
an Azure/LiteLLM routing fix cannot drift between the investigation agent and the
reasoning/classification/toolcall clients.

Roles differ only in the *client family* they build: :data:`LLMRole.AGENT` builds
a tool-calling client (``tool_schemas`` / ``invoke``); the other roles build the
streaming reasoning client (``invoke`` / ``invoke_stream`` / ``with_structured_output``)
for a given model tier. ``get_llm(role)`` is the single entrypoint — callers pass an ``LLMRole``.

Import discipline: construction imports (``sdk`` / ``litellm`` / ``config``) are
done lazily inside functions, matching the rest of ``core.llm``. Keeping them lazy
avoids pulling the full provider stack at module import time and holds the
``importlinter`` contract.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, overload

from core.llm.internal.client_cache_key import current_llm_client_cache_key
from core.llm.transport_mode import use_litellm_for_provider
from core.llm.types import AgentLLMClient

_ModelType = Literal["reasoning", "classification", "toolcall"]


class LLMRole(Enum):
    """The model tier a caller needs, independent of provider/transport."""

    AGENT = "agent"  # tool-calling ReAct (action, gather, investigation)
    REASONING = "reasoning"  # streamed assistant answer / complex reasoning
    CLASSIFICATION = "classification"  # mid-tier classifier
    TOOLCALL = "toolcall"  # lightweight tool selection / action planning


# The non-agent roles map onto the model-tier attribute suffix used by settings.
_MODEL_TYPE_BY_ROLE: dict[LLMRole, _ModelType] = {
    LLMRole.REASONING: "reasoning",
    LLMRole.CLASSIFICATION: "classification",
    LLMRole.TOOLCALL: "toolcall",
}


@dataclass(frozen=True)
class LLMRoute:
    """The resolved provider/transport decision shared by every role this turn."""

    settings: Any
    provider: str  # runtime provider (after auth-method resolution)
    cli_reg: Any | None  # CLI-backed provider registration, or None
    use_litellm: bool


def resolve_llm_route() -> LLMRoute:
    """Resolve settings + runtime provider + transport once (the single routing decision)."""
    settings = _resolve_settings_or_raise()

    from config.llm_auth.auth_method import (
        effective_llm_provider,
        get_configured_llm_auth_method,
    )

    provider = settings.provider
    runtime_provider = effective_llm_provider(provider, get_configured_llm_auth_method(provider))
    return LLMRoute(
        settings=settings,
        provider=runtime_provider,
        cli_reg=_cli_provider_registration(runtime_provider),
        use_litellm=use_litellm_for_provider(runtime_provider),
    )


def _resolve_settings_or_raise() -> Any:
    from pydantic import ValidationError

    from config.config import resolve_llm_settings

    try:
        return resolve_llm_settings()
    except ValidationError as exc:
        errors = exc.errors()
        if len(errors) == 1:
            msg = re.sub(r"^[Vv]alue error,\s*", "", errors[0].get("msg", "")).strip()
            raise RuntimeError(msg or str(exc)) from exc
        raise RuntimeError(str(exc)) from exc


def _cli_provider_registration(provider: str) -> Any:
    """CLI registry entry for *provider*, or None. Lazy import avoids a package cycle."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    return get_cli_provider_registration(provider)


# ---------------------------------------------------------------------------
# Role-specific construction (shared route, different client family)
# ---------------------------------------------------------------------------


def _build_agent_client(route: LLMRoute) -> AgentLLMClient:
    """Build the tool-calling client for the resolved route."""
    from config.config import PROVIDER_BEDROCK, PROVIDER_OLLAMA, PROVIDER_OPENAI
    from core.llm.transports.sdk.agent_clients import (
        AnthropicAgentClient,
        BedrockAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    settings, provider = route.settings, route.provider

    if route.cli_reg is not None:
        model_name = os.getenv(route.cli_reg.model_env_key, "").strip() or None
        return CLIBackedAgentClient(route.cli_reg.adapter_factory(), model=model_name)

    if route.use_litellm:
        from core.llm.transports.litellm.routing import build_litellm_agent_client

        return build_litellm_agent_client(settings, provider)

    if provider == PROVIDER_OPENAI:
        from config.config import OPENAI_LLM_CONFIG

        return OpenAIAgentClient(
            model=settings.openai_reasoning_model,
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
        )

    from core.llm.providers.openai_compat_providers import (
        is_openai_compat_provider,
        resolve_openai_compat_provider,
    )

    if is_openai_compat_provider(provider):
        resolved = resolve_openai_compat_provider(settings, provider, "reasoning")
        max_tokens = 1024 if provider == PROVIDER_OLLAMA else resolved.config.max_tokens
        return OpenAIAgentClient(
            model=resolved.model,
            max_tokens=max_tokens,
            base_url=resolved.base_url,
            api_key_env=resolved.api_key_env,
            api_key_default=resolved.api_key_default,
        )

    if provider == PROVIDER_BEDROCK:
        from config.config import BEDROCK_LLM_CONFIG
        from core.llm.providers.bedrock_model_ids import is_anthropic_bedrock_model

        model = settings.bedrock_reasoning_model
        if is_anthropic_bedrock_model(model):
            return BedrockAgentClient(model=model, max_tokens=BEDROCK_LLM_CONFIG.max_tokens)
        return BedrockConverseAgentClient(model=model, max_tokens=BEDROCK_LLM_CONFIG.max_tokens)

    from config.config import ANTHROPIC_LLM_CONFIG

    return AnthropicAgentClient(
        model=settings.anthropic_reasoning_model,
        max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
    )


def _build_llm_client(route: LLMRoute, model_type: _ModelType) -> Any:
    """Build the streaming reasoning client for the resolved route and model tier."""
    from config.config import PROVIDER_BEDROCK, PROVIDER_OPENAI

    settings, provider = route.settings, route.provider

    def _select_model(provider_prefix: str) -> str:
        return str(getattr(settings, f"{provider_prefix}_{model_type}_model"))

    def _fallback_model(provider_prefix: str) -> str | None:
        if model_type == "toolcall":
            return None
        return str(getattr(settings, f"{provider_prefix}_toolcall_model"))

    if route.cli_reg is not None:
        from config.config import DEFAULT_MAX_TOKENS
        from integrations.llm_cli.runner import CLIBackedLLMClient

        model_name = os.getenv(route.cli_reg.model_env_key, "").strip() or None
        return CLIBackedLLMClient(
            route.cli_reg.adapter_factory(),
            model=model_name,
            max_tokens=DEFAULT_MAX_TOKENS,
            model_type=model_type,
        )

    if route.use_litellm:
        from core.llm.shared.usage import emit_usage
        from core.llm.transports.litellm.routing import build_litellm_llm_client

        return build_litellm_llm_client(
            settings,
            provider,
            model_type,
            usage_callback=emit_usage,
        )

    from core.llm.providers.openai_compat_providers import (
        is_openai_compat_provider,
        resolve_openai_compat_provider,
    )
    from core.llm.transports.sdk import llm_clients as sdk

    if provider == PROVIDER_OPENAI:
        from config.config import OPENAI_LLM_CONFIG

        return sdk.OpenAILLMClient(
            model=_select_model("openai"),
            model_fallback=_fallback_model("openai"),
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
        )
    if is_openai_compat_provider(provider):
        compat = resolve_openai_compat_provider(settings, provider, model_type)
        return sdk.OpenAILLMClient(
            model=compat.model,
            model_fallback=_fallback_model(provider),
            max_tokens=compat.config.max_tokens,
            base_url=compat.base_url,
            api_key_env=compat.api_key_env,
            api_key_default=compat.api_key_default,
            temperature=compat.temperature,
        )
    if provider == PROVIDER_BEDROCK:
        from config.config import BEDROCK_LLM_CONFIG

        return sdk.BedrockLLMClient(
            model=_select_model("bedrock"),
            max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
        )

    from config.config import ANTHROPIC_LLM_CONFIG

    return sdk.LLMClient(
        model=_select_model("anthropic"),
        max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
    )


# ---------------------------------------------------------------------------
# Unified cache + public entrypoint
# ---------------------------------------------------------------------------


class _FactoryCache:
    """One client per role, invalidated together on ``(transport, provider)`` change.

    Wrapped in a class so fields are read/written via attribute access on a stable
    container, avoiding the ``global`` keyword (which CodeQL's unused-global rule
    misreports despite the in-function reads).
    """

    clients: dict[LLMRole, Any]
    cache_key: tuple[str, str] | None = None

    def __init__(self) -> None:
        self.clients = {}


_cache = _FactoryCache()


@overload
def get_llm(role: Literal[LLMRole.AGENT]) -> AgentLLMClient: ...
@overload
def get_llm(role: LLMRole) -> Any: ...


def get_llm(role: LLMRole) -> Any:
    """Return the cached LLM client for *role*, building it once per config."""
    cache_key = current_llm_client_cache_key()
    if _cache.cache_key != cache_key:
        _cache.clients.clear()
        _cache.cache_key = cache_key

    cached = _cache.clients.get(role)
    if cached is not None:
        return cached

    route = resolve_llm_route()
    if role is LLMRole.AGENT:
        client = _build_agent_client(route)
    else:
        client = _build_llm_client(route, _MODEL_TYPE_BY_ROLE[role])
    _cache.clients[role] = client
    return client


def reset_llm_clients() -> None:
    """Clear all cached role clients (tests, benchmarks, ``/model`` switch, env sync)."""
    _cache.clients.clear()
    _cache.cache_key = None


def build_llm_client(model_type: _ModelType) -> Any:
    """Build a fresh (uncached) reasoning-family client for the current config."""
    return _build_llm_client(resolve_llm_route(), model_type)


__all__ = [
    "LLMRole",
    "LLMRoute",
    "build_llm_client",
    "get_llm",
    "reset_llm_clients",
    "resolve_llm_route",
]
