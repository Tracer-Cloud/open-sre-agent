"""SessionGoal: explicit/handoff attach, suppress Want-me-to, continue turns."""

from __future__ import annotations

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.session.pending_offer import first_pending_offer
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    session_goal_from_assistant_handoffs,
    session_goal_from_handoffs,
    session_goal_is_active,
)
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

_FIVE_STEP_ASK = (
    "Do this 5-step sequential process without asking whether to continue: "
    "(1) list the goal, (2) name step one, (3) name step two, "
    "(4) name step three, (5) confirm all five are done."
)


def test_session_goal_from_structured_handoff_not_user_prose() -> None:
    goal = session_goal_from_handoffs(
        (
            "session_goal:continue",
            "session_goal_max_turns:5",
            "session_goal_item:one",
            "session_goal_item:two",
            "session_goal_item:three",
            "session_goal_item:four",
            "session_goal_item:five",
        ),
        condition=_FIVE_STEP_ASK,
    )
    assert goal is not None
    assert goal.max_outer_turns == 5
    # Step count comes from the checklist, not from a packed ``steps=`` token.
    assert goal.step_count == 5
    assert goal.status == SessionGoalStatus.ACTIVE

    assert session_goal_from_handoffs((_FIVE_STEP_ASK,)) is None
    assert session_goal_from_handoffs(("chat:greeting",)) is None


def test_attach_session_goal_on_session_core() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="finish the checklist", max_outer_turns=3)
    attached = attach_session_goal(session, goal)
    assert session.session_goal is attached
    assert attached.condition == goal.condition
    assert attached.started_at is not None
    assert session_goal_is_active(session) is True


def test_clear_session_clears_session_goal() -> None:
    session = SessionCore()
    attach_session_goal(session, SessionGoal(condition="x", max_outer_turns=2))
    session.clear()
    assert session.session_goal is None
    assert session_goal_is_active(session) is False


def test_want_me_to_suppressed_while_session_goal_active() -> None:
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(condition="complete all five steps", max_outer_turns=5),
    )

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("Continue the multi-step process.",),
        )

    result = run_turn(
        _FIVE_STEP_ASK,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "evidence",
        answer=lambda *_a, **_k: type(
            "Run",
            (),
            {"response_text": "Step 1 done."},
        )(),
        accounting=DefaultTurnAccounting(session, _FIVE_STEP_ASK),
    )

    assert first_pending_offer(session) is None
    assert "Want me to:\n" not in (result.assistant_response_text or "")


def test_action_handoff_attaches_session_goal() -> None:
    session = SessionCore()

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("session_goal:max_turns=5;steps=5", "Start the checklist."),
        )

    run_turn(
        _FIVE_STEP_ASK,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "evidence",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Step 1."})(),
        accounting=DefaultTurnAccounting(session, _FIVE_STEP_ASK),
    )

    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.max_outer_turns == 5


def test_typed_assistant_handoff_attaches_session_goal_without_content_tags() -> None:
    """Schema fields alone must attach and drive the session-goal loop.

    Tag strings in ``handoff_contents`` are legacy; a planner that only fills
    ``session_goal`` / ``session_goal_items`` on the tool JSON must still win.
    """
    session = SessionCore()
    typed = AssistantHandoff(
        content="Start the checklist.",
        session_goal="max_turns=5;steps=5",
        session_goal_items=(
            "List the goal",
            "Name step one",
            "Name step two",
            "Name step three",
            "Confirm all five are done",
        ),
    )
    # Prefer typed decode path (same as action_driver → orchestrator).
    assert session_goal_from_assistant_handoffs((typed,)) is not None

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            # Deliberately omit session_goal tags — schema fields must suffice.
            handoff_contents=("Start the checklist.",),
            assistant_handoffs=(typed,),
        )

    run_turn(
        _FIVE_STEP_ASK,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "evidence",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Step 1."})(),
        accounting=DefaultTurnAccounting(session, _FIVE_STEP_ASK),
    )

    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.max_outer_turns == 5
    assert session.session_goal.step_count == 5
    assert len(session.session_goal.checklist) == 5


def test_five_step_outer_loop_continues_until_achieved() -> None:
    session = SessionCore()
    turns: list[str] = []
    checklist = ("list the goal", "step one", "step two", "step three", "confirm done")

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        n = len(turns)
        body = f"Completed item. session_goal:done={n - 1}"
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

    outcome = run_until_session_goal(
        _chat,
        session,
        _FIVE_STEP_ASK,
        goal=SessionGoal(
            condition="complete all five steps",
            max_outer_turns=5,
            step_count=5,
            checklist=checklist,
        ),
    )

    assert len(turns) == 5
    assert turns[0] == _FIVE_STEP_ASK
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.turn_count == 5
    assert outcome.goal.completed == frozenset({0, 1, 2, 3, 4})
    # Progress tags are stripped before the user-visible reply is returned.
    assert "session_goal:" not in (outcome.last_result.assistant_response_text or "")


def test_outer_loop_disabled_fails_five_step_probe() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=f"Completed step {len(turns)} of 5.",
            llm_run=None,
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        _FIVE_STEP_ASK,
        goal=SessionGoal(
            condition="complete all five steps",
            max_outer_turns=1,
        ),
        evaluate=lambda *_a, **_k: SessionGoalStatus.ACTIVE,
    )

    assert len(turns) == 1
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED


def test_without_goal_outer_loop_is_single_chat() -> None:
    """No explicit goal and no handoff attach → one turn, no keyword auto-detect."""
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="ok",
            llm_run=None,
        )

    outcome = run_until_session_goal(_chat, session, _FIVE_STEP_ASK)

    assert len(turns) == 1
    assert outcome.turn_count == 1
    assert outcome.goal.status == SessionGoalStatus.CLEARED


def test_metric_read_handoff_without_session_goal_flag_continues_outer_loop() -> None:
    """``evidence_kind=metric_read`` alone attaches a goal so incomplete answers continue."""
    ask = "how many Windows users in the last 7 days?"
    session = SessionCore()
    typed = AssistantHandoff.from_tool_input({"content": ask, "evidence_kind": "metric_read"})
    assert typed.session_goal is True

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=(ask,),
            assistant_handoffs=(typed,),
        )

    run_turn(
        ask,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "schema only",
        answer=lambda *_a, **_k: type(
            "Run",
            (),
            {"response_text": "Schema found; no count yet."},
        )(),
        accounting=DefaultTurnAccounting(session, ask),
    )
    assert session_goal_is_active(session)

    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if len(turns) == 1:
            body = "Still no number."
        else:
            body = "17 Windows users. session_goal:achieved"
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=body,
            llm_run=None,
        )

    # Goal already active from the handoff turn — continue until achieved.
    outcome = run_until_session_goal(_chat, session, ask)

    assert len(turns) >= 2
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert "17" in (outcome.last_result.assistant_response_text or "")


def test_paused_goal_outer_loop_is_single_chat_without_turn_bump() -> None:
    """``/goal pause`` keeps state; host must not continue or spend budget."""
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="finish later",
            max_outer_turns=5,
            status=SessionGoalStatus.PAUSED,
            turns_used=2,
            host_owned=True,
        ),
    )
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="side question answered",
            llm_run=None,
        )

    outcome = run_until_session_goal(_chat, session, "unrelated question")

    assert len(turns) == 1
    assert turns[0] == "unrelated question"
    assert outcome.goal.status == SessionGoalStatus.PAUSED
    assert outcome.goal.turns_used == 2
    assert outcome.turn_count == 2
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.PAUSED
