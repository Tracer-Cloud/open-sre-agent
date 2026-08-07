"""Process-wide turn concurrency shared by every Gateway ingress.

Production chat turns take the gate via :class:`GatewayTurnHandler` (``gate=``).
:class:`ConcurrencyLimitedTurnHandler` wraps *arbitrary* callbacks (tests,
scheduler helpers) — it is not a second production turn handler.
"""

from __future__ import annotations

import logging
import threading

from core.agent_harness.session import SessionCore
from gateway.core.runtime.sink_protocol import GatewayAgentCallback, GatewaySink
from platform.deployment_contracts.models import SizeProfile

_PROFILE_LIMITS = {
    SizeProfile.SMALL: 1,
    SizeProfile.MEDIUM: 2,
    SizeProfile.LARGE: 4,
}


def turn_limit_for_profile(profile: SizeProfile | str | None = None) -> int:
    """Process-gate / transport-pool default for ``OPENSRE_SIZE_PROFILE``.

    When ``profile`` is omitted, reads the env (default SMALL). Used by
    :class:`TurnConcurrencyGate` and as the default for per-transport
    ``max_concurrent_turns`` when the transport-specific env is unset.
    """
    import os

    raw = profile if profile is not None else os.getenv("OPENSRE_SIZE_PROFILE", "SMALL")
    return _PROFILE_LIMITS[SizeProfile(str(raw).strip().upper())]


class TurnConcurrencyGate:
    """A non-blocking process-wide capacity gate for agent turns."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("turn concurrency limit must be positive")
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)

    @classmethod
    def for_profile(cls, profile: SizeProfile | str) -> TurnConcurrencyGate:
        """Build the documented concurrency limit for a Fargate size profile."""
        return cls(turn_limit_for_profile(profile))

    def try_acquire(self) -> bool:
        """Take one slot without waiting, leaving durable excess work queued."""
        return self._semaphore.acquire(blocking=False)

    def acquire(self, *, timeout: float | None = None) -> bool:
        """Wait for capacity, used by already-claimed scheduler executions."""
        if timeout is None:
            return self._semaphore.acquire()
        return self._semaphore.acquire(timeout=timeout)

    def release(self) -> None:
        """Return one previously acquired slot."""
        self._semaphore.release()


class ConcurrencyLimitedTurnHandler:
    """Capacity wrapper for arbitrary :data:`GatewayAgentCallback` callables.

    Prefer ``GatewayTurnHandler(gate=…)`` for production chat. Keep this for
    tests and non-handler callbacks that share the same gate.
    """

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
    """Wrap an arbitrary callback with the shared capacity gate."""
    return ConcurrencyLimitedTurnHandler(handler=handler, gate=gate)


__all__ = [
    "ConcurrencyLimitedTurnHandler",
    "TurnConcurrencyGate",
    "gated_callback",
    "turn_limit_for_profile",
]
