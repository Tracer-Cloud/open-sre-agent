"""Gateway turn handler: dispatch one inbound message to the agent.

Transport-agnostic — it takes ``(text, session, sink, logger)`` and drives the
shared headless dispatch, then finalizes any outbound text on the sink. It knows
nothing about Telegram (or any specific transport); the composition root builds
one of these and hands it to whichever poller runs.

Transports bind ``surface`` / ``user_id`` via
:func:`platform.analytics.usage_context.bound_usage_context` before calling this
handler so org-level analytics can attribute sessions by channel.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider
from core.agent_harness.session import SessionCore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from gateway.runtime.headless_subprocess_presenter import headless_subprocess_presenter_factory
from gateway.runtime.sink_protocol import GatewaySink
from gateway.runtime.status_messages import status_from_tool_start
from platform.analytics.cli import (
    capture_gateway_turn_completed,
    capture_gateway_turn_failed,
    capture_gateway_turn_started,
)
from platform.analytics.usage_context import bound_usage_context, get_surface
from platform.observability.trace.spans import traced_session

SlashPortsFactory = Callable[[], Any]

_UNSUPPORTED_GATEWAY_CAPABILITIES = (
    "investigation",
    "llm_provider",
    "task_cancel",
)


class _ToolStatusObserver:
    """Live tool-progress feedback for the gateway.

    On each tool start it pushes a status line to the turn's sink — for Telegram
    that surfaces the typing indicator and a ``running tool X`` preview, so the
    user sees progress before the final answer instead of a silent wait.
    """

    def __init__(self, sink: GatewaySink) -> None:
        self._sink = sink

    def __call__(self, kind: str, data: dict[str, object]) -> None:
        if kind != "tool_start":
            return
        tool_name = str(data.get("name") or "").strip()
        if not tool_name or tool_name == "assistant_handoff":
            return
        self._sink.set_tool_status(status_from_tool_start(tool_name, data.get("input")))


class GatewayTurnHandler:
    """Services one inbound gateway message per call (a :data:`GatewayAgentCallback`).

    ``console`` is the only cross-turn state. The session, output sink, and
    accounting are per-turn, so each call builds its own agent — there is no
    persistent per-transport agent, and concurrent turns stay isolated.
    """

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
    ) -> None:
        self._console = console
        self._slash_ports_factory = slash_ports_factory

    def __call__(
        self,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> None:
        session.available_capabilities.update(dict.fromkeys(_UNSUPPORTED_GATEWAY_CAPABILITIES, ()))
        session_id = getattr(session, "session_id", None)
        surface = get_surface() or "gateway"
        started = time.monotonic()

        with (
            bound_usage_context(session_id=session_id),
            traced_session(session_id, component="gateway_turn"),
        ):
            try:
                capture_gateway_turn_started(surface=surface)
                agent = self._agent_for_turn(text=text, session=session, sink=sink, logger=logger)
                turn_result = agent.dispatch(text)
                outbound_text = (
                    turn_result.assistant_response_text or turn_result.action_result.response_text
                ).strip()
                logger.debug(
                    "gateway_turn done intent=%s answered=%s outbound_chars=%s",
                    turn_result.final_intent,
                    turn_result.answered,
                    len(outbound_text),
                )
                # A streamed answer (answered=True) already resolved the placeholder status
                # via the sink. Otherwise always finalize so the placeholder never hangs —
                # even when the turn produced no text.
                if not turn_result.answered:
                    sink.finalize(outbound_text or "I didn't have anything to add for that.")
                capture_gateway_turn_completed(
                    surface=surface,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    answered=bool(turn_result.answered),
                    final_intent=str(turn_result.final_intent or "") or None,
                )
            except Exception as exc:
                capture_gateway_turn_failed(
                    surface=surface,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    error_type=type(exc).__name__,
                )
                raise

    def _agent_for_turn(
        self,
        *,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> HeadlessAgent:
        """Build a fresh agent for a single gateway turn.

        Action tools are resolved from the live session here so integration-scoped
        tools stay available after ``SessionResolver`` hydrates the chat session.
        """
        error_reporter = DefaultErrorReporter(logger)
        observer = _ToolStatusObserver(sink)
        # Transport sinks may carry per-turn tool-execution hooks (the Slack
        # sink attaches the Block Kit approval gate); sinks without the
        # attribute (Telegram) run unhooked as before.
        tool_hooks = getattr(sink, "tool_hooks", None)
        return HeadlessAgent(
            session=session,
            output=sink,
            tool_hooks=tool_hooks,
            tools=DefaultToolProvider(
                session,
                self._console,
                tool_action_logger=logger,
                observer_factory=lambda _message: observer,
                subprocess_presenter_factory=headless_subprocess_presenter_factory,
                slash_ports_factory=self._slash_ports_factory,
            ),
            prompts=DefaultPromptContextProvider(session, surface="gateway"),
            reasoning=DefaultReasoningClientProvider(
                output=sink,
                error_reporter=error_reporter,
                session=session,
            ),
            run_factory=DefaultRunRecordFactory(session),
            accounting=DefaultTurnAccounting(session, text),
            error_reporter=error_reporter,
            gather_enabled=True,
            is_tty=False,
        )


__all__ = ["GatewayTurnHandler"]
