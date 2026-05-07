"""Unit tests for the action planner facade."""

from __future__ import annotations

from app.cli.interactive_shell.action_planner import (
    plan_actions_with_unhandled,
    plan_cli_actions,
    plan_terminal_tasks,
)


def test_plan_cli_actions_health_and_list() -> None:
    msg = "check opensre health and show connected services"
    assert plan_cli_actions(msg) == ["/health", "/list integrations"]


def test_plan_actions_with_unhandled_all_handled() -> None:
    msg = "check opensre health and show connected services"
    actions, unhandled = plan_actions_with_unhandled(msg)
    assert not unhandled
    assert [a.kind for a in actions] == ["slash", "slash"]


def test_plan_terminal_tasks_returns_kinds() -> None:
    msg = "check opensre health and show connected services"
    assert plan_terminal_tasks(msg) == ["slash", "slash"]
