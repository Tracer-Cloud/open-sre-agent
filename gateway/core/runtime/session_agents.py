"""Session-scoped :class:`HeadlessAgent` pool for the gateway turn handler.

Keeps agent construction out of :class:`GatewayTurnHandler` so the handler
stays a thin dispatch/finalize orchestrator. Construction goes through
:func:`~core.agent_harness.turns.default_headless_agent.build_default_headless_agent`
once per session — not a second port-wiring stack.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console

from config.constants import DETACHED_LAUNCH_ROW_TITLE, DETACHED_LAUNCH_TOOL_NAMES
from core.agent_harness.session import SessionCore
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.gather_ports import GatherPorts
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from gateway.core.investigations.launch_ports import gateway_investigation_launch_ports
from gateway.core.investigations.launch_record import current_detached_launch_record
from gateway.core.runtime.live_sink import LiveOutputSink
from gateway.core.runtime.sink_protocol import GatewaySink
from gateway.core.runtime.status_messages import status_from_tool_start
from tools.interactive_shell.subprocess_presenter import (
    headless_subprocess_presenter_factory,
)

SlashPortsFactory = Callable[[], Any]


class _ToolStatusObserver:
    """Push live tool-progress status lines to the turn's bound sink."""

    def __init__(self, sink: LiveOutputSink) -> None:
        self._sink = sink

    def __call__(self, kind: str, data: dict[str, object]) -> None:
        if kind == "tool_start":
            tool_name = str(data.get("name") or "").strip()
            if not tool_name or tool_name == "assistant_handoff":
                return
            call_id = str(data.get("id") or "") or None
            self._sink.set_tool_status(
                status_from_tool_start(tool_name, data.get("input")), call_id=call_id
            )
        elif kind == "tool_end":
            tool_name = str(data.get("name") or "").strip()
            if tool_name == "assistant_handoff":
                return
            # Deliberately *not* mirroring the blank-name guard above: a row is
            # closed by call id, so refusing an unnamed end would leave the row
            # its named start opened spinning for the rest of the turn. An end
            # whose start was skipped closes nothing — the id matches no row.
            call_id = str(data.get("id") or "") or None
            # A duplicate the runtime declined is an error *to the model* — that
            # is how it is told to stop asking — but no work failed, and the
            # first call's row already shows the result. A ✗ here would send the
            # reader hunting a failure that did not happen.
            failed = bool(data.get("is_error")) and not data.get("suppressed_duplicate")

            # Check if THIS call is the one that launched a detached investigation.
            # ``DETACHED_LAUNCH_TOOL_NAMES`` includes ``slash_invoke`` because
            # /investigate can detach through it, but slash_invoke also carries
            # every non-detaching slash command (e.g. /integrations list) — so
            # gating on "any launch happened this turn" left an unrelated,
            # already-completed slash_invoke call in the same batch spinning
            # forever too. ``call_detached`` scopes the check to this call_id.
            record = current_detached_launch_record()
            if (
                record is not None
                and record.call_detached(call_id)
                and tool_name in DETACHED_LAUNCH_TOOL_NAMES
            ):
                self._sink.leave_tool_status_open(call_id=call_id, title=DETACHED_LAUNCH_ROW_TITLE)
                return

            self._sink.end_tool_status(failed=failed, call_id=call_id)


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
        agent = build_default_headless_agent(
            session=session,
            output=live_sink,
            console=self._console,
            logger=logger,
            surface="gateway",
            observer_factory=lambda _message: observer,
            subprocess_presenter_factory=headless_subprocess_presenter_factory,
            investigation_ports_factory=gateway_investigation_launch_ports,
            slash_ports_factory=self._slash_ports_factory,
            gather=GatherPorts(),
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
