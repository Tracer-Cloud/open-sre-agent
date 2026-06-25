"""LLM provider and model detection for the interactive shell.

Exported
--------
resolve_provider_models(settings, provider)  -> (reasoning_model, toolcall_model)
detect_provider_model()                      -> (provider, model)
"""

from __future__ import annotations

import os


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active (reasoning_model, toolcall_model) for a provider."""
    if provider in {
        "codex",
        "claude-code",
        "gemini-cli",
        "antigravity-cli",
        "cursor",
        "kimi",
        "opencode",
    }:
        env_key = {
            "codex": "CODEX_MODEL",
            "claude-code": "CLAUDE_CODE_MODEL",
            "gemini-cli": "GEMINI_CLI_MODEL",
            "antigravity-cli": "ANTIGRAVITY_CLI_MODEL",
            "cursor": "CURSOR_MODEL",
            "kimi": "KIMI_MODEL",
            "opencode": "OPENCODE_MODEL",
        }.get(provider, "")
        cli_model = (os.getenv(env_key, "").strip() if env_key else "") or "CLI default"
        return (cli_model, cli_model)

    single_model = str(getattr(settings, f"{provider}_model", "")).strip()
    if single_model:
        return (single_model, single_model)

    reasoning_model = str(getattr(settings, f"{provider}_reasoning_model", "")).strip()
    toolcall_model = str(getattr(settings, f"{provider}_toolcall_model", "")).strip()
    return (reasoning_model or "default", toolcall_model or reasoning_model or "default")


def detect_provider_model() -> tuple[str, str]:
    """Return (provider, model) for the active LLM config."""
    try:
        from app.config import LLMSettings

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
