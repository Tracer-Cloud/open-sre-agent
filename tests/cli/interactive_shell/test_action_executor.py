"""Direct unit tests for ``action_executor`` (complement to ``test_agent_actions``)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from app.cli.interactive_shell.action_executor import (
    read_diag,
    run_cd_command,
    run_pwd_command,
    run_shell_command,
    run_synthetic_test,
    terminate_child_process,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_execution import ShellExecutionResult
from app.cli.interactive_shell.shell_policy import PolicyDecision


def test_terminate_child_process_noop_when_exited() -> None:
    proc = MagicMock()
    proc.poll.return_value = 0
    terminate_child_process(proc)
    proc.terminate.assert_not_called()


def test_read_diag_respects_byte_cap() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"z" * 5_000)
        text = read_diag(buf)
    assert len(text) == 2_000


def test_run_pwd_command_prints_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/shown/pwd")

    monkeypatch.setattr(Path, "cwd", classmethod(_fake_cwd))

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_pwd_command("pwd", session, console)
    assert "/shown/pwd" in buf.getvalue()
    assert session.history[-1]["type"] == "shell"


def test_run_pwd_command_rejects_multiple_tokens() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_pwd_command("pwd extra", session, console)
    assert "too many arguments" in buf.getvalue().lower()
    assert session.history[-1]["ok"] is False


def test_run_cd_command_chdirs_to_target(monkeypatch: pytest.MonkeyPatch) -> None:
    directories: list[Path] = []

    def _chdir(target: Path) -> None:
        directories.append(target)

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.os.chdir", _chdir)

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_cd_command("cd /tmp/example", session, console)
    assert directories == [Path("/tmp/example")]
    assert session.history[-1]["type"] == "shell"


def test_run_shell_command_records_when_policy_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.cli.interactive_shell.execution_policy.evaluate_policy",
        lambda **_: PolicyDecision(
            allow=False,
            classification="mutating",
            reason="test block",
            hint="use ! for passthrough",
        ),
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command(
        "rm -rf /nope",
        session,
        console,
        confirm_fn=lambda _p: "n",
        is_tty=True,
    )

    assert "test block" in buf.getvalue()
    assert "cancelled" in buf.getvalue().lower()
    assert session.history[-1] == {"type": "shell", "text": "rm -rf /nope", "ok": False}


def test_run_shell_command_silent_success_prints_checkmark(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="true",
            argv=["true"],
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.execute_shell_command",
        _fake_execute,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("true", session, console)
    assert "✓" in buf.getvalue()
    assert session.history[-1] == {"type": "shell", "text": "true", "ok": True}


def test_run_shell_command_failure_prints_exit_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="false",
            argv=["false"],
            stdout="",
            stderr="",
            exit_code=7,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.execute_shell_command",
        _fake_execute,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("false", session, console)
    out = buf.getvalue()
    assert "✗" in out
    assert "exit 7" in out
    assert session.history[-1] == {"type": "shell", "text": "false", "ok": False}


def test_run_shell_command_reports_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_errors: list[BaseException] = []

    def _raise(**_kwargs: object) -> ShellExecutionResult:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.execute_shell_command",
        _raise,
    )
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("true", session, console)

    assert "command failed to start" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert session.history[-1] == {"type": "shell", "text": "true", "ok": False}


def test_run_synthetic_test_unknown_suite_records_failure() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_synthetic_test("nonexistent_suite", session, console)
    assert "unknown synthetic" in buf.getvalue().lower()
    entry = session.history[-1]
    assert entry["type"] == "synthetic_test"
    assert entry["ok"] is False
