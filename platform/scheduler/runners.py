"""The runners a scheduled task dispatches through, bundled as one value.

A scheduled agentic-loop task (Sentry digest, PostHog report, GitHub PR sweep,
manual loop) and a scheduled investigation each run through a registered
runner. Both used to be installed as a side effect and then *rewritten* in
place by the gateway to add its capacity gate — a read-modify-write on module
state that was not idempotent, so gating twice made one run cost two permits.

Here the runners are a value instead. :meth:`SchedulerRunners.gated` returns a
new bundle rather than mutating a registered one, so a host states its gate
once, at construction, and double-gating has no shape to happen in.

The bundle is assembled by the composition root (``bootstrap.adapters``), which
is the only layer allowed to see both ``integrations`` and ``tools``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from platform.scheduler.agent_runner import AgentRunner, register_agent_runner
from platform.scheduler.investigation_runner import (
    InvestigationRunner,
    register_investigation_runner,
)


class TurnGate(Protocol):
    """Capacity a scheduled run takes while it holds a turn."""

    def acquire(self, *, timeout: float | None = None) -> bool:
        """Take one permit, blocking until it is available."""

    def release(self) -> None:
        """Give the permit back."""


def _gated[**P, R](runner: Callable[P, R], gate: TurnGate) -> Callable[P, R]:
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        gate.acquire()
        try:
            return runner(*args, **kwargs)
        finally:
            gate.release()

    return run


@dataclass(frozen=True)
class SchedulerRunners:
    """The scheduler's two runner seams, ready to install."""

    agent: AgentRunner
    investigation: InvestigationRunner

    def gated(self, gate: TurnGate) -> SchedulerRunners:
        """Return a bundle whose runs each take one permit from ``gate``."""
        return SchedulerRunners(
            agent=_gated(self.agent, gate),
            investigation=_gated(self.investigation, gate),
        )

    def install(self) -> None:
        """Bind both runners for the scheduler to dispatch through.

        Investigation first: the scheduled-agent runner resolves against it,
        so the order is load-bearing rather than cosmetic.
        """
        register_investigation_runner(self.investigation)
        register_agent_runner(self.agent)


__all__ = ["SchedulerRunners", "TurnGate"]
