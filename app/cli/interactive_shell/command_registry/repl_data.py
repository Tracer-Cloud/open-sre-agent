"""Lazy loaders for verified integrations and LLM settings (repl slash commands)."""

from __future__ import annotations

from typing import Any


def load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from app.integrations.verify import verify_integrations

    return verify_integrations()


def load_llm_settings() -> Any | None:
    """Best-effort LLM settings load; returns None if env is misconfigured."""
    try:
        from app.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:  # noqa: BLE001 — env/config errors are expected here
        return None


__all__ = ["load_llm_settings", "load_verified_integrations"]
