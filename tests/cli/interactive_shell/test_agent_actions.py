"""Tests for deterministic actions in the interactive terminal assistant."""

from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import NoReturn
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from app.cli.interactive_shell import action_executor, agent_actions, shell_execution
from app.cli.interactive_shell import intent_parser as intent_parser_module
from app.cli.interactive_shell.agent_actions import (
    execute_cli_actions,
    execute_cli_actions_with_metrics,
    plan_cli_actions,
    plan_terminal_tasks,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskStatus


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


def test_implementation_request_plans_implementation_action() -> None:
    assert plan_terminal_tasks("please implement /history search") == ["implementation"]
    assert plan_cli_actions("please implement /history search") == []


def test_generic_synthetic_test_request_plans_synthetic_action() -> None:
    assert plan_terminal_tasks("Can you run a synthetic test?") == ["synthetic_test"]


def test_kill_synthetic_test_request_plans_cancel_action() -> None:
    message = "kill the syntehtic_test because it is runnign way too long"

    assert plan_terminal_tasks(message) == ["task_cancel"]
    assert plan_cli_actions(message) == []


def test_integration_prompt_plans_datadog_lookup_only() -> None:
    message = (
        "tell me about what the discord integration can do and then tell me what "
        "datadog services I have connections to"
    )

    assert plan_cli_actions(message) == ["/integrations show datadog"]


def test_execute_cli_actions_dispatches_planned_commands(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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
    assert "ran /health" in output
    assert "ran /list integrations" in output


def test_execute_cli_actions_falls_through_for_local_llama_request(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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


def test_execute_cli_actions_records_llm_provider_failure(monkeypatch: object) -> None:
    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert provider == "anthropic"
        assert model is None
        console.print("missing credential")
        return False

    monkeypatch.setattr(agent_actions, "switch_llm_provider", _fake_switch)  # type: ignore[attr-defined]

    session = ReplSession()
    console, _ = _capture()
    handled = execute_cli_actions(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled is True
    assert session.history[-1] == {"type": "slash", "text": "/model set anthropic", "ok": False}


def test_execute_cli_actions_runs_implementation_action(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_implementation(
        request: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> None:
        calls.append(request)
        session.record("implementation", request, ok=True)
        console.print(f"implemented {request}")

    monkeypatch.setattr(
        agent_actions,
        "run_claude_code_implementation",
        _fake_run_implementation,
    )

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions("please implement /history search", session, console)

    assert handled is True
    assert calls == ["/history search"]
    assert session.history == [
        {"type": "cli_agent", "text": "please implement /history search", "ok": True},
        {"type": "implementation", "text": "/history search", "ok": True},
    ]
    output = buf.getvalue()
    assert "implementation" in output
    assert "implemented /history search" in output


def test_execute_cli_actions_answers_discord_then_dispatches_datadog(
    monkeypatch: object,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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


def test_compound_prompt_plans_chat_list_and_cli_command() -> None:
    message = (
        "tell me how you are doing AND show me all the services we are connected to "
        "AND then run opensre deploy"
    )

    assert plan_terminal_tasks(message) == ["slash", "cli_command"]
    assert plan_cli_actions(message) == ["/list integrations", "deploy"]


def test_cli_command_requires_explicit_opensre_context() -> None:
    message = "the tool uses -- deploy as an argument separator"

    assert plan_terminal_tasks(message) == []
    assert plan_cli_actions(message) == []


def test_cli_command_preserves_flags_after_explicit_opensre_prefix() -> None:
    assert plan_cli_actions("please run opensre deploy --dry-run") == ["deploy --dry-run"]


def test_compound_prompt_plans_chat_list_and_slash_deploy_paraphrase() -> None:
    message = (
        "tell me how you are doing AND show me all the services we are connected to "
        "AND then deploy OpenSRE to EC2"
    )

    assert plan_terminal_tasks(message) == ["slash", "slash"]
    assert plan_cli_actions(message) == ["/list integrations", "/deploy"]


def test_services_version_deploy_prompt_plans_all_actions() -> None:
    message = (
        "tell me which services are connected AND then tell me the current CLI version "
        "AND then deploy to EC2 within 90 seconds"
    )

    assert plan_terminal_tasks(message) == ["slash", "slash", "slash"]
    assert plan_cli_actions(message) == ["/list integrations", "/version", "/deploy"]


def test_explicit_shell_command_plans_shell_action() -> None:
    assert plan_terminal_tasks("run `whoami`") == ["shell"]
    assert plan_terminal_tasks("run the command `whoami`") == ["shell"]
    assert plan_cli_actions("run `whoami`") == []


def test_direct_shell_command_plans_shell_action() -> None:
    assert plan_terminal_tasks("whoami") == ["shell"]


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

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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
    assert dispatched == ["/list integrations", "/deploy"]
    output = buf.getvalue()
    assert "I'm doing fine" not in output
    assert "EC2 deployment creates AWS" not in output
    assert "ran /list integrations" in output


def test_services_version_deploy_prompt_executes_in_order(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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

    assert handled is True
    assert dispatched == ["/list integrations", "/version", "/deploy"]
    output = buf.getvalue()
    assert output.index("ran /list integrations") < output.index("ran /version")
    assert "EC2 deployment creates AWS" not in output


def test_execute_cli_actions_runs_sample_alert(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_sample_alert_for_session(
        *,
        template_name: str = "generic",
        context_overrides: dict[str, object] | None = None,
        cancel_requested: object | None = None,
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
    inv_tasks = [
        t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
    ]
    assert len(inv_tasks) == 1
    assert inv_tasks[0].status == TaskStatus.COMPLETED
    assert inv_tasks[0].result == "sample failure"
    output = buf.getvalue()
    assert "sample alert" in output
    assert "generic" in output


def test_execute_cli_actions_sample_alert_opensre_error_marks_task_failed(
    monkeypatch: object,
) -> None:
    from app.cli.support.errors import OpenSREError

    def _raise(
        *,
        template_name: str = "generic",
        context_overrides: dict[str, object] | None = None,
        cancel_requested: object | None = None,
    ) -> dict[str, object]:
        raise OpenSREError("sample pipeline blocked")

    import app.cli.investigation as investigation_module

    monkeypatch.setattr(investigation_module, "run_sample_alert_for_session", _raise)

    session = ReplSession()
    console, _ = _capture()
    assert execute_cli_actions("okay launch a simple alert", session, console) is True
    inv_tasks = [
        t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
    ]
    assert len(inv_tasks) == 1
    assert inv_tasks[0].status == TaskStatus.FAILED
    assert inv_tasks[0].error == "sample pipeline blocked"


def test_execute_cli_actions_lists_all_actions_before_synthetic_rds(monkeypatch: object) -> None:
    dispatched: list[str] = []
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    def _fake_popen(command: list[str], **kwargs: object) -> MagicMock:
        popen_calls.append((command, kwargs))
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(action_executor.subprocess, "Popen", _fake_popen)

    session = ReplSession()
    console, buf = _capture()
    handled = execute_cli_actions(
        "show me which services are connected and after that run a synthetic test RDS database",
        session,
        console,
    )

    assert handled is True
    assert dispatched == ["/list integrations"]
    assert len(popen_calls) == 1
    assert popen_calls[0][0] == [
        sys.executable,
        "-m",
        "app.cli",
        "tests",
        "synthetic",
    ]

    assert session.history[:2] == [
        {
            "type": "cli_agent",
            "text": (
                "show me which services are connected and after that run a synthetic test "
                "RDS database"
            ),
            "ok": True,
        },
        {"type": "slash", "text": "/list integrations", "ok": True},
    ]

    for _ in range(100):
        recent = session.task_registry.list_recent(1)
        if recent and recent[0].status != TaskStatus.RUNNING:
            break
        time.sleep(0.01)
    finished = session.task_registry.list_recent(1)[0]
    assert finished.status == TaskStatus.COMPLETED

    synthetic_entry = session.history[-1]
    assert synthetic_entry["type"] == "synthetic_test"
    assert synthetic_entry["ok"] is True
    assert "rds_postgres" in synthetic_entry["text"]
    assert "task:" in synthetic_entry["text"]

    output = buf.getvalue()
    assert output.index("1.") < output.index("$ /list integrations")
    assert output.index("2.") < output.index("$ /list integrations")
    assert output.index("synthetic test") < output.index("$ opensre tests synthetic")
    assert output.index("$ /list integrations") < output.index("$ opensre tests synthetic")


def test_execute_cli_actions_cancels_single_running_synthetic_task() -> None:
    session = ReplSession()
    session.trust_mode = True
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
    task.mark_running()
    proc = MagicMock()
    proc.poll.return_value = None
    task.attach_process(proc)

    console, buf = _capture()
    handled = execute_cli_actions(
        "kill the syntehtic_test because it is runnign way too long",
        session,
        console,
    )

    assert handled is True
    assert task.cancel_requested.is_set()
    proc.terminate.assert_called_once()
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "kill the syntehtic_test because it is runnign way too long",
            "ok": True,
        },
        {"type": "slash", "text": f"/cancel {task.task_id}", "ok": True},
    ]
    output = buf.getvalue()
    assert "cancel task" in output
    assert f"$ /cancel {task.task_id}" in output
    assert "stop requested" in output


def test_partial_match_reports_unhandled_clause(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
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
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/tmp/project")

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for pwd")

    monkeypatch.setattr(action_executor.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `pwd`", session, console) is True
    assert session.history == [
        {"type": "cli_agent", "text": "run `pwd`", "ok": True},
        {"type": "shell", "text": "pwd", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ pwd" in output
    assert "/tmp/project" in output


def test_execute_cli_actions_cd_preserves_windows_paths(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `cd C:\Users\Alice`"
    assert execute_cli_actions(message, session, console) is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r"cd C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_routes_case_insensitively(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for CD")

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `CD C:\Users\Alice`"
    assert execute_cli_actions(message, session, console) is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r"CD C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_handles_trailing_backslash_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `cd C:\`"
    assert execute_cli_actions(message, session, console) is True
    assert changed_directories == [Path("C:\\")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": "cd C:\\", "ok": True},
    ]


def test_execute_cli_actions_cd_strips_quotes_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r'run `cd "C:\Users\Alice"`'
    assert execute_cli_actions(message, session, console) is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r'cd "C:\Users\Alice"', "ok": True},
    ]


def test_execute_cli_actions_records_shell_failure(monkeypatch: object) -> None:
    completed = subprocess.CompletedProcess(
        args=["false"],
        returncode=2,
        stdout="",
        stderr="nope\n",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return completed

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("execute false", session, console) is True
    assert calls == [
        (
            ["false"],
            {
                "shell": False,
                "capture_output": True,
                "text": True,
                "timeout": action_executor.SHELL_COMMAND_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    assert session.history[-1] == {"type": "shell", "text": "false", "ok": False}
    output = buf.getvalue()
    assert "nope" in output
    assert "exit 2" in output


def test_execute_cli_actions_shell_command_times_out(monkeypatch: object) -> None:
    def _timeout(cmd: object, **kwargs: object) -> NoReturn:  # pragma: no cover
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=1,
            output="partial out\n",
            stderr="partial err\n",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _timeout)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `true`", session, console) is True
    assert session.history[-1] == {"type": "shell", "text": "true", "ok": False}
    output = buf.getvalue().lower()
    assert "timed out" in output
    assert "partial out" in output
    assert "partial err" in output


def test_execute_cli_actions_runs_passthrough_with_shell_true(monkeypatch: object) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_run(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `!echo hello`", session, console) is True
    assert calls == [
        (
            "echo hello",
            {
                "shell": True,
                "executable": shell_execution.os.environ.get("SHELL") or None,
                "capture_output": True,
                "text": True,
                "timeout": action_executor.SHELL_COMMAND_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    assert session.history[-1] == {"type": "shell", "text": "!echo hello", "ok": True}
    output = buf.getvalue()
    assert "explicit shell passthrough enabled" in output
    assert "ok" in output


def test_execute_cli_actions_routes_bang_cd_through_builtin(monkeypatch: object) -> None:
    dirs: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        dirs.append(target)

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !cd builtin routing")

    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = ReplSession()
    console, buf = _capture()

    message = "run `!cd /tmp`"
    assert execute_cli_actions(message, session, console) is True
    assert dirs == [Path("/tmp")]
    assert session.history[-1] == {"type": "shell", "text": "cd /tmp", "ok": True}
    captured = buf.getvalue()
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_routes_bang_pwd_through_builtin(monkeypatch: object) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/shown")

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !pwd builtin routing")

    monkeypatch.setattr(action_executor.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `!pwd`", session, console) is True
    assert session.history[-1] == {"type": "shell", "text": "pwd", "ok": True}
    captured = buf.getvalue()
    assert "/shown" in captured
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_declines_mutating_shell_when_user_rejects_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.cli.interactive_shell.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _p: "n",
    )
    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `rm -rf /tmp/demo`", session, console) is True
    assert session.history[-1] == {"type": "shell", "text": "rm -rf /tmp/demo", "ok": False}
    output = buf.getvalue()
    assert "cancelled" in output.lower()
    assert "mutating commands are blocked" in output.lower() or "confirm" in output.lower()


def test_execute_cli_actions_blocks_ambiguous_shell_operators() -> None:
    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions("run `ls | wc -l`", session, console) is True
    assert session.history[-1] == {"type": "shell", "text": "ls | wc -l", "ok": False}
    output = buf.getvalue()
    assert "action blocked" in output.lower()
    assert "shell operators" in output


def test_compound_prompt_plans_chat_list_and_blocked_deploy() -> None:
    message = "show versions AND show services AND opensre agent"
    planned = plan_cli_actions(message)
    assert "agent" in planned
    session = ReplSession()
    console, buf = _capture()
    result = execute_cli_actions("opensre agent", session, console)
    assert result is True
    output = buf.getvalue()
    assert "blocked" in output.lower()


def test_execute_cli_actions_handles_path_with_spaces_run_phrase() -> None:
    session = ReplSession()
    console, buf = _capture()
    result = execute_cli_actions('run cat "/tmp/file with spaces.txt"', session, console)
    assert result is True
    assert session.history[-1]["type"] == "shell"
    output = buf.getvalue()
    assert "/tmp/file with spaces.txt" in output


def test_execute_cli_actions_backtick_shell_preserves_space_path_token(monkeypatch: object) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="done\n",
            stderr="",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, _ = _capture()

    assert execute_cli_actions('run `cat "/tmp/file with spaces.txt"`', session, console) is True
    # On Windows, shlex with posix=False preserves quotes for tokens with spaces.
    expected_path = (
        '"/tmp/file with spaces.txt"'
        if intent_parser_module.IS_WINDOWS
        else "/tmp/file with spaces.txt"
    )
    assert calls[0][0] == ["cat", expected_path]


def test_execute_cli_actions_rejects_malformed_shell_input() -> None:
    session = ReplSession()
    console, buf = _capture()

    assert execute_cli_actions('run `cat "unterminated`', session, console) is True
    assert session.history[-1] == {"type": "shell", "text": 'cat "unterminated', "ok": False}
    output = buf.getvalue()
    assert "action blocked" in output.lower()
    assert "could not parse command" in output


def test_execute_cli_actions_with_metrics_counts_planned_and_executed(monkeypatch: object) -> None:
    captured_planned: list[tuple[int, bool]] = []
    captured_executed: list[tuple[int, int, int]] = []

    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_planned",
        lambda *, planned_count, has_unhandled_clause: captured_planned.append(
            (planned_count, has_unhandled_clause)
        ),
    )
    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_executed",
        lambda *, planned_count, executed_count, executed_success_count: captured_executed.append(
            (planned_count, executed_count, executed_success_count)
        ),
    )

    session = ReplSession()
    console, _ = _capture()
    result = execute_cli_actions_with_metrics("run `pwd`", session, console)

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert captured_planned == [(1, False)]
    assert captured_executed == [(1, 1, 1)]
