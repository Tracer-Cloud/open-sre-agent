"""Tenant-aware credential provider abstraction for multi-tenancy.

Replaces direct os.getenv() credential reads with a swappable provider.
LLM platform keys are explicitly excluded — they stay in os.environ only.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Final

_tenant_ctx: ContextVar[str] = ContextVar("tenant_id")

# LLM platform keys are platform-owned, not tenant-scoped. Attempting to
# read them via CredentialProvider raises KeyError to prevent misuse.
LLM_PLATFORM_KEYS: Final[frozenset[str]] = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "REQUESTY_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "MINIMAX_API_KEY",
    "KIMI_API_KEY",
    "CURSOR_API_KEY",
    "BEDROCK_ACCESS_KEY_ID",
    "BEDROCK_SECRET_ACCESS_KEY",
})

CREDENTIAL_BACKEND: str = os.environ.get("CREDENTIAL_BACKEND", "env")


def get_current_tenant() -> str:
    """Return the tenant ID for the current async context.

    Raises LookupError if no tenant has been set (e.g. outside a request).
    """
    return _tenant_ctx.get()


def set_tenant_context(tenant_id: str) -> object:
    """Set the tenant ID for the current async context. Returns the Token."""
    return _tenant_ctx.set(tenant_id)


class CredentialProvider(ABC):
    @abstractmethod
    def get(self, key: str) -> str: ...


class EnvCredentialProvider(CredentialProvider):
    """Single-tenant provider that reads from os.environ (local dev default)."""

    def get(self, key: str) -> str:
        if key in LLM_PLATFORM_KEYS:
            raise KeyError(
                f"{key!r} is an LLM platform key; use resolve_llm_api_key() instead"
            )
        return os.environ[key]


# Module-level singleton — swapped out at startup for multi-tenant backends.
credential_provider: CredentialProvider = EnvCredentialProvider()


def get_opt(key: str, default: str = "") -> str:
    """Get a tenant credential, returning *default* when the key is absent.

    Use this for optional integration credentials where absence means
    "not configured". LLM platform keys always raise regardless of default.
    """
    if key in LLM_PLATFORM_KEYS:
        raise KeyError(
            f"{key!r} is an LLM platform key; use resolve_llm_api_key() instead"
        )
    try:
        return credential_provider.get(key)
    except KeyError:
        return default
