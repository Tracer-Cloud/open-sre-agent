"""Tests for structured REPL shell execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from tools.interactive_shell.shell.execution import (
    ShellExecutionResult,
    execute_shell_command,
)
from tools.interactive_shell.shell.parsing import parse_shell_command


def test_execute_shell_command_reports_timeout_argv_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> NoReturn:  # pragma: no cover
        raise subprocess.TimeoutExpired(
            cmd=["sleep", "999"],
            timeout=1,
            output="partial out\n",
            stderr="partial err\n",
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.subprocess.run",
        _raise,
    )

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

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.subprocess.run",
        _raise,
    )

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


def test_a_missing_binary_is_reported_the_way_a_shell_reports_it() -> None:
    """Not raised: an agent probing for a tool the image lacks is not a fault.

    Left to propagate, ``FileNotFoundError`` reaches the runner's catch-all and
    is filed as an application exception with a stack trace — one per guess.
    """
    result = execute_shell_command(
        command="definitely-not-installed --version",
        argv=["definitely-not-installed", "--version"],
        use_shell=False,
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.exit_code == 127
    assert result.stderr == "definitely-not-installed: command not found"
    assert result.timed_out is False
    assert result.stdout == ""


def test_the_two_paths_agree_about_a_missing_binary() -> None:
    """``/bin/sh`` already answers 127; the argv path must not differ."""
    through_shell = execute_shell_command(
        command="definitely-not-installed --version",
        argv=None,
        use_shell=True,
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert through_shell.exit_code == 127
    assert "not found" in through_shell.stderr


def test_a_non_executable_file_is_distinguished_from_a_missing_one(tmp_path: Path) -> None:
    """126 vs 127 is the difference between "wrong path" and "chmod +x"."""
    script = tmp_path / "not-executable.sh"
    script.write_text("#!/bin/sh\necho hi\n")

    result = execute_shell_command(
        command=str(script),
        argv=[str(script)],
        use_shell=False,
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.exit_code == 126
    assert result.stderr.endswith(": permission denied")


def test_execute_quoted_heredoc_through_shell() -> None:
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""
    parsed = parse_shell_command(command, is_windows=False)
    assert parsed.use_shell is True

    result = execute_shell_command(
        command=parsed.command,
        argv=parsed.argv,
        use_shell=parsed.use_shell,
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello-heredoc" in result.stdout
