"""Process-wide turn concurrency shared by every Gateway ingress.

Production chat turns take the gate via :class:`TurnHandler` (``gate=``).
``POST /investigate`` and :class:`InvestigationWorker` use the same process
gate (:func:`process_turn_gate`) so HTTP investigate cannot starve chat or
the reverse. Scheduled runs take the same instance: the controller builds
``SchedulerRunners`` and calls ``.gated(turn_gate)`` before installing them.

A callback wrapper for arbitrary handlers is tests-only, not production
``gateway/core/``.
"""

from __future__ import annotations

import os
import threading

from infrastructure.deployment.contracts.models import SizeProfile

_PROFILE_LIMITS = {
    SizeProfile.SMALL: 1,
    SizeProfile.MEDIUM: 2,
    SizeProfile.LARGE: 4,
}

_process_gate: TurnConcurrencyGate | None = None
_process_gate_lock = threading.Lock()


def turn_limit_for_profile(profile: SizeProfile | str | None = None) -> int:
    """Process-gate / transport-pool default for ``OPENSRE_SIZE_PROFILE``.

    When ``profile`` is omitted, reads the env (default SMALL). Used by
    :class:`TurnConcurrencyGate` and as the default for per-transport
    ``max_concurrent_turns`` when the transport-specific env is unset.
    """
    raw = profile if profile is not None else os.getenv("OPENSRE_SIZE_PROFILE", "SMALL")
    return _PROFILE_LIMITS[SizeProfile(str(raw).strip().upper())]


class TurnConcurrencyGate:
    """A process-wide capacity gate for chat, investigate, and scheduled turns."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("turn concurrency limit must be positive")
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)

    @classmethod
    def for_profile(cls, profile: SizeProfile | str) -> TurnConcurrencyGate:
        """Build the documented concurrency limit for an ``OPENSRE_SIZE_PROFILE``."""
        return cls(turn_limit_for_profile(profile))

    def try_acquire(self) -> bool:
        """Take one slot without waiting (chat / sync HTTP investigate)."""
        return self._semaphore.acquire(blocking=False)

    def acquire(self, *, timeout: float | None = None) -> bool:
        """Wait for capacity (scheduler / InvestigationWorker — already claimed)."""
        if timeout is None:
            return self._semaphore.acquire()
        return self._semaphore.acquire(timeout=timeout)

    def release(self) -> None:
        """Return one previously acquired slot."""
        self._semaphore.release()


#: What a caller tells the user when the process is already at capacity.
#: One sentence, one place: chat finalizes it into the conversation and the web
#: app returns it as a 503 body, and they must not drift apart.
AT_CAPACITY_MESSAGE = "OpenSRE is at capacity. Please try again shortly."


def process_turn_gate() -> TurnConcurrencyGate:
    """Return the process-wide gate (lazy from ``OPENSRE_SIZE_PROFILE``).

    :class:`GatewayController` installs its gate here so chat and investigate share one
    semaphore in a full gateway process. Standalone ``WEB_PROFILE`` web creates
    the gate on first investigate/worker use.
    """
    global _process_gate
    with _process_gate_lock:
        if _process_gate is None:
            profile = os.getenv("OPENSRE_SIZE_PROFILE", "SMALL").strip().upper()
            _process_gate = TurnConcurrencyGate.for_profile(profile)
        return _process_gate


def set_process_turn_gate(gate: TurnConcurrencyGate) -> None:
    """Install ``gate`` as the process-wide instance (manager / tests)."""
    global _process_gate
    with _process_gate_lock:
        _process_gate = gate


def reset_process_turn_gate_for_tests() -> None:
    """Clear the process gate singleton so tests can rebuild from env."""
    global _process_gate
    with _process_gate_lock:
        _process_gate = None


__all__ = [
    "AT_CAPACITY_MESSAGE",
    "TurnConcurrencyGate",
    "process_turn_gate",
    "reset_process_turn_gate_for_tests",
    "set_process_turn_gate",
    "turn_limit_for_profile",
]
