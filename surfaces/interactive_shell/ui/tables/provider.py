"""LLM provider and model detection for the interactive shell.

Exported
--------
resolve_provider_models(settings, provider)  -> (reasoning_model, toolcall_model)
detect_provider_model()                      -> (provider, model)
"""

from __future__ import annotations

import os

from core.agent_harness.llm_resolution import resolve_provider_models


def detect_provider_model() -> tuple[str, str]:
    """Return (provider, model) for the active LLM config."""
    try:
        from config.config import LLMSettings

        settings = LLMSettings.from_env()
    except Exception:
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv("LLM_PROVIDER", "anthropic")
    reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    return (provider, reasoning_model)


__all__ = [
    "detect_provider_model",
    "resolve_provider_models",
]
