"""Flush / restore the live :class:`TaskPlan` so resume keeps the checklist."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.agent_harness.task_plan.plan import (
    TaskPlan,
    task_plan_from_payload,
    task_plan_to_payload,
)

TASK_PLAN_STATE_CUSTOM_TYPE = "task_plan_state"


def task_plan_state_snapshot(session: Any) -> dict[str, Any] | None:
    """Flush payload for the session's live task plan, or ``None`` when empty."""
    plan = getattr(session, "task_plan", None)
    if not isinstance(plan, TaskPlan) or not plan.steps:
        return None
    return task_plan_to_payload(plan)


def _last_task_plan_content(
    prior_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for record in reversed(prior_records):
        if record.get("type") != "custom_message":
            continue
        if record.get("custom_type") != TASK_PLAN_STATE_CUSTOM_TYPE:
            continue
        content = record.get("content")
        return content if isinstance(content, dict) else None
    return None


def should_persist_task_plan_state(
    snapshot: dict[str, Any] | None,
    *,
    prior_records: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether flush should append ``snapshot`` as a ``task_plan_state`` record.

    Skip identical tips. A ``None`` snapshot is a tombstone only when the
    transcript already stored a plan — otherwise skip so sessions that never
    planned stay quiet.
    """
    last = _last_task_plan_content(prior_records)
    if snapshot is None:
        return last is not None
    return last != snapshot


def apply_task_plan_state(session: Any, payload: Any) -> None:
    """Rehydrate ``session.task_plan`` from a flush snapshot or tombstone."""
    if not hasattr(session, "task_plan"):
        return
    if payload is None or payload == {}:
        session.task_plan = None
        return
    session.task_plan = task_plan_from_payload(payload)


__all__ = [
    "TASK_PLAN_STATE_CUSTOM_TYPE",
    "apply_task_plan_state",
    "should_persist_task_plan_state",
    "task_plan_state_snapshot",
]
