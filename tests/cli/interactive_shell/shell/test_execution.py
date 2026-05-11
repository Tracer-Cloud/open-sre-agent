"""Tests for structured REPL shell execution."""

from __future__ import annotations

import subprocess
from typing import NoReturn

import pytest

from app.cli.interactive_shell.shell.execution import ShellExecutionResult, execute_shell_command


def test_execute_shell_command_reports_timeout_argv_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> NoReturn:  # pragma: no cover
        raise subprocess.TimeoutExpired(
            cmd=["sleep", "999"],
            timeout=1,
            output="partial out\n",
            stderr="partial err\n",
        )

    monkeypatch.setattr("app.cli.interactive_shell.shell.execution.subprocess.run", _raise)

    result = execute_shell_command(
        command="ignored",
        argv=["sleep", "999"],
        use_shell=False,
        timeout_seconds=1,
        max_output_chars=10_000,
    )

    assert result == ShellExecutionResult(
        command="ignored",
        argv=["sleep", "999"],
        stdout="partial out\n",
        stderr="partial err\n",
        exit_code=None,
        timed_out=True,
        truncated=False,
        executed_with_shell=False,
    )


def test_execute_shell_command_reports_timeout_shell_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> NoReturn:  # pragma: no cover
        raise subprocess.TimeoutExpired(
            cmd="sleep 999",
            timeout=1,
            output="out\n",
            stderr="err\n",
        )

    monkeypatch.setattr("app.cli.interactive_shell.shell.execution.subprocess.run", _raise)

    result = execute_shell_command(
        command="sleep 999",
        argv=None,
        use_shell=True,
        timeout_seconds=1,
        max_output_chars=10_000,
    )

    assert result == ShellExecutionResult(
        command="sleep 999",
        argv=None,
        stdout="out\n",
        stderr="err\n",
        exit_code=None,
        timed_out=True,
        truncated=False,
        executed_with_shell=True,
    )
