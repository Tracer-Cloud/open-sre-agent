"""Structured shell command execution helpers for the interactive REPL."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellExecutionResult:
    """Normalized command execution output."""

    command: str
    argv: list[str] | None
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    executed_with_shell: bool


#: POSIX shell exit conventions. A command the shell could not run is reported
#: with these rather than raised, and the ``argv`` path here answers the same
#: way ``/bin/sh`` does on the shell path — see :func:`_exec_failure`.
_COMMAND_NOT_FOUND = 127
_COMMAND_NOT_EXECUTABLE = 126


def _truncate_output(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return f"{text[:max_chars].rstrip()}\n... output truncated ...", True


def _text_from_timeout_stream(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        return [shell, "/d", "/s", "/c", command]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-lc", command]


def _exec_failure(
    *,
    command: str,
    argv: list[str] | None,
    use_shell: bool,
    exc: OSError,
) -> ShellExecutionResult:
    """Render a failed ``exec`` as a result, the way a shell reports one.

    A binary that is not installed is an ordinary answer to running a command
    somebody typed, not a fault in this process. On the shell path it already
    arrives as exit 127 with ``not found`` on stderr, because ``/bin/sh``
    absorbs it; on the ``argv`` path Python raises ``FileNotFoundError``
    instead. Left to propagate, that difference means the same missing command
    is a clean tool result one way and a stack trace — and a Sentry event —
    the other. An agent probing for ``kubectl`` in a container that has none
    then files a bug report per guess.
    """
    name = exc.filename or (argv[0] if argv else command)
    if isinstance(exc, FileNotFoundError):
        exit_code, detail = _COMMAND_NOT_FOUND, "command not found"
    elif isinstance(exc, PermissionError):
        exit_code, detail = _COMMAND_NOT_EXECUTABLE, "permission denied"
    else:
        exit_code, detail = _COMMAND_NOT_FOUND, exc.strerror or "could not be started"
    return ShellExecutionResult(
        command=command,
        argv=argv,
        stdout="",
        stderr=f"{name}: {detail}",
        exit_code=exit_code,
        timed_out=False,
        truncated=False,
        executed_with_shell=use_shell,
    )


def execute_shell_command(
    *,
    command: str,
    argv: list[str] | None,
    use_shell: bool,
    timeout_seconds: int,
    max_output_chars: int,
) -> ShellExecutionResult:
    """Execute a command and return a structured result object."""
    try:
        if use_shell:
            # Intentional REPL shell passthrough for local terminal commands.
            # The caller runs through interactive confirmation/policy first and
            # records that the command used a shell in ShellExecutionResult.
            completed = subprocess.run(
                _shell_argv(command),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        else:
            if argv is None:
                raise ValueError("argv is required for shell=False execution.")
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
    except OSError as exc:
        return _exec_failure(command=command, argv=argv, use_shell=use_shell, exc=exc)
    except subprocess.TimeoutExpired as exc:
        stdout = _text_from_timeout_stream(exc.stdout)
        stderr = _text_from_timeout_stream(exc.stderr)
        stdout, truncated_stdout = _truncate_output(
            stdout,
            max_chars=max_output_chars,
        )
        stderr, truncated_stderr = _truncate_output(
            stderr,
            max_chars=max_output_chars,
        )
        return ShellExecutionResult(
            command=command,
            argv=argv,
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            timed_out=True,
            truncated=truncated_stdout or truncated_stderr,
            executed_with_shell=use_shell,
        )

    stdout, truncated_stdout = _truncate_output(
        completed.stdout or "",
        max_chars=max_output_chars,
    )
    stderr, truncated_stderr = _truncate_output(
        completed.stderr or "",
        max_chars=max_output_chars,
    )
    return ShellExecutionResult(
        command=command,
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=completed.returncode,
        timed_out=False,
        truncated=truncated_stdout or truncated_stderr,
        executed_with_shell=use_shell,
    )


__all__ = ["ShellExecutionResult", "execute_shell_command"]
