"""LLM provider auth-method selection.

The public provider remains the vendor name (for example ``openai``), while
some auth methods use a provider-specific runtime backend under the hood.
"""

from __future__ import annotations

import os
from typing import Literal

LLM_AUTH_METHOD_ENV = "LLM_AUTH_METHOD"
LLMAuthMethod = Literal["api_key", "oauth"]

API_KEY_AUTH_METHOD: LLMAuthMethod = "api_key"
OAUTH_AUTH_METHOD: LLMAuthMethod = "oauth"

OAUTH_BACKEND_PROVIDER_BY_PROVIDER: dict[str, str] = {
    "openai": "codex",
    "anthropic": "claude-code",
}
OAUTH_PROVIDER_BY_BACKEND_PROVIDER: dict[str, str] = {
    backend: provider for provider, backend in OAUTH_BACKEND_PROVIDER_BY_PROVIDER.items()
}


def normalize_llm_auth_method(value: str | None) -> LLMAuthMethod:
    """Return a supported auth method, defaulting to API-key auth."""
    normalized = (value or "").strip().lower()
    if normalized == OAUTH_AUTH_METHOD:
        return OAUTH_AUTH_METHOD
    return API_KEY_AUTH_METHOD


def supports_oauth_auth_method(provider: str) -> bool:
    """Whether onboarding exposes OAuth for this public provider."""
    return provider.strip().lower() in OAUTH_BACKEND_PROVIDER_BY_PROVIDER


def canonical_llm_provider(provider: str) -> str:
    """Map legacy OAuth backend provider values to their public provider."""
    normalized_provider = provider.strip().lower()
    return OAUTH_PROVIDER_BY_BACKEND_PROVIDER.get(normalized_provider, normalized_provider)


def get_configured_llm_auth_method(provider: str | None = None) -> LLMAuthMethod:
    """Return the active auth method from env, with legacy CLI compatibility."""
    normalized_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
    if normalized_provider in OAUTH_PROVIDER_BY_BACKEND_PROVIDER:
        return OAUTH_AUTH_METHOD
    return normalize_llm_auth_method(os.getenv(LLM_AUTH_METHOD_ENV))


def effective_llm_provider(provider: str, auth_method: str | None = None) -> str:
    """Map a public provider/auth pair to the runtime provider implementation."""
    normalized_provider = provider.strip().lower()
    method = normalize_llm_auth_method(auth_method)
    if method == OAUTH_AUTH_METHOD:
        return OAUTH_BACKEND_PROVIDER_BY_PROVIDER.get(normalized_provider, normalized_provider)
    return normalized_provider


__all__ = [
    "API_KEY_AUTH_METHOD",
    "LLM_AUTH_METHOD_ENV",
    "LLMAuthMethod",
    "OAUTH_AUTH_METHOD",
    "OAUTH_BACKEND_PROVIDER_BY_PROVIDER",
    "OAUTH_PROVIDER_BY_BACKEND_PROVIDER",
    "canonical_llm_provider",
    "effective_llm_provider",
    "get_configured_llm_auth_method",
    "normalize_llm_auth_method",
    "supports_oauth_auth_method",
]
