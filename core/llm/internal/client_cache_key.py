"""Cache-key helpers for LLM singleton invalidation."""

from __future__ import annotations

_ACCOUNT_LLM_CACHE_KEY = ("sdk", "account:openai")


def current_llm_client_cache_key() -> tuple[str, str]:
    """Return ``(transport, runtime_provider)`` for singleton cache invalidation."""
    from config.account import account_llm_route

    if account_llm_route() is not None:
        return _ACCOUNT_LLM_CACHE_KEY

    from config.llm_settings import get_configured_llm_provider
    from core.llm.transport_mode import current_llm_transport

    return (current_llm_transport(), get_configured_llm_provider())


__all__ = ["current_llm_client_cache_key"]
