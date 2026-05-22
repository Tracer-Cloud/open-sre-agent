"""Tests for CLI-parity slash command subprocess delegation."""

from __future__ import annotations

import io
import subprocess

from rich.console import Console

from app.cli.interactive_shell.command_registry import cli_parity


def _console_buffer() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None, width=80)
    return console, buffer


def test_run_cli_command_replays_captured_stdout_and_stderr(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        calls.update(kwargs)
        return subprocess.CompletedProcess(
            cmd,
            returncode=0,
            stdout="catalog output\n",
            stderr="warning output\n",
        )

    monkeypatch.setattr(cli_parity.subprocess, "run", fake_run)
    console, buffer = _console_buffer()

    assert cli_parity.run_cli_command(console, ["tests", "list"], capture_output=True)

    assert calls["capture_output"] is True
    assert calls["text"] is True
    output = buffer.getvalue()
    assert "catalog output" in output
    assert "warning output" in output


def test_run_cli_command_replays_captured_failure(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(
            cmd,
            returncode=2,
            stdout="",
            stderr="Usage: python -m app.cli guardrails [OPTIONS] COMMAND [ARGS]...\n",
        )

    monkeypatch.setattr(cli_parity.subprocess, "run", fake_run)
    console, buffer = _console_buffer()

    assert cli_parity.run_cli_command(console, ["guardrails"], capture_output=True)

    output = buffer.getvalue()
    assert "Usage: python -m app.cli guardrails" in output
    assert "CLI command exited with non-zero code 2" in output
