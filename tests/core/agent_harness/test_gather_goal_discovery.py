"""Gather goal reviewer rejects discovery-only conclusions."""

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
    """Schema thrash + draft query must not conclude gather."""
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


class _CapturingLLM(_ScriptedLLM):
    """Records the review message so the metric-query note can be asserted."""

    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.messages: list[dict[str, Any]] = []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        self.messages.extend(messages)
        return super().invoke(messages, system=system, tools=tools)


def _metric_note(calls: list[tuple[str, dict[str, Any]]]) -> str:
    """Render one review and return the ``Metric query executed:`` verdict."""
    # Arrange
    llm = _CapturingLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_gather_goal_reviewer(llm, "How many Windows users?", calls)
    assert goal.verify is not None

    # Act
    goal.verify(_obs(text="273 Windows users"))

    # Assert
    text = "\n".join(str(m.get("content", "")) for m in llm.messages)
    assert "Metric query executed: " in text
    return text.split("Metric query executed: ", 1)[1].split("\n", 1)[0].strip()


def test_review_note_reports_a_native_metric_tool_as_a_metric_query() -> None:
    """A native metrics/logs query tool is a formed metric query."""
    assert _metric_note([("query_grafana_metrics", {"expr": "up"})]) == "yes"


def test_review_note_reports_a_structured_metric_target_as_a_metric_query() -> None:
    """``call_<vendor>_tool(tool_name=execute-sql)`` is a formed metric query."""
    calls = [("call_posthog_tool", {"tool_name": "execute-sql", "arguments": {}})]
    assert _metric_note(calls) == "yes"


def test_review_note_reports_no_metric_query_when_no_tool_ran() -> None:
    """No executed calls is the only honest "no" for an all-discovery gather.

    A gather where *every* call was schema probing never reaches the reviewer
    at all — it is rejected before the LLM — so the note cannot report on it.
    """
    assert _metric_note([]) == "no"


def test_review_note_separates_a_non_metric_fetch_from_a_metric_query() -> None:
    """A real but non-metric fetch must not claim a metric query ran.

    Sentry issue reads are evidence, so the gather is not discovery-only and
    does reach the reviewer — but the note must still say no query was formed,
    or the metric floor would be suppressed by an unrelated fetch.
    """
    calls = [("call_sentry_tool", {"tool_name": "issue_get", "arguments": {}})]
    assert _metric_note(calls) == "no"


def test_review_note_does_not_yet_know_vendor_query_tools_outside_the_list() -> None:
    """Pins the known limit: the native metric tools are a hard-coded list.

    ``call_newrelic_nrql`` runs a real query, and the note still says no.
    Vendors outside the list in :mod:`gather_discovery_budget` are invisible
    here; registering them per integration is what closes this.
    """
    assert _metric_note([("call_newrelic_nrql", {"query": "SELECT 1"})]) == "no"
