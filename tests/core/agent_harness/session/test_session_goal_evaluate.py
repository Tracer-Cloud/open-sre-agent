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


def test_waiting_for_reason_is_not_an_achieved_claim() -> None:
    """Slash ``/goal set`` paints ``waiting for session_goal:achieved`` — not a claim."""
    from core.agent_harness.session.session_goal_evaluate import (
        reply_claims_session_goal_achieved,
    )

    assert reply_claims_session_goal_achieved("session_goal:achieved") is True
    assert reply_claims_session_goal_achieved("All done.\nsession_goal:achieved") is True
    assert reply_claims_session_goal_achieved("All done. session_goal:achieved") is True
    assert reply_claims_session_goal_achieved("waiting for session_goal:achieved") is False
    assert (
        reply_claims_session_goal_achieved(
            "◎ /goal active\n  reason: waiting for session_goal:achieved"
        )
        is False
    )


def test_slash_capture_waiting_reason_does_not_achieve_host_goal() -> None:
    """Regression: /goal set turn captured status text and falsely achieved."""
    session = SessionCore()
    goal = SessionGoal(
        condition="How many Windows users?",
        max_outer_turns=4,
        host_owned=True,
    )
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result(
            "◎ /goal active · 0s · turn 0/4 · +0 tokens\n"
            "  condition: How many Windows users?\n"
            "  reason: waiting for session_goal:achieved",
            executed=1,
            success=1,
        ),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "waiting" in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_goal_set_attach_turn_does_not_consume_outer_budget() -> None:
    """``/goal set`` attach + autosubmit: first work turn is the next chat."""
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if message.startswith("/goal"):
            attach_session_goal(
                session,
                SessionGoal(
                    condition="count windows users",
                    max_outer_turns=4,
                    host_owned=True,
                ),
            )
            return _result(
                "◎ /goal active\n  reason: waiting for session_goal:achieved",
                executed=1,
                success=1,
            )
        return _result("284 users. session_goal:achieved", executed=1, success=1)

    outcome = run_until_session_goal(_chat, session, "/goal set count windows users")
    assert len(turns) == 1
    assert outcome.turn_count == 0
    assert outcome.goal.status == SessionGoalStatus.ACTIVE
    assert outcome.goal.turns_used == 0

    # Autosubmit path: next chat under the already-active goal.
    outcome2 = run_until_session_goal(_chat, session, "count windows users")
    assert outcome2.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome2.goal.turns_used == 1


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
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_host_owned_achieved_without_tools_completes() -> None:
    """``/goal set`` prose goals may achieve on the tag alone."""
    session = SessionCore()
    goal = SessionGoal(
        condition="list three steps",
        max_outer_turns=3,
        host_owned=True,
    )
    attach_session_goal(session, goal)

    verdict = evaluate_session_goal(
        goal,
        _result("1. a\n2. b\n3. c\nsession_goal:achieved"),
        session=session,
    )

    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACHIEVED


def test_handoff_does_not_replace_host_owned_goal_after_achieve() -> None:
    from core.agent_harness.session.session_goal import attach_session_goal_from_handoffs

    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="list three steps",
            max_outer_turns=3,
            host_owned=True,
            status=SessionGoalStatus.ACHIEVED,
        ),
    )
    again = attach_session_goal_from_handoffs(
        session,
        ("session_goal:continue", "session_goal_item:one", "session_goal_item:two"),
        condition="list three steps",
    )
    assert again is not None
    assert again.host_owned is True
    assert again.max_outer_turns == 3
    assert again.status == SessionGoalStatus.ACHIEVED
    assert again.checklist == ()


def test_achieved_with_tool_evidence_completes_condition_only_goal() -> None:
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    verdict = evaluate_session_goal(
        goal,
        _result("Patched and tested. session_goal:achieved", executed=2, success=2),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == "achieved with tool evidence"


def test_achieved_ignored_while_investigation_dispatched() -> None:
    """Starting RCA must not close a daily-work goal before deliverables exist."""
    session = SessionCore()
    goal = SessionGoal(
        condition="Sentry spike: issue id + next action",
        max_outer_turns=6,
        host_owned=True,
    )
    attach_session_goal(session, goal)
    result = TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            investigation_dispatched=True,
        ),
        assistant_response_text="Dispatching investigation. session_goal:achieved",
    )
    verdict = evaluate_session_goal(goal, result, session=session)
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "investigation" in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_turn_has_session_goal_evidence_false_when_investigation_dispatched() -> None:
    result = TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            investigation_dispatched=True,
        ),
        assistant_response_text="started",
    )
    assert turn_has_session_goal_evidence(result) is False


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
