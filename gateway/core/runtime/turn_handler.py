"""Dispatch one inbound gateway message through the shared headless agent.

This is the **only** gateway turn handler. Transport dispatchers
(Slack/Discord/Telegram) are ingress adapters: they authorize, resolve a
session, build a sink, then call this callback. Process-wide capacity is an
optional gate on the same object — not a second handler.

Transport-agnostic: takes ``(text, session, sink, logger)``, runs the turn, and
finalizes outbound text on the sink. Agent reuse is handled by
:class:`SessionAgentPool`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.runtime import TurnBinding
from core.agent_harness.spi.session_goal import (
    SessionGoal,
    format_session_goal_progress,
    format_session_goal_status_line,
)
from gateway.core.runtime.cancel_console import CancelConsole, ensure_turn_cancel
from gateway.core.runtime.capability_policy import ensure_gateway_capability_policy
from gateway.core.runtime.concurrency import TurnConcurrencyGate
from gateway.core.runtime.session_agents import SessionAgentPool
from gateway.core.runtime.status_messages import EMPTY_RESPONSE_MESSAGE
from gateway.core.transport_api import GatewaySink
from platform.analytics.cli import (
    capture_gateway_turn_completed,
    capture_gateway_turn_failed,
    capture_gateway_turn_started,
)
from platform.analytics.usage_context import (
    CANONICAL_SURFACES,
    bound_usage_context,
    get_surface,
)
from platform.observability.trace.spans import traced_session

SlashPortsFactory = Callable[[], Any]

_DEFAULT_BUSY_MESSAGE = "OpenSRE is at capacity. Please try again shortly."


class GatewayTurnHandler:
    """Services one inbound gateway message per call (a :data:`GatewayAgentCallback`).

    One :class:`HeadlessAgent` is kept per logical session and reused across
    turns; each message goes through :meth:`HeadlessAgent.handle` with a
    :class:`TurnBinding` (sink hooks, cancel console) — the same call the
    interactive shell makes. Concurrent turns for different sessions stay
    isolated.

    When ``gate`` is set, capacity is checked here before the turn runs — the
    manager must not wrap this class in a second "turn handler".
    """

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
        gate: TurnConcurrencyGate | None = None,
        busy_message: str = _DEFAULT_BUSY_MESSAGE,
    ) -> None:
        self._console = console
        self._pool = SessionAgentPool(
            console=console,
            slash_ports_factory=slash_ports_factory,
        )
        # Gateway already bootstrapped env at process start; turns must not reload.
        self._gate = gate
        self._busy_message = busy_message

    def __call__(
        self,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> None:
        if self._gate is not None and not self._gate.try_acquire():
            sink.finalize(self._busy_message)
            return
        try:
            self._run_turn(text, session, sink, logger)
        finally:
            if self._gate is not None:
                self._gate.release()

    def _run_turn(
        self,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> None:
        # Idempotent host policy (also applied in SessionResolver for production).
        ensure_gateway_capability_policy(session)
        session_id = getattr(session, "session_id", None)
        surface = get_surface()
        if surface not in CANONICAL_SURFACES:
            # Require transport binding (Slack/Telegram dispatchers). Do not invent
            # a non-canonical surface that breaks channel breakdowns.
            logger.warning("gateway_turn missing surface binding; started/completed omit surface")
            surface = None
        started = time.monotonic()

        cancel = ensure_turn_cancel(sink)
        turn_console = CancelConsole(self._console, cancel)
        with (
            bound_usage_context(session_id=session_id),
            traced_session(session_id, component="gateway_turn"),
            # Held for the whole turn: the pooled agent's session, sink and
            # accounting are rebound per message, so an overlapping turn for the
            # same session would retarget an agent that is still dispatching.
            self._pool.session_agent(session=session, sink=sink, logger=logger) as agent,
        ):
            try:
                if surface:
                    capture_gateway_turn_started(surface=surface)

                def _cancel_requested() -> bool:
                    return isinstance(cancel, threading.Event) and cancel.is_set()

                def _on_progress(goal: SessionGoal) -> None:
                    # Same progress contract as the interactive shell: paint mid-loop
                    # and scrub happens inside the agent's goal loop. Gateway sinks
                    # get a compact status line; full text goes to debug logs.
                    full = format_session_goal_progress(goal, session=session)
                    compact = format_session_goal_status_line(goal, session=session)
                    if full:
                        logger.debug("gateway_session_goal_progress\n%s", full)
                    if compact:
                        set_status = getattr(sink, "set_tool_status", None)
                        if callable(set_status):
                            set_status(compact)

                turn_result = agent.handle(
                    text,
                    TurnBinding(
                        session=session,
                        tool_hooks=getattr(sink, "tool_hooks", None),
                        console=turn_console,
                        is_tty=False,
                    ),
                    cancel_requested=_cancel_requested,
                    on_progress=_on_progress,
                )
                outbound_text = turn_result.primary_response_text
                logger.debug(
                    "gateway_turn done intent=%s answered=%s outbound_chars=%s",
                    turn_result.final_intent,
                    turn_result.answered,
                    len(outbound_text),
                )
                # Host soft-timeout (or stop) already owns the sink terminal
                # message — do not overwrite it with empty/fallback finalize.
                cancelled = isinstance(cancel, threading.Event) and cancel.is_set()
                # A streamed answer (answered=True) already resolved the placeholder status
                # via the sink. Otherwise always finalize so the placeholder never hangs —
                # even when the turn produced no text.
                if not turn_result.answered and not cancelled:
                    sink.finalize(outbound_text or EMPTY_RESPONSE_MESSAGE)
                # Resolve rebuilds SessionCore from disk next inbound message —
                # persist session_goal (attach / progress / /goal pause) now.
                SessionManager.for_session(session).flush(session)
                if surface:
                    capture_gateway_turn_completed(
                        surface=surface,
                        duration_ms=(time.monotonic() - started) * 1000.0,
                        answered=bool(turn_result.answered),
                        final_intent=str(turn_result.final_intent or "") or None,
                    )
            except Exception as exc:
                # Always emit failure analytics (surface optional) so miswired
                # transports remain visible in PostHog.
                capture_gateway_turn_failed(
                    surface=surface,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    error_type=type(exc).__name__,
                )
                raise


__all__ = ["GatewayTurnHandler"]
