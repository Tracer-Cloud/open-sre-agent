"""Tests for the agent update_plan tool."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.tools.tool_context import ActionToolScope
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import (
    execute_update_plan_tool,
    update_plan_tool,
)


def _ctx(session: Session | None = None) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolScope(
        session=session if session is not None else Session(),
        console=console,
    )


_PLAN: list[dict[str, Any]] = [
    {"step": "Capture 502 samples from checkout", "status": "completed"},
    {"step": "Trace 502s to the last deploy", "status": "in_progress"},
    {"step": "Confirm checkout returns 2xx", "status": "pending"},
]


def test_update_plan_tool_is_action_surface_read_only() -> None:
    assert update_plan_tool.name == "update_plan"
    assert "action" in update_plan_tool.surfaces
    assert update_plan_tool.side_effect_level == "read_only"
    assert update_plan_tool.parallel_safe is False


def test_update_plan_stores_the_checklist_on_the_session() -> None:
    session = Session()
    result = execute_update_plan_tool({"plan": _PLAN}, _ctx(session=session))

    assert result["ok"] is True
    assert result["current"] == 2
    assert result["total"] == 3
    assert session.task_plan is not None
    assert session.task_plan.current_index == 2
    assert "Plan · 2/3" in result["summary"]
    assert "(verify)" in result["summary"]


def test_update_plan_rejects_two_in_progress_steps() -> None:
    result = execute_update_plan_tool(
        {
            "plan": [
                {"step": "First", "status": "in_progress"},
                {"step": "Second", "status": "in_progress"},
            ]
        },
        _ctx(),
    )
    assert result["ok"] is False
    assert "at most one" in result["error"]
