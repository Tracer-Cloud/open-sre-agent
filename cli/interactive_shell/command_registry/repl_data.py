"""Lazy loaders for verified integrations and LLM settings (repl slash commands)."""

from __future__ import annotations

from typing import Any


def load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from app.integrations.verify import verify_integrations

    return verify_integrations()


def configured_integration_names() -> list[str]:
    """Return configured integration service names without running verifiers."""
    from app.integrations.verify import resolve_effective_integrations

    return sorted(resolve_effective_integrations())


def verify_integration(service: str) -> dict[str, str] | None:
    """Verify a single integration and return its result row."""
    from app.integrations.verify import verify_integrations

    normalized = service.strip().lower()
    if not normalized:
        return None
    rows = verify_integrations(normalized)
    return rows[0] if rows else None


def load_llm_settings() -> Any | None:
    """Best-effort LLM settings load; returns None if env is misconfigured."""
    try:
        from app.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:
        return None


__all__ = [
    "configured_integration_names",
    "load_llm_settings",
    "load_verified_integrations",
    "verify_integration",
]
