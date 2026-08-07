"""Process-wide turn concurrency shared by every Gateway ingress.

Production chat turns take the gate via :class:`GatewayTurnHandler` (``gate=``).
A callback wrapper for arbitrary handlers lives under ``gateway/tests/`` only —
see ``gateway/tests/runtime/concurrency_limited_handler.py``.
"""

from __future__ import annotations

import threading

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


__all__ = [
    "TurnConcurrencyGate",
    "turn_limit_for_profile",
]
