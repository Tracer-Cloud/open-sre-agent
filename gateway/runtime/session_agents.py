"""Session-scoped :class:`HeadlessAgent` pool for the gateway turn handler.

Keeps agent construction out of :class:`GatewayTurnHandler` so the handler
stays a thin dispatch/finalize orchestrator (SRP).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider
from core.agent_harness.session import SessionCore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from gateway.runtime.headless_subprocess_presenter import headless_subprocess_presenter_factory
from gateway.runtime.live_sink import LiveOutputSink
from gateway.runtime.sink_protocol import GatewaySink
from gateway.runtime.status_messages import status_from_tool_start

SlashPortsFactory = Callable[[], Any]


class _ToolStatusObserver:
    """Push live tool-progress status lines to the turn's bound sink."""

    def __init__(self, sink: LiveOutputSink) -> None:
        self._sink = sink

    def __call__(self, kind: str, data: dict[str, object]) -> None:
        if kind != "tool_start":
            return
        tool_name = str(data.get("name") or "").strip()
        if not tool_name or tool_name == "assistant_handoff":
            return
        self._sink.set_tool_status(status_from_tool_start(tool_name, data.get("input")))


class SessionAgentPool:
    """One :class:`HeadlessAgent` (+ live sink) per logical session id."""

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
    ) -> None:
        self._console = console
        self._slash_ports_factory = slash_ports_factory
        self._agents: dict[str, HeadlessAgent] = {}
        self._sinks: dict[str, LiveOutputSink] = {}

    def agent_for(
        self,
        *,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> HeadlessAgent:
        """Return a session-scoped agent with ``sink`` bound for this turn."""
        session_id = str(getattr(session, "session_id", "") or "")
        live_sink = self._sinks.get(session_id) if session_id else None
        if live_sink is None:
            live_sink = LiveOutputSink()
            if session_id:
                self._sinks[session_id] = live_sink
        live_sink.bind(sink)

        cached = self._agents.get(session_id) if session_id else None
        if cached is not None:
            return cached

        error_reporter = DefaultErrorReporter(logger)
        observer = _ToolStatusObserver(live_sink)
        agent = HeadlessAgent(
            session=session,
            output=live_sink,
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
                output=live_sink,
                error_reporter=error_reporter,
                session=session,
            ),
            run_factory=DefaultRunRecordFactory(session),
            error_reporter=error_reporter,
            gather_enabled=True,
            is_tty=False,
        )
        if session_id:
            self._agents[session_id] = agent
        return agent

    @property
    def cached_session_ids(self) -> frozenset[str]:
        """Session ids that currently hold a reused agent (test/observability)."""
        return frozenset(self._agents)


__all__ = ["SessionAgentPool"]
