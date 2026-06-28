"""Direct unit tests for ``subprocess_runner`` (complement to ``test_agent_actions``)."""

from __future__ import annotations

import errno
import io
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import ReplSession
from integrations.llm_cli.base import CLIInvocation, CLIProbe
from interactive_shell.runtime.subprocess_runner import (
    _MIN_SUBPROCESS_TERMINAL_WIDTH,
    _TASK_OUTPUT_PREFIX_WIDTH,
    _is_interactive_wizard,
    _pump_task_pty,
    _pump_task_stream,
    read_diag,
    read_task_output,
    run_opensre_cli_command,
    start_background_cli_task,
    terminate_child_process,
)
from platform.common.task_types import TaskKind, TaskStatus
from tools.interactive_shell.implementation.claude_code_executor import (
    run_claude_code_implementation,
)
from tools.interactive_shell.shell.execution import (
    ShellExecutionResult,
)
from tools.interactive_shell.shell.runner import (
    run_cd_command,
    run_pwd_command,
    run_shell_command,
)
from tools.interactive_shell.synthetic.runner import (
    run_synthetic_test,
    watch_synthetic_subprocess,
)


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


def test_read_task_output_handles_none_buffer() -> None:
    assert read_task_output(None, limit=100) == ""


def test_read_task_output_strips_ansi_and_caps() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"\x1b[31merror\x1b[0m line\n")
        text = read_task_output(buf, limit=1_000)
    assert text == "error line"


def test_read_task_output_returns_empty_for_closed_buffer() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"data")
    # Buffer is closed once the ``with`` block exits.
    assert read_task_output(buf, limit=100) == ""


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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.chdir",
        _chdir,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_cd_command("cd /tmp/example", session, console)
    assert directories == [Path("/tmp/example")]
    assert session.history[-1]["type"] == "shell"


def test_run_cd_command_reports_chdir_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_errors: list[BaseException] = []

    def _chdir(_target: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.chdir",
        _chdir,
    )
    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_cd_command("cd /root/blocked", session, console)

    assert "cd failed" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], OSError)
    assert session.history[-1] == {
        "type": "shell",
        "text": "cd /root/blocked",
        "ok": False,
        "response_text": "cd failed: permission denied",
    }


def test_run_shell_command_records_when_input_is_empty() -> None:
    """Alpha mode allows every command; only empty input is rejected.

    A bare ``!`` has nothing to run, so the REPL prints a ``blocked`` notice,
    never echoes a command, and records the attempt as ``ok=False``. (Restricted
    commands like ``sudo`` are no longer blocked — there is no deny floor.)
    """
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command(
        "!",
        session,
        console,
        confirm_fn=lambda _p: "n",
        is_tty=True,
    )

    out = buf.getvalue()
    assert "blocked" in out.lower()
    # Nothing should be echoed/executed for empty input.
    assert "$ " not in out
    assert session.history[-1] == {"type": "shell", "text": "!", "ok": False}


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
            return "changed interactive_shell\n", ""

        def poll(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        popen_calls.append((command, kwargs))
        return _FakeProcess()

    monkeypatch.delenv("CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.ClaudeCodeAdapter",
        _FakeAdapter,
    )
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    session.agent.messages.append(
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
        "tools.interactive_shell.shell.execution.execute_shell_command",
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
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("false", session, console)
    out = buf.getvalue()
    assert "✗" in out
    assert "exit 7" in out
    assert session.history[-1] == {
        "type": "shell",
        "text": "false",
        "ok": False,
        "response_text": "✗ exit 7",
    }


def test_run_shell_command_reports_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_errors: list[BaseException] = []

    def _raise(**_kwargs: object) -> ShellExecutionResult:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _raise,
    )
    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("true", session, console)

    assert "command failed to start" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert session.history[-1] == {
        "type": "shell",
        "text": "true",
        "ok": False,
        "response_text": "command failed to start: spawn failed",
    }


def test_run_opensre_agents_scan_prints_clean_foreground_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[-2:] == ["fleet", "scan"]
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="agent scan\n777 claude-code-777 claude code\nNext: register\n",
            stderr="",
        )

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert run_opensre_cli_command("fleet scan", session, console) is True

    out = buf.getvalue()
    assert "$ opensre fleet scan" in out
    assert "agent scan" in out
    assert "777 claude-code-777 claude code" in out
    assert "started." not in out
    assert "stdout │" not in out
    assert session.history[-1] == {"type": "cli_command", "text": "opensre fleet scan", "ok": True}


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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "fleet scan --register",
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
        "text": "opensre fleet scan --register",
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
        assert command[-3:] == ["fleet", "watch", "1234"]
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "fleet watch 1234",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "$ opensre fleet watch 1234" in out
    assert "watching pid 1234" in out
    assert "pid 1234 exited" in out
    assert "started" not in out
    assert "timeout" not in popen_kwargs[0]
    assert popen_kwargs[0]["stderr"] is subprocess.STDOUT
    assert session.task_registry.list_recent() == []
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre fleet watch 1234",
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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.openpty",
        lambda: (10, 11),
        raising=False,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.read",
        _fake_read,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.close",
        lambda fd: closed_fds.append(fd),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = _TtyBuffer()
    console = Console(file=buf, force_terminal=True)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "cli", "tests", "synthetic"],
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


def test_start_background_cli_task_falls_back_to_pipes_when_pty_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []

    class _TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("pipe progress\n")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    def _fake_popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.delattr(
        "interactive_shell.runtime.subprocess_runner.os.openpty",
        raising=False,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = _TtyBuffer()
    console = Console(file=buf, force_terminal=True)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "cli", "tests", "synthetic"],
        session=session,
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
        use_pty=True,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert popen_kwargs[0]["stdout"] is subprocess.PIPE
    assert popen_kwargs[0]["stderr"] is subprocess.PIPE
    assert popen_kwargs[0]["text"] is True
    assert "pipe progress" in buf.getvalue()


def test_start_background_cli_task_logs_failure_outcome_to_posthog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background CLI task that fails asynchronously must still send its real
    stderr/exit outcome to the prompt-log/PostHog sink. This is the regression
    for ``opensre investigate`` errors arriving after the turn recorder flushed.
    """
    captured: list[dict[str, object]] = []

    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "0")
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_LOCAL_DISABLED", "1")
    monkeypatch.setattr(
        "interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda properties: captured.append(properties),
    )

    class _FakeProcess:
        returncode = 1
        stdout = io.StringIO("")
        stderr = io.StringIO("No remote named 'myportfolio' is configured.\n")

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        lambda _command, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre investigate --service myportfolio",
        argv_list=["python", "-m", "cli", "investigate", "--service", "myportfolio"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert len(captured) == 1
    props = captured[0]
    assert props["cli_turn_kind"] == "background_task"
    assert props["$ai_trace_id"] == task.task_id
    ai_input = props["$ai_input"]
    assert isinstance(ai_input, list)
    assert ai_input[0]["content"] == "opensre investigate --service myportfolio"
    choices = props["$ai_output_choices"]
    assert isinstance(choices, list)
    content = choices[0]["content"]
    assert "command failed (exit 1)" in content
    assert "No remote named 'myportfolio' is configured." in content


def test_start_background_cli_task_logs_success_outcome_to_posthog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful background task logs its stdout outcome, not an empty reply."""
    captured: list[dict[str, object]] = []

    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "0")
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_LOCAL_DISABLED", "1")
    monkeypatch.setattr(
        "interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda properties: captured.append(properties),
    )

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("investigation complete: root cause identified\n")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        lambda _command, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre investigate --service checkout",
        argv_list=["python", "-m", "cli", "investigate", "--service", "checkout"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert len(captured) == 1
    content = captured[0]["$ai_output_choices"][0]["content"]
    assert "command completed (exit 0)" in content
    assert "investigation complete: root cause identified" in content


def test_task_output_stream_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    class _BrokenStream:
        def __iter__(self) -> _BrokenStream:
            return self

        def __next__(self) -> str:
            raise RuntimeError("stream broke")

    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = ReplSession()
    task = session.task_registry.create(TaskKind.CLI_COMMAND, command="demo")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    _pump_task_stream(
        task=task,
        stream_name="stdout",
        stream=_BrokenStream(),  # type: ignore[arg-type]
        console=console,
    )

    assert "stream ended unexpectedly" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_task_pty_stream_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    closed_fds: list[int] = []

    def _raise_read(_fd: int, _size: int) -> bytes:
        raise RuntimeError("pty broke")

    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.read",
        _raise_read,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.os.close",
        lambda fd: closed_fds.append(fd),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    with tempfile.SpooledTemporaryFile() as capture:  # type: ignore[type-arg]
        _pump_task_pty(master_fd=123, console=console, capture=capture)

    assert "terminal stream ended unexpectedly" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert closed_fds == [123]


def test_start_background_cli_task_reports_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    def _fake_popen(_command: list[str], **_kwargs: object) -> object:
        raise RuntimeError("spawn broke")

    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "cli", "tests", "synthetic"],
        session=session,
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
    )

    assert task is None
    assert "failed to start" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert session.task_registry.list_recent(1)[0].status == TaskStatus.FAILED


def test_start_background_cli_task_reports_watcher_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    class _FakeProcess:
        stdout = None
        stderr = None
        returncode = 1

        def poll(self) -> int:
            return 1

    def _fake_popen(_command: list[str], **_kwargs: object) -> _FakeProcess:
        return _FakeProcess()

    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.read_diag",
        lambda _buf: (_ for _ in ()).throw(RuntimeError("diag broke")),
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "cli", "tests", "synthetic"],
        session=session,
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
    )

    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert "error:" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_start_background_cli_task_skips_follow_up_after_session_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProcess:
        stdout = None
        stderr = None
        returncode = 1

        def poll(self) -> int:
            return 1

    class _DeferredThread:
        pending: list[object] = []

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
            del group, name, daemon, args, kwargs
            if callable(target):
                _DeferredThread.pending.append(target)

        def start(self) -> None:
            return

    def _fake_popen(_command: list[str], **_kwargs: object) -> _FakeProcess:
        return _FakeProcess()

    _DeferredThread.pending.clear()
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _DeferredThread,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["python", "-m", "cli", "tests", "synthetic"],
        session=session,
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
    )
    assert task is not None
    assert len(_DeferredThread.pending) == 1
    session.clear()
    _DeferredThread.pending[0]()  # type: ignore[operator]
    assert session.pending_prompt_default is None
    _DeferredThread.pending.clear()


def test_watch_synthetic_subprocess_reports_daemon_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    class _FakeProcess:
        stdout = None
        stderr = None

        def poll(self) -> int:
            raise RuntimeError("poll broke")

    monkeypatch.setattr(
        "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST, command="suite")
    task.mark_running()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    with tempfile.SpooledTemporaryFile() as stderr_buf:  # type: ignore[type-arg]
        watch_synthetic_subprocess(
            task,
            _FakeProcess(),  # type: ignore[arg-type]
            session,
            "suite:001-test",
            stderr_buf,
            console,
        )

    assert task.status == TaskStatus.FAILED
    assert "synthetic watcher failed" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
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


def test_run_synthetic_test_all_launches_suite_alias(
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

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_synthetic_test(
        "rds_postgres:all",
        session,
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert popen_commands[0][-2:] == ["synthetic", "all"]
    assert "opensre tests synthetic all" in buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess terminal width forwarding
# ─────────────────────────────────────────────────────────────────────────────
#
# Regression: subprocess Rich output (synthetic suite panels and tables) used
# to render at the default 80-column width because the subprocess's stdout is
# a pipe. ``_print_task_output_line`` then prepended an 18-char ``<task_id>
# <stream> │ `` prefix, producing 98-char lines that wrapped mid-row in the
# user's narrower terminal — the visible symptom was broken table headers and
# panel borders. We forward ``user_width - prefix - 1`` via ``COLUMNS`` so the
# subprocess renders narrow enough that the relayed line fits intact.


class _CapturedPopen:
    returncode = 0
    stdout = io.StringIO("")
    stderr = io.StringIO("")

    def poll(self) -> int:
        return 0


def _capture_popen_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _fake_popen(_command: list[str], **kwargs: object) -> _CapturedPopen:
        captured.append(kwargs)
        return _CapturedPopen()

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )
    return captured


def _start_one_task(console: Console) -> None:
    start_background_cli_task(
        display_command="opensre tests synthetic --scenario 001-replication-lag",
        argv_list=["opensre", "tests", "synthetic", "--scenario", "001-replication-lag"],
        session=ReplSession(),
        console=console,
        kind=TaskKind.SYNTHETIC_TEST,
    )


def test_background_task_forwards_columns_minus_prefix_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess COLUMNS = user_width − task-output prefix − 1.

    A 100-col user terminal must hand the subprocess ``100 − 18 − 1 = 81``
    columns so its Rich rendering fits within the user's terminal once the
    18-char ``<task_id> stdout │ `` prefix is prepended by the line pump.
    """
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    _start_one_task(console)

    assert captured, "Popen must have been called"
    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("COLUMNS") == str(100 - _TASK_OUTPUT_PREFIX_WIDTH - 1)


def test_background_task_floors_subprocess_columns_for_tiny_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COLUMNS must never go below ``_MIN_SUBPROCESS_TERMINAL_WIDTH``.

    On absurdly narrow terminals (CI jobs, restricted SSH PTYs) the naive
    ``width − prefix − 1`` calculation could fall below the point where Rich
    panels render at all. We floor at 60 so borders remain drawable; visible
    wrapping is better than a crushed rendering.
    """
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=40)

    _start_one_task(console)

    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("COLUMNS") == str(_MIN_SUBPROCESS_TERMINAL_WIDTH)


def test_background_task_preserves_existing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forwarded env must inherit ``os.environ`` so the subprocess sees PATH etc.

    We only inject COLUMNS/LINES; everything else (PATH, HOME, virtualenv,
    auth tokens) must reach the synthetic suite unchanged.
    """
    monkeypatch.setenv("OPENSRE_TEST_MARKER", "preserved-value")
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=120)

    _start_one_task(console)

    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("OPENSRE_TEST_MARKER") == "preserved-value"


def test_run_synthetic_test_forwards_columns_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_synthetic_test`` opens its own Popen — it must also forward COLUMNS.

    Regression: this code path bypasses ``start_background_cli_task`` and so
    was missed by the first iteration of the width-forwarding fix. The
    visible symptom was that the synthetic suite's Rich Panel and Table
    output (the per-scenario "Synthetic RDS Run" panel, the "Synthetic
    Suite Report" table, and the "Level Summary" table) rendered at the
    pipe-default 80 columns and then wrapped mid-row in the user's terminal
    once the 18-char ``<task_id> stdout │ `` prefix had been prepended.
    """
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=110)

    run_synthetic_test(
        "rds_postgres:005-failover",
        ReplSession(),
        console,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    assert captured, "run_synthetic_test must spawn at least one subprocess"
    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("COLUMNS") == str(110 - _TASK_OUTPUT_PREFIX_WIDTH - 1)


@pytest.mark.parametrize(
    "tokens,expected",
    [
        # Single-token interactive wizards.
        (["onboard"], True),
        (["ONBOARD"], True),  # case-insensitive
        (["onboard", "local_llm"], True),  # extra args still classified
        # Two-token interactive wizard.
        (["integrations", "setup"], True),
        (["INTEGRATIONS", "SETUP"], True),
        (["integrations", "setup", "datadog"], True),
        # Two-token NON-wizard under integrations — must NOT match.
        (["integrations", "list"], False),
        (["integrations", "verify"], False),
        # Other subcommands — must NOT match.
        (["health"], False),
        (["version"], False),
        (["fleet", "list"], False),
        # Edge: empty.
        ([], False),
    ],
)
def test_is_interactive_wizard_classifies_command_paths(tokens: list[str], expected: bool) -> None:
    """The wizard classifier is the data-driven contract for the LLM-classified
    path (``cli_exec`` tool). When the LLM tries to invoke a wizard via
    ``cli_exec``, we redirect the user to the equivalent slash command instead
    because exclusive stdin is only guaranteed for the slash-command path.
    Adding a new interactive command later should be a one-line set entry —
    this test pins the current set + the case-insensitive lookup behavior.
    """
    assert _is_interactive_wizard(tokens) is expected


def test_run_opensre_cli_command_refuses_onboard_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM-classified path (``cli_exec`` tool) for ``opensre onboard``
    must not spawn a subprocess — exclusive stdin is not guaranteed when the
    LLM planner is involved. It must instead print a message directing the
    user to the ``/onboard`` slash command, which does have exclusive stdin.
    """
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    def _fake_popen(command: list[str], **_kwargs: object) -> None:
        popen_calls.append(command)
        raise AssertionError("subprocess.Popen must not be called for interactive subcommand")

    def _fake_run(command: list[str], **_kwargs: object) -> None:
        run_calls.append(command)
        raise AssertionError("subprocess.run must not be called for interactive subcommand")

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = ReplSession()
    buf = io.StringIO()
    # Width >80 so the multi-line warning doesn't wrap mid-substring on
    # the assertions below.
    console = Console(file=buf, force_terminal=False, width=200)

    assert (
        run_opensre_cli_command(
            "onboard",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" in out
    assert "opensre onboard" in out
    # Directs user to the slash command (not "exit the shell").
    assert "/onboard" in out
    assert popen_calls == []
    assert run_calls == []
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre onboard",
        "ok": False,
    }


def test_run_opensre_cli_command_refuses_integrations_setup_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opensre integrations setup`` via the LLM ``cli_exec`` path must redirect
    to ``/integrations setup`` (which has exclusive stdin). Same pattern as ``onboard``.
    """
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.Popen",
        lambda cmd, **_kw: popen_calls.append(cmd),
    )
    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.subprocess.run",
        lambda cmd, **_kw: run_calls.append(cmd),
    )

    session = ReplSession()
    buf = io.StringIO()
    # Width >80 so the multi-line warning doesn't wrap mid-substring on
    # assertions below.
    console = Console(file=buf, force_terminal=False, width=200)

    assert (
        run_opensre_cli_command(
            "integrations setup",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" in out
    assert "opensre integrations setup" in out
    # Directs user to the slash command (not "exit the shell").
    assert "/integrations setup" in out
    assert popen_calls == []
    assert run_calls == []
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre integrations setup",
        "ok": False,
    }


def test_run_opensre_cli_command_skips_confirmation_for_investigate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opensre investigate`` is the primary REPL purpose — no Proceed? prompt."""
    confirm_calls: list[str] = []
    start_calls: list[list[str]] = []

    def _fake_confirm(prompt: str) -> str:
        confirm_calls.append(prompt)
        return "n"

    def _fake_start_background_cli_task(*, argv_list: list[str], **_kw: object) -> None:
        start_calls.append(argv_list)

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.start_background_cli_task",
        _fake_start_background_cli_task,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "investigate -i alert.json",
            session,
            console,
            confirm_fn=_fake_confirm,
            is_tty=True,
        )
        is True
    )

    assert confirm_calls == []
    assert start_calls
    assert "investigate" in start_calls[0]
    assert "-i" in start_calls[0]
    assert "alert.json" in start_calls[0]
    assert "Proceed?" not in buf.getvalue()
    assert "may change local config" not in buf.getvalue()


def test_run_opensre_cli_command_allows_integrations_list_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the ``setup`` subcommand under ``integrations`` is the wizard.
    Other ``integrations`` subcommands like ``integrations list`` must
    not get caught by the interactive-wizard block — guard against an
    over-broad refusal.
    """
    start_calls: list[list[str]] = []

    def _fake_start_background_cli_task(*, argv_list: list[str], **_kw: object) -> None:
        start_calls.append(argv_list)

    monkeypatch.setattr(
        "interactive_shell.runtime.subprocess_runner.start_background_cli_task",
        _fake_start_background_cli_task,
    )

    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "integrations list",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" not in out
    # The dispatcher should have reached the background-task path
    # (proving the wizard block didn't fire).
    assert start_calls, "background task starter was not invoked"
    assert "integrations" in start_calls[0]
    assert "list" in start_calls[0]
