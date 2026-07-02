"""Per-provider LiteLLM model, credentials, and base-URL resolution.

Maps each API provider (anthropic, openai, bedrock, openai-compat) to the
correct LiteLLM model prefix, credential env var, and optional ``api_base``
so the dispatch entrypoints can build a :class:`~core.llm.litellm.clients.LiteLLMAgentClient`
or :class:`~core.llm.litellm.clients.LiteLLMLLMClient` without embedding
provider-specific knowledge.
"""

from __future__ import annotations

from typing import Any

from core.llm.azure_openai import (
    is_azure_openai_provider,
    resolve_azure_openai_request_kwargs,
)
from core.llm.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient
from core.llm.openai_compat_providers import (
    ModelType,
    is_openai_compat_provider,
    resolve_openai_compat_provider,
)


def _litellm_model_for_compat(model: str) -> str:
    """Prefix model with ``openai/`` if not already prefixed, for compat endpoints."""
    return model if model.startswith("openai/") else f"openai/{model}"


def build_litellm_agent_client(settings: Any, provider: str) -> LiteLLMAgentClient:
    """Build a :class:`LiteLLMAgentClient` for the given provider and settings."""
    if provider == "anthropic":
        from config.config import ANTHROPIC_LLM_CONFIG

        return LiteLLMAgentClient(
            litellm_model=f"anthropic/{settings.anthropic_reasoning_model}",
            max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
            api_key_env="ANTHROPIC_API_KEY",
        )

    if provider == "openai":
        from config.config import OPENAI_LLM_CONFIG

        return LiteLLMAgentClient(
            litellm_model=f"openai/{settings.openai_reasoning_model}",
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
            api_key_env="OPENAI_API_KEY",
        )

    if provider == "bedrock":
        from config.config import BEDROCK_LLM_CONFIG

        model = settings.bedrock_reasoning_model
        return LiteLLMAgentClient(
            litellm_model=f"bedrock/{model}",
            max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
        )

    if is_azure_openai_provider(provider):
        from config.config import AZURE_OPENAI_LLM_CONFIG

        azure = resolve_azure_openai_request_kwargs(settings, model_type="reasoning")
        return LiteLLMAgentClient(
            litellm_model=azure["litellm_model"],
            max_tokens=AZURE_OPENAI_LLM_CONFIG.max_tokens,
            api_base=azure["api_base"],
            api_version=azure["api_version"],
            api_key_env=azure["api_key_env"],
        )

    if is_openai_compat_provider(provider):
        resolved = resolve_openai_compat_provider(settings, provider, "reasoning")
        max_tokens = 1024 if provider == "ollama" else resolved.config.max_tokens
        return LiteLLMAgentClient(
            litellm_model=_litellm_model_for_compat(resolved.model),
            max_tokens=max_tokens,
            api_base=resolved.base_url,
            api_key_env=resolved.api_key_env,
            api_key_default=resolved.api_key_default,
            temperature=resolved.temperature,
        )

    raise RuntimeError(
        f"No LiteLLM routing configured for provider '{provider}'. "
        "Use OPENSRE_LLM_TRANSPORT=sdk or add routing support for this provider."
    )


def build_litellm_llm_client(
    settings: Any,
    provider: str,
    model_type: ModelType,
    *,
    usage_callback: Any = None,
) -> LiteLLMLLMClient:
    """Build a :class:`LiteLLMLLMClient` for the given provider, model tier, and settings."""

    def _fallback(provider_prefix: str) -> str | None:
        if model_type == "toolcall":
            return None
        attr = f"{provider_prefix}_toolcall_model"
        return str(getattr(settings, attr, None) or "")

    if provider == "anthropic":
        from config.config import ANTHROPIC_LLM_CONFIG

        attr = f"anthropic_{model_type}_model"
        model = str(getattr(settings, attr))
        return LiteLLMLLMClient(
            litellm_model=f"anthropic/{model}",
            model_fallback=(_fallback("anthropic") and f"anthropic/{_fallback('anthropic')}")
            or None,
            max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
            api_key_env="ANTHROPIC_API_KEY",
            usage_callback=usage_callback,
        )

    if provider == "openai":
        from config.config import OPENAI_LLM_CONFIG

        attr = f"openai_{model_type}_model"
        model = str(getattr(settings, attr))
        return LiteLLMLLMClient(
            litellm_model=f"openai/{model}",
            model_fallback=(_fallback("openai") and f"openai/{_fallback('openai')}") or None,
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
            api_key_env="OPENAI_API_KEY",
            usage_callback=usage_callback,
        )

    if provider == "bedrock":
        from config.config import BEDROCK_LLM_CONFIG

        attr = f"bedrock_{model_type}_model"
        model = str(getattr(settings, attr))
        return LiteLLMLLMClient(
            litellm_model=f"bedrock/{model}",
            model_fallback=(_fallback("bedrock") and f"bedrock/{_fallback('bedrock')}") or None,
            max_tokens=BEDROCK_LLM_CONFIG.max_tokens,
            usage_callback=usage_callback,
        )

    if is_azure_openai_provider(provider):
        from config.config import AZURE_OPENAI_LLM_CONFIG

        azure = resolve_azure_openai_request_kwargs(settings, model_type=model_type)
        raw_fallback = _fallback("azure_openai")
        azure_fallback_model: str | None = None
        if raw_fallback:
            azure_fallback_model = (
                raw_fallback if raw_fallback.startswith("azure/") else f"azure/{raw_fallback}"
            )
        return LiteLLMLLMClient(
            litellm_model=azure["litellm_model"],
            model_fallback=azure_fallback_model,
            max_tokens=AZURE_OPENAI_LLM_CONFIG.max_tokens,
            api_base=azure["api_base"],
            api_version=azure["api_version"],
            api_key_env=azure["api_key_env"],
            usage_callback=usage_callback,
        )

    if is_openai_compat_provider(provider):
        compat = resolve_openai_compat_provider(settings, provider, model_type)
        raw_fallback = _fallback(provider)
        fallback_model: str | None = None
        if raw_fallback:
            fallback_compat = resolve_openai_compat_provider(settings, provider, "toolcall")
            fallback_model = _litellm_model_for_compat(fallback_compat.model)
        return LiteLLMLLMClient(
            litellm_model=_litellm_model_for_compat(compat.model),
            model_fallback=fallback_model,
            max_tokens=compat.config.max_tokens,
            api_base=compat.base_url,
            api_key_env=compat.api_key_env,
            api_key_default=compat.api_key_default,
            temperature=compat.temperature,
            usage_callback=usage_callback,
        )

    raise RuntimeError(
        f"No LiteLLM routing configured for provider '{provider}'. "
        "Use OPENSRE_LLM_TRANSPORT=sdk or add routing support for this provider."
    )
