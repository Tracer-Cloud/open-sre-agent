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
            completed = subprocess.run(
                command,
                shell=True,
                executable=os.environ.get("SHELL") or None,
                capture_output=True,
                text=True,
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
                timeout=timeout_seconds,
                check=False,
            )
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
