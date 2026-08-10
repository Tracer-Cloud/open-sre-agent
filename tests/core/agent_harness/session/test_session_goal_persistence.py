"""SessionGoal (+ CTA state) survives flush → restore."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStorage, SessionCore, SessionManager
from core.agent_harness.session.pending_offer import PendingIntegrationSetupOffer
from core.agent_harness.session.session_goal import (
    SESSION_GOAL_STATE_CUSTOM_TYPE,
    SessionGoal,
    SessionGoalStatus,
    apply_session_goal_state,
    attach_session_goal,
    session_goal_from_payload,
    session_goal_is_active,
    session_goal_state_snapshot,
    session_goal_to_payload,
)


def test_session_goal_round_trips_through_payload() -> None:
    goal = SessionGoal(
        condition="finish checklist",
        max_outer_turns=4,
        status=SessionGoalStatus.ACTIVE,
        turns_used=2,
        step_count=3,
        checklist=("a", "b", "c"),
        completed=frozenset({0}),
    )
    restored = session_goal_from_payload(session_goal_to_payload(goal))
    assert restored == goal


def test_session_goal_state_snapshot_includes_cta_and_pending() -> None:
    session = SessionCore(storage=InMemorySessionStorage())
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=3, checklist=("one", "two")),
    )
    session.offered_upgrade_ctas.add("cta:posthog_mcp")
    session.pending_integration_setup_offer = PendingIntegrationSetupOffer(
        service_id="posthog_mcp"
    )

    snapshot = session_goal_state_snapshot(session)
    other = SessionCore(storage=InMemorySessionStorage())
    apply_session_goal_state(other, snapshot)

    assert session_goal_is_active(other)
    assert other.session_goal is not None
    assert other.session_goal.checklist == ("one", "two")
    assert other.offered_upgrade_ctas == {"cta:posthog_mcp"}
    assert isinstance(other.pending_integration_setup_offer, PendingIntegrationSetupOffer)
    assert other.pending_integration_setup_offer.service_id == "posthog_mcp"


def test_flush_persists_session_goal_state_and_restore_context_applies_it() -> None:
    storage = InMemorySessionStorage()
    session = SessionCore(storage=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    attach_session_goal(
        session,
        SessionGoal(
            condition="three steps",
            max_outer_turns=5,
            checklist=("gather", "analyze", "report"),
            completed=frozenset({0}),
            turns_used=1,
        ),
    )
    session.offered_upgrade_ctas.add("cta:posthog_mcp")

    storage.flush(session)
    records = storage.read(session.session_id)
    assert any(
        rec.get("type") == "custom_message"
        and rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        for rec in records
    )
    content = next(
        rec["content"]
        for rec in records
        if rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    )

    restored = SessionCore(storage=InMemorySessionStorage())
    SessionManager(storage=InMemorySessionStorage()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "session_goal_state": content,
            "history": [],
        },
    )
    assert session_goal_is_active(restored)
    assert restored.session_goal is not None
    assert restored.session_goal.completed == frozenset({0})
    assert restored.offered_upgrade_ctas == {"cta:posthog_mcp"}
