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

from dataclasses import dataclass

from infrastructure.process.turn_capacity import TurnGate, queued_turn_slot
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload, AgentRunner
from infrastructure.scheduling.scheduler.investigation_runner import (
    AlertPayload,
    InvestigationResult,
    InvestigationRunner,
)


def _gated_agent(runner: AgentRunner, gate: TurnGate) -> AgentRunner:
    def run(payload: AgentPayload) -> str:
        with queued_turn_slot(gate):
            return runner(payload)

    return run


def _gated_investigation(
    runner: InvestigationRunner,
    gate: TurnGate,
) -> InvestigationRunner:
    def run(alert_payload: AlertPayload) -> InvestigationResult | None:
        with queued_turn_slot(gate):
            return runner(alert_payload)

    return run


@dataclass(frozen=True)
class SchedulerRunners:
    """The scheduler's two runner seams, ready to install."""

    agent: AgentRunner
    investigation: InvestigationRunner

    def gated(self, gate: TurnGate) -> SchedulerRunners:
        """Return a bundle whose runs each take one permit from ``gate``."""
        return SchedulerRunners(
            agent=_gated_agent(self.agent, gate),
            investigation=_gated_investigation(self.investigation, gate),
        )


__all__ = ["SchedulerRunners", "TurnGate"]
