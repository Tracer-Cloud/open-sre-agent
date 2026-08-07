"""Bind scheduler runners to the same process-wide Gateway turn gate."""

from __future__ import annotations

from collections.abc import Callable

from gateway.core.runtime.concurrency import TurnConcurrencyGate
from platform.scheduler.agent_runner import (
    get_agent_runner,
    register_agent_runner,
)
from platform.scheduler.investigation_runner import (
    get_investigation_runner,
    register_investigation_runner,
)


def _with_gate[**P, R](
    runner: Callable[P, R],
    gate: TurnConcurrencyGate,
) -> Callable[P, R]:
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        gate.acquire()
        try:
            return runner(*args, **kwargs)
        finally:
            gate.release()

    return run


def gate_registered_scheduler_runners(gate: TurnConcurrencyGate) -> None:
    """Wrap both scheduler runner seams after their normal bootstraps install."""
    if (agent_runner := get_agent_runner()) is not None:
        register_agent_runner(_with_gate(agent_runner, gate))
    if (investigation_runner := get_investigation_runner()) is not None:
        register_investigation_runner(_with_gate(investigation_runner, gate))


__all__ = ["gate_registered_scheduler_runners"]
