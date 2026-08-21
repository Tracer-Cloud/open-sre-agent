"""Scheduled agentic-loop runs share the gateway's turn gate.

The scheduler runs agentic loop tasks (Sentry digest, PostHog report, GitHub PR
sweep, manual loop). Each one costs a turn, so it must take the same capacity
gate chat turns take, exactly once, and give it back even when the run fails.

The runners are a value the host gates once at construction, so these also pin
that gating cannot compound the way the old read-modify-write on module state
could.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from infrastructure.scheduling.scheduler.agent_runner import (
    get_agent_runner,
    invoke_agent_runner,
    register_agent_runner,
)
from infrastructure.scheduling.scheduler.investigation_runner import (
    get_investigation_runner,
    register_investigation_runner,
)
from infrastructure.scheduling.scheduler.runners import SchedulerRunners


class _CountingGate:
    """Stands in for ``TurnConcurrencyGate``, recording each acquire/release."""

    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self, *, timeout: float | None = None) -> bool:
        _ = timeout
        self.acquired += 1
        return True

    def release(self) -> None:
        self.released += 1


def _unused_runner(payload: dict[str, Any]) -> None:
    """Stands in for the seam a given test never dispatches."""
    _ = payload
    return None


@pytest.fixture
def restored_scheduler_runners() -> Iterator[None]:
    """Both runner seams are module globals; put them back after each test."""
    agent = get_agent_runner()
    investigation = get_investigation_runner()
    yield
    register_agent_runner(agent)
    register_investigation_runner(investigation)


@pytest.mark.usefixtures("restored_scheduler_runners")
def test_a_scheduled_run_takes_the_gate_once_and_gives_it_back() -> None:
    # Arrange — a loop task registered the way its bootstrap registers it
    runs: list[dict[str, Any]] = []

    def runner(payload: dict[str, Any]) -> str:
        runs.append(payload)
        return "report body"

    gate = _CountingGate()
    SchedulerRunners(agent=runner, investigation=_unused_runner).gated(gate).install()

    # Act
    result = invoke_agent_runner({"source": "sentry_digest"})

    # Assert — one turn's worth of capacity, and the body reaches the scheduler
    assert (gate.acquired, gate.released) == (1, 1)
    assert runs == [{"source": "sentry_digest"}]
    assert result == "report body"


@pytest.mark.usefixtures("restored_scheduler_runners")
def test_the_gate_is_given_back_when_a_scheduled_run_fails() -> None:
    # Arrange — a loop task that raises partway through
    def failing_runner(payload: dict[str, Any]) -> str:
        _ = payload
        raise RuntimeError("digest failed")

    gate = _CountingGate()
    SchedulerRunners(agent=failing_runner, investigation=_unused_runner).gated(gate).install()

    # Act
    with pytest.raises(RuntimeError, match="digest failed"):
        invoke_agent_runner({"source": "sentry_digest"})

    # Assert — a failed run must not leak capacity
    assert (gate.acquired, gate.released) == (1, 1)


@pytest.mark.usefixtures("restored_scheduler_runners")
def test_gating_a_bundle_twice_still_costs_one_permit_a_run() -> None:
    """The hazard that made the runners a value rather than module state.

    Gating used to read the registered runner, wrap it and write it back, so a
    second gating wrapped an already gated runner and one scheduled run cost
    two permits. Gating a value returns a new bundle instead of mutating a
    registered one, so installing again cannot compound the cost.
    """

    # Arrange
    def runner(payload: dict[str, Any]) -> str:
        _ = payload
        return "report body"

    gate = _CountingGate()
    bundle = SchedulerRunners(agent=runner, investigation=_unused_runner).gated(gate)

    # Act — as if two hosts, or a reload path, each installed the runners
    bundle.install()
    bundle.install()
    invoke_agent_runner({"source": "manual_loop"})

    # Assert — one run, one permit
    assert (gate.acquired, gate.released) == (1, 1)
