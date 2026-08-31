"""Cache-key helpers for LLM singleton invalidation."""

from __future__ import annotations


def current_llm_client_cache_key() -> tuple[str, str]:
    """Return ``(transport, runtime_provider)`` for singleton cache invalidation."""
    from config.llm_settings import get_configured_llm_provider
    from core.llm.transport_mode import current_llm_transport

    return (current_llm_transport(), get_configured_llm_provider())


__all__ = ["current_llm_client_cache_key"]
