"""Core-owned default reasoning-client provider for the shared agent harness."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from core.agent_harness.ports import (
    ErrorReporter,
    OutputSink,
)


def _llm_client_unavailable_message(exc: Exception) -> str:
    """Render the reasoning-client import failure; on ImportError, hint at a restart."""
    base = f"LLM client unavailable: {escape(str(exc))}"
    if isinstance(exc, ImportError):
        return (
            f"{base} — this usually means the OpenSRE code changed while this "
            "process was running. Restart it (relaunch with `uv run opensre …`) "
            "to load the updated modules."
        )
    return base


class DefaultReasoningClientProvider:
    """:class:`core.agent_harness.ports.ReasoningClientProvider` for assistant answers."""

    def __init__(
        self,
        *,
        output: OutputSink | None = None,
        error_reporter: ErrorReporter | None = None,
        session: Any | None = None,
    ) -> None:
        self._output = output
        self._error_reporter = error_reporter
        self._session = session

    def get(self) -> Any | None:
        try:
            from core.llm.factory import LLMRole, get_llm
        except Exception as exc:
            self._handle_unavailable(
                exc, context="core.agent_harness.default_reasoning_client.import"
            )
            return None
        try:
            return get_llm(LLMRole.REASONING)
        except Exception as exc:
            self._handle_unavailable(
                exc, context="core.agent_harness.default_reasoning_client.create"
            )
            return None

    def _handle_unavailable(self, exc: Exception, *, context: str) -> None:
        if self._error_reporter is not None:
            self._error_reporter.report(exc, context=context)
        if self._session is not None:
            from core.agent_harness.turns.orchestrator import stage_turn_error

            stage_turn_error(self._session, "llm_unavailable", str(exc))
        if self._output is not None:
            self._output.render_error(_llm_client_unavailable_message(exc))
