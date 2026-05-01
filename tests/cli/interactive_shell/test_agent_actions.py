"""Tests for deterministic actions in the interactive terminal assistant."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell import agent_actions
from app.cli.interactive_shell.agent_actions import (
    execute_cli_actions,
    plan_cli_actions,
    plan_terminal_tasks,
)
from app.cli.interactive_shell.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_health_then_connected_services_plans_two_actions_in_order() -> None:
    message = "check the health of my opensre and then show me all connected services"

    assert plan_cli_actions(message) == ["/health", "/list integrations"]


def test_integration_prompt_plans_datadog_lookup_only() -> None:
    message = (
        "tell me about what the discord integration can do and then tell me what "
        "datadog services I have connections to"
    )

    assert plan_cli_actions(message) == ["/integrations show datadog"]


def test_execute_cli_actions_dispatches_planned_commands(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        "check the health of my opensre and then show me all connected services",
        session,
        console,
    )

    assert handled is True
    assert dispatched == ["/health", "/list integrations"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "check the health of my opensre and then show me all connected services",
            "ok": True,
        },
        {"type": "slash", "text": "/health", "ok": True},
        {"type": "slash", "text": "/list integrations", "ok": True},
    ]
    output = buf.getvalue()
    assert "Running requested actions" in output
    assert "ran /health" in output
    assert "ran /list integrations" in output


def test_execute_cli_actions_answers_discord_then_dispatches_datadog(
    monkeypatch: object,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        (
            "tell me about what the discord integration can do and then tell me what "
            "datadog services I have connections to"
        ),
        session,
        console,
    )

    assert handled is False
    assert dispatched == ["/integrations show datadog"]
    output = buf.getvalue()
    assert "Discord integration" not in output
    assert "ran /integrations show datadog" in output


def test_compound_prompt_plans_chat_list_and_blocked_deploy() -> None:
    message = (
        "tell me how you are doing AND show me all the services we are connected to "
        "AND then deploy OpenSRE to EC2"
    )

    assert plan_terminal_tasks(message) == ["slash"]
    assert plan_cli_actions(message) == ["/list integrations"]


def test_services_version_deploy_prompt_plans_all_actions() -> None:
    message = (
        "tell me which services are connected AND then tell me the current CLI version "
        "AND then deploy to EC2 within 90 seconds"
    )

    assert plan_terminal_tasks(message) == ["slash", "slash"]
    assert plan_cli_actions(message) == ["/list integrations", "/version"]


def test_compound_prompt_executes_all_supported_tasks(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        (
            "tell me how you are doing AND show me all the services we are connected to "
            "AND then deploy OpenSRE to EC2"
        ),
        session,
        console,
    )

    assert handled is False
    assert dispatched == ["/list integrations"]
    output = buf.getvalue()
    assert "I'm doing fine" not in output
    assert "EC2 deployment creates AWS" not in output
    assert "ran /list integrations" in output


def test_services_version_deploy_prompt_executes_in_order(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        (
            "tell me which services are connected AND then tell me the current CLI version "
            "AND then deploy to EC2 within 90 seconds"
        ),
        session,
        console,
    )

    assert handled is False
    assert dispatched == ["/list integrations", "/version"]
    output = buf.getvalue()
    assert output.index("ran /list integrations") < output.index("ran /version")
    assert "EC2 deployment creates AWS" not in output


def test_partial_match_reports_unhandled_clause(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()

    assert not execute_cli_actions("show me connected services and sing a song", session, console)
    assert dispatched == ["/list integrations"]
    assert "don't have a safe built-in action" not in buf.getvalue()


def test_execute_cli_actions_falls_through_for_chat() -> None:
    session = ReplSession()
    console, _ = _capture()

    assert execute_cli_actions("hey", session, console) is False
    assert session.history == []
