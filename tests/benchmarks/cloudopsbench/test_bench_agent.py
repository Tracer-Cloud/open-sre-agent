"""Tests for the CloudOpsBench-specific investigation agent subclass."""

from __future__ import annotations

import pytest

from app.agent.investigation import ConnectedInvestigationAgent
from tests.benchmarks.cloudopsbench.bench_agent import BenchInvestigationAgent


def test_bench_agent_is_subclass_of_production_agent() -> None:
    """Subclass relationship is what lets the bench inject its agent via the
    pipeline's ``agent_class`` parameter without production code knowing."""
    assert issubclass(BenchInvestigationAgent, ConnectedInvestigationAgent)


def test_bench_agent_blocks_conclusion_below_floor() -> None:
    """Below MIN_TOOL_CALLS: reject conclusion + emit nudge user message.
    This is the entire point of the subclass — gpt-5 was bailing after 4
    tool calls on the June-3 run; the floor forces deeper investigation."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=3, iteration=2)
    assert accept is False
    assert nudge is not None
    # Nudge text must mention the count so the model knows where it stands.
    assert "3 tool result" in nudge


def test_bench_agent_allows_conclusion_at_threshold() -> None:
    """Exactly MIN_TOOL_CALLS evidence entries → accept conclusion. The
    threshold is INCLUSIVE so the agent isn't forced to do extra calls
    when it has already met the floor."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(
        evidence_count=BenchInvestigationAgent.MIN_TOOL_CALLS,
        iteration=BenchInvestigationAgent.MIN_TOOL_CALLS,
    )
    assert accept is True
    assert nudge is None


def test_bench_agent_allows_conclusion_above_threshold() -> None:
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(
        evidence_count=BenchInvestigationAgent.MIN_TOOL_CALLS + 10, iteration=8
    )
    assert accept is True
    assert nudge is None


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 6, 7])
def test_bench_agent_rejects_below_floor_for_every_count_under_min(count: int) -> None:
    """Exhaustive: every count below the floor must be rejected. Guards
    against a future off-by-one in the floor comparison."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=count, iteration=count)
    assert accept is False
    assert nudge is not None


def test_bench_agent_threshold_is_class_attribute_for_easy_override() -> None:
    """The threshold is a class attribute so a future bench (or one-off
    experiment) can subclass and tweak it without rebuilding the agent
    instance or duplicating the hook method."""

    class _RelaxedBench(BenchInvestigationAgent):
        MIN_TOOL_CALLS = 3

    agent = _RelaxedBench()
    accept, _ = agent._should_accept_conclusion(evidence_count=3, iteration=2)
    assert accept is True
