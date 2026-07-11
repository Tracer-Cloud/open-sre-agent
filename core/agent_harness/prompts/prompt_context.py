"""Default prompt-context provider for agent-harness sessions with grounding."""

from __future__ import annotations

from typing import Any

from config.constants.prompts import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
from core.agent_harness.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from core.agent_harness.llm_resolution import resolve_provider_models
from core.agent_harness.prompts import build_environment_block
from platform.observability.trace.spans import component_span


def load_llm_settings() -> Any | None:
    """Best-effort LLM settings load for prompt environment grounding."""
    try:
        from config.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:
        return None


def supports_default_prompt_context(session: object) -> bool:
    """Return whether ``session`` exposes the grounding fields this provider needs."""
    grounding = getattr(session, "grounding", None)
    return (
        grounding is not None
        and hasattr(grounding, "agents_md")
        and hasattr(session, "configured_integrations")
        and hasattr(session, "configured_integrations_known")
    )


class DefaultPromptContextProvider:
    """:class:`core.agent_harness.ports.PromptContextProvider` over session grounding."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def cli_reference(self) -> str:
        return ""

    def agents_md(self) -> str:
        return str(self._session.grounding.agents_md.build_text())

    def investigation_flow(self) -> str:
        return build_investigation_flow_reference_text()

    def environment_block(self) -> str:
        sid = getattr(self._session, "session_id", None)
        with component_span("runtime_metadata:env_block", session_id=sid):
            settings = load_llm_settings()
            llm_provider: str | None = None
            reasoning_model: str | None = None
            toolcall_model: str | None = None
            llm_settings_available = settings is not None
            if settings is not None:
                llm_provider = str(getattr(settings, "provider", "") or "unknown")
                try:
                    reasoning_model, toolcall_model = resolve_provider_models(
                        settings, llm_provider
                    )
                except Exception:
                    llm_settings_available = False
            runtime = getattr(self._session, "runtime_metadata", None)
            if not isinstance(runtime, dict) or not runtime:
                from config.runtime_metadata import build_runtime_metadata

                runtime = build_runtime_metadata()
            return build_environment_block(
                integrations=tuple(self._session.configured_integrations),
                known=self._session.configured_integrations_known,
                llm_provider=llm_provider,
                reasoning_model=reasoning_model,
                toolcall_model=toolcall_model,
                llm_settings_available=llm_settings_available,
                opensre_version=str(runtime.get("opensre_version") or ""),
                opensre_build=str(runtime.get("opensre_build") or ""),
                runtime_env=str(runtime.get("runtime_env") or ""),
            )

    def suggested_synthetic_prompt(self) -> str:
        return SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

    def log_diagnostics(self, reason: str) -> None:
        self._session.grounding.log_cache_diagnostics(reason)


__all__ = [
    "DefaultPromptContextProvider",
    "load_llm_settings",
    "supports_default_prompt_context",
]
