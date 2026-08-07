"""Dispatch one inbound gateway message through the shared headless agent.

Transport-agnostic: takes ``(text, session, sink, logger)``, runs the turn, and
finalizes outbound text on the sink. Agent reuse is handled by
:class:`SessionAgentPool`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.session import SessionCore
from gateway.core.runtime.session_agents import SessionAgentPool
from gateway.core.runtime.sink_protocol import GatewaySink
from gateway.core.runtime.status_messages import EMPTY_RESPONSE_MESSAGE
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

_UNSUPPORTED_GATEWAY_CAPABILITIES = (
    "investigation",
    "llm_provider",
    "task_cancel",
)


class GatewayTurnHandler:
    """Services one inbound gateway message per call (a :data:`GatewayAgentCallback`).

    One :class:`HeadlessAgent` is kept per logical session and reused across
    turns; per-turn sinks, accounting, and tool hooks are rebound via
    :meth:`HeadlessAgent.bind_turn`. Concurrent turns for different sessions
    stay isolated.
    """

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
    ) -> None:
        self._pool = SessionAgentPool(
            console=console,
            slash_ports_factory=slash_ports_factory,
        )
        # Gateway already bootstrapped env at process start; turns must not reload.
        self._harness = AgentHarness(HarnessConfig(load_env=False))

    def __call__(
        self,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> None:
        session.available_capabilities.update(dict.fromkeys(_UNSUPPORTED_GATEWAY_CAPABILITIES, ()))
        session_id = getattr(session, "session_id", None)
        surface = get_surface()
        if surface not in CANONICAL_SURFACES:
            # Require transport binding (Slack/Telegram dispatchers). Do not invent
            # a non-canonical surface that breaks channel breakdowns.
            logger.warning("gateway_turn missing surface binding; started/completed omit surface")
            surface = None
        started = time.monotonic()

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
                agent.bind_turn(
                    session=session,
                    accounting=DefaultTurnAccounting(session, text),
                    tool_hooks=getattr(sink, "tool_hooks", None),
                )
                turn_result = self._harness.dispatch_message(text, agent=agent)
                outbound_text = turn_result.primary_response_text
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
                    sink.finalize(outbound_text or EMPTY_RESPONSE_MESSAGE)
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
