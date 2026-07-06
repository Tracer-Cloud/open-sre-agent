"""Single LLM factory: one provider-routing decision for every model role.

The provider/transport decision (CLI-backed vs LiteLLM vs native SDK, and which
vendor) is resolved once in :func:`resolve_llm_route` and reused by every role, so
an Azure/LiteLLM routing fix cannot drift between the investigation agent and the
reasoning/classification/toolcall clients.

Roles differ only in the *client family* they build: :data:`LLMRole.AGENT` builds
a tool-calling client (``tool_schemas`` / ``invoke``); the other roles build the
streaming reasoning client (``invoke`` / ``invoke_stream`` / ``with_structured_output``)
for a given model tier. ``get_llm(role)`` is the single entrypoint — callers pass an ``LLMRole``.

This module owns routing (:func:`resolve_llm_route`), the per-role cache, and the
public entrypoint. Constructing the concrete client for a route lives in
:mod:`core.llm.client_builders`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, overload

from core.llm import client_builders
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
    cli_provider_registration: Any | None
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
        cli_provider_registration=_cli_provider_registration(runtime_provider),
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
        client = client_builders.build_agent_client(route)
    else:
        client = client_builders.build_reasoning_client(route, _MODEL_TYPE_BY_ROLE[role])
    _cache.clients[role] = client
    return client


def reset_llm_clients() -> None:
    """Clear all cached role clients (tests, benchmarks, ``/model`` switch, env sync)."""
    _cache.clients.clear()
    _cache.cache_key = None


def build_llm_client(model_type: _ModelType) -> Any:
    """Build a fresh (uncached) reasoning-family client for the current config."""
    return client_builders.build_reasoning_client(resolve_llm_route(), model_type)


__all__ = [
    "LLMRole",
    "LLMRoute",
    "build_llm_client",
    "get_llm",
    "reset_llm_clients",
    "resolve_llm_route",
]
