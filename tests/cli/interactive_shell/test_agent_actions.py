"""Tests for deterministic actions in the interactive terminal assistant."""

from __future__ import annotations

import io
import subprocess

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


def test_local_llama_connect_is_not_hardcoded_as_cli_action() -> None:
    assert plan_cli_actions("please connect to local llama") == []


def test_provider_switch_plans_provider_action() -> None:
    message = "switch from the current ollama model to setting the model to anthropic"

    assert plan_terminal_tasks(message) == ["llm_provider"]
    assert plan_cli_actions(message) == []


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
    assert output.index("Requested actions") < output.index("$ /health")
    assert output.index("1.") < output.index("$ /health")
    assert output.index("2.") < output.index("$ /health")
    assert "Running requested actions" in output
    assert "ran /health" in output
    assert "ran /list integrations" in output


def test_execute_cli_actions_falls_through_for_local_llama_request(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, _ = _capture()
    handled = execute_cli_actions("please connect to local llama", session, console)

    assert handled is False
    assert dispatched == []
    assert session.history == []


def test_execute_cli_actions_switches_llm_provider(monkeypatch: object) -> None:
    switches: list[str] = []

    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert model is None
        switches.append(provider)
        console.print(f"switched to {provider}")
        return True

    monkeypatch.setattr(agent_actions, "switch_llm_provider", _fake_switch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled is True
    assert switches == ["anthropic"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "switch from the current ollama model to setting the model to anthropic",
            "ok": True,
        },
        {"type": "slash", "text": "/model set anthropic", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ /model set anthropic" in output
    assert "switched to anthropic" in output


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


def test_explicit_shell_command_plans_shell_action() -> None:
    assert plan_terminal_tasks("run `pwd`") == ["shell"]
    assert plan_terminal_tasks("run the command `pwd`") == ["shell"]
    assert plan_cli_actions("run `pwd`") == []


def test_direct_shell_command_plans_shell_action() -> None:
    assert plan_terminal_tasks("pwd") == ["shell"]


def test_sample_alert_launch_plans_sample_alert_action() -> None:
    assert plan_terminal_tasks("okay launch a simple alert") == ["sample_alert"]
    assert plan_cli_actions("okay launch a simple alert") == []


def test_compound_services_and_synthetic_rds_plans_all_actions() -> None:
    message = (
        "show me which services are connected and after that run a synthetic test RDS database"
    )

    assert plan_terminal_tasks(message) == ["slash", "synthetic_test"]
    assert plan_cli_actions(message) == ["/list integrations"]


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


def test_execute_cli_actions_runs_sample_alert(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_sample_alert_for_session(
        *,
        template_name: str = "generic",
        context_overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append(template_name)
        assert context_overrides is None
        return {
            "root_cause": "sample failure",
            "problem_md": "sample",
            "is_noise": False,
        }

    import app.cli.investigation as investigation_module

    monkeypatch.setattr(
        investigation_module,
        "run_sample_alert_for_session",
        _fake_run_sample_alert_for_session,
    )

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("okay launch a simple alert", session, console) is True
    assert calls == ["generic"]
    assert session.last_state == {
        "root_cause": "sample failure",
        "problem_md": "sample",
        "is_noise": False,
    }
    assert session.history[-1] == {"type": "alert", "text": "sample:generic", "ok": True}
    output = buf.getvalue()
    assert "sample alert" in output
    assert "generic" in output


def test_execute_cli_actions_lists_all_actions_before_synthetic_rds(monkeypatch: object) -> None:
    dispatched: list[str] = []
    synthetic_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_dispatch(command: str, _session: ReplSession, console: Console) -> bool:
        dispatched.append(command)
        console.print(f"ran {command}")
        return True

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        synthetic_calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
        )

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(agent_actions.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        "show me which services are connected and after that run a synthetic test RDS database",
        session,
        console,
    )

    assert handled is True
    assert dispatched == ["/list integrations"]
    assert synthetic_calls == [
        (
            [agent_actions.sys.executable, "-m", "app.cli", "tests", "synthetic"],
            {
                "timeout": agent_actions._SYNTHETIC_TEST_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": (
                "show me which services are connected and after that run a synthetic test "
                "RDS database"
            ),
            "ok": True,
        },
        {"type": "slash", "text": "/list integrations", "ok": True},
        {"type": "synthetic_test", "text": "rds_postgres", "ok": True},
    ]
    output = buf.getvalue()
    assert output.index("1.") < output.index("$ /list integrations")
    assert output.index("2.") < output.index("$ /list integrations")
    assert output.index("synthetic test") < output.index("$ opensre tests synthetic")
    assert output.index("$ /list integrations") < output.index("$ opensre tests synthetic")


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


def test_execute_cli_actions_runs_shell_command(monkeypatch: object) -> None:
    completed = subprocess.CompletedProcess(
        args="pwd",
        returncode=0,
        stdout="/tmp/project\n",
        stderr="",
    )
    calls: list[str] = []

    def _fake_run(command: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed

    monkeypatch.setattr(agent_actions.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `pwd`", session, console) is True
    assert calls == ["pwd"]
    assert session.history == [
        {"type": "cli_agent", "text": "run `pwd`", "ok": True},
        {"type": "shell", "text": "pwd", "ok": True},
    ]
    output = buf.getvalue()
    assert "Running requested actions" in output
    assert "$ pwd" in output
    assert "/tmp/project" in output


def test_execute_cli_actions_records_shell_failure(monkeypatch: object) -> None:
    completed = subprocess.CompletedProcess(
        args="false",
        returncode=2,
        stdout="",
        stderr="nope\n",
    )

    def _fake_run(command: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr(agent_actions.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("execute false", session, console) is True
    assert session.history[-1] == {"type": "shell", "text": "false", "ok": False}
    output = buf.getvalue()
    assert "nope" in output
    assert "exit code" in output
