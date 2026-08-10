"""Strict / independent SessionGoal evaluation."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session.session_goal_evaluate import (
    evaluate_session_goal,
    turn_has_session_goal_evidence,
)
from core.agent_harness.session.session_goal_review import build_session_goal_llm_evaluator
from core.agent_harness.turns.session_goal_loop import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _result(
    text: str,
    *,
    executed: int = 0,
    success: int = 0,
) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=executed,
            executed_count=executed,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_turn_has_session_goal_evidence_requires_a_tool_that_succeeded() -> None:
    """Evidence is work that worked, not work that was attempted.

    A tool that ran and errored must not let an ``achieved`` claim through:
    the goal is not met just because the agent tried something.
    """
    # No tools at all.
    assert turn_has_session_goal_evidence(_result("done")) is False
    # One tool ran and failed — attempted, not achieved.
    assert turn_has_session_goal_evidence(_result("done", executed=1)) is False
    # One tool ran and succeeded.
    assert turn_has_session_goal_evidence(_result("done", executed=1, success=1)) is True


def test_bare_achieved_without_checklist_or_tools_stays_active() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)

    verdict = evaluate_session_goal(
        goal,
        _result("All done. session_goal:achieved"),
        session=session,
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "no tool evidence" in verdict.reason
    assert session.session_goal is not None
    assert "no tool evidence" in session.session_goal.last_reason


def test_achieved_with_tool_evidence_completes_condition_only_goal() -> None:
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    verdict = evaluate_session_goal(
        goal,
        _result("Patched and tested. session_goal:achieved", executed=2, success=2),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == "achieved with tool evidence"


def test_achieved_ignored_when_checklist_incomplete() -> None:
    goal = SessionGoal(
        condition="checklist",
        checklist=("A", "B"),
        completed=frozenset({0}),
    )
    verdict = evaluate_session_goal(
        goal,
        _result("Done enough. session_goal:achieved"),
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "achieved tag ignored" in verdict.reason
    assert "next: B" in verdict.reason


def test_checklist_complete_achieves_without_achieved_tag() -> None:
    goal = SessionGoal(
        condition="checklist",
        checklist=("A", "B"),
        completed=frozenset({0}),
    )
    verdict = evaluate_session_goal(
        goal,
        _result("Finished B. session_goal:done=1"),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == "checklist complete"


def test_outer_loop_rejects_bare_achieved_until_budget() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _result("pretending. session_goal:achieved")

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="real work", max_outer_turns=2),
    )

    assert len(turns) == 2
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert any("no tool evidence" in t for t in turns[1:])


def test_llm_evaluator_rejects_soft_achieve() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return type("R", (), {"content": "NOT_REACHED"})()

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    evaluate = build_session_goal_llm_evaluator(_LLM())  # type: ignore[arg-type]
    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)

    status = evaluate(
        goal,
        _result("session_goal:achieved", executed=1, success=1),
        session=session,
    )
    assert status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert "not reached" in session.session_goal.last_reason.lower()


def test_llm_evaluator_confirms_soft_achieve() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return type("R", (), {"content": "GOAL_REACHED"})()

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    evaluate = build_session_goal_llm_evaluator(_LLM())  # type: ignore[arg-type]
    status = evaluate(
        SessionGoal(condition="finish migration", max_outer_turns=3),
        _result("session_goal:achieved", executed=1, success=1),
    )
    assert status == SessionGoalStatus.ACHIEVED
