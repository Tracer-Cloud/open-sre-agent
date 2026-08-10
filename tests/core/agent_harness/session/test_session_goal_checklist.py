"""Checklist success criteria on SessionGoal (structured tags only)."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    apply_session_goal_progress,
    attach_session_goal,
    default_evaluate_session_goal,
    format_session_goal_checklist,
    session_goal_from_assistant_handoffs,
    session_goal_from_handoffs,
)
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.session_goal_loop import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def test_checklist_items_from_structured_handoff_tags() -> None:
    goal = session_goal_from_handoffs(
        (
            "session_goal:max_turns=5;steps=3",
            "session_goal_item:List the goal",
            "session_goal_item:Name step one",
            "session_goal_item:Confirm done",
        ),
        condition="run the checklist",
    )

    assert goal is not None
    assert goal.checklist == ("List the goal", "Name step one", "Confirm done")
    assert goal.completed == frozenset()
    assert goal.max_outer_turns == 5


def test_checklist_items_from_typed_assistant_handoff_fields() -> None:
    goal = session_goal_from_assistant_handoffs(
        (
            AssistantHandoff(
                content="run the checklist",
                session_goal="max_turns=5;steps=3",
                session_goal_items=("List the goal", "Name step one", "Confirm done"),
            ),
        ),
        condition="run the checklist",
    )

    assert goal is not None
    assert goal.checklist == ("List the goal", "Name step one", "Confirm done")
    assert goal.max_outer_turns == 5
    assert goal.step_count == 3


def test_done_tags_mark_checklist_items_and_achieve_when_complete() -> None:
    goal = SessionGoal(
        condition="checklist",
        max_outer_turns=5,
        checklist=("A", "B", "C"),
    )
    after_one = apply_session_goal_progress(goal, "Did A. session_goal:done=0")
    assert after_one.completed == frozenset({0})
    assert (
        default_evaluate_session_goal(
            after_one,
            type("R", (), {"assistant_response_text": "session_goal:done=0"})(),
        )
        == SessionGoalStatus.ACTIVE
    )

    after_all = apply_session_goal_progress(
        after_one,
        "Finished. session_goal:done=1,2",
    )
    assert after_all.completed == frozenset({0, 1, 2})
    assert (
        default_evaluate_session_goal(
            after_all,
            type("R", (), {"assistant_response_text": "session_goal:done=1,2"})(),
        )
        == SessionGoalStatus.ACHIEVED
    )


def test_format_session_goal_checklist_shows_progress() -> None:
    goal = SessionGoal(
        condition="x",
        checklist=("One", "Two", "Three"),
        completed=frozenset({0}),
    )
    rendered = format_session_goal_checklist(goal)

    assert "One" in rendered and "Two" in rendered
    assert "[x]" in rendered or "✓" in rendered or "[done]" in rendered.lower()
    assert "[ ]" in rendered or "pending" in rendered.lower()


def test_outer_loop_achieves_via_checklist_without_achieved_tag() -> None:
    session = SessionCore()
    turns: list[str] = []
    goal = SessionGoal(
        condition="three checks",
        max_outer_turns=5,
        checklist=("A", "B", "C"),
    )
    attach_session_goal(session, goal)

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        n = len(turns)
        # Mark one new item per turn via structured done tags.
        body = f"Working. session_goal:done={n - 1}"
        return TurnResult(
            final_intent="cli_agent_fallback",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=body,
            llm_run=None,
        )

    outcome = run_until_session_goal(_chat, session, "go", goal=goal)

    assert len(turns) == 3
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.goal.completed == frozenset({0, 1, 2})


def test_nudge_lists_unfinished_checklist_items() -> None:
    from core.agent_harness.session.session_goal import continuation_nudge

    goal = SessionGoal(
        condition="x",
        checklist=("A", "B", "C"),
        completed=frozenset({0}),
    )
    nudge = continuation_nudge(goal)

    assert "B" in nudge and "C" in nudge
    assert "session_goal:done=" in nudge


def test_strip_session_goal_progress_tags_hides_harness_tokens() -> None:
    from core.agent_harness.session.session_goal import strip_session_goal_progress_tags

    raw = "Finished step two.\nsession_goal:done=1\nMore prose. session_goal:achieved"
    cleaned = strip_session_goal_progress_tags(raw)

    assert "session_goal:" not in cleaned
    assert "Finished step two." in cleaned
    assert "More prose." in cleaned
