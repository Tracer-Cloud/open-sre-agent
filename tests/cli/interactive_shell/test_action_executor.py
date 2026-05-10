"""Direct unit tests for ``action_executor`` (complement to ``test_agent_actions``)."""

from __future__ import annotations

import errno
import io
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from app.cli.interactive_shell.action_executor import (
    read_diag,
    run_cd_command,
    run_claude_code_implementation,
    run_opensre_cli_command,
    run_pwd_command,
    run_shell_command,
    run_synthetic_test,
    start_background_cli_task,
    terminate_child_process,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_execution import ShellExecutionResult
from app.cli.interactive_shell.shell_policy import PolicyDecision
from app.cli.interactive_shell.tasks import TaskKind, TaskStatus
from app.integrations.llm_cli.base import CLIInvocation, CLIProbe


class _ImmediateThread:
    def __init__(
        self,
        group: object = None,
        target: object = None,
        name: object = None,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        *,
        daemon: object = None,
    ) -> None:
        del group, name, daemon
        if not callable(target):
            raise TypeError("target required")
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout


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


def test_run_claude_code_implementation_starts_tracked_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    stdin_seen: list[str | None] = []

    class _FakeAdapter:
        def detect(self) -> CLIProbe:
            return CLIProbe(
                installed=True,
                version="1.2.3",
                logged_in=True,
                bin_path="/usr/local/bin/claude",
                detail="ok",
            )

        def build(
            self,
            *,
            prompt: str,
            model: str | None,
            workspace: str,
            reasoning_effort: str | None = None,
        ) -> CLIInvocation:
            assert model is None
            assert workspace
            assert reasoning_effort is None
            assert "Recent OpenSRE terminal assistant context" in prompt
            assert "Process auto-discovery" in prompt
            assert "Do not create a git commit" in prompt
            return CLIInvocation(
                argv=("/usr/local/bin/claude", "-p", "--output-format", "text"),
                stdin=prompt,
                cwd=workspace,
                env={"CLAUDE_TEST": "1"},
                timeout_sec=120.0,
            )

    class _FakeProcess:
        returncode = 0

        def communicate(
            self,
            input: str | None = None,
            timeout: int | None = None,
        ) -> tuple[str, str]:
            assert timeout is not None
            stdin_seen.append(input)
            return "changed app/cli/interactive_shell\n", ""

        def poll(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        popen_calls.append((command, kwargs))
        return _FakeProcess()

    monkeypatch.delenv("CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE", raising=False)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.ClaudeCodeAdapter",
        _FakeAdapter,
    )
    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    session.cli_agent_messages.append(
        ("assistant", "Process auto-discovery should scan local agent processes.")
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_claude_code_implementation(
        "implement",
        session,
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert len(popen_calls) == 1
    command, kwargs = popen_calls[0]
    assert command == [
        "/usr/local/bin/claude",
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "acceptEdits",
    ]
    assert kwargs["cwd"]
    assert stdin_seen and "Process auto-discovery" in stdin_seen[0]
    assert session.history[-1] == {"type": "implementation", "text": "implement", "ok": True}
    task = session.task_registry.list_recent(1)[0]
    assert task.kind == TaskKind.CODE_AGENT
    assert task.status == TaskStatus.COMPLETED
    out = buf.getvalue()
    assert "Claude Code started" in out
    assert "Claude Code completed" in out


def test_run_claude_code_implementation_rejects_vague_request_without_context() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_claude_code_implementation(
        "implement",
        session,
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert "too vague" in buf.getvalue()
    assert session.history[-1] == {"type": "implementation", "text": "implement", "ok": False}
    assert session.task_registry.list_recent(1) == []


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


def test_run_opensre_agents_scan_prints_clean_foreground_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[-2:] == ["agents", "scan"]
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="agent scan\n777 claude-code-777 claude code\nNext: register\n",
            stderr="",
        )

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.run", _fake_run)

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert run_opensre_cli_command("agents scan", session, console) is True

    out = buf.getvalue()
    assert "$ opensre agents scan" in out
    assert "agent scan" in out
    assert "777 claude-code-777 claude code" in out
    assert "started." not in out
    assert "stdout │" not in out
    assert session.history[-1] == {"type": "cli_command", "text": "opensre agents scan", "ok": True}


def test_run_opensre_agents_scan_register_explains_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="registered 1 agent(s)\n",
            stderr="",
        )

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.run", _fake_run)

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "agents scan --register",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "register discovered local AI-agent processes" in out
    assert "registered 1 agent(s)" in out
    assert "stdout │" not in out
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre agents scan --register",
        "ok": True,
    }


def test_run_opensre_agents_watch_runs_in_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []

    class _FakeProcess:
        stdout = iter(["watching pid 1234; press Ctrl+C to stop\n", "pid 1234 exited\n"])

        def wait(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        assert command[-3:] == ["agents", "watch", "1234"]
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.Popen", _fake_popen)

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "agents watch 1234",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "$ opensre agents watch 1234" in out
    assert "watching pid 1234" in out
    assert "pid 1234 exited" in out
    assert "started" not in out
    assert "timeout" not in popen_kwargs[0]
    assert popen_kwargs[0]["stderr"] is subprocess.STDOUT
    assert session.task_registry.list_recent() == []
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre agents watch 1234",
        "ok": True,
    }


def test_start_background_cli_task_uses_pty_for_live_terminal_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []
    closed_fds: list[int] = []
    chunks = [b"live progress\r\n"]

    class _TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    class _FakeProcess:
        returncode = 0
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 0

    def _fake_popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    def _fake_read(fd: int, _size: int) -> bytes:
        assert fd == 10
        if chunks:
            return chunks.pop(0)
        raise OSError(errno.EIO, "pty closed")

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.os.openpty", lambda: (10, 11))
    monkeypatch.setattr("app.cli.interactive_shell.action_executor.os.read", _fake_read)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.os.close",
        lambda fd: closed_fds.append(fd),
    )
    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = _TtyBuffer()
    console = Console(file=buf, force_terminal=True)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "app.cli", "tests", "synthetic"],
        session=session,
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
        use_pty=True,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert popen_kwargs[0]["stdout"] == 11
    assert popen_kwargs[0]["stderr"] == 11
    assert "text" not in popen_kwargs[0]
    assert "live progress" in buf.getvalue()
    assert 10 in closed_fds
    assert 11 in closed_fds


def test_run_synthetic_test_unknown_suite_records_failure() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_synthetic_test("nonexistent_suite", session, console)
    assert "unknown synthetic" in buf.getvalue().lower()
    entry = session.history[-1]
    assert entry["type"] == "synthetic_test"
    assert entry["ok"] is False


def test_run_synthetic_test_streams_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []
    popen_commands: list[list[str]] = []

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("collecting fixtures\nrunning investigation\n")
        stderr = io.StringIO("warning: slow cloudwatch response\n")

        def poll(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        popen_commands.append(command)
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_synthetic_test(
        "rds_postgres",
        session,
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert popen_commands[0][1] == "-u"
    assert popen_commands[0][-2:] == ["--scenario", "001-replication-lag"]
    assert popen_kwargs[0]["stdout"] is not None
    assert popen_kwargs[0]["stderr"] is not None
    assert popen_kwargs[0]["text"] is True
    out = buf.getvalue()
    assert "collecting fixtures" in out
    assert "running investigation" in out
    assert "warning: slow cloudwatch response" in out
    task = session.task_registry.list_recent(1)[0]
    assert task.status == TaskStatus.COMPLETED


def test_run_synthetic_test_honours_explicit_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_commands: list[list[str]] = []

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("scenario run\n")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    def _fake_popen(command: list[str], **_kwargs: object) -> _FakeProcess:
        popen_commands.append(command)
        return _FakeProcess()

    monkeypatch.setattr("app.cli.interactive_shell.action_executor.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        "app.cli.interactive_shell.action_executor.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_synthetic_test(
        "rds_postgres:005-failover",
        session,
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert popen_commands[0][-2:] == ["--scenario", "005-failover"]
    assert "opensre tests synthetic --scenario 005-failover" in buf.getvalue()
