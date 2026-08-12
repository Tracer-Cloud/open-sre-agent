"""Gather goal reviewer rejects discovery-only conclusions (S1 interactive)."""

from __future__ import annotations

from typing import Any

from core.agent.goals import GoalObservation
from core.agent_harness.turns.goal_review import build_gather_goal_reviewer
from core.llm.types import AgentLLMResponse


class _ScriptedLLM:
    model_id = "test"

    def __init__(self, content: str) -> None:
        self.content = content
        self.invokes = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        _ = tools
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        self.invokes += 1
        return AgentLLMResponse(content=self.content)


def _obs(*, text: str = "draft HogQL…", evidence: int = 2) -> GoalObservation:
    return GoalObservation(
        final_text=text,
        evidence_count=evidence,
        iteration=1,
        max_iterations=6,
    )


def test_gather_discovery_only_rejected_even_when_llm_says_reached() -> None:
    """Interactive S1: schema thrash + draft HogQL must not conclude gather."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    calls: list[tuple[str, dict[str, Any]]] = [
        ("list_posthog_tools", {"name_filter": "", "include_schema": True}),
        (
            "call_posthog_tool",
            {"tool_name": "read-data-schema", "arguments": {"entity": "events"}},
        ),
    ]
    goal = build_gather_goal_reviewer(llm, "How many Windows users?", calls)
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0


def test_gather_after_execute_sql_defers_to_llm() -> None:
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    calls: list[tuple[str, dict[str, Any]]] = [
        ("list_posthog_tools", {}),
        (
            "call_posthog_tool",
            {
                "tool_name": "execute-sql",
                "arguments": {"query": "SELECT uniqExact(distinct_id) FROM events"},
            },
        ),
    ]
    goal = build_gather_goal_reviewer(llm, "How many Windows users?", calls)
    assert goal.verify is not None
    assert goal.verify(_obs(text="273 Windows users", evidence=2)) is True
    assert llm.invokes == 1
