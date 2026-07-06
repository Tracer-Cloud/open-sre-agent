"""LLM provider and model resolution shared by agent harness surfaces."""

from __future__ import annotations

import os
from typing import Any


def default_llm_factory() -> Any:
    """Return the default agent LLM client.

    Uses a lazy import to avoid pulling in the full LLM stack at module load time.
    """
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.AGENT)


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active ``(reasoning_model, toolcall_model)`` for a provider."""
    try:
        from config.llm_auth.auth_method import (
            effective_llm_provider,
            get_configured_llm_auth_method,
        )

        runtime_provider = effective_llm_provider(
            provider, get_configured_llm_auth_method(provider)
        )
    except Exception:
        runtime_provider = provider
    if runtime_provider != provider:
        return resolve_provider_models(settings, runtime_provider)

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


__all__ = ["default_llm_factory", "resolve_provider_models"]
