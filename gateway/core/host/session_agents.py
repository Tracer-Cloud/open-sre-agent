"""Session-scoped :class:`HeadlessAgent` pool for the gateway turn handler.

Keeps agent construction out of :class:`GatewayTurnHandler` so the handler
stays a thin dispatch/finalize orchestrator. Construction goes through
:meth:`~core.agent_harness.turns.port_families.DefaultPorts.agent`
once per session — not a second port-wiring stack.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from core.agent_harness import SessionCore
from core.agent_harness.ports import SlashPortsFactory
from core.agent_harness.runtime import DefaultPorts, DefaultToolProvider, GatherPorts, HeadlessAgent
from gateway.core.host.capability_policy import ensure_gateway_capability_policy
from gateway.core.host.live_sink import LiveOutputSink
from gateway.core.host.status_messages import status_from_tool_start
from gateway.core.transport_api import GatewaySink
from tools.interactive_shell.subprocess_presenter import (
    headless_subprocess_presenter_factory,
)


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


#: Built per session, because each needs the session it serves.
ToolProviderFactory = Callable[[Any, Console, logging.Logger, Any], Any]
PromptsFactory = Callable[[Any], Any]
GatherFactory = Callable[[Any, Console], Any]
CapabilityPolicy = Callable[[Any], None]


@dataclass(frozen=True)
class ChannelAgentPorts:
    """What a channel supplies so the gateway can build its agent for it.

    Every field defaults to the chat behaviour the four transports use, so a
    channel states only what is genuinely its own. The interactive shell needs
    all of them — its tools carry the REPL slash ports and ``request_exit``,
    its prompts ground the model in the CLI, its gather writes progress lines
    to the console. A chat transport needs none.

    The pool still decides which agent is reused and how the live sink is bound.
    Capability policy is a channel field so the shell is not forced onto
    gateway-chat withholds (investigation / llm_provider / task_cancel).
    Ports are process-lifetime: a cached agent is not rebuilt if they change.
    """

    #: Analytics surface for the turn, e.g. ``gateway`` or ``interactive_shell``.
    surface: str = "gateway"
    #: ``(session, console, logger, observer) -> ToolProvider``
    build_tools: ToolProviderFactory | None = None
    #: ``(session) -> PromptContextProvider``
    build_prompts: PromptsFactory | None = None
    #: ``(session, console) -> GatherPorts``
    build_gather: GatherFactory | None = None
    #: Where a swallowed exception is reported; the harness default when absent.
    error_reporter: Any | None = None
    #: Applied before construction. ``None`` means gateway-chat withholds.
    apply_capability_policy: CapabilityPolicy | None = None


class SessionAgentPool:
    """One :class:`HeadlessAgent` (+ live sink) per logical session id."""

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
        ports: ChannelAgentPorts | None = None,
    ) -> None:
        self._console = console
        self._slash_ports_factory = slash_ports_factory
        self._ports = ports if ports is not None else ChannelAgentPorts()
        self._agents: dict[str, HeadlessAgent] = {}
        self._sinks: dict[str, LiveOutputSink] = {}
        # One agent serves every turn of a session, and each turn rebinds its
        # session and live sink. Turns for the same session must therefore not
        # overlap, or one turn's output goes to the other's sink. Different
        # sessions are independent and stay concurrent.
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, session_id: str) -> threading.Lock:
        """The lock guarding one session's agent, created on first use."""
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, threading.Lock())

    @contextmanager
    def session_agent(
        self,
        *,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> Iterator[HeadlessAgent]:
        """Hold this session's agent for the whole turn.

        The lock spans dispatch, not just the handout: rebinding is what makes
        the agent turn-specific, so releasing before the turn finishes would
        let the next turn retarget an agent that is still streaming.
        """
        session_id = str(getattr(session, "session_id", "") or "")
        if not session_id:
            # No id means no cache entry and nothing shared to protect.
            yield self.agent_for(session=session, sink=sink, logger=logger)
            return
        with self._lock_for(session_id):
            yield self.agent_for(session=session, sink=sink, logger=logger)

    def agent_for(
        self,
        *,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> HeadlessAgent:
        """Return a session-scoped agent with ``sink`` bound for this turn.

        Prefer :meth:`session_agent`, which holds the session's lock for the
        whole turn. This is the unsynchronised primitive it wraps.
        """
        policy = self._ports.apply_capability_policy
        if policy is None:
            policy = ensure_gateway_capability_policy
        policy(session)
        session_id = str(getattr(session, "session_id", "") or "")
        live_sink = self._sinks.get(session_id) if session_id else None
        if live_sink is None:
            live_sink = LiveOutputSink()
            if session_id:
                self._sinks[session_id] = live_sink
        live_sink.bind(sink)

        cached = self._agents.get(session_id) if session_id else None
        if cached is not None:
            # Resolve returns a new SessionCore each turn; keep the cached agent
            # but point every session-scoped port at the current object.
            cached.bind_session(session)
            return cached

        observer = _ToolStatusObserver(live_sink)
        channel = self._ports
        if channel.build_tools is not None:
            tools = channel.build_tools(session, self._console, logger, observer)
        else:
            tools = DefaultToolProvider(
                session,
                self._console,
                tool_action_logger=logger,
                observer_factory=lambda _message: observer,
                subprocess_presenter_factory=headless_subprocess_presenter_factory,
                slash_ports_factory=self._slash_ports_factory,
            )
        prompts = channel.build_prompts(session) if channel.build_prompts is not None else None
        gather = (
            channel.build_gather(session, self._console)
            if channel.build_gather is not None
            else GatherPorts()
        )
        agent = DefaultPorts(
            session=session,
            output=live_sink,
            console=self._console,
            logger=logger,
            surface=channel.surface,
            error_reporter=channel.error_reporter,
        ).agent(
            tools=tools,
            prompts=prompts,
            gather=gather,
        )
        if session_id:
            self._agents[session_id] = agent
        return agent

    @property
    def cached_session_ids(self) -> frozenset[str]:
        """Session ids that currently hold a reused agent (test/observability)."""
        return frozenset(self._agents)


__all__ = ["ChannelAgentPorts", "SessionAgentPool"]
