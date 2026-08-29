"""Test helpers to build the ``SchedulerRunners`` bundle scheduler tests pass in.

The runners re-read their concrete implementations (``run_investigation`` and
the per-source agent runners) on each call, so a bundle built once still honours
a test's monkeypatch of those attributes.
"""

from __future__ import annotations

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload, AgentRunner
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest
from tools.investigation.scheduler_bootstrap import run_scheduled_investigation


def real_runners() -> SchedulerRunners:
    """The production runner bundle (real investigation + agent digest)."""
    return SchedulerRunners(
        agent=run_scheduled_agent_digest,
        investigation=run_scheduled_investigation,
    )


def runners_with_agent(agent: AgentRunner) -> SchedulerRunners:
    """A bundle whose agent runner is ``agent`` and investigation is the real one."""
    return SchedulerRunners(agent=agent, investigation=run_scheduled_investigation)


__all__ = ["AgentPayload", "real_runners", "runners_with_agent"]
