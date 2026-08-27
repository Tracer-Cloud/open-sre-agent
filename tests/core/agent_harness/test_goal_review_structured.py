"""Inner ReAct goal reviewer uses closed structured verdicts."""

from __future__ import annotations

from typing import Any

from core.agent.goals import GoalObservation
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.action_driver import _goal_review_user_request
from core.agent_harness.turns.goal_review import build_goal_reviewer
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
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


def _obs(*, text: str = "done", evidence: int = 1) -> GoalObservation:
    return GoalObservation(
        final_text=text,
        evidence_count=evidence,
        iteration=1,
        max_iterations=4,
    )


def test_goal_reviewer_rejects_on_structured_not_reached() -> None:
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 1


def test_goal_reviewer_accepts_on_structured_reached() -> None:
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_goal_reviewer_fails_open_on_free_text() -> None:
    """Prose without JSON must accept (fail open) — never force extra ReAct work."""
    llm = _ScriptedLLM("NOT_REACHED — keep going")
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_structured_answer_goal_review_recovers_latest_non_qa_user_request() -> None:
    original = "For facebook/react, return merged PR count, median time-to-merge, and star gain."
    prior_answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which window?", options=("7d", "30d")),),
        ("7d",),
    )
    current_answers = format_ask_user_answers(
        (AskUserQuestion(label="Zone", title="Which timezone?", options=("UTC", "Local")),),
        ("UTC",),
    )
    snapshot = TurnSnapshot(
        text=current_answers,
        conversation_messages=(
            ("user", original),
            ("assistant", "Which window?"),
            ("user", prior_answers),
            ("assistant", "Which timezone?"),
        ),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(current_answers, snapshot) == original


def test_ordinary_turn_goal_review_uses_current_message() -> None:
    snapshot = TurnSnapshot(
        text="Run the same analysis for vercel/next.js",
        conversation_messages=(("user", "Analyze facebook/react"),),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(snapshot.text, snapshot) == snapshot.text
