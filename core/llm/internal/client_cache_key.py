"""Cache-key helpers for LLM singleton invalidation."""

from __future__ import annotations


def current_llm_client_cache_key() -> tuple[str, str]:
    """Return ``(transport, runtime_provider)`` for singleton cache invalidation."""
    from config.config import get_configured_llm_provider
    from config.llm_auth.auth_method import effective_llm_provider, get_configured_llm_auth_method
    from core.llm.transport_mode import current_llm_transport

    configured = get_configured_llm_provider()
    runtime = effective_llm_provider(configured, get_configured_llm_auth_method(configured))
    return (current_llm_transport(), runtime)


__all__ = ["current_llm_client_cache_key"]
