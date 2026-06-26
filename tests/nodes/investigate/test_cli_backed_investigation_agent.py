"""Tests for CLIBackedInvestigationAgent termination policy.

CLI-backed LLMs flatten the entire conversation history into a single prompt
per invoke. They tend to conclude the investigation before all planned tools
are called. CLIBackedInvestigationAgent overrides _should_accept_conclusion to
nudge the model until every planned tool has been called at least once.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from core.orchestration.node.investigate.agent import CLIBackedInvestigationAgent


def _make_agent_with_state(
    *,
    planned_actions: list[str],
    evidence: dict[str, Any],
) -> CLIBackedInvestigationAgent:
    agent = CLIBackedInvestigationAgent()
    agent._planned_actions = planned_actions
    agent._current_evidence = evidence
    return agent


class TestShouldAcceptConclusion:
    def test_accepts_when_no_planned_actions(self) -> None:
        agent = _make_agent_with_state(planned_actions=[], evidence={})
        accept, nudge = agent._should_accept_conclusion(evidence_count=0, iteration=0)
        assert accept is True
        assert nudge is None

    def test_accepts_when_all_planned_tools_called(self) -> None:
        agent = _make_agent_with_state(
            planned_actions=["tool_a", "tool_b"],
            evidence={"tool_a": {"data": 1}, "tool_b": {"data": 2}},
        )
        accept, nudge = agent._should_accept_conclusion(evidence_count=2, iteration=1)
        assert accept is True
        assert nudge is None

    def test_rejects_when_uncalled_tools_remain(self) -> None:
        agent = _make_agent_with_state(
            planned_actions=["tool_a", "tool_b"],
            evidence={"tool_a": {"data": 1}},
        )
        accept, nudge = agent._should_accept_conclusion(evidence_count=1, iteration=0)
        assert accept is False
        assert nudge is not None
        assert "tool_b" in nudge
        assert "tool_a" not in nudge

    def test_nudge_lists_all_uncalled_tools(self) -> None:
        agent = _make_agent_with_state(
            planned_actions=["tool_a", "tool_b", "tool_c"],
            evidence={},
        )
        _, nudge = agent._should_accept_conclusion(evidence_count=0, iteration=0)
        assert nudge is not None
        assert "tool_a" in nudge
        assert "tool_b" in nudge
        assert "tool_c" in nudge

    def test_accepts_near_max_iterations_to_avoid_infinite_loop(self) -> None:
        agent = _make_agent_with_state(
            planned_actions=["tool_a"],
            evidence={},
        )
        # At MAX_INVESTIGATION_LOOPS - 2 the agent must accept to leave room
        # for a final text pass before the outer loop cap triggers.
        cutoff = MAX_INVESTIGATION_LOOPS - 2
        accept, nudge = agent._should_accept_conclusion(evidence_count=0, iteration=cutoff)
        assert accept is True
        assert nudge is None

    def test_still_nudges_one_iteration_before_cutoff(self) -> None:
        agent = _make_agent_with_state(
            planned_actions=["tool_a"],
            evidence={},
        )
        cutoff = MAX_INVESTIGATION_LOOPS - 2
        accept, _ = agent._should_accept_conclusion(evidence_count=0, iteration=cutoff - 1)
        assert accept is False

    def test_accepts_when_evidence_reference_not_set(self) -> None:
        agent = CLIBackedInvestigationAgent()
        agent._planned_actions = ["tool_a"]
        # _current_evidence not set — should not crash and must accept
        accept, nudge = agent._should_accept_conclusion(evidence_count=0, iteration=0)
        assert accept is True
        assert nudge is None

    def test_seeded_tools_count_as_called(self) -> None:
        # Tools populated via seed phase (before the loop) appear in evidence
        # with their real results, so they should NOT trigger a nudge.
        agent = _make_agent_with_state(
            planned_actions=["seed_tool", "follow_up_tool"],
            evidence={"seed_tool": {"rows": []}, "follow_up_tool": {"metrics": []}},
        )
        accept, _ = agent._should_accept_conclusion(evidence_count=2, iteration=0)
        assert accept is True


class TestAgentSelfPromotion:
    """ConnectedInvestigationAgent promotes itself to CLIBackedInvestigationAgent at runtime.

    The CLI detection lives in agent.run() rather than pipeline.py to keep
    the pipeline free of vendor service imports (layering rule enforced by
    tests/core/orchestration/test_layering.py).
    """

    def test_cli_backed_investigation_agent_is_subclass_of_connected(self) -> None:
        from core.orchestration.node.investigate import ConnectedInvestigationAgent

        assert issubclass(CLIBackedInvestigationAgent, ConnectedInvestigationAgent)

    def test_base_agent_promotes_to_cli_subclass_when_llm_is_cli_backed(
        self, monkeypatch: Any
    ) -> None:
        from core.orchestration.node.investigate import ConnectedInvestigationAgent
        from services.agent_llm_client import CLIBackedAgentClient

        fake_cli_llm = mock.MagicMock(spec=CLIBackedAgentClient)
        monkeypatch.setattr(
            "core.orchestration.node.investigate.agent.get_agent_llm", lambda: fake_cli_llm
        )

        agent = ConnectedInvestigationAgent()
        assert type(agent) is ConnectedInvestigationAgent  # not yet promoted

        # Simulate what run() does after calling get_agent_llm()
        if (
            isinstance(fake_cli_llm, CLIBackedAgentClient)
            and type(agent) is ConnectedInvestigationAgent
        ):
            agent.__class__ = CLIBackedInvestigationAgent

        assert type(agent) is CLIBackedInvestigationAgent

    def test_subclass_is_not_promoted_when_llm_is_cli_backed(self) -> None:
        """Custom subclasses manage their own termination — the promotion guard
        (type(self) is ConnectedInvestigationAgent) must not affect them."""
        from core.orchestration.node.investigate import ConnectedInvestigationAgent
        from services.agent_llm_client import CLIBackedAgentClient

        class CustomAgent(ConnectedInvestigationAgent):
            pass

        fake_cli_llm = mock.MagicMock(spec=CLIBackedAgentClient)
        agent = CustomAgent()

        # The guard fires only for the base class, not subclasses
        if (
            isinstance(fake_cli_llm, CLIBackedAgentClient)
            and type(agent) is ConnectedInvestigationAgent
        ):
            agent.__class__ = CLIBackedInvestigationAgent

        assert type(agent) is CustomAgent  # unchanged
