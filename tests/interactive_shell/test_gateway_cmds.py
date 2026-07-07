"""Tests for the /gateway slash command."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry.gateway_cmds import _cmd_gateway

_MODULE = "surfaces.interactive_shell.command_registry.gateway_cmds"


@pytest.fixture
def console() -> Console:
    return Console(record=True, force_terminal=False, width=200)


def _output(console: Console) -> str:
    return console.export_text()


def test_status_shows_running_daemon_and_components(
    monkeypatch: pytest.MonkeyPatch, console: Console
) -> None:
    monkeypatch.setattr(f"{_MODULE}.gateway_daemon_pid", lambda: 4242)
    monkeypatch.setattr(
        f"{_MODULE}.read_component_status",
        lambda: {"web": "serving :8000", "telegram": "polling for messages"},
    )

    assert _cmd_gateway(MagicMock(), console, ["status"]) is True

    out = _output(console)
    assert "running (pid 4242)" in out
    assert "web: serving :8000" in out
    assert "telegram: polling for messages" in out


def test_bare_gateway_defaults_to_status(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    monkeypatch.setattr(f"{_MODULE}.gateway_daemon_pid", lambda: None)
    monkeypatch.setattr(f"{_MODULE}.read_component_status", dict)

    assert _cmd_gateway(MagicMock(), console, []) is True
    assert "stopped" in _output(console)


def test_start_reports_outcome_and_log_path(
    monkeypatch: pytest.MonkeyPatch, console: Console
) -> None:
    monkeypatch.setattr(
        f"{_MODULE}.start_gateway_daemon",
        lambda: (True, "OpenSRE gateway started (pid 7)."),
    )

    assert _cmd_gateway(MagicMock(), console, ["start"]) is True

    out = _output(console)
    assert "started (pid 7)" in out
    assert "gateway.log" in out


def test_logs_prints_tail(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    monkeypatch.setattr(f"{_MODULE}.read_gateway_log_tail", lambda lines: f"tail of {lines}")

    assert _cmd_gateway(MagicMock(), console, ["logs", "7"]) is True
    assert "tail of 7" in _output(console)


def test_unknown_subcommand_prints_usage(console: Console) -> None:
    assert _cmd_gateway(MagicMock(), console, ["restart"]) is True
    assert "usage:" in _output(console)
