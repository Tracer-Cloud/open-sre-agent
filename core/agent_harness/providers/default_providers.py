"""Core-owned default providers for the shared agent harness."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from rich.markup import escape

from core.agent_harness.accounting.token_accounting import build_llm_run_info
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    ToolEventObserver,
)
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_context
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolContext,
)
from platform.observability.sentry_sdk import capture_exception

log = logging.getLogger(__name__)

ActionObserverFactory = Callable[[Any, Any, str], ToolEventObserver]
_TOOL_INPUT_LOG_PREVIEW_LIMIT = 500


def _tool_input_preview(value: Any) -> str:
    preview = repr(value)
    if len(preview) > _TOOL_INPUT_LOG_PREVIEW_LIMIT:
        return f"{preview[: _TOOL_INPUT_LOG_PREVIEW_LIMIT - 3]}..."
    return preview


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


class DefaultToolProvider:
    """:class:`core.agent_harness.ports.ToolProvider` backed by action tools."""

    def __init__(
        self,
        session: Any,
        console: Any,
        *,
        request_exit: Callable[[], None] | None = None,
        precomputed_action_tools: list[Any] | None = None,
        observer_factory: ActionObserverFactory | None = None,
        tool_action_logger: logging.Logger | None = None,
    ) -> None:
        self._session = session
        self._console = console
        self._request_exit = request_exit
        self._precomputed_action_tools = precomputed_action_tools
        self._observer_factory = observer_factory
        self._tool_action_logger = tool_action_logger
        self._tool_context: ActionToolContext | None = None

    def action_tools(self, *, confirm_fn: ConfirmFn | None, is_tty: bool | None) -> list[Any]:
        ctx = ActionToolContext(
            session=self._session,
            console=self._console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=self._request_exit,
            action_already_listed=True,
        )
        self._tool_context = ctx
        if self._precomputed_action_tools is not None:
            return list(self._precomputed_action_tools)
        return get_action_tools_from_integrations_context(
            ctx, resolved_integrations=self._resolved_integrations()
        )

    def tool_resources(self) -> dict[str, Any]:
        if self._tool_context is None:
            return {}
        return {ACTION_TOOL_CONTEXT_RESOURCE_KEY: self._tool_context}

    def observer(self, *, message: str) -> ToolEventObserver:
        if self._observer_factory is not None:
            observer = self._observer_factory(self._session, self._console, message)
        else:

            def observer(_kind: str, _data: dict[str, Any]) -> None:
                return None

        if self._tool_action_logger is None:
            return observer
        logger = self._tool_action_logger

        def _logging_observer(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_start":
                tool_name = str(data.get("name") or "tool").strip()
                if tool_name:
                    logger.info(
                        "tool action name=%s input=%s",
                        tool_name,
                        _tool_input_preview(data.get("input", {})),
                    )
            observer(kind, data)

        return _logging_observer

    def _resolved_integrations(self) -> dict[str, Any]:
        from core.agent import Agent

        # Agent.resolve_integrations already returns a fresh dict.
        return Agent.resolve_integrations(self._session)


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
            from core.llm.llm_client import get_llm_for_reasoning
        except Exception as exc:
            self._handle_unavailable(
                exc, context="core.agent_harness.default_reasoning_client.import"
            )
            return None
        try:
            return get_llm_for_reasoning()
        except Exception as exc:
            self._handle_unavailable(
                exc, context="core.agent_harness.default_reasoning_client.create"
            )
            return None

    def _handle_unavailable(self, exc: Exception, *, context: str) -> None:
        if self._error_reporter is not None:
            self._error_reporter.report(exc, context=context)
        if self._session is not None:
            from core.agent_harness.agents.turn_orchestrator import stage_turn_error

            stage_turn_error(self._session, "llm_unavailable", str(exc))
        if self._output is not None:
            self._output.render_error(_llm_client_unavailable_message(exc))


class DefaultRunRecordFactory:
    """:class:`core.agent_harness.ports.RunRecordFactory` producing ``LlmRunInfo``."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def build(self, *, client: Any, prompt: str, response_text: str, started: float) -> Any:
        return build_llm_run_info(
            session=self._session,
            prompt=prompt,
            response_text=response_text,
            started=started,
            client=client,
        )


class DefaultErrorReporter:
    """:class:`core.agent_harness.ports.ErrorReporter` using platform observability."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or log

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        if expected:
            self._logger.debug("%s: %s", context, exc)
            return
        self._logger.debug("%s", context, exc_info=exc)
        capture_exception(exc, context=context)


class DefaultTurnAccounting:
    """:class:`core.agent_harness.ports.TurnAccounting` for non-terminal surfaces."""

    def __init__(self, session: Any, text: str) -> None:
        self._session = session
        self._text = text

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        _ = action_result

    def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
        response = (result.assistant_response_text or "").strip()
        if response:
            _append_turn_detail(
                self._session,
                kind="chat",
                prompt=self._text,
                response=response,
                llm_run=result.llm_run,
            )
        with contextlib.suppress(AttributeError):
            self._session.last_assistant_intent = result.final_intent
        return result


def _append_turn_detail(
    session: Any,
    *,
    kind: str,
    prompt: str,
    response: str,
    llm_run: Any | None = None,
) -> None:
    storage = getattr(session, "storage", None)
    append_turn_detail = getattr(storage, "append_turn_detail", None)
    session_id = getattr(session, "session_id", "")
    if not callable(append_turn_detail) or not isinstance(session_id, str) or not session_id:
        return
    try:
        append_turn_detail(
            session_id,
            kind,
            prompt,
            response=response,
            model=getattr(llm_run, "model", None) if llm_run is not None else None,
            provider=getattr(llm_run, "provider", None) if llm_run is not None else None,
            latency_ms=getattr(llm_run, "latency_ms", None) if llm_run is not None else None,
            system_prompt=getattr(llm_run, "final_system_prompt", None)
            if llm_run is not None
            else None,
        )
    except Exception:
        log.debug("failed to persist default turn detail", exc_info=True)


__all__ = [
    "DefaultErrorReporter",
    "DefaultReasoningClientProvider",
    "DefaultRunRecordFactory",
    "DefaultTurnAccounting",
    "DefaultToolProvider",
]
