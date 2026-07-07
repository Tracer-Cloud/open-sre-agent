"""Lazy loaders for verified integrations and LLM settings (repl slash commands)."""

from __future__ import annotations

from typing import Any


def load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from integrations.verify import verify_integrations

    return verify_integrations()


def load_configured_integrations() -> list[dict[str, str]]:
    """Return locally configured integrations without running live verifiers."""
    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()
    rows: list[dict[str, str]] = []
    for service in sorted(effective):
        entry = effective[service]
        source = str(entry.get("source") or "-")
        rows.append(
            {
                "service": str(service),
                "source": source,
                "status": "configured",
                "detail": "Run /integrations verify to check connectivity.",
            }
        )
    return rows


def configured_integration_names() -> list[str]:
    """Return configured integration service names without running verifiers."""
    from integrations.catalog import resolve_effective_integrations

    return sorted(resolve_effective_integrations())


def verify_integration(service: str) -> dict[str, str] | None:
    """Verify a single integration and return its result row."""
    from integrations.verify import verify_integrations

    normalized = service.strip().lower()
    if not normalized:
        return None
    rows = verify_integrations(normalized)
    return rows[0] if rows else None


def load_llm_settings() -> Any | None:
    """Best-effort LLM settings load; returns None if env is misconfigured."""
    try:
        from config.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:
        return None


__all__ = [
    "configured_integration_names",
    "load_configured_integrations",
    "load_llm_settings",
    "load_verified_integrations",
    "verify_integration",
]
