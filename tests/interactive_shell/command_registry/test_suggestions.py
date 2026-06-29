"""Tests for slash typo suggestion helpers."""

from __future__ import annotations

import io

from rich.console import Console

from core.agent_harness.session import ReplSession
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.suggestions import (
    format_invalid_subcommand_message,
    format_unknown_slash_message,
    resolve_literal_slash_typo,
)
from surfaces.interactive_shell.runtime.shell_turn_execution import run_action_tool_turn


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_format_unknown_slash_message_without_suggestion_points_to_help() -> None:
    message = format_unknown_slash_message(
        "/made-up",
        command_names=tuple(SLASH_COMMANDS),
    )
    assert message == "Unknown command: /made-up. Type /help for the full command list."


def test_format_unknown_slash_message_with_suggestion() -> None:
    message = format_unknown_slash_message(
        "/modle",
        command_names=tuple(SLASH_COMMANDS),
    )
    assert "Did you mean /model?" in message
    assert "Type /help" in message


def test_resolve_literal_slash_typo_unknown_root() -> None:
    typo = resolve_literal_slash_typo("/invest", SLASH_COMMANDS)
    assert typo is not None
    assert typo.outcome == "unknown_command"
    assert "Did you mean /investigate?" in typo.message


def test_resolve_literal_slash_typo_invalid_subcommand() -> None:
    typo = resolve_literal_slash_typo("/integrations bogus", SLASH_COMMANDS)
    assert typo is not None
    assert typo.outcome == "invalid_subcommand"
    assert "Invalid subcommand: bogus" in typo.message
    assert "/integrations list" in typo.message


def test_format_invalid_subcommand_message_lists_known_subcommands() -> None:
    cmd = SLASH_COMMANDS["/integrations"]
    message = format_invalid_subcommand_message(cmd, ["bogus"])
    assert "Invalid subcommand: bogus" in message
    assert "/integrations list" in message


def test_dispatch_unknown_command_records_full_response_and_outcome() -> None:
    session = ReplSession()
    console, buf = _capture()
    assert dispatch_slash("/modle", session, console) is True
    output = buf.getvalue()
    assert "Unknown command" in output
    latest = session.history[-1]
    assert latest["ok"] is False
    assert latest["slash_outcome"] == "unknown_command"
    assert latest["response_text"] == latest["response_text"].strip()
    assert "Type /help" in latest["response_text"]


def test_run_action_tool_turn_handles_unknown_literal_slash_before_tool_validation() -> None:
    session = ReplSession()
    console, buf = _capture()
    result = run_action_tool_turn("/invest", session, console)
    assert result.handled is True
    assert result.response_text
    assert "Unknown command" in result.response_text
    assert "Unknown command" in buf.getvalue()
    latest = session.history[-1]
    assert latest["type"] == "slash"
    assert latest["slash_outcome"] == "unknown_command"
    assert latest["response_text"] == result.response_text
