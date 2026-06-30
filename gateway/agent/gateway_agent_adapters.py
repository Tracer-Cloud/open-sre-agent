"""Gateway adapters implementing :mod:`core.agent_harness.ports` for Telegram turns."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from core.agent_harness.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from core.agent_harness.headless import SimpleRunRecord, SimpleRunRecordFactory
from core.agent_harness.prompts import build_environment_block
from core.agent_harness.session import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST, ReplSession

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
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
        return _resolve_provider_models(settings, runtime_provider)

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


def _load_llm_settings() -> Any | None:
    try:
        from config.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:
        return None


class GatewayPromptContextProvider:
    """:class:`core.agent_harness.ports.PromptContextProvider` over session grounding caches."""

    def __init__(self, session: ReplSession) -> None:
        self._session = session

    def cli_reference(self) -> str:
        return self._session.grounding.cli.build_text()

    def agents_md(self) -> str:
        return self._session.grounding.agents_md.build_text()

    def investigation_flow(self) -> str:
        return build_investigation_flow_reference_text()

    def environment_block(self) -> str:
        settings = _load_llm_settings()
        llm_provider: str | None = None
        reasoning_model: str | None = None
        toolcall_model: str | None = None
        llm_settings_available = settings is not None
        if settings is not None:
            llm_provider = str(getattr(settings, "provider", "") or "unknown")
            try:
                reasoning_model, toolcall_model = _resolve_provider_models(settings, llm_provider)
            except Exception:
                llm_settings_available = False
        return build_environment_block(
            integrations=tuple(self._session.configured_integrations),
            known=self._session.configured_integrations_known,
            llm_provider=llm_provider,
            reasoning_model=reasoning_model,
            toolcall_model=toolcall_model,
            llm_settings_available=llm_settings_available,
        )

    def suggested_synthetic_prompt(self) -> str:
        return SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

    def log_diagnostics(self, reason: str) -> None:
        self._session.grounding.log_cache_diagnostics(reason)


class GatewayRunRecordFactory(SimpleRunRecordFactory):
    """Build run records and accumulate token usage on the gateway session."""

    def __init__(self, session: ReplSession) -> None:
        self._session = session

    def build(
        self, *, client: Any, prompt: str, response_text: str, started: float
    ) -> SimpleRunRecord:
        _ = client
        input_tokens = _estimate_tokens(prompt)
        output_tokens = _estimate_tokens(response_text)
        self._session.record_token_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated=True,
        )
        _ = started, time.monotonic()
        return SimpleRunRecord(response_text=response_text, prompt=prompt, started=started)


class GatewayErrorReporter:
    """:class:`core.agent_harness.ports.ErrorReporter` over gateway logging."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        if expected:
            self._logger.debug("[%s] %s", context, exc, exc_info=exc)
            return
        self._logger.exception("[%s] %s", context, exc)


__all__ = [
    "GatewayErrorReporter",
    "GatewayPromptContextProvider",
    "GatewayRunRecordFactory",
]
