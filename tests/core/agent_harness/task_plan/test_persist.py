"""TaskPlan survives flush → restore so compacted transcripts keep the checklist."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.task_plan.persist import (
    TASK_PLAN_STATE_CUSTOM_TYPE,
    apply_task_plan_state,
    task_plan_state_snapshot,
)
from core.agent_harness.task_plan.plan import parse_task_plan


def _plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Run /health and read the result", "status": "completed"},
                {"step": "List connected integrations", "status": "in_progress"},
                {"step": "Confirm both outputs answered the ask", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_flush_persists_task_plan_and_restore_context_applies_it() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    session.task_plan = _plan()

    storage.flush(session)
    records = storage.read(session.session_id)
    content = next(
        rec["content"] for rec in records if rec.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    )

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "task_plan_state": content,
            "history": [],
        },
    )
    assert restored.task_plan is not None
    assert restored.task_plan.current_index == 2
    assert restored.task_plan.steps[-1].step.startswith("Confirm")


def test_clearing_the_plan_writes_a_tombstone() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    session.task_plan = _plan()
    storage.flush(session)

    session.task_plan = None
    storage.flush(session)

    snapshots = [
        record["content"]
        for record in storage.read(session.session_id)
        if record.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    ]
    assert len(snapshots) >= 2
    restored = SessionCore(store=InMemorySessionStore())
    apply_task_plan_state(restored, snapshots[-1])
    assert restored.task_plan is None
    assert task_plan_state_snapshot(restored) is None
