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


def test_plan_terminal_tasks_returns_implementation_action() -> None:
    msg = "please implement process auto-discovery"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("implementation", "process auto-discovery")]
    assert plan_terminal_tasks(msg) == ["implementation"]
    assert plan_cli_actions(msg) == []


def test_plan_task_cancel_before_shell_kill() -> None:
    msg = "kill the syntehtic_test because it is running way too long"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("task_cancel", "synthetic_test")]
    assert plan_terminal_tasks(msg) == ["task_cancel"]
    assert plan_cli_actions(msg) == []


def test_stop_process_prompt_is_not_task_cancel() -> None:
    msg = "stop the process of auto-investigation and give me a manual runbook"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert actions == []
    assert unhandled is True


def test_plan_cli_actions_remote_deployment_inventory_questions() -> None:
    messages = (
        "Which remote deployments are connected?",
        "Which remote's deployments are connected?",
        "What remote deployments are connected?",
        "show remote deployments",
        "list remote deployments",
    )

    for message in messages:
        assert plan_cli_actions(message) == ["/remote"]
