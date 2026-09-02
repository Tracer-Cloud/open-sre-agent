"""Strict / independent SessionGoal evaluation."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.confirm import build_session_goal_llm_evaluator
from core.agent_harness.session_goal.evaluate import (
    evaluate_session_goal,
    turn_has_session_goal_evidence,
)
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMResponse


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
    """Host waiting copy must never count as a ``session_goal:achieved`` claim."""
    from core.agent_harness.session_goal.evaluate import (
        reply_claims_session_goal_achieved,
    )
    from core.agent_harness.session_goal.goal import SessionGoalReason

    assert reply_claims_session_goal_achieved("session_goal:achieved") is True
    assert reply_claims_session_goal_achieved("All done.\nsession_goal:achieved") is True
    assert reply_claims_session_goal_achieved("All done. session_goal:achieved") is True
    # Current host reasons (no tag grammar).
    assert reply_claims_session_goal_achieved(SessionGoalReason.WAITING_HOST_SIGNAL) is False
    assert (
        reply_claims_session_goal_achieved(
            f"◎ /goal active\n  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}"
        )
        is False
    )
    # Legacy progress reasons that embedded the tag literal.
    assert reply_claims_session_goal_achieved("waiting for session_goal:achieved") is False
    assert (
        reply_claims_session_goal_achieved(
            "◎ /goal active\n  reason: waiting for session_goal:achieved"
        )
        is False
    )


def test_slash_capture_waiting_reason_does_not_achieve_host_goal() -> None:
    """Regression: /goal set turn captured status text and falsely achieved."""
    from core.agent_harness.session_goal.progress import (
        SESSION_GOAL_PROGRESS_MARK,
        SESSION_GOAL_USER_WORD,
    )

    session = SessionCore()
    goal = SessionGoal(
        condition="How many Windows users?",
        max_outer_turns=4,
        host_owned=True,
    )
    attach_session_goal(session, goal)
    progress_text = (
        f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD} active · 0s · turn 0/4 · +0 tokens\n"
        "  condition: How many Windows users?\n"
        f"  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}"
    )
    verdict = evaluate_session_goal(
        goal,
        _result(progress_text, executed=1, success=1),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.WAITING_HOST_SIGNAL
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_goal_set_attach_turn_does_not_consume_outer_budget() -> None:
    """``/goal set`` attach + autosubmit: first work turn is the next chat."""
    from core.agent_harness.session_goal.goal import SessionGoalReason

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
                f"◎ /goal active\n  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}",
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


def test_host_owned_tool_answer_without_achieved_tag_completes_same_turn() -> None:
    """Live metric + tools must not force a second identical turn.

    Models often omit ``session_goal:achieved`` (and the shell scrubs it from
    the visible reply). Host-owned goals previously stayed ACTIVE → continuation
    nudge → duplicate analytics count.
    """
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="How many Windows users in the last 7 days?",
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _result(
            "I found: 272 Windows users.\n\n| Window | Users |\n| Last 7 days | 272 |",
            executed=2,
            success=2,
        )

    outcome = run_until_session_goal(_chat, session, "How many Windows users in the last 7 days?")
    assert len(turns) == 1
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.goal.turns_used == 1
    assert outcome.goal.last_reason == SessionGoalReason.ACHIEVED_TOOL_EVIDENCE


def test_host_owned_fallback_route_without_tool_evidence_stays_active() -> None:
    """Route identity is not evidence — unsupported fallbacks must not close.

    ``cli_agent_fallback`` alone previously closed host-owned goals even when
    no action/gather tool succeeded (draft-only / failed-query answers).
    """
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="How many Windows users in the last 7 days?",
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        TurnResult(
            final_intent="cli_agent_fallback",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=False,
            ),
            assistant_response_text=(
                "I could not get a live count. Here is draft HogQL and a setup CTA."
            ),
        ),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.WAITING_HOST_SIGNAL


def test_host_owned_without_tools_or_achieved_tag_stays_active() -> None:
    session = SessionCore()
    goal = SessionGoal(
        condition="list three steps",
        max_outer_turns=3,
        host_owned=True,
    )
    attach_session_goal(session, goal)

    verdict = evaluate_session_goal(
        goal,
        _result("Still thinking."),
        session=session,
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.WAITING_HOST_SIGNAL


def test_host_owned_signup_unverified_refuse_achieves_without_gather() -> None:
    """Honest signup-unverified refuse must close the host goal."""
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition=(
                "What is D7 retention for users who signed up on Windows in the last 30 days?"
            ),
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        _result(
            "signup event unverified — cannot provide a retention percentage. "
            "Draft HogQL with a <signup_event> placeholder.",
        ),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.ACHIEVED_HOST_SET


def test_host_owned_signup_refuse_after_gather_is_host_set_not_tool_evidence() -> None:
    """Live probes + unavailable retention must not use tool-evidence.

    Tool-evidence achieves go through optional LLM confirm, which treats a
    refuse+draft as "retention % not reached" and leaves the goal active.
    """
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition=(
                "What is D7 retention for users who signed up on Windows "
                "in the last 30 days? Prefer live PostHog; otherwise draft HogQL."
            ),
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    reply = (
        "Live PostHog returned no signup-like events in the last 30 days, so "
        "D7 Windows retention cannot be calculated reliably. "
        "D7 retention Unavailable — no percentage inferred. "
        "Draft HogQL once <SIGNUP_EVENT> is confirmed."
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        _result(reply),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.ACHIEVED_HOST_SET


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


def test_achieved_with_tool_evidence_completes_condition_only_goal() -> None:
    from core.agent_harness.session_goal.goal import SessionGoalReason

    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    verdict = evaluate_session_goal(
        goal,
        _result("Patched and tested. session_goal:achieved", executed=2, success=2),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.ACHIEVED_TOOL_EVIDENCE


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


def test_achieved_with_tool_evidence_completes_short_checklist() -> None:
    from core.agent_harness.session_goal.goal import SessionGoalReason

    goal = SessionGoal(
        condition="how many windows users?",
        checklist=("Query PostHog", "Report the number"),
    )
    verdict = evaluate_session_goal(
        goal,
        _result("262 Windows users. session_goal:achieved", executed=1, success=1),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.CHECKLIST_COMPLETE_SAME_TURN


def test_same_turn_tool_answer_completes_short_checklist_without_done_tags() -> None:
    """metric_read often answers query+report in one turn and forgets done= tags."""
    from core.agent_harness.session_goal.goal import SessionGoalReason

    session = SessionCore()
    goal = SessionGoal(
        condition="what windows users number did open opensre during last 7 days?",
        checklist=(
            "Query PostHog for distinct Windows users",
            "Report the number to the user",
        ),
    )
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result(
            "I found: Windows had 262 distinct users.\n\nHere's what that looks like:",
            executed=2,
            success=2,
        ),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.CHECKLIST_COMPLETE_SAME_TURN
    assert session.session_goal is not None
    assert session.session_goal.checklist_complete


def test_same_turn_partial_done_tag_still_completes_short_checklist() -> None:
    """Partial ``done=`` must not force a redundant outer turn.

    Live Windows-users: model marks query ``done=0``, reports the count in
    prose, forgets ``done=1`` / ``achieved`` — host must still finish.
    """
    from core.agent_harness.session_goal.goal import SessionGoalReason

    session = SessionCore()
    goal = SessionGoal(
        condition="what windows users number did open opensre during last 7 days?",
        checklist=(
            "Query PostHog for distinct Windows users who opened OpenSRE in last 7 days",
            "Report the count to the user",
        ),
    )
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result(
            "I found: 17 distinct users invoked the OpenSRE CLI from Windows "
            "in the last 7 days.\n\nsession_goal:done=0",
            executed=4,
            success=4,
        ),
        session=session,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.CHECKLIST_COMPLETE_SAME_TURN
    assert session.session_goal is not None
    assert session.session_goal.checklist_complete


def test_same_turn_tool_answer_does_not_false_complete_long_checklist() -> None:
    goal = SessionGoal(
        condition="five step walkthrough",
        checklist=("A", "B", "C"),
    )
    verdict = evaluate_session_goal(
        goal,
        _result("Started with A.", executed=1, success=1),
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "checklist 0/3" in verdict.reason


def test_checklist_complete_achieves_without_achieved_tag() -> None:
    from core.agent_harness.session_goal.goal import SessionGoalReason

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
    assert verdict.reason == SessionGoalReason.CHECKLIST_COMPLETE


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
            return AgentLLMResponse(content='{"verdict": "NOT_REACHED"}')

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
    assert session.session_goal.status == SessionGoalStatus.ACTIVE
    assert "not reached" in session.session_goal.last_reason.lower()


def test_llm_reject_survives_outer_loop_session_reread() -> None:
    """Reviewer ACTIVE must win over structured ACHIEVED left on the session."""

    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content='{"verdict": "NOT_REACHED"}')

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    session = SessionCore()
    progress_updates: list[str] = []

    def _chat(message: str) -> TurnResult:
        _ = message
        return _result("Patched. session_goal:achieved", executed=1, success=1)

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="finish migration", max_outer_turns=2),
        evaluate=build_session_goal_llm_evaluator(_LLM()),  # type: ignore[arg-type]
        on_progress=lambda g: progress_updates.append(g.status),
    )

    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert SessionGoalStatus.ACHIEVED not in progress_updates
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.BUDGET_EXHAUSTED


def test_budget_exhaustion_reports_progress_once() -> None:
    session = SessionCore()
    progress_updates: list[str] = []

    def _chat(message: str) -> TurnResult:
        _ = message
        return _result("still working")

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="never done", max_outer_turns=1, host_owned=True),
        on_progress=lambda g: progress_updates.append(f"{g.status}:{g.last_reason}"),
    )

    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    budget_updates = [
        p for p in progress_updates if p.startswith(SessionGoalStatus.BUDGET_EXHAUSTED)
    ]
    assert len(budget_updates) == 1


def test_llm_evaluator_confirms_soft_achieve() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content='{"verdict": "GOAL_REACHED"}')

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    evaluate = build_session_goal_llm_evaluator(_LLM())  # type: ignore[arg-type]
    status = evaluate(
        SessionGoal(condition="finish migration", max_outer_turns=3),
        _result("session_goal:achieved", executed=1, success=1),
    )
    assert status == SessionGoalStatus.ACHIEVED


def test_llm_evaluator_fails_closed_on_free_text_verdict() -> None:
    """Prose ``GOAL_REACHED`` without schema JSON must not false-complete."""

    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content="GOAL_REACHED — looks done to me")

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)
    status = build_session_goal_llm_evaluator(_LLM())(  # type: ignore[arg-type]
        goal,
        _result("session_goal:achieved", executed=1, success=1),
        session=session,
    )
    assert status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_achieved_claim_does_not_false_complete_a_long_checklist() -> None:
    """A claim plus one tool must not close a multi-turn walkthrough.

    The same-turn shortcut exists for query+report metric asks. On a longer
    checklist the model must keep explicit ``done=`` tracking, or a first turn
    that touched one item marks every remaining item done.
    """
    # Arrange — five steps, one successful tool, reply claims the goal is met
    # while saying it only started step A.
    goal = SessionGoal(condition="five step walkthrough", checklist=("A", "B", "C", "D", "E"))

    # Act.
    verdict = evaluate_session_goal(
        goal,
        _result("Started with A. session_goal:achieved", executed=1, success=1),
    )

    # Assert — the claim is ignored, and B..E are still outstanding.
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "checklist 0/5" in verdict.reason


def test_pending_user_choice_outranks_an_achieved_claim_with_evidence() -> None:
    """A queued choice gates the goal before any other rule is consulted.

    The strongest possible achieve signal (claim + successful tool + a complete
    checklist) must still lose to an unanswered question, or the session closes
    a goal whose next step is waiting on the user.
    """
    from core.agent_harness.session.pending_choice import PendingUserChoice

    # Arrange
    session = SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?", options=("staging", "production")
    )
    goal = SessionGoal(
        condition="restart the service",
        checklist=("Pick the environment",),
        completed=frozenset({0}),
    )
    attach_session_goal(session, goal)

    # Act
    verdict = evaluate_session_goal(
        goal,
        _result("Restarted. session_goal:achieved", executed=1, success=1),
        session=session,
    )

    # Assert
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.WAITING_USER_CHOICE


def test_prior_turn_progress_blocks_the_untagged_same_turn_completion() -> None:
    """The untagged shortcut is for a goal answered in one turn, not a resumed one.

    Without a claim, a short checklist may auto-complete only when nothing was
    done before this turn. A goal already carrying progress is mid-walkthrough,
    so a turn that merely ran a tool must not close the remaining item.
    """
    # Arrange: same shape as the untagged same-turn case that does achieve,
    # differing only in that item 0 was completed on an earlier turn.
    session = SessionCore()
    goal = SessionGoal(
        condition="how many windows users?",
        checklist=("Query PostHog", "Report the number"),
        completed=frozenset({0}),
    )
    attach_session_goal(session, goal)

    # Act
    verdict = evaluate_session_goal(
        goal,
        _result("Still working on it.", executed=1, success=1),
        session=session,
    )

    # Assert
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "checklist 1/2" in verdict.reason


def test_evidence_is_false_when_the_success_counts_are_not_numbers() -> None:
    """A malformed count is absence of evidence, never a crash mid-turn.

    ``executed_success_count`` is read off a duck-typed result, so a stub can
    carry a non-numeric value. Failing closed keeps it from reading as proof.
    """

    class _BadCounts:
        executed_success_count = "two"

    class _BadResult:
        action_result = _BadCounts()
        assistant_response_text = "done"

    assert turn_has_session_goal_evidence(_BadResult()) is False
