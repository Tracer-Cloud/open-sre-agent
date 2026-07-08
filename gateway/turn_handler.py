"""Gateway turn handler: dispatch one inbound message to the agent.

Transport-agnostic — it takes ``(text, session, sink, logger)`` and drives the
shared headless dispatch, then finalizes any outbound text on the sink. It knows
nothing about Telegram (or any specific transport); the composition root builds
one of these and hands it to whichever poller runs.
"""

from __future__ import annotations

import logging

from rich.console import Console

from core.agent_harness.ports import ToolEventObserver
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
    DefaultToolProvider,
    DefaultTurnAccounting,
)
from core.agent_harness.session import Session
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from gateway.gateway_output_sink import GatewayOutputSink
from gateway.polling.handle_polled_inbound_telegram_msg import GatewayAgentCallback
from gateway.status_messages import status_from_tool_start


class GatewayTurnHandler:
    """Services one inbound gateway message per call (a :data:`GatewayAgentCallback`).

    ``console`` is the only cross-turn state. The session, output sink, and
    accounting are per-turn, so each call builds its own agent — there is no
    persistent per-transport agent, and concurrent turns stay isolated.
    """

    def __init__(self, *, console: Console) -> None:
        self._console = console

    def __call__(
        self,
        text: str,
        session: Session,
        sink: GatewayOutputSink,
        logger: logging.Logger,
    ) -> None:
        agent = self._agent_for_turn(text=text, session=session, sink=sink, logger=logger)
        turn_result = agent.dispatch(text)
        outbound_text = (
            turn_result.assistant_response_text or turn_result.action_result.response_text
        ).strip()
        # A streamed answer (answered=True) already resolved the placeholder status
        # via the sink. Otherwise always finalize so the placeholder never hangs —
        # even when the turn produced no text.
        if not turn_result.answered:
            sink.finalize(outbound_text or "I didn't have anything to add for that.")

    def _agent_for_turn(
        self,
        *,
        text: str,
        session: Session,
        sink: GatewayOutputSink,
        logger: logging.Logger,
    ) -> HeadlessAgent:
        """Build a fresh agent for a single gateway turn.

        Action tools are resolved from the live session here so integration-scoped
        tools stay available after ``SessionResolver`` hydrates the chat session.
        """
        error_reporter = DefaultErrorReporter(logger)
        observer = self._tool_status_observer(sink)
        return HeadlessAgent(
            session=session,
            output=sink,
            tools=DefaultToolProvider(
                session,
                self._console,
                tool_action_logger=logger,
                observer_factory=lambda _message: observer,
            ),
            prompts=DefaultPromptContextProvider(session),
            reasoning=DefaultReasoningClientProvider(
                output=sink,
                error_reporter=error_reporter,
                session=session,
            ),
            run_factory=DefaultRunRecordFactory(session),
            accounting=DefaultTurnAccounting(session, text),
            error_reporter=error_reporter,
            gather_enabled=True,
        )

    @staticmethod
    def _tool_status_observer(sink: GatewayOutputSink) -> ToolEventObserver:
        """Push a status line to ``sink`` whenever a tool starts during the turn."""

        def observer(kind: str, data: dict[str, object]) -> None:
            if kind != "tool_start":
                return
            tool_name = str(data.get("name") or "").strip()
            if not tool_name or tool_name == "assistant_handoff":
                return
            sink.set_tool_status(status_from_tool_start(tool_name, data.get("input")))

        return observer


def build_gateway_turn_handler(*, console: Console) -> GatewayAgentCallback:
    """Return a gateway turn callback backed by :class:`GatewayTurnHandler`."""
    return GatewayTurnHandler(console=console)


__all__ = ["GatewayTurnHandler", "build_gateway_turn_handler"]
