"""Tests for update_plan host policy."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, parse_task_plan
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_host_policy
from core.agent_harness.tools.tool_context import ActionToolScope
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import execute_update_plan_tool


def _ask_user_turn_text() -> str:
    return format_ask_user_answers(
        (
            AskUserQuestion(label="Onset", title="When did it start?", options=("Gradual",)),
            AskUserQuestion(label="Signal", title="Strongest signal?", options=("CPU",)),
        ),
        ("Gradual", "CPU"),
    )


_PLAN: list[dict[str, Any]] = [
    {"step": "Pinpoint onset on p99", "status": "pending"},
    {"step": "Confirm checkout returns 2xx", "status": "pending"},
]


def test_ask_user_turn_strips_plan_only_by_default() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_ask_user_turn_honors_armed_plan_only_latch() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True
    # Set-only: the policy does not consume the latch — only the gate lifts it.
    assert session.plan_only_until_authorized is True


def test_ask_user_turn_model_cannot_drop_user_plan_only() -> None:
    """A plan-only Ask User hand-off stays gated even if the model sends false."""
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_ask_user_turn_existing_latch_survives_model_false() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_update_plan_tool_end_to_end_after_ask_user() -> None:
    session = Session()
    ctx = ActionToolScope(
        session=session,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        turn_user_message=_ask_user_turn_text(),
    )
    result = execute_update_plan_tool({"plan": _PLAN, "plan_only": True}, ctx)
    assert result["ok"] is True
    assert session.plan_only_until_authorized is False
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_normal_turn_honors_requested_plan_only() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message="plan this, do not run yet",
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_normal_turn_promotes_gap_after_completed_step() -> None:
    session = Session()
    plan, _error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm telemetry source", "status": "completed"},
                {"step": "Query latency", "status": "pending"},
                {"step": "Verify baseline", "status": "pending"},
            ]
        }
    )
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message="run the plan",
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert normalized.current_index == 2


def test_normal_turn_promotes_all_pending_when_execution_authorized() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message="make a plan and run it",
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_apply_update_plan_session_is_set_only_for_the_latch() -> None:
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    apply_update_plan_session(session, plan, plan_only=False)
    assert session.task_plan is plan
    assert session.plan_only_until_authorized is True


def test_apply_update_plan_session_refreshes_the_live_prompt() -> None:
    """Pinned overlay must repaint as soon as the plan is stored, not later."""
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    session = Session()
    refreshes = {"count": 0}
    session.terminal.prompt_refresh_fn = lambda: refreshes.__setitem__(
        "count", refreshes["count"] + 1
    )
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    apply_update_plan_session(session, plan, plan_only=True)
    assert session.task_plan is plan
    assert refreshes["count"] == 1
