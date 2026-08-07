"""Test-only capacity wrapper for arbitrary gateway callbacks.

Production chat uses :class:`~gateway.core.runtime.turn_handler.GatewayTurnHandler`
with ``gate=``. This helper stays under ``gateway/tests/`` so it cannot be
mistaken for a second production turn-handler class (Wave C4 quarantine).
"""

from __future__ import annotations

import logging

from core.agent_harness.session import SessionCore
from gateway.core.runtime.concurrency import TurnConcurrencyGate
from gateway.core.runtime.sink_protocol import GatewayAgentCallback, GatewaySink


class ConcurrencyLimitedTurnHandler:
    """Capacity wrapper for arbitrary :data:`GatewayAgentCallback` callables."""

    def __init__(
        self,
        *,
        handler: GatewayAgentCallback,
        gate: TurnConcurrencyGate,
        busy_message: str = "OpenSRE is at capacity. Please try again shortly.",
    ) -> None:
        self._handler = handler
        self._gate = gate
        self._busy_message = busy_message

    def __call__(
        self,
        text: str,
        session: SessionCore,
        sink: GatewaySink,
        logger: logging.Logger,
    ) -> None:
        if not self._gate.try_acquire():
            sink.finalize(self._busy_message)
            return
        try:
            self._handler(text, session, sink, logger)
        finally:
            self._gate.release()


def gated_callback(
    handler: GatewayAgentCallback,
    gate: TurnConcurrencyGate,
) -> GatewayAgentCallback:
    """Wrap an arbitrary callback with the shared capacity gate (tests only)."""
    return ConcurrencyLimitedTurnHandler(handler=handler, gate=gate)
